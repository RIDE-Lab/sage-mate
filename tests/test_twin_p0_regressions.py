import asyncio
from datetime import datetime
from pathlib import Path

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, InteractionIntent
from sage_faculty_twin.persona import build_system_prompt
from sage_faculty_twin.service import DigitalTwinService
from sage_faculty_twin.skills import SkillResult
from sage_faculty_twin.web_search import WebSearchClient, WebSearchResult


class _RecordingLLM:
    model_name = "test-model"

    def __init__(self, answer: str = "标准管线回答") -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        return InteractionIntent(
            action="answer",
            domain="research",
            retrieval_scopes=["publications", "profile"],
            decision_mode="direct_answer",
            confidence=0.95,
        )

    async def classify_interaction_intent(self, *args, **kwargs) -> InteractionIntent:
        return self.classify_interaction_intent_sync(*args, **kwargs)

    def classify_booking_intent_sync(self, *args, **kwargs) -> bool:
        return False

    async def classify_booking_intent(self, *args, **kwargs) -> bool:
        return False

    def answer_question_sync(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append(user_prompt)
        return self.answer

    async def answer_question(self, system_prompt: str, user_prompt: str) -> str:
        return self.answer_question_sync(system_prompt, user_prompt)


class _ReviewBiasedLLM(_RecordingLLM):
    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        return InteractionIntent(
            action="review_queue",
            domain="advising",
            decision_mode="review_queue",
            escalation_reason="模型误判为需要人工审核",
            confidence=0.95,
        )


def _settings(tmp_path: Path, **overrides) -> AppSettings:
    defaults = {
        "knowledge_base_dir": tmp_path / "knowledge",
        "knowledge_backend": "local",
        "conversation_memory_dir": tmp_path / "conversation-memory",
        "escalation_queue_dir": tmp_path / "escalations",
        "planner_comparison_dir": tmp_path / "planner-comparisons",
    }
    defaults.update(overrides)
    return AppSettings(**defaults)


def test_legacy_skill_match_does_not_bypass_grounded_pipeline_by_default(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "research_mentoring.json").write_text(
        """{
          "skill_id": "research_mentoring",
          "name": "Research mentoring",
          "trigger_patterns": ["研究方向"],
          "system_prompt": "Legacy shortcut",
          "user_prompt_template": "{question}",
          "tools": [],
          "max_turns": 1,
          "output_format": "free_form",
          "composes_with": [],
          "enabled": true,
          "min_app_version": "4.0.0"
        }""",
        encoding="utf-8",
    )
    service = DigitalTwinService(_settings(tmp_path, skill_dir=skill_dir))
    llm = _RecordingLLM(answer="来自标准检索与记忆管线的回答")
    service._llm_client = llm
    service._skill_runner.run = lambda *args, **kwargs: SkillResult(
        skill_id="research_mentoring",
        answer="旧技能短路回答",
        success=True,
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                conversation_id="conv-skill-grounding",
                question="请介绍一下你的研究方向。",
            )
        )
    )

    assert response.answer == "来自标准检索与记忆管线的回答"
    assert response.workflow_action != "skill_answer"
    assert response.workflow_trace


def test_grounded_attachment_question_cannot_be_overridden_into_review_queue(
    tmp_path: Path,
) -> None:
    service = DigitalTwinService(_settings(tmp_path))
    service._llm_client = _ReviewBiasedLLM(
        answer="第一点是缩小实验范围；风险是样本不足。"
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                conversation_id="conv-attachment-safe",
                question="请根据附件总结第一项计划和主要风险。",
                attachments=[
                    {
                        "file_name": "plan.txt",
                        "media_type": "text/plain",
                        "text_content": "第一项计划：缩小实验范围。主要风险：样本不足。",
                    }
                ],
            )
        )
    )

    assert response.workflow_action == "answer"
    assert response.escalation_record is None
    assert response.answer == "第一点是缩小实验范围；风险是样本不足。"
    assert any(item.basis_label == "上传材料" for item in response.answer_basis)


def test_same_conversation_recalls_explicit_non_secret_value_without_llm(
    tmp_path: Path,
) -> None:
    service = DigitalTwinService(_settings(tmp_path))
    llm = _RecordingLLM(answer="已记住。")
    service._llm_client = llm

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                conversation_id="conv-value-recall",
                question="记住这个临时代号：蓝鲸-714。然后只回复已记住。",
            )
        )
    )
    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                conversation_id="conv-value-recall",
                question="我刚才让你记住的临时代号是什么？只回复代号。",
            )
        )
    )

    assert response.answer == "蓝鲸-714"
    assert len(llm.prompts) == 1


def test_system_prompt_contains_current_local_date(tmp_path: Path) -> None:
    settings = _settings(tmp_path, booking_timezone="Asia/Shanghai")
    prompt = build_system_prompt(settings)
    expected = datetime.now().astimezone().date().isoformat()

    assert expected in prompt
    assert "Current date:" in prompt

def test_current_year_question_uses_authoritative_local_date_without_llm(
    tmp_path: Path,
) -> None:
    service = DigitalTwinService(_settings(tmp_path, booking_timezone="Asia/Shanghai"))
    llm = _RecordingLLM(answer="2024")
    service._llm_client = llm

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                conversation_id="conv-current-year",
                question="当前是哪一年？只回复四位年份。",
            )
        )
    )

    assert response.answer == str(datetime.now().astimezone().year)
    assert llm.prompts == []
    interaction_step = next(
        step for step in response.workflow_trace if step.key == "interaction_understand"
    )
    assert interaction_step.summary == "已直接读取系统当前日期。"
    usefulness_step = next(
        step for step in response.workflow_trace if step.key == "memory_usefulness_score"
    )
    assert usefulness_step.status == "skipped"
    assert "无需评估记忆证据" in usefulness_step.summary




def test_latest_vllm_release_uses_official_repository_detection() -> None:
    assert WebSearchClient._known_official_release_repo(
        "请联网查询 vLLM 最新 release"
    ) == ("vllm-project", "vllm")
    assert WebSearchClient._known_official_release_repo("vLLM 的调度器怎么工作") is None


def test_latest_vllm_release_search_prefers_official_result() -> None:
    client = WebSearchClient(timeout_seconds=1, max_results=3)
    client._search_github_latest_release = lambda owner, repository: WebSearchResult(
        title="vLLM v1.2.3",
        url="https://github.com/vllm-project/vllm/releases/tag/v1.2.3",
        snippet="Official GitHub release tag: v1.2.3.",
        score=100.0,
    )
    client._search_bing_rss = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("Bing must not run when the official release lookup succeeds")
    )

    results = client.search("请联网查询 vLLM 最新 release")

    assert len(results) == 1
    assert results[0].url.endswith("/v1.2.3")


def test_requested_web_search_without_hits_is_explicit_in_prompt(
    tmp_path: Path,
) -> None:
    service = DigitalTwinService(_settings(tmp_path))
    request = ChatRequest(
        student_name="Alice",
        question="请联网查询 vLLM 最新 release",
        web_search=True,
    )

    prompt = service._build_student_prompt(request, [], web_search_hits=[])

    assert "联网检索已经执行" in prompt
    assert "不要再提示用户开启联网检索" in prompt
