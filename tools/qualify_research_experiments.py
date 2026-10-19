"""Real configured-model experiment-plan qualification; auth stays only in RAM."""
import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-snapshot", type=Path, required=True)
    parser.add_argument("--source-title", default="系统研究概览")
    args = parser.parse_args()
    pid = subprocess.check_output(["systemctl", "--user", "show", "sage-mate-app.service",
        "--property=MainPID", "--value"], text=True).strip()
    if not pid.isdigit() or int(pid)<=0:
        raise RuntimeError("active service required for configured model lookup")
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        if key.startswith(b"DIGITAL_TWIN_") and b"SLACK" not in key:
            os.environ[key.decode()] = value.decode()
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    from sage.runtime.research_experiments import (
        EXPERIMENT_SYSTEM_PROMPT, experiment_response_format, prepare_experiments, render_experiments, validate_experiments,
    )
    from sage.runtime.source_answer_plan import PlanSource, prepare_source_plan
    from sage_faculty_twin.config import AppSettings
    from sage_faculty_twin.knowledge_catalog import KnowledgeCatalog
    from sage_faculty_twin.knowledge_base import _document_is_visible_to_requester
    from sage_faculty_twin.llm_client import VllmChatClient
    catalog = KnowledgeCatalog(args.wiki_snapshot, namespace="wiki")
    try:
        _, records = catalog.snapshot()
    finally:
        catalog.close()
    sources = tuple(PlanSource(key, record.title, str(record.metadata.get("wiki_url") or ""), record.content)
        for key, record in records.items() if args.source_title in record.title
        and _document_is_visible_to_requester(record, visitor_profile="general_visitor", admin_role=None))
    question = ("根据公开 Wiki，评审 StateAxis，并提出两项能区分安全性与性能的对照实验。"
        "每项说明假设、对照、固定变量、指标及证伪观察；不要把资料未展示数据说成作者没有做实验。")
    request = prepare_experiments(prepare_source_plan(question, sources, allow_inferences=True),
        tuple(source.source_id for source in sources))
    settings = AppSettings(_env_file=None)
    client = VllmChatClient(settings)
    started = time.monotonic()
    try:
        raw = client.answer_question_sync(EXPERIMENT_SYSTEM_PROMPT, request.payload,
            temperature=0, max_tokens=int(settings.llm_policy_output_max_tokens_cap), enable_thinking=False,
            neutral_sampling=True, use_reuse_hints=False, continue_on_length=False,
            response_format=experiment_response_format(request), cache_namespace=f"source-experiments:{request.digest}")
        plan = validate_experiments(request, raw)
        print(json.dumps({"seconds": round(time.monotonic()-started, 3), "model_alias": client.model_name,
            "plan": asdict(plan), "rendered": render_experiments(request, plan)}, ensure_ascii=False), flush=True)
    finally:
        asyncio.run(client.aclose())


if __name__ == "__main__":
    main()
