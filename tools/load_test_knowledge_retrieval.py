from __future__ import annotations

import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sage_faculty_twin.config import AppSettings  # noqa: E402


@dataclass(frozen=True)
class LoadResult:
    concurrency: int
    requests: int
    successes: int
    errors: int
    non_empty_responses: int
    elapsed_seconds: float
    requests_per_second: float
    mean_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    max_latency_ms: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load-test the authenticated Twin knowledge-search endpoint.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:55603")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=REPO_ROOT / "tests" / "fixtures" / "retrieval_eval.json",
    )
    parser.add_argument("--concurrencies", default="1,2,4")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def load_cases(path: Path, rounds: int) -> list[tuple[str, str | None]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    base_cases = [
        (str(item["query"]), str(item["visitor_profile"]) if item.get("visitor_profile") else None)
        for item in payload.get("queries", [])
    ]
    if not base_cases:
        raise ValueError(f"no queries found in {path}")
    return base_cases * rounds


def run_request(
    client: httpx.Client,
    query: str,
    visitor_profile: str | None,
) -> tuple[bool, bool, float]:
    started = perf_counter()
    try:
        response = client.get(
            "/knowledge/search",
            params={"query": query, "visitor_profile": visitor_profile},
        )
        response.raise_for_status()
        payload = response.json()
        return True, bool(payload.get("hits")), (perf_counter() - started) * 1000.0
    except (httpx.HTTPError, TypeError, ValueError):
        return False, False, (perf_counter() - started) * 1000.0


def run_level(
    client: httpx.Client,
    cases: list[tuple[str, str | None]],
    concurrency: int,
) -> LoadResult:
    started = perf_counter()
    rows: list[tuple[bool, bool, float]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(run_request, client, query, visitor_profile)
            for query, visitor_profile in cases
        ]
        for future in as_completed(futures):
            rows.append(future.result())
    elapsed = perf_counter() - started
    latencies = [row[2] for row in rows]
    successes = sum(1 for success, _, _ in rows if success)
    return LoadResult(
        concurrency=concurrency,
        requests=len(rows),
        successes=successes,
        errors=len(rows) - successes,
        non_empty_responses=sum(1 for success, non_empty, _ in rows if success and non_empty),
        elapsed_seconds=elapsed,
        requests_per_second=len(rows) / elapsed if elapsed else 0.0,
        mean_latency_ms=statistics.fmean(latencies),
        p50_latency_ms=percentile(latencies, 0.50),
        p95_latency_ms=percentile(latencies, 0.95),
        max_latency_ms=max(latencies),
    )


def default_output_path() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / ".benchmarks" / f"retrieval-load-{timestamp}.json"


def main() -> int:
    args = parse_args()
    if args.rounds < 1:
        raise ValueError("--rounds must be at least 1")
    concurrencies = [int(value) for value in args.concurrencies.split(",")]
    if not concurrencies or any(value < 1 for value in concurrencies):
        raise ValueError("--concurrencies must contain positive integers")

    settings = AppSettings(_env_file=None)
    cases = load_cases(args.dataset, args.rounds)
    with httpx.Client(
        base_url=args.base_url.rstrip("/") + "/",
        timeout=args.timeout_seconds,
    ) as client:
        login = client.post(
            "/auth/admin/login",
            json={
                "username": settings.admin_username,
                "password": settings.admin_password,
            },
        )
        login.raise_for_status()
        results = [run_level(client, cases, concurrency) for concurrency in concurrencies]

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "dataset": str(args.dataset),
        "rounds": args.rounds,
        "results": [asdict(result) for result in results],
    }
    output_path = args.output or default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        print(
            f"c={result.concurrency}",
            f"ok={result.successes}/{result.requests}",
            f"non_empty={result.non_empty_responses}/{result.requests}",
            f"qps={result.requests_per_second:.2f}",
            f"mean={result.mean_latency_ms:.1f}ms",
            f"p95={result.p95_latency_ms:.1f}ms",
            f"max={result.max_latency_ms:.1f}ms",
        )
    print(f"wrote {output_path}")
    return 0 if all(result.errors == 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
