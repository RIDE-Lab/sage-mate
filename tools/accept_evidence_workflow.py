"""Public-only real QA against an owned isolated loopback preview.

Prints public questions/answers and content-free execution receipts only. No
cookies, credentials, private records or arbitrary response metadata are saved.
These checks are structural gates plus material for human semantic review.
"""

import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

CASES = (
    ("guide", "请依据公开 Wiki《系统研究概览：SAGE、ECPA、StateAxis 与 vLLM-HUST》，用面向初次来访者的语言说明主题、课题组相关工作与下一步阅读入口，并附完整原文链接。"),
    ("roles", "根据公开 Wiki，ECPA 和 StateAxis 分别承担什么角色？请保留完整来源链接，不要把设计目标说成实测结论。"),
    ("boundary", "根据公开 Wiki，是否可以认定 StateAxis 从来没有做过实验？请明确区分资料未展示数据与实验不存在，并保留完整来源链接。"),
    ("research", "根据公开 Wiki，评审 StateAxis：分别列出来源原文能支持的事实、这些资料尚不能确认的内容，并提出两项能区分安全性与性能的对照实验。每项说明假设、对照、固定变量、指标及证伪观察；不要把资料未展示数据说成作者没有做实验。"),
)


def observe_owned_chat(client, body, request_id, *, timeout=330):
    started, polls = time.monotonic(), 0
    while True:
        remaining = timeout-(time.monotonic()-started)
        if remaining <= 0:
            raise TimeoutError("owned QA observer deadline; producer was not cancelled")
        response = client.post("/chat", params={"request_id": request_id}, json=body, timeout=remaining)
        # Isolated HTTP only; do not change production cookie/security policy.
        for cookie in client.cookies.jar:
            cookie.secure = False
        if response.status_code not in {429, 504} or response.headers.get("X-Sage-Error-Code")=="work_capacity_exhausted":
            return response, polls
        polls += 1
        time.sleep(min(2, max(0, timeout-(time.monotonic()-started))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:59676")
    parser.add_argument("--case", choices=[name for name, _ in CASES])
    parser.add_argument("--wiki-snapshot", type=Path, required=True)
    args = parser.parse_args()
    target = urlsplit(args.base_url)
    if target.scheme != "http" or target.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("only an owned isolated loopback HTTP preview is allowed")
    from sage_faculty_twin.knowledge_catalog import KnowledgeCatalog
    catalog = KnowledgeCatalog(args.wiki_snapshot, namespace="wiki")
    try:
        revision, records = catalog.snapshot()
    finally:
        catalog.close()
    failures = 0
    with httpx.Client(base_url=args.base_url, timeout=330, trust_env=False) as client:
        for name, question in CASES:
            if args.case and name != args.case:
                continue
            started = time.monotonic()
            request_id = "evidence-qa-"+uuid4().hex
            body = {
                "student_name": "工作流验收合成访客", "visitor_profile": "general_visitor",
                "question": question, "knowledge_sources": ["wiki"], "deep_thinking": False,
                "skill_routing": False}
            response, polls = observe_owned_chat(client, body, request_id)
            data = response.json()
            events = data.get("execution_events", [])
            starts = [event.get("operator_key") for event in events if event.get("state")=="started"]
            blocks = data.get("answer_blocks", [])
            hits = data.get("knowledge_hits", [])
            selected_ids = {block.get("source_id") for block in blocks if block.get("kind")=="source"}
            selected_hits = [hit for hit in hits if hit.get("evidence_source_id") in selected_ids]
            overview_url = "https://lab.sage.org.ai/sage-wiki/docs/achievements/sage-system-overview"
            canonical = "\n\n".join(block.get("markdown", "") for block in blocks).strip()
            checks = {"http_success": response.status_code==200,
                "canonical_blocks": bool(blocks) and canonical==data.get("answer"),
                "native_coverage": "assess-coverage" in starts,
                "native_final_verify": "verify-evidence-answer" in starts,
                "no_private_reader": "read-private" not in starts,
                "bounded_supplement": starts.count("supplement-evidence") <= 1,
                "structured_experiment_plan": name!="research" or "plan-experiments" in starts,
                "selected_source_ids_resolve": {hit.get("evidence_source_id") for hit in selected_hits}==selected_ids
                    and len(selected_hits)==len(selected_ids),
                "source_urls_preserved": all(not hit.get("metadata", {}).get("wiki_url") or
                    hit["metadata"]["wiki_url"] in data.get("answer", "") for hit in selected_hits),
                "source_bodies_preserved": all(hit.get("document_id", "").startswith("wiki::") and
                    (record := records.get(hit["document_id"].partition("::")[2])) is not None
                    and record.content in data.get("answer", "") for hit in selected_hits),
                "requested_source_retrieved": name not in {"guide", "roles", "research"} or
                    any(str(hit.get("metadata", {}).get("wiki_url", "")).rstrip("/")==overview_url for hit in selected_hits),
                "requested_subjects_present": name!="roles" or all(subject in data.get("answer", "")
                    for subject in ("ECPA", "StateAxis"))}
            failures += not all(checks.values())
            print(json.dumps({"case": name, "question": question, "status": response.status_code,
                "request_id": request_id, "observer_polls": polls,
                "seconds": round(time.monotonic()-started, 3), "checks": checks,
                "wiki_snapshot_revision": revision,
                "answer": data.get("answer"),
                "error": data.get("detail"), "starts": starts,
                "sources": [{"title": hit.get("title"), "url": hit.get("metadata", {}).get("wiki_url")}
                    for hit in hits]}, ensure_ascii=False), flush=True)
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
