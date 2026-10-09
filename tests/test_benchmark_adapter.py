from __future__ import annotations

import json
from pathlib import Path

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.benchmark_adapter import (
    BenchmarkTraceDiagnostics,
    CharacterEvalPrediction,
    CharacterEvalSample,
    FacultyCharacterEvalScenario,
    FacultyCharacterEvalScoreReport,
    FacultyCharacterEvalRubric,
    FacultyCharacterEvalScenarioScore,
    LocalLampScenario,
    LocalLampRubric,
    LocalLampScoreReport,
    LocalLampScenarioScore,
    MemoryFollowupPrediction,
    MemoryFollowupScenario,
    MemoryFollowupRubric,
    MemoryFollowupScoreReport,
    MemoryFollowupScenarioScore,
    MemoryFollowupTurn,
    LampPredictionEnvelope,
    LampQuestionSample,
    UnifiedBenchmarkSummaryReport,
    build_charactereval_request,
    build_lamp_request,
    build_memory_followup_request,
    default_faculty_charactereval_subset_path,
    default_local_lamp_subset_path,
    default_memory_followup_subset_path,
    load_faculty_charactereval_scenarios,
    load_charactereval_samples,
    load_local_lamp_scenarios,
    load_memory_followup_scenarios,
    load_lamp_questions,
    run_charactereval_adapter,
    run_lamp_adapter,
    run_memory_followup_adapter,
    score_faculty_charactereval_predictions,
    score_local_lamp_predictions,
    score_memory_followup_predictions,
    summarize_local_benchmark_reports,
)
from sage_faculty_twin.models import ChatResponse
from sage_faculty_twin.service import DigitalTwinService


def test_repo_charactereval_faculty_subset_loads() -> None:
    path = default_faculty_charactereval_subset_path()
    samples = load_faculty_charactereval_scenarios(path)

    assert path.is_file()
    assert len(samples) >= 24
    assert samples[0].role == "张书豪老师"
    assert samples[0].scenario_id == "faculty-bio-greeting"
    assert samples[0].rubric.must_include_all


def test_load_charactereval_samples_from_json_list(tmp_path: Path) -> None:
    path = tmp_path / "charactereval.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "role": "张老师",
                    "context": "学生：老师好\n张老师：你好\n学生：我想问一下研究方向",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    samples = load_charactereval_samples(path)

    assert samples == [
        CharacterEvalSample(
            id=1,
            role="张老师",
            context="学生：老师好\n张老师：你好\n学生：我想问一下研究方向",
        )
    ]


def test_load_lamp_questions_supports_list_and_envelope(tmp_path: Path) -> None:
    list_path = tmp_path / "lamp-list.json"
    list_path.write_text(
        json.dumps(
            [
                {
                    "id": "a",
                    "input": "Write a title",
                    "profile": [{"id": "p1", "title": "A", "text": "B"}],
                }
            ]
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / "lamp-env.json"
    env_path.write_text(
        json.dumps(
            {
                "task": "LaMP_6",
                "golds": [{"id": "b", "input": "Write a subject", "profile": []}],
            }
        ),
        encoding="utf-8",
    )

    list_task, list_samples = load_lamp_questions(list_path)
    env_task, env_samples = load_lamp_questions(env_path)

    assert list_task is None
    assert list_samples == [
        LampQuestionSample(
            id="a",
            input="Write a title",
            profile=[{"id": "p1", "title": "A", "text": "B"}],
            output=None,
        )
    ]
    assert env_task == "LaMP_6"
    assert env_samples == [
        LampQuestionSample(id="b", input="Write a subject", profile=[], output=None)
    ]


def test_repo_local_lamp_subset_loads() -> None:
    path = default_local_lamp_subset_path()
    scenarios = load_local_lamp_scenarios(path)

    assert path.is_file()
    assert len(scenarios) >= 16
    assert scenarios[0].task_name == "LaMP-Local"
    assert scenarios[0].rubric.profile_grounding_terms


def test_repo_memory_followup_subset_loads() -> None:
    path = default_memory_followup_subset_path()
    scenarios = load_memory_followup_scenarios(path)

    assert path.is_file()
    assert len(scenarios) >= 4
    assert scenarios[0].scenario_id == "recent-topic-followup"
    assert scenarios[0].rubric.require_memory_used is True


def test_build_charactereval_request_includes_role_and_context() -> None:
    request = build_charactereval_request(
        CharacterEvalSample(
            id=7, role="张老师", context="学生：老师您好\n学生：这周 office hour 还有吗"
        )
    )

    assert request.course_context == "CharacterEval role-play benchmark"
    assert request.visitor_profile == "general_visitor"
    assert "目标角色：张老师" in request.question
    assert "学生：这周 office hour 还有吗" in request.question
    assert "请只输出目标角色的下一句回复" in request.question


def test_build_lamp_request_renders_profile_items() -> None:
    request = build_lamp_request(
        LampQuestionSample(
            id="demo",
            input="Generate a personalized reply",
            profile=[
                {
                    "id": "p1",
                    "title": "Paper",
                    "abstract": "About retrieval",
                    "date": "2026-05-01",
                }
            ],
        ),
        task_name="LaMP-5",
    )

    assert request.course_context == "LaMP personalization benchmark"
    assert "Task: LaMP-5" in request.question
    assert "学生画像：" in request.question
    assert "title: Paper" in request.question
    assert "abstract: About retrieval" in request.question
    assert "学生当前想请教老师的问题：Generate a personalized reply" in request.question


def test_build_memory_followup_request_reuses_conversation_id() -> None:
    scenario = MemoryFollowupScenario(
        scenario_id="memory-demo",
        title="记忆承接",
        focus="conversation-memory",
        student_name="Alice",
        student_email="alice@example.com",
        course_context="科研指导",
        visitor_profile="general_visitor",
        seed_turns=[MemoryFollowupTurn(question="先记录一个偏好")],
        evaluation_turn=MemoryFollowupTurn(question="再给我建议"),
        rubric=MemoryFollowupRubric(required_memory_types=["short_term"]),
    )

    request = build_memory_followup_request(
        scenario,
        scenario.evaluation_turn,
        conversation_id="memory-followup-memory-demo",
    )

    assert request.course_context == "科研指导"
    assert request.visitor_profile == "general_visitor"
    assert request.conversation_id == "memory-followup-memory-demo"
    assert request.question == "再给我建议"


async def _fake_responder(request):
    return ChatResponse(
        answer=f"reply for {request.conversation_id}",
        owner_name="张书豪",
        used_model="fake",
        conversation_id=request.conversation_id,
        workflow_action="answer",
    )


class _RecordingMemoryLLM:
    model_name = "test-model"

    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.prompts: list[str] = []

    def close(self) -> None:
        return None

    async def aclose(self) -> None:
        return None

    def classify_interaction_intent_sync(
        self, question: str, course_context: str | None = None
    ):
        from sage_faculty_twin.models import InteractionIntent

        return InteractionIntent(
            action="answer",
            domain="advising",
            retrieval_scopes=["preparation", "meeting_policy", "profile"],
            exclude_scopes=["courseware"],
            decision_mode="advise_only",
            confidence=0.95,
        )

    async def classify_interaction_intent(
        self, question: str, course_context: str | None = None
    ):
        return self.classify_interaction_intent_sync(question, course_context)

    def classify_booking_intent_sync(
        self, question: str, course_context: str | None = None
    ) -> bool:
        return False

    async def classify_booking_intent(
        self, question: str, course_context: str | None = None
    ) -> bool:
        return False

    def answer_question_sync(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append(user_prompt)
        return self._answer

    async def answer_question(self, system_prompt: str, user_prompt: str) -> str:
        return self.answer_question_sync(system_prompt, user_prompt)


def test_run_charactereval_adapter_exports_generation_records() -> None:
    predictions = __import__("asyncio").run(
        run_charactereval_adapter(
            [CharacterEvalSample(id=1, role="张老师", context="学生：老师您好")],
            _fake_responder,
        )
    )

    assert predictions == [
        CharacterEvalPrediction(
            id=1,
            role="张老师",
            context="学生：老师您好",
            model_output="reply for charactereval-1",
            conversation_id="charactereval-1",
            workflow_action="answer",
        )
    ]


def test_run_lamp_adapter_exports_prediction_envelope() -> None:
    envelope = __import__("asyncio").run(
        run_lamp_adapter(
            [
                LampQuestionSample(
                    id="x", input="Do task", profile=[{"id": "p1", "text": "demo"}]
                )
            ],
            _fake_responder,
            task_name="LaMP-6",
        )
    )

    assert envelope == LampPredictionEnvelope(
        task="LaMP_6",
        golds=[{"id": "x", "output": "reply for lamp-LaMP-6-x"}],
    )


def test_run_memory_followup_adapter_exports_memory_audit_fields() -> None:
    recorded_conversation_ids: list[str | None] = []

    async def responder(request):
        recorded_conversation_ids.append(request.conversation_id)
        is_evaluation_turn = "再给我建议" in request.question
        return ChatResponse(
            answer=f"reply for {request.question}",
            owner_name="张书豪",
            used_model="fake",
            conversation_id=request.conversation_id,
            workflow_action="advise_only",
            memory_used=is_evaluation_turn,
            memory_write_back=True,
            workflow_trace=[
                {
                    "key": "memory_retrieve",
                    "title": "对话记忆检索",
                    "summary": "诊断测试",
                    "detail": "诊断测试",
                    "status": "completed",
                    "duration_ms": 2 if not is_evaluation_turn else 5,
                },
                {
                    "key": "prompt_build",
                    "title": "构造回答上下文",
                    "summary": "诊断测试",
                    "detail": "诊断测试",
                    "status": "completed",
                    "duration_ms": 1 if not is_evaluation_turn else 3,
                },
                {
                    "key": "llm_answer",
                    "title": "生成回答",
                    "summary": "诊断测试",
                    "detail": "诊断测试",
                    "status": "completed",
                    "duration_ms": 11 if not is_evaluation_turn else 33,
                },
            ],
            retrieved_items=[]
            if not is_evaluation_turn
            else [
                {
                    "entry_id": "recent-1",
                    "memory_type": "short_term",
                    "source": "conversation",
                    "topic": "research_advising",
                    "source_label": "近期交流记录",
                    "summary": "之前提到了推理引擎与服务系统。",
                }
            ],
        )

    predictions = __import__("asyncio").run(
        run_memory_followup_adapter(
            [
                MemoryFollowupScenario(
                    scenario_id="memory-demo",
                    title="记忆承接",
                    focus="conversation-memory",
                    seed_turns=[MemoryFollowupTurn(question="先记录一个偏好")],
                    evaluation_turn=MemoryFollowupTurn(question="再给我建议"),
                    rubric=MemoryFollowupRubric(required_memory_types=["short_term"]),
                )
            ],
            responder,
        )
    )

    assert recorded_conversation_ids == [
        "memory-followup-memory-demo",
        "memory-followup-memory-demo",
    ]
    assert predictions == [
        MemoryFollowupPrediction(
            scenario_id="memory-demo",
            title="记忆承接",
            focus="conversation-memory",
            conversation_id="memory-followup-memory-demo",
            model_output="reply for 再给我建议",
            workflow_action="advise_only",
            memory_used=True,
            memory_write_back=True,
            retrieved_item_count=1,
            retrieved_memory_types=["short_term"],
            scenario_duration_ms=55,
            evaluation_diagnostics=BenchmarkTraceDiagnostics(
                workflow_duration_ms=41,
                llm_answer_duration_ms=33,
                step_durations_ms={
                    "memory_retrieve": 5,
                    "prompt_build": 3,
                    "llm_answer": 33,
                },
                step_statuses={
                    "memory_retrieve": "completed",
                    "prompt_build": "completed",
                    "llm_answer": "completed",
                },
            ),
            seed_turn_diagnostics=[
                BenchmarkTraceDiagnostics(
                    workflow_duration_ms=14,
                    llm_answer_duration_ms=11,
                    step_durations_ms={
                        "memory_retrieve": 2,
                        "prompt_build": 1,
                        "llm_answer": 11,
                    },
                    step_statuses={
                        "memory_retrieve": "completed",
                        "prompt_build": "completed",
                        "llm_answer": "completed",
                    },
                )
            ],
        )
    ]


def test_run_memory_followup_adapter_with_service_reuses_memory(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)
    llm = _RecordingMemoryLLM(
        answer="建议你先围绕推理引擎与推理服务系统的取舍收窄问题。"
    )
    service._llm_client = llm
    scenario = MemoryFollowupScenario(
        scenario_id="memory-live",
        title="真实服务记忆复用",
        focus="conversation-memory",
        student_name="Alice",
        student_email="alice@example.com",
        course_context="科研指导",
        visitor_profile="general_visitor",
        seed_turns=[
            MemoryFollowupTurn(question="我在推理引擎和推理服务系统之间有点摇摆。")
        ],
        evaluation_turn=MemoryFollowupTurn(question="再根据刚才的上下文给我一个建议。"),
        rubric=MemoryFollowupRubric(required_memory_types=["short_term"]),
    )

    try:
        predictions = __import__("asyncio").run(
            run_memory_followup_adapter([scenario], service.answer_in_process)
        )
    finally:
        __import__("asyncio").run(service.aclose())

    assert len(predictions) == 1
    prediction = predictions[0]
    assert prediction.memory_used is True
    assert prediction.memory_write_back is True
    # Immediate same-conversation context is intentionally kept as an
    # internal prompt aid and is not surfaced as a duplicate Support item.
    assert prediction.retrieved_item_count == 0
    assert prediction.retrieved_memory_types == []
    assert prediction.scenario_duration_ms is not None
    assert prediction.evaluation_diagnostics is not None
    assert prediction.evaluation_diagnostics.workflow_duration_ms is not None
    assert prediction.evaluation_diagnostics.step_statuses["llm_answer"] == "completed"
    assert len(llm.prompts) == 2
    assert "Immediate session context (same conversation):" in llm.prompts[-1]


def test_score_faculty_charactereval_predictions_reports_pass_and_fail() -> None:
    scenarios = [
        FacultyCharacterEvalScenario(
            id="faculty-001",
            scenario_id="bio",
            title="自我介绍",
            role="张书豪老师",
            context="学生：老师您好，您主要做什么方向？",
            focus="persona-grounding",
            expected_traits=["研究方向聚焦"],
            rubric=FacultyCharacterEvalRubric(
                must_include_all=["大模型", "系统"],
                must_include_any=["推理", "智能体"],
                must_not_include=["office hour"],
                preferred_keywords=["主要", "聚焦"],
                min_chars=20,
                max_chars=120,
                pass_threshold=70,
            ),
        ),
        FacultyCharacterEvalScenario(
            id="faculty-002",
            scenario_id="policy",
            title="沟通边界",
            role="张书豪老师",
            context="学生：老师，什么问题适合邮件先整理？",
            focus="meeting-policy",
            expected_traits=["区分异步与线下"],
            rubric=FacultyCharacterEvalRubric(
                must_include_all=["邮件"],
                must_include_any=["线下", "当面"],
                must_not_include=["周一下午两点"],
                preferred_keywords=["建议"],
                min_chars=12,
                max_chars=120,
                pass_threshold=70,
            ),
        ),
    ]
    predictions = [
        CharacterEvalPrediction(
            id="faculty-001",
            role="张书豪老师",
            context="学生：老师您好，您主要做什么方向？",
            model_output="我目前主要聚焦大模型系统，尤其关注推理引擎和智能体基础设施。",
        ),
        CharacterEvalPrediction(
            id="faculty-002",
            role="张书豪老师",
            context="学生：老师，什么问题适合邮件先整理？",
            model_output="建议你先邮件整理。我们周一下午两点见。",
        ),
    ]

    report = score_faculty_charactereval_predictions(scenarios, predictions)

    assert isinstance(report, FacultyCharacterEvalScoreReport)
    assert report.scenario_count == 2
    assert report.evaluated_count == 2
    assert report.average_score == 80.0
    assert report.pass_rate == 0.5
    assert report.focus_average_scores == {
        "meeting-policy": 60.0,
        "persona-grounding": 100.0,
    }

    first, second = report.results

    assert first.scenario_id == "bio"
    assert first.passed is True
    assert first.total_score == 100
    assert first.matched_all == ["大模型", "系统"]
    assert first.matched_any == ["推理", "智能体"]
    assert first.preferred_hits == ["主要", "聚焦"]
    assert first.forbidden_hits == []
    assert "大模型系统" in first.output_excerpt

    assert second.scenario_id == "policy"
    assert second.passed is False
    assert second.total_score == 60
    assert second.matched_all == ["邮件"]
    assert second.matched_any == []
    assert second.missing_any == ["线下", "当面"]
    assert second.preferred_hits == ["建议"]
    assert second.forbidden_hits == ["周一下午两点"]
    assert second.output_excerpt == "建议你先邮件整理。我们周一下午两点见。"


def test_score_local_lamp_predictions_reports_profile_grounding() -> None:
    scenarios = [
        LocalLampScenario(
            id="lamp-001",
            scenario_id="profile-reading-plan",
            title="按学生背景给阅读计划",
            task_name="LaMP-Local",
            focus="profile-grounding",
            input="基于我的背景，接下来两周先学什么更合适？",
            profile=[
                {
                    "id": "p1",
                    "identity": "华科计院本科生",
                    "interest": "大模型推理系统",
                    "constraint": "每周只有6小时",
                }
            ],
            expected_traits=["要体现时间约束和兴趣方向"],
            rubric=LocalLampRubric(
                must_include_all=["建议"],
                must_include_any=["两周", "先学"],
                must_not_include=["每天十小时"],
                preferred_keywords=["可以"],
                profile_grounding_terms=["推理系统", "6小时"],
                min_chars=16,
                max_chars=160,
                pass_threshold=70,
            ),
        ),
        LocalLampScenario(
            id="lamp-002",
            scenario_id="profile-writing-help",
            title="按论文写作背景给下一步建议",
            task_name="LaMP-Local",
            focus="writing-personalization",
            input="结合我的情况，下一步最值得补哪一块？",
            profile=[
                {
                    "id": "p1",
                    "course": "论文写作",
                    "weakness": "related work 松散",
                    "deadline": "两周后交摘要",
                }
            ],
            expected_traits=["要连到 related work 或摘要"],
            rubric=LocalLampRubric(
                must_include_all=["建议"],
                must_include_any=["related work", "摘要"],
                must_not_include=["推理系统"],
                preferred_keywords=["先"],
                profile_grounding_terms=["related work", "两周"],
                min_chars=16,
                max_chars=160,
                pass_threshold=70,
            ),
        ),
    ]
    predictions = LampPredictionEnvelope(
        task="LaMP_Local",
        golds=[
            {
                "id": "lamp-001",
                "output": "建议你这两周先学推理系统里的基础路径，每周6小时先把主线建立起来。",
            },
            {"id": "lamp-002", "output": "建议你先看推理系统，不用管 related work。"},
        ],
    )

    report = score_local_lamp_predictions(scenarios, predictions)

    assert isinstance(report, LocalLampScoreReport)
    assert report.task_name == "LaMP_Local"
    assert report.scenario_count == 2
    assert report.evaluated_count == 2
    assert report.average_score == 77.5
    assert report.pass_rate == 0.5
    assert report.focus_average_scores == {
        "profile-grounding": 90.0,
        "writing-personalization": 65.0,
    }

    first, second = report.results

    assert first.scenario_id == "profile-reading-plan"
    assert first.passed is True
    assert first.total_score == 90
    assert first.matched_any == ["两周", "先学"]
    assert first.preferred_hits == []
    assert first.profile_hits == ["推理系统", "6小时"]
    assert first.forbidden_hits == []

    assert second.scenario_id == "profile-writing-help"
    assert second.passed is False
    assert second.total_score == 65
    assert second.matched_any == ["related work"]
    assert second.profile_hits == ["related work"]
    assert second.missing_profile_terms == ["两周"]
    assert second.forbidden_hits == ["推理系统"]


def test_score_memory_followup_predictions_reports_memory_usage() -> None:
    scenarios = [
        MemoryFollowupScenario(
            scenario_id="memory-pass",
            title="承接上轮话题",
            focus="conversation-memory",
            seed_turns=[
                MemoryFollowupTurn(question="我想讨论推理引擎和服务系统的取舍")
            ],
            evaluation_turn=MemoryFollowupTurn(
                question="再根据刚才的上下文给我一个建议"
            ),
            rubric=MemoryFollowupRubric(
                must_include_all=["建议"],
                must_include_any=["推理引擎", "服务系统"],
                preferred_keywords=["先"],
                memory_grounding_terms=["推理引擎", "服务系统"],
                required_memory_types=["short_term"],
                require_memory_used=True,
                min_retrieved_items=1,
                min_chars=10,
                max_chars=180,
                pass_threshold=70,
            ),
        ),
        MemoryFollowupScenario(
            scenario_id="memory-fail",
            title="没有承接记忆",
            focus="profile-memory",
            seed_turns=[MemoryFollowupTurn(question="请记住我想聊论文进展")],
            evaluation_turn=MemoryFollowupTurn(
                question="根据你记得的我的预约习惯提醒我准备什么"
            ),
            rubric=MemoryFollowupRubric(
                must_include_all=["准备"],
                must_include_any=["论文进展"],
                memory_grounding_terms=["论文进展"],
                required_memory_types=["long_term"],
                require_memory_used=True,
                min_retrieved_items=1,
                min_chars=10,
                max_chars=180,
                pass_threshold=70,
            ),
        ),
    ]
    predictions = [
        MemoryFollowupPrediction(
            scenario_id="memory-pass",
            title="承接上轮话题",
            focus="conversation-memory",
            conversation_id="memory-followup-memory-pass",
            model_output="建议你先把推理引擎和服务系统分别列出评价指标，再收窄。",
            workflow_action="advise_only",
            memory_used=True,
            memory_write_back=True,
            retrieved_item_count=2,
            retrieved_memory_types=["short_term"],
        ),
        MemoryFollowupPrediction(
            scenario_id="memory-fail",
            title="没有承接记忆",
            focus="profile-memory",
            conversation_id="memory-followup-memory-fail",
            model_output="准备几个问题再来。",
            workflow_action="advise_only",
            memory_used=False,
            memory_write_back=True,
            retrieved_item_count=0,
            retrieved_memory_types=[],
        ),
    ]

    report = score_memory_followup_predictions(scenarios, predictions)

    assert isinstance(report, MemoryFollowupScoreReport)
    assert report.scenario_count == 2
    assert report.evaluated_count == 2
    assert report.average_score == 69.5
    assert report.pass_rate == 0.5
    assert report.focus_average_scores == {
        "conversation-memory": 100.0,
        "profile-memory": 39.0,
    }

    first, second = report.results

    assert first.scenario_id == "memory-pass"
    assert first.passed is True
    assert first.total_score == 100
    assert first.memory_used is True
    assert first.retrieved_item_count == 2
    assert first.retrieved_memory_types == ["short_term"]
    assert first.missing_memory_types == []
    assert first.memory_grounding_hits == ["推理引擎", "服务系统"]

    assert second.scenario_id == "memory-fail"
    assert second.passed is False
    assert second.total_score == 39
    assert second.memory_used is False
    assert second.retrieved_item_count == 0
    assert second.missing_memory_types == ["long_term"]
    assert second.missing_memory_grounding_terms == ["论文进展"]


def test_summarize_local_benchmark_reports_combines_overview() -> None:
    faculty_report = FacultyCharacterEvalScoreReport(
        scenario_count=3,
        evaluated_count=3,
        average_score=70.0,
        pass_rate=0.3333,
        focus_average_scores={"persona-grounding": 80.0, "meeting-policy": 55.0},
        results=[
            FacultyCharacterEvalScenarioScore(
                scenario_id="faculty-a",
                title="A",
                focus="persona-grounding",
                prediction_id="a",
                passed=True,
                total_score=90,
                output_length=10,
            ),
            FacultyCharacterEvalScenarioScore(
                scenario_id="faculty-b",
                title="B",
                focus="meeting-policy",
                prediction_id="b",
                passed=False,
                total_score=55,
                output_length=10,
            ),
            FacultyCharacterEvalScenarioScore(
                scenario_id="faculty-c",
                title="C",
                focus="meeting-policy",
                prediction_id="c",
                passed=False,
                total_score=65,
                output_length=10,
            ),
        ],
    )
    lamp_report = LocalLampScoreReport(
        task_name="LaMP_Local",
        scenario_count=2,
        evaluated_count=2,
        average_score=80.0,
        pass_rate=0.5,
        focus_average_scores={"profile-grounding": 80.0},
        results=[
            LocalLampScenarioScore(
                scenario_id="lamp-a",
                title="L1",
                task_name="LaMP-Local",
                focus="profile-grounding",
                prediction_id="l1",
                passed=True,
                total_score=95,
                output_length=10,
            ),
            LocalLampScenarioScore(
                scenario_id="lamp-b",
                title="L2",
                task_name="LaMP-Local",
                focus="profile-grounding",
                prediction_id="l2",
                passed=False,
                total_score=65,
                output_length=10,
            ),
        ],
    )
    memory_report = MemoryFollowupScoreReport(
        scenario_count=2,
        evaluated_count=2,
        average_score=85.0,
        pass_rate=1.0,
        focus_average_scores={"conversation-memory": 85.0},
        results=[
            MemoryFollowupScenarioScore(
                scenario_id="memory-a",
                title="M1",
                focus="conversation-memory",
                passed=True,
                total_score=80,
                memory_used=True,
                memory_write_back=True,
                retrieved_item_count=1,
                retrieved_memory_types=["short_term"],
                output_length=10,
            ),
            MemoryFollowupScenarioScore(
                scenario_id="memory-b",
                title="M2",
                focus="conversation-memory",
                passed=True,
                total_score=90,
                memory_used=True,
                memory_write_back=True,
                retrieved_item_count=2,
                retrieved_memory_types=["short_term", "long_term"],
                output_length=10,
            ),
        ],
    )

    summary = summarize_local_benchmark_reports(
        faculty_report, lamp_report, memory_report=memory_report, lowest_k=3
    )

    assert isinstance(summary, UnifiedBenchmarkSummaryReport)
    assert summary.benchmark_count == 3
    assert summary.evaluated_count == 7
    assert summary.overall_average_score == 77.14
    assert summary.overall_pass_rate == 0.5714
    assert [item.benchmark_name for item in summary.benchmark_summaries] == [
        "faculty-charactereval",
        "local-lamp",
        "memory-followup",
    ]
    assert summary.benchmark_summaries[0].failure_count == 2
    assert summary.benchmark_summaries[1].failure_count == 1
    assert summary.benchmark_summaries[2].failure_count == 0
    assert [
        (item.benchmark_name, item.scenario_id, item.total_score)
        for item in summary.weakest_scenarios
    ] == [
        ("faculty-charactereval", "faculty-b", 55),
        ("faculty-charactereval", "faculty-c", 65),
        ("local-lamp", "lamp-b", 65),
    ]
