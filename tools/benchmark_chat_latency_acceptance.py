#!/usr/bin/env python3
"""Repeatable Sage Mate #9 latency, attribution, and reference acceptance gate."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx


WORKLOADS = (
    {
        "name": "simple_grounded",
        "question": "张书豪老师主要研究哪些方向？",
        "deep_thinking": False,
        "limit_seconds": 3.0,
    },
    {
        "name": "complex_research",
        "question": (
            "请比较 SAGE、vLLM-HUST 和 vLLM-Ascend-HUST 的定位、协作关系与"
            "各自适用场景，并给出有依据的选择建议。"
        ),
        "deep_thinking": False,
        "limit_seconds": 30.0,
    },
    {
        "name": "deep_research",
        "question": (
            "围绕长上下文大模型推理的研究方向，请从 baseline、公平对比和关键消融"
            "三个方面给出一页组会汇报骨架。"
        ),
        "deep_thinking": True,
        "limit_seconds": 60.0,
    },
)

RUNTIME_RECEIPT_FIELDS = (
    "app_version",
    "model_name",
    "sage_runtime",
    "knowledge_backend",
    "knowledge_embedding_backend",
    "knowledge_documents",
    "stack_version_sage",
    "stack_version_neuromem",
    "stack_version_vllm_hust",
    "stack_version_sagevdb",
    "stack_version_sage_anns",
    "engine_image",
    "npu_devices",
)


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def run_once(
    client: httpx.Client,
    base_url: str,
    workload: dict[str, Any],
    run_index: int,
) -> dict[str, Any]:
    trace_id = f"accept-{workload['name']}-{uuid4().hex}"
    payload = {
        "student_name": "Latency Acceptance",
        "visitor_profile": "general_visitor",
        "conversation_id": trace_id,
        "question": workload["question"],
        "deep_thinking": workload["deep_thinking"],
        "deep_thinking_explicit": True,
        "web_search": False,
    }
    started = perf_counter()
    response = client.post(
        f"{base_url}/chat",
        params={"request_id": trace_id},
        json=payload,
    )
    result: dict[str, Any] = {
        "run": run_index,
        "trace_id": trace_id,
        "status": response.status_code,
        "wall_seconds": round(perf_counter() - started, 3),
    }
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:500]}
    if response.status_code != 200:
        result.update(
            error=body,
            retry_after=response.headers.get("retry-after"),
        )
        return result
    timing = body.get("request_timing") or {}
    total_ms = float(timing.get("total_duration_ms") or 0.0)
    unattributed_ms = float(timing.get("unattributed_duration_ms") or 0.0)
    result.update(
        server_seconds=round(total_ms / 1000.0, 3),
        ttft_seconds=(
            round(float(timing["llm_ttft_ms"]) / 1000.0, 3)
            if timing.get("llm_ttft_ms") is not None
            else None
        ),
        route=timing.get("route"),
        stage_durations_ms=timing.get("stage_durations_ms") or {},
        unattributed_ms=round(unattributed_ms, 3),
        unattributed_ratio=round(unattributed_ms / total_ms, 4) if total_ms else 0.0,
        llm_calls=int(timing.get("llm_call_count") or 0),
        llm_retries=int(timing.get("llm_retry_count") or 0),
        llm_cache_hits=int(timing.get("llm_cache_hits") or 0),
        llm_cache_misses=int(timing.get("llm_cache_misses") or 0),
        knowledge_hits=len(body.get("knowledge_hits") or []),
        web_hits=len(body.get("web_search_hits") or []),
        answer_basis=len(body.get("answer_basis") or []),
        answer_chars=len(str(body.get("answer") or "")),
        used_model=body.get("used_model"),
    )
    return result


def summarize(workload: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [run for run in runs if run["status"] == 200]
    latencies = [float(run["wall_seconds"]) for run in successes]
    ttfts = [float(run["ttft_seconds"]) for run in successes if run.get("ttft_seconds") is not None]
    referenced = [
        run for run in successes if run.get("knowledge_hits", 0) + run.get("web_hits", 0) > 0
    ]
    p95 = percentile(latencies, 0.95)
    passed = bool(
        len(successes) == len(runs)
        and p95 <= float(workload["limit_seconds"])
        and len(referenced) == len(successes)
        and all(
            run.get("unattributed_ms", 0.0) <= 2000
            and run.get("unattributed_ratio", 0.0) <= 0.05
            for run in successes
        )
    )
    return {
        "name": workload["name"],
        "runs": len(runs),
        "successes": len(successes),
        "p50_seconds": round(median(latencies), 3) if latencies else None,
        "p95_seconds": round(p95, 3) if latencies else None,
        "ttft_p95_seconds": round(percentile(ttfts, 0.95), 3) if ttfts else None,
        "limit_seconds": workload["limit_seconds"],
        "reference_coverage": round(len(referenced) / len(successes), 3) if successes else 0.0,
        "max_unattributed_ms": max((run.get("unattributed_ms", 0.0) for run in successes), default=0.0),
        "max_unattributed_ratio": max(
            (run.get("unattributed_ratio", 0.0) for run in successes), default=0.0
        ),
        "total_retries": sum(run.get("llm_retries", 0) for run in successes),
        "passed": passed,
        "samples": runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SAGE_MATE_ACCEPTANCE_BASE_URL", ""),
        help="Sage Mate origin; may also be set via SAGE_MATE_ACCEPTANCE_BASE_URL",
    )
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=95.0)
    parser.add_argument("--mixed-concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.base_url:
        parser.error("--base-url or SAGE_MATE_ACCEPTANCE_BASE_URL is required")
    if args.runs < 1:
        parser.error("--runs must be positive")

    base_url = args.base_url.rstrip("/")
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "workloads": [],
    }
    # Public browsers negotiate HTTP/2 with Cloudflare.  Use the same transport
    # here so HTTP/1.1 connection churn is not misreported as application p95.
    with httpx.Client(timeout=args.timeout, follow_redirects=True, http2=True) as client:
        health_response = client.get(f"{base_url}/health")
        health_response.raise_for_status()
        health = health_response.json()
        report["runtime"] = {
            field: health.get(field)
            for field in RUNTIME_RECEIPT_FIELDS
            if health.get(field) not in (None, "")
        }
        for workload in WORKLOADS:
            runs = [run_once(client, base_url, workload, index + 1) for index in range(args.runs)]
            report["workloads"].append(summarize(workload, runs))

    def run_mixed(index: int, workload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=args.timeout, follow_redirects=True, http2=True) as client:
            return run_once(client, base_url, workload, index + 1)

    jobs = [WORKLOADS[index % len(WORKLOADS)] for index in range(args.mixed_concurrency)]
    with ThreadPoolExecutor(max_workers=args.mixed_concurrency) as executor:
        mixed_runs = list(executor.map(run_mixed, range(len(jobs)), jobs))
    mixed_valid = all(
        run["status"] == 200
        or (run["status"] == 429 and str(run.get("retry_after") or "").isdigit())
        for run in mixed_runs
    )
    report["mixed_load"] = {
        "concurrency": args.mixed_concurrency,
        "passed": mixed_valid,
        "samples": mixed_runs,
    }
    report["passed"] = all(item["passed"] for item in report["workloads"]) and mixed_valid

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
