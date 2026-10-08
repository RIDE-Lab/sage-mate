import asyncio
from datetime import datetime
from pathlib import Path

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, InteractionIntent, KnowledgeSearchHit
from sage_faculty_twin.persona import build_system_prompt
from sage_faculty_twin.service import DigitalTwinService, FacultyTwinWorkflowSupport
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

    write_response = asyncio.run(
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
    assert write_response.answer == "已记住。"
    assert llm.prompts == []
    assert not write_response.answer_basis
    profile_step = next(
        step
        for step in write_response.workflow_trace
        if step.key == "memory_profile_consolidate"
    )
    assert profile_step.status == "skipped"


def test_same_conversation_recalls_short_remembered_value_query_without_llm(
    tmp_path: Path,
) -> None:
    service = DigitalTwinService(_settings(tmp_path))
    llm = _RecordingLLM(answer="不应调用模型")
    service._llm_client = llm

    write_response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                conversation_id="conv-short-value-recall",
                question="请记住这个临时代号：海燕-908。",
            )
        )
    )
    read_response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                conversation_id="conv-short-value-recall",
                question="刚才的临时代号是什么？",
            )
        )
    )

    assert write_response.answer == "已记住。"
    assert read_response.answer == "你刚才让我记住的是：海燕-908"
    assert llm.prompts == []
    assert not write_response.answer_basis
    assert not read_response.answer_basis


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
    persist_step = next(
        step for step in response.workflow_trace if step.key == "memory_persist"
    )
    assert persist_step.status == "skipped"
    assert response.memory_write_back is False




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


def test_award_search_rewrites_entities_and_drops_irrelevant_results() -> None:
    query = "张书豪老师是否获得了 2026 年图灵奖？请核实并说明依据。"
    assert WebSearchClient._rewrite_query_for_bing(query) == '"张书豪" "图灵奖" 2026'

    results = WebSearchClient._rerank_results(
        query,
        [
            WebSearchResult(
                title="张（汉语汉字）_百度百科",
                url="https://baike.baidu.com/item/%E5%BC%A0/31793",
                snippet="张是常见汉字。",
                score=3.0,
            ),
            WebSearchResult(
                title="ACM A.M. Turing Award",
                url="https://awards.acm.org/turing",
                snippet="Official Turing Award information and recipients.",
                score=2.0,
            ),
        ],
        3,
    )

    assert [result.url for result in results] == ["https://awards.acm.org/turing"]


def test_award_search_prefers_official_award_directory() -> None:
    client = WebSearchClient(timeout_seconds=1, max_results=3)
    client._search_official_award_page = lambda title, url: WebSearchResult(
        title=title,
        url=url,
        snippet="Official award recipient directory.",
        score=100.0,
    )
    client._search_bing_rss = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("Bing must not run when the official award directory succeeds")
    )

    results = client.search("张书豪老师是否获得了 2026 年图灵奖？")

    assert len(results) == 1
    assert results[0].url == "https://amturing.acm.org/?pg=awards.html"


def test_official_award_directory_keeps_restricted_official_endpoint() -> None:
    client = WebSearchClient(timeout_seconds=1, max_results=3)

    class _RestrictedResponse:
        status_code = 403

        def raise_for_status(self) -> None:
            raise AssertionError("403 official endpoint should remain usable as a citation")

    class _RestrictedClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url):
            assert url == "https://amturing.acm.org/?pg=awards.html"
            return _RestrictedResponse()

    client._client = lambda: _RestrictedClient()

    result = client._search_official_award_page(
        "ACM A.M. Turing Award official winners",
        "https://amturing.acm.org/?pg=awards.html",
    )

    assert result is not None
    assert "restricted" in result.snippet


def test_award_question_filters_unrelated_local_knowledge_hits() -> None:
    relevant = KnowledgeSearchHit(
        document_id="profile-awards",
        title="公开资料精选 · 张书豪公开个人简介、奖励与学术服务",
        excerpt="公开奖励记录。",
        score=96.0,
        tags=["audience:public"],
        source_name="public-profile:bio-awards-service",
    )
    unrelated = KnowledgeSearchHit(
        document_id="inference-survey",
        title="国产推理引擎综述",
        excerpt="讨论调度、缓存与网络控制面。",
        score=80.0,
        tags=["audience:public"],
        source_name="survey.pdf",
    )

    assert [
        hit.document_id
        for hit in FacultyTwinWorkflowSupport._filter_knowledge_hits_for_question(
            "张书豪老师是否获得了 2026 年图灵奖？",
            [unrelated, relevant],
        )
    ] == ["profile-awards"]


def test_owner_award_fact_check_is_date_grounded_and_conservative(tmp_path: Path) -> None:
    service = DigitalTwinService(_settings(tmp_path, booking_timezone="Asia/Shanghai"))
    llm = _RecordingLLM(answer="截至当前（2024 年），候选人资格不符。")
    service._llm_client = llm

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Verifier",
                conversation_id="conv-owner-award-check",
                question="张书豪老师是否获得了 2026 年图灵奖？请核实并说明依据。",
                web_search=True,
            )
        )
    )

    assert str(datetime.now().astimezone().year) in response.answer
    assert "没有可靠证据支持该说法" in response.answer
    assert "资格不符" not in response.answer
    assert llm.prompts == []


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
