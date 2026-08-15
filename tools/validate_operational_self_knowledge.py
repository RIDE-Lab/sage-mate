#!/usr/bin/env python3
"""Run the post-deploy operational self-knowledge contradiction gate."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

from sage_faculty_twin.operational_acceptance import (
    OPERATIONAL_QUESTIONS,
    OperationalExpectedFacts,
    evaluate_operational_response,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SAGE_MATE_ACCEPTANCE_BASE_URL", ""),
    )
    parser.add_argument(
        "--expected-fixture",
        type=Path,
        help="Independent expected-facts JSON; defaults to deriving facts from /health.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-questions", type=int, default=len(OPERATIONAL_QUESTIONS))
    args = parser.parse_args()
    if not args.base_url:
        parser.error("--base-url or SAGE_MATE_ACCEPTANCE_BASE_URL is required")

    base_url = args.base_url.rstrip("/")
    headers = {"User-Agent": "SAGE-Operational-Acceptance/1"}
    with httpx.Client(
        timeout=args.timeout,
        follow_redirects=True,
        http2=True,
        headers=headers,
    ) as client:
        health_response = client.get(f"{base_url}/health")
        health_response.raise_for_status()
        health = health_response.json()
        expected = (
            OperationalExpectedFacts.from_dict(
                json.loads(args.expected_fixture.read_text(encoding="utf-8"))
            )
            if args.expected_fixture
            else OperationalExpectedFacts.from_health(health)
        )
        samples = []
        for question in OPERATIONAL_QUESTIONS[: max(1, args.max_questions)]:
            trace_id = f"ops-self-{uuid4().hex}"
            payload = {
                "student_name": "Operational acceptance",
                "visitor_profile": "general_visitor",
                "conversation_id": trace_id,
                "question": question,
                "deep_thinking": False,
                "web_search": False,
            }
            started = perf_counter()
            response = client.post(
                f"{base_url}/chat",
                params={"request_id": trace_id},
                json=payload,
            )
            elapsed = perf_counter() - started
            try:
                body = response.json()
            except ValueError:
                body = {"answer": response.text[:500]}
            samples.append(
                evaluate_operational_response(
                    question=question,
                    expected=expected,
                    status_code=response.status_code,
                    elapsed_seconds=elapsed,
                    body=body,
                )
            )

    passed = all(sample["passed"] for sample in samples)
    report = {
        "schema_version": "operational-self-knowledge/v1",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": base_url,
        "runtime_source": health.get("runtime_identity_source"),
        "expected": {
            field: getattr(expected, field) for field in expected.__dataclass_fields__
        },
        "summary": {
            "questions": len(samples),
            "passed": sum(1 for sample in samples if sample["passed"]),
            "reference_coverage": round(
                sum(sample["reference_coverage"] for sample in samples)
                / max(1, len(samples)),
                4,
            ),
            "max_contradiction_score": max(
                (sample["contradiction_score"] for sample in samples), default=0.0
            ),
        },
        "passed": passed,
        "samples": samples,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
