"""Offline replay of historical poor-answer cases and simulated student
questions.

For each fixture this script runs the current chat workflow in-process
(against the live vLLM backend) and prints BEFORE/AFTER deltas. A markdown
report is emitted to docs/student-questions-replay-report.md.

Usage:
    cd /home/shuhao/sage-mate
    PYTHONPATH=src:/home/shuhao/SAGE/src:/home/shuhao/sageVDB:/home/shuhao/neuromem \
        /home/shuhao/miniforge3/envs/sagellm/bin/python3 tools/replay_poor_cases.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

# Apply the torch_npu / HF cache shim before importing anything that may
# transitively load transformers (only matters when the FAISS+bge backend is
# selected, but it is a no-op otherwise).
import _setup_hf_cache  # noqa: E402, F401

from sage_faculty_twin.config import AppSettings  # noqa: E402
from sage_faculty_twin.models import ChatRequest, InteractionIntent  # noqa: E402
from sage_faculty_twin.service import DigitalTwinService  # noqa: E402

POOR_DOC = ROOT / "docs" / "student-questions-poor-answers.md"
SIMULATED_DOC = ROOT / "data" / "simulated_student_questions.json"
REPORT_DOC = ROOT / "docs" / "student-questions-replay-report.md"


@dataclass
class Fixture:
    source: str
    student_name: str
    case_index: int  # per-student
    timestamp: str
    domain: str
    workflow_before: str
    course_context: str
    kb_hits_before: int
    failure_modes: str
    question: str
    answer_before: str

    # Replay output
    workflow_after: str = ""
    answer_after: str = ""
    kb_hits_after: int = 0
    error: str = ""
    verdict: str = ""

    # ordering
    global_index: int = 0
    conversation_id: str = field(default_factory=lambda: f"replay-{uuid.uuid4().hex[:12]}")
    student_email: str | None = None
    visitor_profile: str | None = None

    # Simulated-case expectations.
    scenario_id: str | None = None
    expected_behavior: str | None = None
    expected_domain: str | None = None
    min_kb_hits: int = 0
    min_answer_chars: int = 0
    must_include_any: list[str] = field(default_factory=list)
    notes: str = ""
    expectation_errors: list[str] = field(default_factory=list)
    evaluation_mode: str = "live"


_HEADER_RE = re.compile(
    r"^### (?P<idx>\d+)\.\s*\[(?P<ts>[^\]]+)\]\s*\*(?P<domain>[^/]+)\s*/\s*(?P<wf>[^*]+)\*"
    r"\s*课程:(?P<course>[^\s]+)\s*KB:(?P<kb>\d+)"
)
_STUDENT_HEADER_RE = re.compile(r"^##\s+(?P<name>[^(\s]+)\s*\(问题不佳:")


def parse_fixtures(md_path: Path) -> list[Fixture]:
    text = md_path.read_text(encoding="utf-8")
    fixtures: list[Fixture] = []
    current_student: str | None = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m_student = _STUDENT_HEADER_RE.match(line)
        if m_student:
            current_student = m_student.group("name").strip()
            i += 1
            continue
        m = _HEADER_RE.match(line)
        if m and current_student:
            idx = int(m.group("idx"))
            ts = m.group("ts").strip()
            domain = m.group("domain").strip()
            wf_before = m.group("wf").strip()
            course = m.group("course").strip()
            kb_before = int(m.group("kb"))
            # next line: failure modes
            failure_modes = ""
            j = i + 1
            while j < len(lines) and not lines[j].startswith("**问题不佳的原因**"):
                j += 1
            if j < len(lines):
                failure_modes = lines[j].split(":", 1)[-1].strip()
            # then **学生提问:** then quoted block of one or more lines
            while j < len(lines) and not lines[j].startswith("**学生提问"):
                j += 1
            j += 1
            question_lines: list[str] = []
            while j < len(lines) and lines[j].startswith(">"):
                question_lines.append(lines[j].lstrip("> ").rstrip())
                j += 1
            question = "\n".join(question_lines).strip()
            # then **Twin 回答:**
            while j < len(lines) and not lines[j].startswith("**Twin 回答"):
                j += 1
            j += 1
            answer_lines: list[str] = []
            while j < len(lines) and (lines[j].startswith(">") or lines[j].strip() == "" and j + 1 < len(lines) and lines[j + 1].startswith(">")):
                if lines[j].startswith(">"):
                    answer_lines.append(lines[j].lstrip("> ").rstrip())
                j += 1
            answer = "\n".join(answer_lines).strip()
            fixtures.append(
                Fixture(
                    source="historical_poor_answer",
                    student_name=current_student,
                    case_index=idx,
                    timestamp=ts,
                    domain=domain,
                    workflow_before=wf_before,
                    course_context=course,
                    kb_hits_before=kb_before,
                    failure_modes=failure_modes,
                    question=question,
                    answer_before=answer,
                )
            )
            i = j
            continue
        i += 1
    for n, fx in enumerate(fixtures):
        fx.global_index = n + 1
    return fixtures


def load_simulated_fixtures(path: Path = SIMULATED_DOC) -> list[Fixture]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    fixtures: list[Fixture] = []
    for idx, item in enumerate(payload, start=1):
        scenario_id = item["scenario_id"]
        fixtures.append(
            Fixture(
                source="simulated_student_question",
                student_name=item["student_name"],
                student_email=item.get("student_email"),
                visitor_profile=item.get("visitor_profile"),
                case_index=idx,
                timestamp="simulated",
                domain=item.get("expected_domain", "general"),
                workflow_before="simulated",
                course_context=item.get("course_context") or "初次来访",
                kb_hits_before=0,
                failure_modes="simulated expectation check",
                question=item["question"],
                answer_before=f"Synthetic scenario {scenario_id}: {item.get('notes', '')}".strip(),
                scenario_id=scenario_id,
                expected_behavior=item.get("expected_behavior"),
                expected_domain=item.get("expected_domain"),
                min_kb_hits=int(item.get("min_kb_hits", 0)),
                min_answer_chars=int(item.get("min_answer_chars", 0)),
                must_include_any=list(item.get("must_include_any", [])),
                notes=item.get("notes", ""),
                conversation_id=f"sim-{item.get('conversation_group', scenario_id)}",
            )
        )
    for n, fx in enumerate(fixtures):
        fx.global_index = n + 1
    return fixtures


def assign_conversation_ids(fixtures: list[Fixture]) -> None:
    """Group consecutive same-student same-course cases into one conversation
    so multi-turn follow-ups (e.g. '具体内容') can resolve via session context.
    """
    last_key = None
    last_conv = None
    for fx in fixtures:
        if fx.source == "simulated_student_question":
            continue
        key = (fx.student_name, fx.course_context)
        if key != last_key:
            last_conv = f"replay-{uuid.uuid4().hex[:12]}"
            last_key = key
        fx.conversation_id = last_conv


def _evaluate_simulated_expectations(fx: Fixture) -> list[str]:
    errors: list[str] = []
    if fx.expected_behavior == "answer" and fx.workflow_after in {
        "ask_follow_up",
        "review_queue",
        "human_handoff",
    }:
        errors.append(f"expected answer-like behavior, got {fx.workflow_after}")
    if fx.expected_behavior == "ask_follow_up" and fx.workflow_after != "ask_follow_up":
        errors.append(f"expected ask_follow_up, got {fx.workflow_after}")
    if fx.min_kb_hits and fx.kb_hits_after < fx.min_kb_hits:
        errors.append(f"kb_hits {fx.kb_hits_after} < expected {fx.min_kb_hits}")
    if fx.evaluation_mode == "mock":
        return errors
    if fx.min_answer_chars and len(fx.answer_after) < fx.min_answer_chars:
        errors.append(
            f"answer length {len(fx.answer_after)} < expected {fx.min_answer_chars}"
        )
    if fx.must_include_any:
        lowered_answer = fx.answer_after.lower()
        if not any(term.lower() in lowered_answer for term in fx.must_include_any):
            terms = ", ".join(fx.must_include_any)
            errors.append(f"answer did not include any expected cue: {terms}")
    if "[" in fx.answer_after and "]" in fx.answer_after:
        errors.append("answer contains bracketed placeholder-like text")
    return errors


def _classify_verdict(fx: Fixture) -> str:
    if fx.error:
        return "error"
    if fx.source == "simulated_student_question":
        fx.expectation_errors = _evaluate_simulated_expectations(fx)
        return "passed" if not fx.expectation_errors else "failed"
    before_short = len(fx.answer_before) < 80
    after_short = len(fx.answer_after) < 80
    was_followup = fx.workflow_before == "ask_follow_up"
    is_followup = fx.workflow_after == "ask_follow_up"
    kb_gain = fx.kb_hits_after > fx.kb_hits_before
    has_placeholder_before = "[" in fx.answer_before and "]" in fx.answer_before
    has_placeholder_after = "[" in fx.answer_after and "]" in fx.answer_after
    became_review = fx.workflow_after in {"review_queue", "human_handoff"}
    was_review = fx.workflow_before in {"review_queue", "human_handoff"}

    improvements = 0
    regressions = 0
    if was_followup and not is_followup:
        improvements += 1
    if not was_followup and is_followup:
        regressions += 1
    if before_short and not after_short:
        improvements += 1
    if not before_short and after_short:
        regressions += 1
    if kb_gain:
        improvements += 1
    if has_placeholder_before and not has_placeholder_after:
        improvements += 1
    if not has_placeholder_before and has_placeholder_after:
        regressions += 1
    if was_review and not became_review:
        improvements += 1
    if not was_review and became_review:
        regressions += 1
    if improvements > regressions:
        return "improved"
    if regressions > improvements:
        return "regressed"
    return "unchanged"


def _install_mock_llm(service: DigitalTwinService) -> None:
    """Patch the service's LLM client so the replay can exercise the
    deterministic guardrails (Tasks 2/4/5/8) and the deferred-clarification
    routing (Task 3) without needing the live Qwen3-32B endpoint.

    The mock classifier returns the **baseline** behaviour the analysis
    diagnosed: ``ask_followup`` with a generic clarification message. The
    deterministic post-processor in ``_normalize_interaction_intent`` is
    expected to demote that on cases that match research/advising/identity
    anchors. The mock answerer returns a templated string so we can still
    assert end-to-end shape (workflow_action, knowledge_hits) without LLM
    cost.
    """
    client = service._llm_client  # noqa: SLF001

    def _mock_classify_intent_sync(question, course_context, recent_session_context=None):  # noqa: ARG001
        return InteractionIntent(
            action="ask_followup",
            domain="general",
            decision_mode="direct_answer",
            needs_clarification=True,
            clarification_message="请问您具体需要了解什么?",
            confidence=0.4,
        )

    def _mock_answer_question_sync(system_prompt, user_prompt, **kwargs):  # noqa: ARG001
        # Echo the first 200 chars of the retrieved-context user prompt so
        # we can verify retrieval surfaced material into the prompt.
        snippet = user_prompt.replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:240] + "..."
        return f"[MOCK-LLM回复] 根据检索到的上下文: {snippet}"

    client.classify_interaction_intent_sync = _mock_classify_intent_sync  # type: ignore[assignment]
    client.answer_question_sync = _mock_answer_question_sync  # type: ignore[assignment]


async def run_replay(fixtures: list[Fixture], settings: AppSettings, *, max_cases: int | None = None, mock_llm: bool = False) -> None:
    service = DigitalTwinService(settings)
    if mock_llm:
        _install_mock_llm(service)
        for fx in fixtures:
            fx.evaluation_mode = "mock"
        print("[MOCK-LLM] classify_interaction_intent_sync + answer_question_sync patched")
    try:
        cases = fixtures if max_cases is None else fixtures[:max_cases]
        for n, fx in enumerate(cases, start=1):
            t0 = time.perf_counter()
            req = ChatRequest(
                student_name=fx.student_name,
                student_email=fx.student_email,
                question=fx.question,
                course_context=fx.course_context,
                visitor_profile=fx.visitor_profile,
                conversation_id=fx.conversation_id,
                deep_thinking=False,
            )
            try:
                resp = await service.answer(req)
                fx.answer_after = resp.answer
                fx.workflow_after = resp.workflow_action
                fx.kb_hits_after = len(resp.knowledge_hits)
            except Exception as exc:  # noqa: BLE001
                fx.error = f"{type(exc).__name__}: {exc}"
            fx.verdict = _classify_verdict(fx)
            dt = time.perf_counter() - t0
            print(
                f"[{n:>2}/{len(cases)}] {fx.student_name} #{fx.case_index} "
                f"verdict={fx.verdict:9s} wf:{fx.workflow_before}→{fx.workflow_after} "
                f"kb:{fx.kb_hits_before}→{fx.kb_hits_after} t={dt:.1f}s",
                flush=True,
            )
    finally:
        await service.aclose()


def write_report(fixtures: list[Fixture], path: Path) -> None:
    counts: dict[str, int] = {
        "improved": 0,
        "unchanged": 0,
        "regressed": 0,
        "passed": 0,
        "failed": 0,
        "error": 0,
    }
    for fx in fixtures:
        counts[fx.verdict] = counts.get(fx.verdict, 0) + 1
    total = len(fixtures)
    out: list[str] = []
    out.append("# my-twin 回答效果不佳问题清单 — Replay 报告")
    out.append("")
    out.append(f"> Replayed at: {time.strftime('%Y-%m-%d %H:%M:%S')}  ")
    out.append(f"> Total cases: **{total}**  ")
    imp = counts.get("improved", 0)
    unc = counts.get("unchanged", 0)
    reg = counts.get("regressed", 0)
    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0)
    err = counts.get("error", 0)
    out.append(
        f"> Historical improved: **{imp}** | unchanged: **{unc}** | regressed: **{reg}**  "
    )
    out.append(f"> Simulated passed: **{passed}** | failed: **{failed}** | error: **{err}**")
    out.append("")
    out.append("## TL;DR")
    out.append("")
    historical_total = sum(1 for fx in fixtures if fx.source == "historical_poor_answer")
    simulated_total = sum(1 for fx in fixtures if fx.source == "simulated_student_question")
    if historical_total:
        out.append(
            f"- 历史弱案例改善 {imp}/{historical_total} 条 "
            f"({imp / historical_total * 100:.0f}%)"
        )
    if simulated_total:
        out.append(
            f"- 模拟学生问题通过 {passed}/{simulated_total} 条 "
            f"({passed / simulated_total * 100:.0f}%)"
        )
    out.append(f"- 仍待优化 {unc + reg + failed + err}/{total} 条")
    out.append("")
    out.append("## 详细对照")
    out.append("")
    out.append("| # | 来源 | 学生 | 课程 | wf BEFORE→AFTER | kb BEFORE→AFTER | verdict | 问题 (摘要) |")
    out.append("|---|------|------|------|------------------|------------------|---------|--------------|")
    for fx in fixtures:
        q_brief = fx.question.replace("\n", " ").replace("|", "\\|")
        if len(q_brief) > 60:
            q_brief = q_brief[:57] + "..."
        out.append(
            f"| {fx.global_index} | {fx.source} | {fx.student_name} | {fx.course_context} | "
            f"{fx.workflow_before}→{fx.workflow_after} | {fx.kb_hits_before}→{fx.kb_hits_after} | "
            f"{fx.verdict} | {q_brief} |"
        )
    out.append("")
    out.append("## 完整答案 (前后对比)")
    out.append("")
    for fx in fixtures:
        title = fx.scenario_id or f"{fx.student_name} — {fx.course_context}"
        out.append(f"### {fx.global_index}. {title} *(verdict: {fx.verdict})*")
        out.append("")
        out.append(f"**问题:** {fx.question}")
        out.append("")
        out.append(f"**BEFORE [{fx.workflow_before} / kb={fx.kb_hits_before}]:**")
        out.append("")
        out.append("> " + fx.answer_before.replace("\n", "\n> "))
        out.append("")
        out.append(f"**AFTER [{fx.workflow_after} / kb={fx.kb_hits_after}]:**")
        out.append("")
        if fx.error:
            out.append(f"> ERROR: {fx.error}")
        else:
            out.append("> " + (fx.answer_after or "(empty)").replace("\n", "\n> "))
        if fx.expectation_errors:
            out.append("")
            out.append("**Expectation errors:**")
            for error in fx.expectation_errors:
                out.append(f"- {error}")
        out.append("")
        out.append("---")
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"\nReport written to {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-cases", type=int, default=None,
                        help="Run only the first N cases (smoke test)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse fixtures only, no LLM call")
    parser.add_argument("--mock-llm", action="store_true",
                        help="Patch LLM client with deterministic stubs to verify guardrails without the live model")
    parser.add_argument("--fixture-source", choices=["historical", "simulated", "both"],
                        default="historical",
                        help="Which replay fixture set to run")
    parser.add_argument("--out", type=Path, default=REPORT_DOC)
    parser.add_argument("--json-out", type=Path, default=None,
                        help="Optional JSON dump of all fixtures + replay results")
    parser.add_argument("--persist-runtime", action="store_true",
                        help="Write replay memory/queues to configured data dirs instead of a temporary runtime")
    args = parser.parse_args()

    fixtures: list[Fixture] = []
    if args.fixture_source in {"historical", "both"}:
        historical = parse_fixtures(POOR_DOC)
        print(f"Parsed {len(historical)} fixtures from {POOR_DOC.name}")
        fixtures.extend(historical)
    if args.fixture_source in {"simulated", "both"}:
        simulated = load_simulated_fixtures(SIMULATED_DOC)
        print(f"Parsed {len(simulated)} fixtures from {SIMULATED_DOC.name}")
        fixtures.extend(simulated)
    for n, fx in enumerate(fixtures):
        fx.global_index = n + 1
    assign_conversation_ids(fixtures)
    if args.dry_run:
        for fx in fixtures:
            print(f"  [{fx.global_index:>2}] {fx.student_name:<6} #{fx.case_index:<2} "
                  f"wf={fx.workflow_before:<14} kb={fx.kb_hits_before} "
                  f"course={fx.course_context} | Q={fx.question[:60]!r}")
        return 0

    replay_fixtures = fixtures if args.max_cases is None else fixtures[:args.max_cases]
    settings = AppSettings()
    if args.persist_runtime:
        asyncio.run(run_replay(replay_fixtures, settings, max_cases=None, mock_llm=args.mock_llm))
    else:
        with tempfile.TemporaryDirectory(prefix="twin-replay-") as tmp:
            runtime_root = Path(tmp)
            isolated_settings = settings.model_copy(
                update={
                    "conversation_memory_dir": runtime_root / "conversation_memory",
                    "context_digest_dir": runtime_root / "conversation_memory" / "digests",
                    "follow_up_queue_dir": runtime_root / "follow_up_actions",
                    "escalation_queue_dir": runtime_root / "escalations",
                    "artifact_memory_draft_dir": runtime_root / "artifact_memory_drafts",
                    "knowledge_gap_draft_dir": runtime_root / "knowledge_gap_drafts",
                    "online_presence_dir": runtime_root / "online_presence",
                    "operations_task_state_dir": runtime_root / "operations_task_state",
                    "suggestion_board_dir": runtime_root / "suggestions",
                    "user_account_store_dir": runtime_root / "user_accounts",
                    "planner_comparison_dir": runtime_root / "planner-comparisons",
                    "planner_metrics_dir": runtime_root / "planner-metrics",
                }
            )
            asyncio.run(
                run_replay(
                    replay_fixtures,
                    isolated_settings,
                    max_cases=None,
                    mock_llm=args.mock_llm,
                )
            )
    write_report(replay_fixtures, args.out)
    if args.json_out is not None:
        args.json_out.write_text(
            json.dumps([asdict(fx) for fx in replay_fixtures], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"JSON dump: {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
