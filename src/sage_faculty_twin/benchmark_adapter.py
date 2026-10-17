from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, TypeVar

from .runtime_env import bootstrap_runtime_env

bootstrap_runtime_env(require_policy=True, require_fastapi=False)

from pydantic import BaseModel, ConfigDict, Field

from .config import AppSettings
from .models import ChatRequest, ChatResponse
from .service import DigitalTwinService


class ChatResponder(Protocol):
    async def __call__(self, request: ChatRequest) -> ChatResponse: ...


_T = TypeVar("_T")


class CharacterEvalSample(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | int
    role: str = Field(min_length=1, max_length=128)
    context: str = Field(min_length=1)


class CharacterEvalPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | int
    role: str
    context: str
    model_output: str
    conversation_id: str | None = None
    workflow_action: str | None = None


class FacultyCharacterEvalRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_include_all: list[str] = Field(default_factory=list)
    must_include_any: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    preferred_keywords: list[str] = Field(default_factory=list)
    min_chars: int = Field(default=0, ge=0, le=4000)
    max_chars: int = Field(default=240, ge=1, le=4000)
    pass_threshold: int = Field(default=70, ge=0, le=100)


class FacultyCharacterEvalScenario(CharacterEvalSample):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    focus: str = Field(min_length=1, max_length=64)
    expected_traits: list[str] = Field(default_factory=list)
    rubric: FacultyCharacterEvalRubric


class FacultyCharacterEvalScenarioScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    title: str
    focus: str
    prediction_id: str | int
    passed: bool
    total_score: int = Field(ge=0, le=100)
    matched_all: list[str] = Field(default_factory=list)
    missing_all: list[str] = Field(default_factory=list)
    matched_any: list[str] = Field(default_factory=list)
    missing_any: list[str] = Field(default_factory=list)
    preferred_hits: list[str] = Field(default_factory=list)
    forbidden_hits: list[str] = Field(default_factory=list)
    output_length: int = Field(ge=0)
    output_excerpt: str = Field(default="", max_length=400)


class FacultyCharacterEvalScoreReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_count: int = Field(ge=0)
    evaluated_count: int = Field(ge=0)
    average_score: float = Field(ge=0, le=100)
    pass_rate: float = Field(ge=0, le=1)
    focus_average_scores: dict[str, float] = Field(default_factory=dict)
    results: list[FacultyCharacterEvalScenarioScore] = Field(default_factory=list)


class LampProfileItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | int | None = None


class LampQuestionSample(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | int
    input: str = Field(min_length=1)
    profile: list[dict[str, Any]] = Field(default_factory=list)
    output: str | None = None


class LampPredictionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | int
    output: str


class LampPredictionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=32)
    golds: list[LampPredictionItem] = Field(default_factory=list)


class LocalLampRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_include_all: list[str] = Field(default_factory=list)
    must_include_any: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    preferred_keywords: list[str] = Field(default_factory=list)
    profile_grounding_terms: list[str] = Field(default_factory=list)
    min_chars: int = Field(default=0, ge=0, le=4000)
    max_chars: int = Field(default=240, ge=1, le=4000)
    pass_threshold: int = Field(default=70, ge=0, le=100)


class LocalLampScenario(LampQuestionSample):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    task_name: str = Field(default="LaMP-Local", min_length=1, max_length=32)
    focus: str = Field(min_length=1, max_length=64)
    expected_traits: list[str] = Field(default_factory=list)
    rubric: LocalLampRubric


class LocalLampScenarioScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    title: str
    task_name: str
    focus: str
    prediction_id: str | int
    passed: bool
    total_score: int = Field(ge=0, le=100)
    matched_all: list[str] = Field(default_factory=list)
    missing_all: list[str] = Field(default_factory=list)
    matched_any: list[str] = Field(default_factory=list)
    missing_any: list[str] = Field(default_factory=list)
    preferred_hits: list[str] = Field(default_factory=list)
    profile_hits: list[str] = Field(default_factory=list)
    missing_profile_terms: list[str] = Field(default_factory=list)
    forbidden_hits: list[str] = Field(default_factory=list)
    output_length: int = Field(ge=0)
    output_excerpt: str = Field(default="", max_length=400)


class LocalLampScoreReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_name: str = Field(min_length=1, max_length=32)
    scenario_count: int = Field(ge=0)
    evaluated_count: int = Field(ge=0)
    average_score: float = Field(ge=0, le=100)
    pass_rate: float = Field(ge=0, le=1)
    focus_average_scores: dict[str, float] = Field(default_factory=dict)
    results: list[LocalLampScenarioScore] = Field(default_factory=list)


class BenchmarkTraceDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_duration_ms: int | None = Field(default=None, ge=0)
    llm_answer_duration_ms: int | None = Field(default=None, ge=0)
    step_durations_ms: dict[str, int] = Field(default_factory=dict)
    step_statuses: dict[str, str] = Field(default_factory=dict)


class MemoryFollowupTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)


class MemoryFollowupRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_include_all: list[str] = Field(default_factory=list)
    must_include_any: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    preferred_keywords: list[str] = Field(default_factory=list)
    memory_grounding_terms: list[str] = Field(default_factory=list)
    required_memory_types: list[str] = Field(default_factory=list)
    require_memory_used: bool = True
    min_retrieved_items: int = Field(default=1, ge=0, le=20)
    min_chars: int = Field(default=0, ge=0, le=4000)
    max_chars: int = Field(default=240, ge=1, le=4000)
    pass_threshold: int = Field(default=70, ge=0, le=100)


class MemoryFollowupScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    focus: str = Field(min_length=1, max_length=64)
    student_name: str = Field(
        default="Memory Benchmark User", min_length=1, max_length=128
    )
    student_email: str | None = Field(default=None, max_length=256)
    course_context: str | None = Field(default="科研指导", max_length=512)
    visitor_profile: str | None = Field(default="general_visitor", max_length=64)
    conversation_id: str | None = Field(default=None, max_length=128)
    seed_turns: list[MemoryFollowupTurn] = Field(min_length=1)
    evaluation_turn: MemoryFollowupTurn
    expected_traits: list[str] = Field(default_factory=list)
    rubric: MemoryFollowupRubric


class MemoryFollowupPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    focus: str = Field(min_length=1, max_length=64)
    conversation_id: str | None = Field(default=None, max_length=128)
    model_output: str
    workflow_action: str | None = None
    memory_used: bool = False
    memory_write_back: bool = False
    retrieved_item_count: int = Field(default=0, ge=0)
    retrieved_memory_types: list[str] = Field(default_factory=list)
    scenario_duration_ms: int | None = Field(default=None, ge=0)
    evaluation_diagnostics: BenchmarkTraceDiagnostics | None = None
    seed_turn_diagnostics: list[BenchmarkTraceDiagnostics] = Field(default_factory=list)


class MemoryFollowupScenarioScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    title: str
    focus: str
    passed: bool
    total_score: int = Field(ge=0, le=100)
    matched_all: list[str] = Field(default_factory=list)
    missing_all: list[str] = Field(default_factory=list)
    matched_any: list[str] = Field(default_factory=list)
    missing_any: list[str] = Field(default_factory=list)
    preferred_hits: list[str] = Field(default_factory=list)
    memory_grounding_hits: list[str] = Field(default_factory=list)
    missing_memory_grounding_terms: list[str] = Field(default_factory=list)
    retrieved_memory_types: list[str] = Field(default_factory=list)
    missing_memory_types: list[str] = Field(default_factory=list)
    memory_used: bool = False
    memory_write_back: bool = False
    retrieved_item_count: int = Field(default=0, ge=0)
    forbidden_hits: list[str] = Field(default_factory=list)
    output_length: int = Field(ge=0)
    output_excerpt: str = Field(default="", max_length=400)


class MemoryFollowupScoreReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_count: int = Field(ge=0)
    evaluated_count: int = Field(ge=0)
    average_score: float = Field(ge=0, le=100)
    pass_rate: float = Field(ge=0, le=1)
    focus_average_scores: dict[str, float] = Field(default_factory=dict)
    results: list[MemoryFollowupScenarioScore] = Field(default_factory=list)


class UnifiedBenchmarkFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark_name: str = Field(min_length=1, max_length=64)
    scenario_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    focus: str = Field(min_length=1, max_length=64)
    passed: bool
    total_score: int = Field(ge=0, le=100)


class UnifiedBenchmarkDatasetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark_name: str = Field(min_length=1, max_length=64)
    scenario_count: int = Field(ge=0)
    evaluated_count: int = Field(ge=0)
    average_score: float = Field(ge=0, le=100)
    pass_rate: float = Field(ge=0, le=1)
    failure_count: int = Field(ge=0)
    focus_average_scores: dict[str, float] = Field(default_factory=dict)
    lowest_scenarios: list[UnifiedBenchmarkFinding] = Field(default_factory=list)


class UnifiedBenchmarkSummaryReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark_count: int = Field(ge=0)
    evaluated_count: int = Field(ge=0)
    overall_average_score: float = Field(ge=0, le=100)
    overall_pass_rate: float = Field(ge=0, le=1)
    benchmark_summaries: list[UnifiedBenchmarkDatasetSummary] = Field(
        default_factory=list
    )
    weakest_scenarios: list[UnifiedBenchmarkFinding] = Field(default_factory=list)


def load_charactereval_samples(path: Path) -> list[CharacterEvalSample]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("CharacterEval input must be a JSON list.")
    return [CharacterEvalSample.model_validate(item) for item in payload]


def default_faculty_charactereval_subset_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "data"
        / "charactereval_faculty_subset.json"
    )


def load_faculty_charactereval_scenarios(
    path: Path | None = None,
) -> list[FacultyCharacterEvalScenario]:
    scenario_path = path or default_faculty_charactereval_subset_path()
    payload = json.loads(scenario_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Faculty CharacterEval scenario file must be a JSON list.")
    return [FacultyCharacterEvalScenario.model_validate(item) for item in payload]


def load_charactereval_predictions(path: Path) -> list[CharacterEvalPrediction]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("CharacterEval prediction file must be a JSON list.")
    return [CharacterEvalPrediction.model_validate(item) for item in payload]


def load_lamp_questions(path: Path) -> tuple[str | None, list[LampQuestionSample]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        task_name = str(payload.get("task")) if payload.get("task") else None
        raw_samples = payload.get("golds")
    else:
        task_name = None
        raw_samples = payload

    if not isinstance(raw_samples, list):
        raise ValueError(
            "LaMP question file must be a JSON list or a {task, golds} envelope."
        )

    return task_name, [LampQuestionSample.model_validate(item) for item in raw_samples]


def default_local_lamp_subset_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "data"
        / "lamp_personalization_subset.json"
    )


def load_local_lamp_scenarios(path: Path | None = None) -> list[LocalLampScenario]:
    scenario_path = path or default_local_lamp_subset_path()
    payload = json.loads(scenario_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Local LaMP scenario file must be a JSON list.")
    return [LocalLampScenario.model_validate(item) for item in payload]


def default_memory_followup_subset_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "data"
        / "memory_followup_subset.json"
    )


def load_memory_followup_scenarios(
    path: Path | None = None,
) -> list[MemoryFollowupScenario]:
    scenario_path = path or default_memory_followup_subset_path()
    payload = json.loads(scenario_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Memory follow-up scenario file must be a JSON list.")
    return [MemoryFollowupScenario.model_validate(item) for item in payload]


def load_lamp_prediction_envelope(path: Path) -> LampPredictionEnvelope:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("LaMP prediction file must be a JSON object envelope.")
    return LampPredictionEnvelope.model_validate(payload)


def load_faculty_charactereval_score_report(
    path: Path,
) -> FacultyCharacterEvalScoreReport:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Faculty CharacterEval score report must be a JSON object.")
    return FacultyCharacterEvalScoreReport.model_validate(payload)


def load_local_lamp_score_report(path: Path) -> LocalLampScoreReport:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Local LaMP score report must be a JSON object.")
    return LocalLampScoreReport.model_validate(payload)


def load_memory_followup_score_report(path: Path) -> MemoryFollowupScoreReport:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Memory follow-up score report must be a JSON object.")
    return MemoryFollowupScoreReport.model_validate(payload)


def save_charactereval_predictions(
    path: Path, predictions: list[CharacterEvalPrediction]
) -> None:
    path.write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in predictions],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def save_lamp_predictions(path: Path, envelope: LampPredictionEnvelope) -> None:
    path.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")


def save_faculty_charactereval_score_report(
    path: Path, report: FacultyCharacterEvalScoreReport
) -> None:
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def save_local_lamp_score_report(path: Path, report: LocalLampScoreReport) -> None:
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def save_memory_followup_predictions(
    path: Path, predictions: list[MemoryFollowupPrediction]
) -> None:
    path.write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in predictions],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def save_memory_followup_score_report(
    path: Path, report: MemoryFollowupScoreReport
) -> None:
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def save_unified_benchmark_summary(
    path: Path, report: UnifiedBenchmarkSummaryReport
) -> None:
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def build_charactereval_request(sample: CharacterEvalSample) -> ChatRequest:
    question = (
        "你正在参与一个 CharacterEval 风格的多轮角色一致性评测。"
        "请只输出目标角色的下一句回复，不要解释，不要附加说明。\n"
        f"目标角色：{sample.role}\n"
        "下面是已有对话，上下文最后一行是对方刚说的话：\n"
        f"{sample.context}"
    )
    return ChatRequest(
        student_name="CharacterEval Runner",
        question=question,
        course_context="CharacterEval role-play benchmark",
        visitor_profile="general_visitor",
        conversation_id=f"charactereval-{sample.id}",
    )


def build_lamp_request(sample: LampQuestionSample, *, task_name: str) -> ChatRequest:
    profile_text = _render_lamp_profile(sample.profile)
    question = (
        "下面是一个 LaMP 风格的个性化回答任务。"
        "请结合学生画像，直接回答学生当前想请教老师的问题，只返回答案本身，不要解释。\n"
        f"Task: {task_name}\n"
        f"学生画像：\n{profile_text}\n"
        f"学生当前想请教老师的问题：{sample.input}"
    )
    return ChatRequest(
        student_name="LaMP User",
        student_email="lamp-user@example.com",
        question=question,
        course_context="LaMP personalization benchmark",
        visitor_profile="general_visitor",
        conversation_id=f"lamp-{task_name}-{sample.id}",
    )


def build_memory_followup_request(
    scenario: MemoryFollowupScenario,
    turn: MemoryFollowupTurn,
    *,
    conversation_id: str,
) -> ChatRequest:
    return ChatRequest(
        student_name=scenario.student_name,
        student_email=scenario.student_email,
        question=turn.question,
        course_context=scenario.course_context,
        visitor_profile=scenario.visitor_profile,
        conversation_id=conversation_id,
    )


async def run_charactereval_adapter(
    samples: list[CharacterEvalSample],
    responder: ChatResponder,
) -> list[CharacterEvalPrediction]:
    predictions: list[CharacterEvalPrediction] = []
    for sample in samples:
        response = await responder(build_charactereval_request(sample))
        predictions.append(
            CharacterEvalPrediction(
                id=sample.id,
                role=sample.role,
                context=sample.context,
                model_output=response.answer.strip(),
                conversation_id=response.conversation_id,
                workflow_action=response.workflow_action,
            )
        )
    return predictions


async def run_lamp_adapter(
    samples: list[LampQuestionSample],
    responder: ChatResponder,
    *,
    task_name: str,
) -> LampPredictionEnvelope:
    outputs: list[LampPredictionItem] = []
    for sample in samples:
        response = await responder(build_lamp_request(sample, task_name=task_name))
        outputs.append(LampPredictionItem(id=sample.id, output=response.answer.strip()))
    return LampPredictionEnvelope(task=_normalize_task_name(task_name), golds=outputs)


async def run_memory_followup_adapter(
    scenarios: list[MemoryFollowupScenario],
    responder: ChatResponder,
) -> list[MemoryFollowupPrediction]:
    predictions: list[MemoryFollowupPrediction] = []
    for scenario in scenarios:
        conversation_id = (
            scenario.conversation_id or f"memory-followup-{scenario.scenario_id}"
        )
        seed_turn_diagnostics: list[BenchmarkTraceDiagnostics] = []
        scenario_duration_ms = 0
        has_duration_data = False
        for turn in scenario.seed_turns:
            seed_response = await responder(
                build_memory_followup_request(
                    scenario, turn, conversation_id=conversation_id
                )
            )
            diagnostics = _extract_trace_diagnostics(seed_response)
            if diagnostics is not None:
                seed_turn_diagnostics.append(diagnostics)
                if diagnostics.workflow_duration_ms is not None:
                    scenario_duration_ms += diagnostics.workflow_duration_ms
                    has_duration_data = True
        response = await responder(
            build_memory_followup_request(
                scenario, scenario.evaluation_turn, conversation_id=conversation_id
            )
        )
        evaluation_diagnostics = _extract_trace_diagnostics(response)
        if (
            evaluation_diagnostics is not None
            and evaluation_diagnostics.workflow_duration_ms is not None
        ):
            scenario_duration_ms += evaluation_diagnostics.workflow_duration_ms
            has_duration_data = True
        predictions.append(
            MemoryFollowupPrediction(
                scenario_id=scenario.scenario_id,
                title=scenario.title,
                focus=scenario.focus,
                conversation_id=response.conversation_id or conversation_id,
                model_output=response.answer.strip(),
                workflow_action=response.workflow_action,
                memory_used=response.memory_used,
                memory_write_back=response.memory_write_back,
                retrieved_item_count=len(response.retrieved_items),
                retrieved_memory_types=sorted(
                    {item.memory_type for item in response.retrieved_items}
                ),
                scenario_duration_ms=scenario_duration_ms
                if has_duration_data
                else None,
                evaluation_diagnostics=evaluation_diagnostics,
                seed_turn_diagnostics=seed_turn_diagnostics,
            )
        )
    return predictions


def _extract_trace_diagnostics(
    response: ChatResponse,
) -> BenchmarkTraceDiagnostics | None:
    if not response.workflow_trace:
        return None

    step_durations_ms: dict[str, int] = {}
    step_statuses: dict[str, str] = {}
    workflow_duration_ms = 0
    has_duration_data = False
    llm_answer_duration_ms: int | None = None

    for step in response.workflow_trace:
        step_statuses[step.key] = step.status
        if step.duration_ms is None:
            continue
        step_durations_ms[step.key] = step.duration_ms
        workflow_duration_ms += step.duration_ms
        has_duration_data = True
        if step.key == "llm_answer":
            llm_answer_duration_ms = step.duration_ms

    return BenchmarkTraceDiagnostics(
        workflow_duration_ms=workflow_duration_ms if has_duration_data else None,
        llm_answer_duration_ms=llm_answer_duration_ms,
        step_durations_ms=step_durations_ms,
        step_statuses=step_statuses,
    )


def score_faculty_charactereval_predictions(
    scenarios: list[FacultyCharacterEvalScenario],
    predictions: list[CharacterEvalPrediction],
) -> FacultyCharacterEvalScoreReport:
    prediction_by_id = {item.id: item for item in predictions}
    results: list[FacultyCharacterEvalScenarioScore] = []

    for scenario in scenarios:
        prediction = prediction_by_id.get(scenario.id)
        output_text = prediction.model_output.strip() if prediction else ""
        matched_all = [
            term
            for term in scenario.rubric.must_include_all
            if term and term in output_text
        ]
        missing_all = [
            term
            for term in scenario.rubric.must_include_all
            if term and term not in output_text
        ]
        matched_any = [
            term
            for term in scenario.rubric.must_include_any
            if term and term in output_text
        ]
        missing_any = [
            term
            for term in scenario.rubric.must_include_any
            if term and term not in output_text
        ]
        preferred_hits = [
            term
            for term in scenario.rubric.preferred_keywords
            if term and term in output_text
        ]
        forbidden_hits = [
            term
            for term in scenario.rubric.must_not_include
            if term and term in output_text
        ]

        required_all_score = _fractional_score(
            len(matched_all), len(scenario.rubric.must_include_all), 40
        )
        required_any_score = (
            20 if not scenario.rubric.must_include_any else (20 if matched_any else 0)
        )
        preferred_score = _fractional_score(
            len(preferred_hits), len(scenario.rubric.preferred_keywords), 15
        )
        length_score = _length_score(
            output_text, scenario.rubric.min_chars, scenario.rubric.max_chars, 25
        )
        penalty = min(40, 20 * len(forbidden_hits))
        total_score = max(
            0,
            required_all_score
            + required_any_score
            + preferred_score
            + length_score
            - penalty,
        )

        passed = (
            prediction is not None
            and not missing_all
            and (not scenario.rubric.must_include_any or bool(matched_any))
            and not forbidden_hits
            and total_score >= scenario.rubric.pass_threshold
        )

        results.append(
            FacultyCharacterEvalScenarioScore(
                scenario_id=scenario.scenario_id,
                title=scenario.title,
                focus=scenario.focus,
                prediction_id=scenario.id,
                passed=passed,
                total_score=total_score,
                matched_all=matched_all,
                missing_all=missing_all,
                matched_any=matched_any,
                missing_any=missing_any,
                preferred_hits=preferred_hits,
                forbidden_hits=forbidden_hits,
                output_length=len(output_text),
                output_excerpt=output_text[:400],
            )
        )

    average_score = (
        round(sum(item.total_score for item in results) / len(results), 2)
        if results
        else 0.0
    )
    pass_rate = (
        round(sum(1 for item in results if item.passed) / len(results), 4)
        if results
        else 0.0
    )

    focus_buckets: dict[str, list[int]] = {}
    for item in results:
        focus_buckets.setdefault(item.focus, []).append(item.total_score)
    focus_average_scores = {
        focus: round(sum(scores) / len(scores), 2)
        for focus, scores in sorted(focus_buckets.items())
    }

    return FacultyCharacterEvalScoreReport(
        scenario_count=len(scenarios),
        evaluated_count=len(results),
        average_score=average_score,
        pass_rate=pass_rate,
        focus_average_scores=focus_average_scores,
        results=results,
    )


def score_local_lamp_predictions(
    scenarios: list[LocalLampScenario],
    predictions: LampPredictionEnvelope,
) -> LocalLampScoreReport:
    prediction_by_id = {item.id: item.output.strip() for item in predictions.golds}
    results: list[LocalLampScenarioScore] = []
    task_name = predictions.task

    for scenario in scenarios:
        output_text = prediction_by_id.get(scenario.id, "")
        matched_all = [
            term
            for term in scenario.rubric.must_include_all
            if term and term in output_text
        ]
        missing_all = [
            term
            for term in scenario.rubric.must_include_all
            if term and term not in output_text
        ]
        matched_any = [
            term
            for term in scenario.rubric.must_include_any
            if term and term in output_text
        ]
        missing_any = [
            term
            for term in scenario.rubric.must_include_any
            if term and term not in output_text
        ]
        preferred_hits = [
            term
            for term in scenario.rubric.preferred_keywords
            if term and term in output_text
        ]
        profile_hits = [
            term
            for term in scenario.rubric.profile_grounding_terms
            if term and term in output_text
        ]
        missing_profile_terms = [
            term
            for term in scenario.rubric.profile_grounding_terms
            if term and term not in output_text
        ]
        forbidden_hits = [
            term
            for term in scenario.rubric.must_not_include
            if term and term in output_text
        ]

        required_all_score = _fractional_score(
            len(matched_all), len(scenario.rubric.must_include_all), 25
        )
        required_any_score = (
            15 if not scenario.rubric.must_include_any else (15 if matched_any else 0)
        )
        preferred_score = _fractional_score(
            len(preferred_hits), len(scenario.rubric.preferred_keywords), 10
        )
        profile_score = _fractional_score(
            len(profile_hits), len(scenario.rubric.profile_grounding_terms), 30
        )
        length_score = _length_score(
            output_text, scenario.rubric.min_chars, scenario.rubric.max_chars, 20
        )
        penalty = min(40, 20 * len(forbidden_hits))
        total_score = max(
            0,
            required_all_score
            + required_any_score
            + preferred_score
            + profile_score
            + length_score
            - penalty,
        )

        passed = (
            not missing_all
            and (not scenario.rubric.must_include_any or bool(matched_any))
            and (not scenario.rubric.profile_grounding_terms or bool(profile_hits))
            and not forbidden_hits
            and total_score >= scenario.rubric.pass_threshold
        )

        results.append(
            LocalLampScenarioScore(
                scenario_id=scenario.scenario_id,
                title=scenario.title,
                task_name=scenario.task_name,
                focus=scenario.focus,
                prediction_id=scenario.id,
                passed=passed,
                total_score=total_score,
                matched_all=matched_all,
                missing_all=missing_all,
                matched_any=matched_any,
                missing_any=missing_any,
                preferred_hits=preferred_hits,
                profile_hits=profile_hits,
                missing_profile_terms=missing_profile_terms,
                forbidden_hits=forbidden_hits,
                output_length=len(output_text),
                output_excerpt=output_text[:400],
            )
        )

    average_score = (
        round(sum(item.total_score for item in results) / len(results), 2)
        if results
        else 0.0
    )
    pass_rate = (
        round(sum(1 for item in results if item.passed) / len(results), 4)
        if results
        else 0.0
    )
    focus_buckets: dict[str, list[int]] = {}
    for item in results:
        focus_buckets.setdefault(item.focus, []).append(item.total_score)
    focus_average_scores = {
        focus: round(sum(scores) / len(scores), 2)
        for focus, scores in sorted(focus_buckets.items())
    }

    return LocalLampScoreReport(
        task_name=task_name,
        scenario_count=len(scenarios),
        evaluated_count=len(results),
        average_score=average_score,
        pass_rate=pass_rate,
        focus_average_scores=focus_average_scores,
        results=results,
    )


def score_memory_followup_predictions(
    scenarios: list[MemoryFollowupScenario],
    predictions: list[MemoryFollowupPrediction],
) -> MemoryFollowupScoreReport:
    prediction_by_id = {item.scenario_id: item for item in predictions}
    results: list[MemoryFollowupScenarioScore] = []

    for scenario in scenarios:
        prediction = prediction_by_id.get(scenario.scenario_id)
        output_text = prediction.model_output.strip() if prediction else ""
        matched_all = [
            term
            for term in scenario.rubric.must_include_all
            if term and term in output_text
        ]
        missing_all = [
            term
            for term in scenario.rubric.must_include_all
            if term and term not in output_text
        ]
        matched_any = [
            term
            for term in scenario.rubric.must_include_any
            if term and term in output_text
        ]
        missing_any = [
            term
            for term in scenario.rubric.must_include_any
            if term and term not in output_text
        ]
        preferred_hits = [
            term
            for term in scenario.rubric.preferred_keywords
            if term and term in output_text
        ]
        memory_grounding_hits = [
            term
            for term in scenario.rubric.memory_grounding_terms
            if term and term in output_text
        ]
        missing_memory_grounding_terms = [
            term
            for term in scenario.rubric.memory_grounding_terms
            if term and term not in output_text
        ]
        forbidden_hits = [
            term
            for term in scenario.rubric.must_not_include
            if term and term in output_text
        ]
        retrieved_memory_types = prediction.retrieved_memory_types if prediction else []
        missing_memory_types = [
            memory_type
            for memory_type in scenario.rubric.required_memory_types
            if memory_type not in retrieved_memory_types
        ]
        retrieved_item_count = prediction.retrieved_item_count if prediction else 0
        memory_used = prediction.memory_used if prediction else False
        memory_write_back = prediction.memory_write_back if prediction else False

        required_all_score = _fractional_score(
            len(matched_all), len(scenario.rubric.must_include_all), 20
        )
        required_any_score = (
            10 if not scenario.rubric.must_include_any else (10 if matched_any else 0)
        )
        preferred_score = _fractional_score(
            len(preferred_hits), len(scenario.rubric.preferred_keywords), 10
        )
        grounding_score = _fractional_score(
            len(memory_grounding_hits),
            len(scenario.rubric.memory_grounding_terms),
            20,
        )
        memory_used_score = (
            20 if (memory_used or not scenario.rubric.require_memory_used) else 0
        )
        retrieved_count_score = (
            _fractional_score(
                min(retrieved_item_count, scenario.rubric.min_retrieved_items),
                scenario.rubric.min_retrieved_items,
                10,
            )
            if scenario.rubric.min_retrieved_items
            else 10
        )
        memory_type_score = _fractional_score(
            len(scenario.rubric.required_memory_types) - len(missing_memory_types),
            len(scenario.rubric.required_memory_types),
            10,
        )
        length_score = _length_score(
            output_text, scenario.rubric.min_chars, scenario.rubric.max_chars, 10
        )
        penalty = min(40, 20 * len(forbidden_hits))
        total_score = min(
            100,
            max(
                0,
                required_all_score
                + required_any_score
                + preferred_score
                + grounding_score
                + memory_used_score
                + retrieved_count_score
                + memory_type_score
                + length_score
                - penalty,
            ),
        )

        passed = (
            prediction is not None
            and not missing_all
            and (not scenario.rubric.must_include_any or bool(matched_any))
            and not forbidden_hits
            and (not scenario.rubric.require_memory_used or memory_used)
            and retrieved_item_count >= scenario.rubric.min_retrieved_items
            and not missing_memory_types
            and total_score >= scenario.rubric.pass_threshold
        )

        results.append(
            MemoryFollowupScenarioScore(
                scenario_id=scenario.scenario_id,
                title=scenario.title,
                focus=scenario.focus,
                passed=passed,
                total_score=total_score,
                matched_all=matched_all,
                missing_all=missing_all,
                matched_any=matched_any,
                missing_any=missing_any,
                preferred_hits=preferred_hits,
                memory_grounding_hits=memory_grounding_hits,
                missing_memory_grounding_terms=missing_memory_grounding_terms,
                retrieved_memory_types=retrieved_memory_types,
                missing_memory_types=missing_memory_types,
                memory_used=memory_used,
                memory_write_back=memory_write_back,
                retrieved_item_count=retrieved_item_count,
                forbidden_hits=forbidden_hits,
                output_length=len(output_text),
                output_excerpt=output_text[:400],
            )
        )

    average_score = (
        round(sum(item.total_score for item in results) / len(results), 2)
        if results
        else 0.0
    )
    pass_rate = (
        round(sum(1 for item in results if item.passed) / len(results), 4)
        if results
        else 0.0
    )
    focus_buckets: dict[str, list[int]] = {}
    for item in results:
        focus_buckets.setdefault(item.focus, []).append(item.total_score)
    focus_average_scores = {
        focus: round(sum(scores) / len(scores), 2)
        for focus, scores in sorted(focus_buckets.items())
    }

    return MemoryFollowupScoreReport(
        scenario_count=len(scenarios),
        evaluated_count=len(results),
        average_score=average_score,
        pass_rate=pass_rate,
        focus_average_scores=focus_average_scores,
        results=results,
    )


def summarize_local_benchmark_reports(
    faculty_report: FacultyCharacterEvalScoreReport,
    lamp_report: LocalLampScoreReport,
    *,
    memory_report: MemoryFollowupScoreReport | None = None,
    lowest_k: int = 5,
) -> UnifiedBenchmarkSummaryReport:
    benchmark_summaries = [
        _build_dataset_summary(
            benchmark_name="faculty-charactereval",
            scenario_count=faculty_report.scenario_count,
            evaluated_count=faculty_report.evaluated_count,
            average_score=faculty_report.average_score,
            pass_rate=faculty_report.pass_rate,
            focus_average_scores=faculty_report.focus_average_scores,
            results=faculty_report.results,
            lowest_k=lowest_k,
        ),
        _build_dataset_summary(
            benchmark_name="local-lamp",
            scenario_count=lamp_report.scenario_count,
            evaluated_count=lamp_report.evaluated_count,
            average_score=lamp_report.average_score,
            pass_rate=lamp_report.pass_rate,
            focus_average_scores=lamp_report.focus_average_scores,
            results=lamp_report.results,
            lowest_k=lowest_k,
        ),
    ]
    if memory_report is not None:
        benchmark_summaries.append(
            _build_dataset_summary(
                benchmark_name="memory-followup",
                scenario_count=memory_report.scenario_count,
                evaluated_count=memory_report.evaluated_count,
                average_score=memory_report.average_score,
                pass_rate=memory_report.pass_rate,
                focus_average_scores=memory_report.focus_average_scores,
                results=memory_report.results,
                lowest_k=lowest_k,
            )
        )

    total_evaluated = sum(item.evaluated_count for item in benchmark_summaries)
    weighted_score_sum = sum(
        item.average_score * item.evaluated_count for item in benchmark_summaries
    )
    passed_count = sum(
        0 if item.evaluated_count == 0 else round(item.pass_rate * item.evaluated_count)
        for item in benchmark_summaries
    )
    weakest_scenarios = sorted(
        [
            finding
            for summary in benchmark_summaries
            for finding in summary.lowest_scenarios
        ],
        key=lambda item: (item.total_score, item.benchmark_name, item.scenario_id),
    )[:lowest_k]

    return UnifiedBenchmarkSummaryReport(
        benchmark_count=len(benchmark_summaries),
        evaluated_count=total_evaluated,
        overall_average_score=round(weighted_score_sum / total_evaluated, 2)
        if total_evaluated
        else 0.0,
        overall_pass_rate=round(passed_count / total_evaluated, 4)
        if total_evaluated
        else 0.0,
        benchmark_summaries=benchmark_summaries,
        weakest_scenarios=weakest_scenarios,
    )


async def answer_with_service(
    request: ChatRequest,
    *,
    settings: AppSettings,
) -> ChatResponse:
    service = DigitalTwinService(settings)
    try:
        return await service.answer(request)
    finally:
        await service.aclose()


async def _run_with_benchmark_service(
    settings: AppSettings,
    operation: Callable[[ChatResponder], Awaitable[_T]],
) -> _T:
    service = DigitalTwinService(settings)

    async def responder(request: ChatRequest) -> ChatResponse:
        return await service.answer(request)

    try:
        return await operation(responder)
    finally:
        await service.aclose()


def _render_lamp_profile(profile: list[dict[str, Any]]) -> str:
    if not profile:
        return "- no profile items"

    lines: list[str] = []
    for index, item in enumerate(profile, start=1):
        parts: list[str] = []
        for key, value in item.items():
            if key == "id" or value in (None, ""):
                continue
            if isinstance(value, (str, int, float)):
                parts.append(f"{key}: {value}")
        lines.append(f"- item {index}: {' | '.join(parts) if parts else 'empty'}")
    return "\n".join(lines)


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run benchmark adapters against sage-mate."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    char_parser = subparsers.add_parser(
        "charactereval", help="Run CharacterEval-style generation export."
    )
    char_parser.add_argument("--input", required=True, type=Path)
    char_parser.add_argument("--output", required=True, type=Path)

    score_parser = subparsers.add_parser(
        "score-charactereval-faculty",
        help="Score a faculty-specific CharacterEval prediction file against the local rubric scaffold.",
    )
    score_parser.add_argument("--scenarios", default=None, type=Path)
    score_parser.add_argument("--predictions", required=True, type=Path)
    score_parser.add_argument("--output", required=True, type=Path)

    lamp_parser = subparsers.add_parser(
        "lamp", help="Run LaMP-style prediction export."
    )
    lamp_parser.add_argument("--questions", required=True, type=Path)
    lamp_parser.add_argument("--output", required=True, type=Path)
    lamp_parser.add_argument("--task-name", default=None)

    score_lamp_parser = subparsers.add_parser(
        "score-lamp-local",
        help="Score a local LaMP-style personalization prediction file against the repo-local rubric scaffold.",
    )
    score_lamp_parser.add_argument("--scenarios", default=None, type=Path)
    score_lamp_parser.add_argument("--predictions", required=True, type=Path)
    score_lamp_parser.add_argument("--output", required=True, type=Path)

    memory_parser = subparsers.add_parser(
        "memory-followup",
        help="Run a repo-local multi-turn memory follow-up benchmark.",
    )
    memory_parser.add_argument("--scenarios", default=None, type=Path)
    memory_parser.add_argument("--output", required=True, type=Path)

    score_memory_parser = subparsers.add_parser(
        "score-memory-followup",
        help="Score a repo-local memory follow-up prediction file against the rubric scaffold.",
    )
    score_memory_parser.add_argument("--scenarios", default=None, type=Path)
    score_memory_parser.add_argument("--predictions", required=True, type=Path)
    score_memory_parser.add_argument("--output", required=True, type=Path)

    summary_parser = subparsers.add_parser(
        "summarize-local-benchmarks",
        help="Combine the local CharacterEval and LaMP score reports into one summary report.",
    )
    summary_parser.add_argument("--charactereval-report", required=True, type=Path)
    summary_parser.add_argument("--lamp-report", required=True, type=Path)
    summary_parser.add_argument("--memory-report", default=None, type=Path)
    summary_parser.add_argument("--output", required=True, type=Path)
    summary_parser.add_argument("--lowest-k", type=int, default=5)

    return parser


async def _main_async(args: argparse.Namespace) -> None:
    settings = AppSettings()

    if args.command == "charactereval":
        samples = load_charactereval_samples(args.input)
        predictions = await _run_with_benchmark_service(
            settings,
            lambda responder: run_charactereval_adapter(samples, responder),
        )
        save_charactereval_predictions(args.output, predictions)
        return

    if args.command == "score-charactereval-faculty":
        scenarios = load_faculty_charactereval_scenarios(args.scenarios)
        predictions = load_charactereval_predictions(args.predictions)
        report = score_faculty_charactereval_predictions(scenarios, predictions)
        save_faculty_charactereval_score_report(args.output, report)
        return

    if args.command == "lamp":
        inferred_task_name, samples = load_lamp_questions(args.questions)
        task_name = args.task_name or inferred_task_name
        if not task_name:
            raise ValueError(
                "LaMP adapter requires --task-name when the question file has no top-level task field."
            )
        envelope = await _run_with_benchmark_service(
            settings,
            lambda responder: run_lamp_adapter(samples, responder, task_name=task_name),
        )
        save_lamp_predictions(args.output, envelope)
        return

    if args.command == "memory-followup":
        scenarios = load_memory_followup_scenarios(args.scenarios)
        predictions = await _run_with_benchmark_service(
            settings,
            lambda responder: run_memory_followup_adapter(scenarios, responder),
        )
        save_memory_followup_predictions(args.output, predictions)
        return

    if args.command == "score-lamp-local":
        scenarios = load_local_lamp_scenarios(args.scenarios)
        predictions = load_lamp_prediction_envelope(args.predictions)
        report = score_local_lamp_predictions(scenarios, predictions)
        save_local_lamp_score_report(args.output, report)
        return

    if args.command == "score-memory-followup":
        scenarios = load_memory_followup_scenarios(args.scenarios)
        payload = json.loads(args.predictions.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Memory follow-up prediction file must be a JSON list.")
        predictions = [
            MemoryFollowupPrediction.model_validate(item) for item in payload
        ]
        report = score_memory_followup_predictions(scenarios, predictions)
        save_memory_followup_score_report(args.output, report)
        return

    if args.command == "summarize-local-benchmarks":
        faculty_report = load_faculty_charactereval_score_report(
            args.charactereval_report
        )
        lamp_report = load_local_lamp_score_report(args.lamp_report)
        memory_report = (
            load_memory_followup_score_report(args.memory_report)
            if args.memory_report
            else None
        )
        report = summarize_local_benchmark_reports(
            faculty_report,
            lamp_report,
            memory_report=memory_report,
            lowest_k=args.lowest_k,
        )
        save_unified_benchmark_summary(args.output, report)
        return

    raise ValueError(f"Unsupported command: {args.command}")


def _fractional_score(hit_count: int, total_count: int, max_score: int) -> int:
    if total_count == 0:
        return max_score
    return round(max_score * hit_count / total_count)


def _normalize_task_name(task_name: str) -> str:
    return task_name.replace("-", "_")


def _build_dataset_summary(
    *,
    benchmark_name: str,
    scenario_count: int,
    evaluated_count: int,
    average_score: float,
    pass_rate: float,
    focus_average_scores: dict[str, float],
    results: list[Any],
    lowest_k: int,
) -> UnifiedBenchmarkDatasetSummary:
    findings = [
        UnifiedBenchmarkFinding(
            benchmark_name=benchmark_name,
            scenario_id=result.scenario_id,
            title=result.title,
            focus=result.focus,
            passed=result.passed,
            total_score=result.total_score,
        )
        for result in results
    ]
    findings.sort(key=lambda item: (item.total_score, item.scenario_id))
    failure_count = sum(1 for item in findings if not item.passed)
    return UnifiedBenchmarkDatasetSummary(
        benchmark_name=benchmark_name,
        scenario_count=scenario_count,
        evaluated_count=evaluated_count,
        average_score=average_score,
        pass_rate=pass_rate,
        failure_count=failure_count,
        focus_average_scores=focus_average_scores,
        lowest_scenarios=findings[:lowest_k],
    )


def _length_score(text: str, min_chars: int, max_chars: int, max_score: int) -> int:
    length = len(text.strip())
    if length == 0:
        return 0
    if min_chars <= length <= max_chars:
        return max_score
    if length < min_chars and min_chars > 0:
        return round(max_score * max(0, length) / min_chars)
    if length > max_chars and max_chars > 0:
        overflow_ratio = min(1.0, (length - max_chars) / max_chars)
        return round(max_score * (1 - overflow_ratio))
    return 0


def main() -> None:
    parser = _build_cli()
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
