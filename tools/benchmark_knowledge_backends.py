from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sage_faculty_twin.config import AppSettings  # noqa: E402
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore  # noqa: E402
from sage_faculty_twin.models import KnowledgeDocumentCreate  # noqa: E402


@dataclass
class BenchmarkResult:
    backend: str
    algorithm: str | None
    document_count: int
    query_count: int
    repeats: int
    build_seconds: float
    total_search_seconds: float
    mean_query_ms: float
    p50_query_ms: float
    p95_query_ms: float
    queries_per_second: float
    non_empty_hit_rate: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark my-twin knowledge retrieval backends on the same workload.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=REPO_ROOT / "data" / "knowledge_base",
        help="Directory with existing knowledge-base JSON documents.",
    )
    parser.add_argument(
        "--docs",
        type=int,
        default=128,
        help="Number of documents to benchmark.",
    )
    parser.add_argument(
        "--queries",
        type=int,
        default=64,
        help="Number of distinct queries to benchmark.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="How many times to replay the query set for latency statistics.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Search top-k used for each query.",
    )
    parser.add_argument(
        "--dimension",
        type=int,
        default=256,
        help="Hash embedding dimension used for both backends.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=7,
        help="Random seed for deterministic document/query sampling.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path for JSON benchmark output. Defaults to .benchmarks/knowledge-backends-<timestamp>.json",
    )
    return parser.parse_args()


def load_documents(data_dir: Path, max_docs: int, seed: int) -> list[KnowledgeDocumentCreate]:
    if not data_dir.exists():
        raise FileNotFoundError(f"Knowledge-base directory not found: {data_dir}")

    payloads: list[KnowledgeDocumentCreate] = []
    for path in sorted(data_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        payloads.append(
            KnowledgeDocumentCreate(
                title=str(raw.get("title") or "Untitled document"),
                content=str(raw.get("content") or ""),
                tags=[str(tag) for tag in (raw.get("tags") or [])],
                source_name=str(raw.get("source_name") or path.stem),
            )
        )

    if not payloads:
        raise RuntimeError(f"No JSON documents found under {data_dir}")

    if len(payloads) <= max_docs:
        return payloads

    sampler = random.Random(seed)
    return [payloads[index] for index in sorted(sampler.sample(range(len(payloads)), k=max_docs))]


def build_queries(documents: list[KnowledgeDocumentCreate], max_queries: int, seed: int) -> list[str]:
    sampler = random.Random(seed + 1)
    sampled = documents if len(documents) <= max_queries else sampler.sample(documents, k=max_queries)

    queries: list[str] = []
    for doc in sampled:
        title = doc.title.strip()
        if title:
            queries.append(title)
            continue
        tags = " ".join(doc.tags[:3]).strip()
        if tags:
            queries.append(tags)
            continue
        content = " ".join(doc.content.split())
        queries.append(content[:80] or "general question")
    return queries


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def benchmark_backend(
    *,
    backend: str,
    algorithm: str | None,
    documents: list[KnowledgeDocumentCreate],
    queries: list[str],
    repeats: int,
    top_k: int,
    dimension: int,
) -> BenchmarkResult:
    with TemporaryDirectory(prefix=f"my-twin-{backend}-") as temp_dir:
        settings = AppSettings(
            knowledge_base_dir=Path(temp_dir),
            knowledge_backend="sagevdb",
            retrieval_top_k=top_k,
            sagevdb_embedding_backend="hash",
            sagevdb_dimension=dimension,
            sagevdb_backend=backend,
            sagevdb_anns_algorithm=algorithm or "faiss_hnsw",
        )
        store = LocalKnowledgeStore(settings)

        build_started = perf_counter()
        for document in documents:
            store.add_document(document, rebuild_indexes=False)
        store.rebuild_indexes()
        build_seconds = perf_counter() - build_started

        latencies_ms: list[float] = []
        non_empty_hits = 0
        total_queries = len(queries) * repeats
        search_started = perf_counter()
        for _ in range(repeats):
            for query in queries:
                started = perf_counter()
                hits = store.search(query, top_k=top_k)
                latencies_ms.append((perf_counter() - started) * 1000.0)
                if hits:
                    non_empty_hits += 1
        total_search_seconds = perf_counter() - search_started

    return BenchmarkResult(
        backend=backend,
        algorithm=algorithm,
        document_count=len(documents),
        query_count=len(queries),
        repeats=repeats,
        build_seconds=build_seconds,
        total_search_seconds=total_search_seconds,
        mean_query_ms=statistics.fmean(latencies_ms),
        p50_query_ms=percentile(latencies_ms, 0.50),
        p95_query_ms=percentile(latencies_ms, 0.95),
        queries_per_second=(float(total_queries) / total_search_seconds) if total_search_seconds > 0 else 0.0,
        non_empty_hit_rate=(float(non_empty_hits) / float(total_queries)) if total_queries > 0 else 0.0,
    )


def default_output_path() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = REPO_ROOT / ".benchmarks"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"knowledge-backends-{timestamp}.json"


def print_summary(results: list[BenchmarkResult]) -> None:
    print()
    print("backend\tbuild_s\tsearch_s\tmean_ms\tp50_ms\tp95_ms\tqps\thit_rate")
    for item in results:
        print(
            f"{item.backend}:{item.algorithm or '-'}\t"
            f"{item.build_seconds:.4f}\t"
            f"{item.total_search_seconds:.4f}\t"
            f"{item.mean_query_ms:.4f}\t"
            f"{item.p50_query_ms:.4f}\t"
            f"{item.p95_query_ms:.4f}\t"
            f"{item.queries_per_second:.2f}\t"
            f"{item.non_empty_hit_rate:.3f}"
        )


def main() -> int:
    args = parse_args()
    documents = load_documents(args.data_dir, args.docs, args.seed)
    queries = build_queries(documents, args.queries, args.seed)

    benchmark_plan = [("cpp", None), ("sage-anns", "faiss_hnsw")]
    results: list[BenchmarkResult] = []
    errors: list[dict[str, str]] = []

    for backend, algorithm in benchmark_plan:
        try:
            results.append(
                benchmark_backend(
                    backend=backend,
                    algorithm=algorithm,
                    documents=documents,
                    queries=queries,
                    repeats=args.repeats,
                    top_k=args.top_k,
                    dimension=args.dimension,
                )
            )
        except Exception as exc:  # pragma: no cover - benchmark diagnostics
            errors.append({"backend": backend, "error": str(exc)})

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "documents": len(documents),
        "queries": len(queries),
        "repeats": args.repeats,
        "top_k": args.top_k,
        "dimension": args.dimension,
        "results": [asdict(item) for item in results],
        "errors": errors,
    }

    output_path = args.output or default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print_summary(results)
    if errors:
        print()
        print(json.dumps({"errors": errors}, indent=2, ensure_ascii=False))
    print()
    print(f"Wrote benchmark results to {output_path}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
