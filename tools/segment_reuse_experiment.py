"""Run controlled native-prefix vs segment-reuse smoke experiments.

This script talks directly to the OpenAI-compatible serving endpoint. It does
not use /chat, so service-layer semantic/knowledge caches are bypassed. The
goal is to keep the application-side experiment honest:

* Baseline: E|B prompt order, no cache_salt, no segment hint.
* Native prefix: B|E prompt order, cache_salt enabled.
* Segment hint: E|B prompt order, extra_key/markers enabled.

Seeing extra_key accepted is not counted as segment stitch success. The output
reports engine-side segment metrics only when the serving engine exposes them.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from benchmark_vllm_latency import _derive_metrics_url, _load_env, _metric_delta
except ModuleNotFoundError:  # pragma: no cover - used when imported as tools.*
    from tools.benchmark_vllm_latency import _derive_metrics_url, _load_env, _metric_delta


BODY_TOKEN_OPTIONS = (512, 2048, 8192)
ENVELOPE_TOKEN_OPTIONS = (32, 128, 512)
CONCURRENCY_OPTIONS = (1, 4, 16)
VARIANTS = ("baseline_e_b", "native_b_e", "segment_hint_e_b")
WORKLOADS = ("policy_first", "task_first", "agent_state_first")

BODY_MARKER_BEGIN = "<SEGMENT_REUSE_BODY"
BODY_MARKER_END = "</SEGMENT_REUSE_BODY>"

METRIC_ALIASES = {
    "vllm:prefix_cache_queries": "prefix_cache_queries",
    "vllm:prefix_cache_queries_total": "prefix_cache_queries",
    "vllm:prefix_cache_hits": "prefix_cache_hits",
    "vllm:prefix_cache_hits_total": "prefix_cache_hits",
    "vllm:prefix_cache_block_queries": "prefix_cache_block_queries",
    "vllm:prefix_cache_block_queries_total": "prefix_cache_block_queries",
    "vllm:prefix_cache_block_hits": "prefix_cache_block_hits",
    "vllm:prefix_cache_block_hits_total": "prefix_cache_block_hits",
    "vllm:prefix_cache_blocks_cached": "prefix_cache_blocks_cached",
    "vllm:prefix_cache_blocks_cached_total": "prefix_cache_blocks_cached",
    "vllm:prompt_tokens_cached_total": "native_prefix_tokens_cached",
    "vllm:prompt_tokens_by_source_total": "prompt_tokens_by_source",
    "vllm:request_prompt_tokens_sum": "request_prompt_tokens",
    "vllm:request_generation_tokens_sum": "request_generation_tokens",
    "vllm:request_prefill_time_seconds_sum": "prefill_time_s",
    "vllm:request_decode_time_seconds_sum": "decode_time_s",
    "vllm:e2e_request_latency_seconds_sum": "engine_e2e_latency_s",
    "vllm:segment_reuse_queries": "segment_reuse_queries",
    "vllm:segment_reuse_queries_total": "segment_reuse_queries",
    "vllm:segment_reuse_hits": "segment_reuse_hits",
    "vllm:segment_reuse_hits_total": "segment_reuse_hits",
    "vllm:segment_reuse_stitch_committed": "segment_stitch_committed",
    "vllm:segment_reuse_stitch_committed_total": "segment_stitch_committed",
    "vllm:segment_reuse_reused_body_tokens": "segment_reused_body_tokens",
    "vllm:segment_reuse_reused_body_tokens_total": "segment_reused_body_tokens",
    "vllm:segment_reuse_fresh_envelope_tokens": "segment_fresh_envelope_tokens",
    "vllm:segment_reuse_fresh_envelope_tokens_total": "segment_fresh_envelope_tokens",
    "vllm:segment_reuse_pinned_body_blocks": "segment_pinned_body_blocks",
    "vllm:segment_reuse_pinned_body_blocks_total": "segment_pinned_body_blocks",
    "vllm:segment_reuse_tokens_recomputed": "segment_tokens_recomputed",
    "vllm:segment_reuse_tokens_recomputed_total": "segment_tokens_recomputed",
}


@dataclass(frozen=True)
class TrialConfig:
    variant: str
    workload: str
    body_tokens: int
    envelope_tokens: int
    concurrency: int
    repeated_mode: str


def _read_metrics(metrics_url: str, *, api_key: str) -> dict[str, float]:
    selected = {
        "prefix_cache_queries": 0.0,
        "prefix_cache_hits": 0.0,
        "prefix_cache_block_queries": 0.0,
        "prefix_cache_block_hits": 0.0,
        "prefix_cache_blocks_cached": 0.0,
        "native_prefix_tokens_cached": 0.0,
        "request_prompt_tokens": 0.0,
        "request_generation_tokens": 0.0,
        "prefill_time_s": 0.0,
        "decode_time_s": 0.0,
        "engine_e2e_latency_s": 0.0,
        "segment_reuse_queries": 0.0,
        "segment_reuse_hits": 0.0,
        "segment_stitch_committed": 0.0,
        "segment_reused_body_tokens": 0.0,
        "segment_fresh_envelope_tokens": 0.0,
        "segment_pinned_body_blocks": 0.0,
        "segment_tokens_recomputed": 0.0,
    }
    if not metrics_url:
        return selected
    try:
        request = urllib.request.Request(
            metrics_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )
        with urllib.request.urlopen(request, timeout=3.0) as response:
            text = response.read().decode("utf-8", errors="replace")
    except (TimeoutError, OSError, urllib.error.URLError):
        return selected

    for raw in text.splitlines():
        if not raw or raw.startswith("#"):
            continue
        name = raw.split("{", 1)[0].split(" ", 1)[0]
        alias = METRIC_ALIASES.get(name)
        if alias == "prompt_tokens_by_source":
            if 'source="local_cache_hit"' not in raw:
                continue
            alias = "native_prefix_tokens_cached"
        if alias not in selected:
            continue
        try:
            selected[alias] += float(raw.rsplit(" ", 1)[-1])
        except ValueError:
            continue
    return selected


def _approx_text(label: str, target_tokens: int) -> str:
    # Roughly 12 English-ish tokens per sentence. Exact token counts are not
    # important here because the serving engine owns exact boundary resolution.
    sentence = (
        f"{label} reusable systems note about KV cache, prefix reuse, scheduling, "
        "permission scope, tokenizer-stable markers, and latency measurement. "
    )
    repeats = max(1, target_tokens // 12)
    return (sentence * repeats).strip()


def _body_text(target_tokens: int, *, workload: str) -> str:
    if workload == "policy_first":
        seed = (
            "BODY shared public lab handbook. It contains research routes, "
            "onboarding steps, paper-reading expectations, experiment hygiene, "
            "systems project rubrics, and examples of reusable guidance. "
        )
    elif workload == "task_first":
        seed = (
            "BODY shared technical corpus. It describes KV cache reuse, "
            "PagedAttention, prefix caching, segment stitch, admission control, "
            "allocator pinning, demotion reasons, and evaluation metrics. "
        )
    elif workload == "agent_state_first":
        seed = (
            "BODY shared tool and skill manual. It lists available tools, "
            "schemas, safety contracts, planner conventions, workflow actions, "
            "and stable instructions for answer composition. "
        )
    else:
        raise ValueError(f"unknown workload: {workload}")
    repeats = max(1, target_tokens // 18)
    return (seed * repeats).strip()


def _envelope_text(
    target_tokens: int,
    request_id: int,
    *,
    stable: bool,
    workload: str,
) -> str:
    unique = "stable" if stable else f"request-{request_id}-{time.time_ns()}"
    if workload == "policy_first":
        seed = (
            "ENVELOPE dynamic access policy first. The current visitor profile, "
            "permission scope, audit id, and redaction rule must be interpreted "
            "before reading the shared handbook. "
        )
        task = "Apply this policy before using the body. Answer with exactly two concise bullets."
    elif workload == "task_first":
        seed = (
            "ENVELOPE dynamic task instruction first. This request asks for a "
            "specific analysis objective, evaluation axis, output format, and "
            "fresh request id before reading the shared technical corpus. "
        )
        task = "Use the body only for this task. Answer with exactly two concise bullets."
    elif workload == "agent_state_first":
        seed = (
            "ENVELOPE dynamic agent state first. The current planner step, "
            "completed tool calls, pending constraint, and failure budget must "
            "be processed before reading the shared tool manual. "
        )
        task = "Follow this agent state before using the body. Answer with exactly two concise bullets."
    else:
        raise ValueError(f"unknown workload: {workload}")
    repeats = max(1, target_tokens // 18)
    return (seed * repeats).strip() + f"\nDynamic request id: {unique}. {task}"


def _segment_extra_key(
    *,
    model: str,
    scope: str,
    body: str,
    body_tokens: int,
    request_id: int,
) -> str:
    digest = hashlib.sha256(f"{model}\n{body}".encode("utf-8")).hexdigest()[:16]
    namespace = f"sage-mate:experiment:{scope}:{digest}"
    return (
        f"{namespace}||"
        "segreuse:v1;mode=leading-envelope;segments=1;"
        f"tokens={body_tokens};leading_tokens=;"
        "boundary_class=control-only;"
        "attention_contract=control-envelope-excluded-from-model-body;"
        f"note=source=twin-experiment|body={digest}|rid={request_id}"
    )


def _build_messages(
    config: TrialConfig,
    *,
    model: str,
    request_id: int,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    body = _body_text(config.body_tokens, workload=config.workload)
    stable_envelope = config.repeated_mode == "hot"
    envelope = _envelope_text(
        config.envelope_tokens,
        request_id,
        stable=stable_envelope,
        workload=config.workload,
    )
    if config.repeated_mode == "mixed" and request_id % 2 == 0:
        envelope = _envelope_text(
            config.envelope_tokens,
            0,
            stable=True,
            workload=config.workload,
        )

    extras: dict[str, Any] = {}
    if config.variant == "baseline_e_b":
        system = f"{envelope}\n\n{BODY_MARKER_BEGIN}>{body}{BODY_MARKER_END}"
    elif config.variant == "native_b_e":
        system = f"{BODY_MARKER_BEGIN}>{body}{BODY_MARKER_END}\n\n{envelope}"
        extras["cache_salt"] = (
            f"native-prefix:{model}:{config.workload}:"
            f"{config.body_tokens}:{config.envelope_tokens}"
        )
    elif config.variant == "segment_hint_e_b":
        body_digest = hashlib.sha256(f"{model}\n{body}".encode("utf-8")).hexdigest()[:16]
        system = (
            f"{envelope}\n\n"
            f"{BODY_MARKER_BEGIN} digest={body_digest} scope=lab_member>"
            f"{body}{BODY_MARKER_END}"
        )
        extras["extra_key"] = _segment_extra_key(
            model=model,
            scope="lab_member",
            body=body,
            body_tokens=config.body_tokens,
            request_id=request_id,
        )
    else:
        raise ValueError(f"unknown variant: {config.variant}")

    return (
        [
            {"role": "system", "content": system},
            {"role": "user", "content": "Summarize the relevant reusable body for this envelope."},
        ],
        extras,
    )


def _call_chat(
    *,
    host: str,
    model: str,
    api_key: str,
    config: TrialConfig,
    request_id: int,
    max_tokens: int,
    retries: int,
) -> dict[str, Any]:
    messages, extras = _build_messages(config, model=model, request_id=request_id)
    body: dict[str, Any] = {
        "model": model,
        "stream": False,
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "messages": messages,
        "chat_template_kwargs": {"enable_thinking": False},
        **extras,
    }
    request = urllib.request.Request(
        f"{host.rstrip('/')}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    started = time.perf_counter()
    last_error = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            elapsed_s = time.perf_counter() - started
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {exc.code}: {detail[:240]}"
            if exc.code < 500 or attempt >= retries:
                raise
            time.sleep(min(2.0 * (attempt + 1), 8.0))
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt >= retries:
                raise
            time.sleep(min(2.0 * (attempt + 1), 8.0))
    else:  # pragma: no cover - loop always breaks or raises
        raise RuntimeError(last_error or "chat completion failed")
    usage = payload.get("usage") or {}
    return {
        "elapsed_s": elapsed_s,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "extra_key_sent": "extra_key" in extras,
        "cache_salt_sent": "cache_salt" in extras,
    }


def _run_trial(
    *,
    host: str,
    model: str,
    api_key: str,
    metrics_url: str,
    config: TrialConfig,
    requests_per_trial: int,
    max_tokens: int,
    retries: int,
) -> dict[str, Any]:
    before = _read_metrics(metrics_url, api_key=api_key)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        futures = [
            executor.submit(
                _call_chat,
                host=host,
                model=model,
                api_key=api_key,
                config=config,
                request_id=index,
                max_tokens=max_tokens,
                retries=retries,
            )
            for index in range(requests_per_trial)
        ]
        for future in as_completed(futures):
            results.append(future.result())
    after = _read_metrics(metrics_url, api_key=api_key)
    delta = _metric_delta(before, after)
    latencies = [item["elapsed_s"] for item in results]
    sorted_latencies = sorted(latencies)
    p95_index = math.ceil(0.95 * len(sorted_latencies)) - 1 if sorted_latencies else 0
    prefix_queries = delta.get("prefix_cache_queries", 0.0)
    prefix_hits = delta.get("prefix_cache_hits", 0.0)
    segment_queries = delta.get("segment_reuse_queries", 0.0)
    segment_committed = delta.get("segment_stitch_committed", 0.0)
    return {
        "variant": config.variant,
        "workload": config.workload,
        "body_tokens_target": config.body_tokens,
        "envelope_tokens_target": config.envelope_tokens,
        "concurrency": config.concurrency,
        "repeated_mode": config.repeated_mode,
        "requests": len(results),
        "median_e2e_s": statistics.median(latencies) if latencies else 0.0,
        "p95_e2e_s": sorted_latencies[p95_index] if sorted_latencies else 0.0,
        "mean_prompt_tokens": statistics.mean(item["prompt_tokens"] for item in results)
        if results
        else 0.0,
        "extra_key_sent": any(item["extra_key_sent"] for item in results),
        "cache_salt_sent": any(item["cache_salt_sent"] for item in results),
        "prefix_hit_rate": prefix_hits / prefix_queries if prefix_queries else 0.0,
        "prefix_cache_queries": prefix_queries,
        "prefix_cache_hits": prefix_hits,
        "prefix_cache_block_queries": delta.get("prefix_cache_block_queries", 0.0),
        "prefix_cache_block_hits": delta.get("prefix_cache_block_hits", 0.0),
        "native_prefix_tokens_cached": delta.get("native_prefix_tokens_cached", 0.0),
        "segment_reuse_queries": segment_queries,
        "segment_reuse_hits": delta.get("segment_reuse_hits", 0.0),
        "segment_stitch_committed": segment_committed,
        "segment_reused_body_tokens": delta.get("segment_reused_body_tokens", 0.0),
        "segment_fresh_envelope_tokens": delta.get("segment_fresh_envelope_tokens", 0.0),
        "segment_pinned_body_blocks": delta.get("segment_pinned_body_blocks", 0.0),
        "segment_tokens_recomputed": delta.get("segment_tokens_recomputed", 0.0),
        "engine_metrics_delta": delta,
        "segment_stitch_observed": segment_committed > 0
        or delta.get("segment_reused_body_tokens", 0.0) > 0,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [key for key in rows[0].keys() if key != "engine_metrics_delta"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--host", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--metrics-url", default=None)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--requests-per-trial", type=int, default=4)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--body-tokens", default="512,2048")
    parser.add_argument("--envelope-tokens", default="32,128")
    parser.add_argument("--concurrency", default="1")
    parser.add_argument("--repeated-modes", default="cold,hot")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--workloads", default="policy_first,task_first,agent_state_first")
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-csv", type=Path, default=None)
    args = parser.parse_args()

    env = _load_env(args.repo_root)
    host = (args.host or env.get("DIGITAL_TWIN_LLM_BASE_URL") or "http://127.0.0.1:8000/v1").rstrip("/")
    model = args.model or env.get("DIGITAL_TWIN_MODEL_NAME") or "qwen3-32b"
    api_key = env.get("DIGITAL_TWIN_API_KEY") or os.environ.get("DIGITAL_TWIN_API_KEY") or ""
    if not api_key or api_key == "EMPTY":
        raise SystemExit("DIGITAL_TWIN_API_KEY must be set to a real key for online experiments.")
    metrics_url = args.metrics_url or env.get("DIGITAL_TWIN_VLLM_METRICS_URL") or _derive_metrics_url(host)

    body_tokens = [int(item) for item in args.body_tokens.split(",") if item.strip()]
    envelope_tokens = [int(item) for item in args.envelope_tokens.split(",") if item.strip()]
    concurrencies = [int(item) for item in args.concurrency.split(",") if item.strip()]
    repeated_modes = [item.strip() for item in args.repeated_modes.split(",") if item.strip()]
    variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    workloads = [item.strip() for item in args.workloads.split(",") if item.strip()]

    rows: list[dict[str, Any]] = []
    for variant in variants:
        if variant not in VARIANTS:
            raise SystemExit(f"Unknown variant: {variant}; expected one of {', '.join(VARIANTS)}")
        for workload in workloads:
            if workload not in WORKLOADS:
                raise SystemExit(
                    f"Unknown workload: {workload}; expected one of {', '.join(WORKLOADS)}"
                )
            for body_target in body_tokens:
                for envelope_target in envelope_tokens:
                    for concurrency in concurrencies:
                        for repeated_mode in repeated_modes:
                            config = TrialConfig(
                                variant=variant,
                                workload=workload,
                                body_tokens=body_target,
                                envelope_tokens=envelope_target,
                                concurrency=concurrency,
                                repeated_mode=repeated_mode,
                            )
                            print(
                                "RUN",
                                variant,
                                workload,
                                f"body={body_target}",
                                f"envelope={envelope_target}",
                                f"conc={concurrency}",
                                repeated_mode,
                                flush=True,
                            )
                            rows.append(
                                _run_trial(
                                    host=host,
                                    model=model,
                                    api_key=api_key,
                                    metrics_url=metrics_url,
                                    config=config,
                                    requests_per_trial=args.requests_per_trial,
                                    max_tokens=args.max_tokens,
                                    retries=args.retries,
                                )
                            )

    report = {
        "host": host,
        "model": model,
        "metrics_url": metrics_url,
        "service_cache": "bypassed-direct-openai-endpoint",
        "rows": rows,
        "interpretation_guardrail": (
            "extra_key_sent=true is not segment stitch success; require "
            "segment_stitch_committed or segment_reused_body_tokens from engine metrics."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.output_csv:
        _write_csv(args.output_csv, rows)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {detail[:800]}") from exc
