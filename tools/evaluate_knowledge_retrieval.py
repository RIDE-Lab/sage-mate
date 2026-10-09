from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sage_faculty_twin.config import AppSettings  # noqa: E402
from sage_faculty_twin.knowledge_base import (  # noqa: E402
    LocalKnowledgeStore,
    _build_query_profile,
    _document_is_visible_to_requester,
)


@dataclass(frozen=True)
class EvaluationQuery:
    query_id: str
    query: str
    visitor_profile: str | None
    relevant_source_groups: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_10: float
    latency_ms: float
    candidate_count: int
    plan_reason: str
    bypassed_remote: bool
    fallback_used: bool
    max_deterministic_score: float
    deterministic_top_scores: tuple[float, ...]
    deterministic_top_sources: tuple[str, ...]
    selected_candidate_sources: tuple[str, ...]
    returned_sources: tuple[str, ...]


class _ForcedFailureReranker:
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        del query, documents
        raise RuntimeError("forced evaluation fallback")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate fixed and adaptive Twin retrieval against labelled queries.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=REPO_ROOT / "tests" / "fixtures" / "retrieval_eval.json",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--recall-k", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="JSON output path. Defaults to .benchmarks/retrieval-eval-<timestamp>.json.",
    )
    return parser.parse_args()


def load_queries(path: Path) -> list[EvaluationQuery]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    queries = []
    for item in payload.get("queries", []):
        groups = tuple(
            tuple(str(pattern) for pattern in group)
            for group in item.get("relevant_source_groups", [])
        )
        if not groups:
            raise ValueError(f"query {item.get('id')!r} has no relevance groups")
        queries.append(
            EvaluationQuery(
                query_id=str(item["id"]),
                query=str(item["query"]),
                visitor_profile=(
                    str(item["visitor_profile"]) if item.get("visitor_profile") else None
                ),
                relevant_source_groups=groups,
            )
        )
    if not queries:
        raise ValueError(f"no labelled queries found in {path}")
    return queries


def matching_group(source_name: str, groups: tuple[tuple[str, ...], ...]) -> int | None:
    normalized = source_name.lower()
    for group_index, patterns in enumerate(groups):
        if any(pattern.lower() in normalized for pattern in patterns):
            return group_index
    return None


def score_ranking(
    sources: list[str],
    groups: tuple[tuple[str, ...], ...],
    *,
    recall_k: int,
) -> tuple[float, float, float]:
    groups_in_recall: set[int] = set()
    seen_groups: set[int] = set()
    reciprocal_rank = 0.0
    dcg = 0.0
    for rank, source_name in enumerate(sources[:10], start=1):
        group = matching_group(source_name, groups)
        if group is None or group in seen_groups:
            continue
        seen_groups.add(group)
        if rank <= recall_k:
            groups_in_recall.add(group)
        if reciprocal_rank == 0.0:
            reciprocal_rank = 1.0 / float(rank)
        dcg += 1.0 / math.log2(rank + 1.0)
    ideal_dcg = sum(
        1.0 / math.log2(rank + 1.0)
        for rank in range(1, min(len(groups), 10) + 1)
    )
    return (
        len(groups_in_recall) / float(len(groups)),
        reciprocal_rank,
        dcg / ideal_dcg if ideal_dcg else 0.0,
    )


def evaluate_mode(
    mode: str,
    queries: list[EvaluationQuery],
    *,
    top_k: int,
    recall_k: int,
    repeats: int,
) -> dict[str, Any]:
    settings = AppSettings(
        _env_file=None,
        retrieval_top_k=top_k,
        knowledge_search_cache_ttl_seconds=0,
        knowledge_search_cache_max_entries=0,
        sagevdb_reranker_adaptive_enabled=(mode == "adaptive"),
    )
    if settings.knowledge_backend.lower() != "sagevdb":
        raise RuntimeError("evaluation requires DIGITAL_TWIN_KNOWLEDGE_BACKEND=sagevdb")
    if not settings.sagevdb_reranker_enabled:
        raise RuntimeError("evaluation requires the configured remote reranker")
    store = LocalKnowledgeStore(settings)

    rows: list[QueryResult] = []
    for _ in range(repeats):
        for item in queries:
            query_tokens = store._tokenize(item.query)
            query_profile = _build_query_profile(
                item.query,
                visitor_profile=item.visitor_profile,
            )
            deterministic_ranking = sorted(
                (
                    (
                        store._score_document(document, query_tokens, query_profile),
                        document.source_name or "",
                    )
                    for document in store.list_documents()
                    if _document_is_visible_to_requester(
                        document,
                        item.visitor_profile,
                    )
                ),
                reverse=True,
            )
            started = perf_counter()
            hits = store.search(
                item.query,
                top_k=top_k,
                visitor_profile=item.visitor_profile,
            )
            latency_ms = (perf_counter() - started) * 1000.0
            sources = [hit.source_name or "" for hit in hits]
            recall, reciprocal_rank, ndcg = score_ranking(
                sources,
                item.relevant_source_groups,
                recall_k=recall_k,
            )
            plan = store._last_rerank_plan
            rows.append(
                QueryResult(
                    query_id=item.query_id,
                    recall_at_k=recall,
                    reciprocal_rank=reciprocal_rank,
                    ndcg_at_10=ndcg,
                    latency_ms=latency_ms,
                    candidate_count=plan.candidate_count if plan else 0,
                    plan_reason=plan.reason if plan else "none",
                    bypassed_remote=plan.bypass_remote if plan else False,
                    fallback_used=store._last_rerank_fallback,
                    max_deterministic_score=(
                        deterministic_ranking[0][0] if deterministic_ranking else 0.0
                    ),
                    deterministic_top_scores=tuple(
                        score for score, _ in deterministic_ranking[:5]
                    ),
                    deterministic_top_sources=tuple(
                        source_name for _, source_name in deterministic_ranking[:5]
                    ),
                    selected_candidate_sources=store._last_rerank_candidate_sources,
                    returned_sources=tuple(sources),
                )
            )

    latencies = [row.latency_ms for row in rows]
    aggregate = {
        "recall_at_k": statistics.fmean(row.recall_at_k for row in rows),
        "mrr": statistics.fmean(row.reciprocal_rank for row in rows),
        "ndcg_at_10": statistics.fmean(row.ndcg_at_10 for row in rows),
        "mean_latency_ms": statistics.fmean(latencies),
        "p95_latency_ms": percentile(latencies, 0.95),
        "mean_candidate_count": statistics.fmean(row.candidate_count for row in rows),
        "remote_bypass_rate": statistics.fmean(
            float(row.bypassed_remote) for row in rows
        ),
        "fallback_rate": statistics.fmean(float(row.fallback_used) for row in rows),
    }
    return {
        "mode": mode,
        "document_count": store.count_documents(),
        "generation": getattr(store._sagevdb_persistence, "generation", None),
        "aggregate": aggregate,
        "queries": [asdict(row) for row in rows],
    }


def evaluate_forced_fallback(
    queries: list[EvaluationQuery],
    *,
    top_k: int,
    recall_k: int,
) -> dict[str, float | int]:
    settings = AppSettings(
        _env_file=None,
        retrieval_top_k=top_k,
        knowledge_search_cache_ttl_seconds=0,
        knowledge_search_cache_max_entries=0,
        sagevdb_reranker_adaptive_enabled=True,
    )
    store = LocalKnowledgeStore(settings)
    store._reranker = _ForcedFailureReranker()
    attempted = 0
    successful = 0
    recall_scores: list[float] = []
    previous_disable_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        for item in queries:
            hits = store.search(
                item.query,
                top_k=top_k,
                visitor_profile=item.visitor_profile,
            )
            plan = store._last_rerank_plan
            if plan is None or plan.bypass_remote:
                continue
            attempted += 1
            sources = [hit.source_name or "" for hit in hits]
            recall, _, _ = score_ranking(
                sources,
                item.relevant_source_groups,
                recall_k=recall_k,
            )
            recall_scores.append(recall)
            if hits and store._last_rerank_fallback:
                successful += 1
    finally:
        logging.disable(previous_disable_level)
    return {
        "attempted": attempted,
        "successful": successful,
        "success_rate": successful / float(attempted) if attempted else 1.0,
        "recall_at_k": statistics.fmean(recall_scores) if recall_scores else 0.0,
    }


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


def default_output_path() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / ".benchmarks" / f"retrieval-eval-{timestamp}.json"


def main() -> int:
    args = parse_args()
    if args.top_k < 10:
        raise ValueError("--top-k must be at least 10 to report nDCG@10")
    if args.recall_k < 1 or args.recall_k > args.top_k:
        raise ValueError("--recall-k must be between 1 and --top-k")
    if args.repeats < 1:
        raise ValueError("--repeats must be at least 1")

    queries = load_queries(args.dataset)
    results = [
        evaluate_mode(
            mode,
            queries,
            top_k=args.top_k,
            recall_k=args.recall_k,
            repeats=args.repeats,
        )
        for mode in ("fixed", "adaptive")
    ]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(args.dataset),
        "query_count": len(queries),
        "repeats": args.repeats,
        "top_k": args.top_k,
        "recall_k": args.recall_k,
        "results": results,
        "forced_fallback": evaluate_forced_fallback(
            queries,
            top_k=args.top_k,
            recall_k=args.recall_k,
        ),
    }
    output_path = args.output or default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for result in results:
        aggregate = result["aggregate"]
        print(
            result["mode"],
            f"Recall@{args.recall_k}={aggregate['recall_at_k']:.3f}",
            f"MRR={aggregate['mrr']:.3f}",
            f"nDCG@10={aggregate['ndcg_at_10']:.3f}",
            f"mean={aggregate['mean_latency_ms']:.1f}ms",
            f"p95={aggregate['p95_latency_ms']:.1f}ms",
            f"candidates={aggregate['mean_candidate_count']:.1f}",
            f"bypass={aggregate['remote_bypass_rate']:.3f}",
            f"fallback={aggregate['fallback_rate']:.3f}",
        )
    print(
        "forced-fallback",
        json.dumps(payload["forced_fallback"], ensure_ascii=False, sort_keys=True),
    )
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
