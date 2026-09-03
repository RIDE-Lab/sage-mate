import json
import threading
from collections import OrderedDict
from contextlib import nullcontext
from pathlib import Path

import httpx
import pytest

from sage_faculty_twin.benchmark_adapter import (
    build_lamp_request,
    load_local_lamp_scenarios,
)
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.llm_client import IncompleteCompletionError, VllmChatClient
from sage_faculty_twin.models import InteractionIntent


def test_normalize_interaction_intent_for_explicit_teaching_question() -> None:
    client = object.__new__(VllmChatClient)
    raw_intent = InteractionIntent(
        action="answer",
        domain="research",
        retrieval_scopes=["publications"],
        exclude_scopes=["courseware"],
        confidence=0.4,
    )

    normalized = client._normalize_interaction_intent(
        "第 7 讲 发表高水平论文讲什么？",
        None,
        raw_intent,
    )

    assert normalized.action == "answer"
    assert normalized.domain == "teaching"
    assert normalized.retrieval_scopes == ["courseware"]
    assert normalized.exclude_scopes == ["publications"]


def test_normalize_interaction_intent_for_course_question_logistics() -> None:
    client = object.__new__(VllmChatClient)
    raw_intent = InteractionIntent(
        action="ask_followup",
        domain="general",
        retrieval_scopes=[],
        exclude_scopes=[],
        needs_clarification=True,
        clarification_message="你是想问课程内容还是沟通方式？",
        confidence=0.4,
    )

    normalized = client._normalize_interaction_intent(
        "结合我的情况，我下次问课上问题时应该先整理哪几类信息？作业里端到端性能分析不清楚，而且问题比较碎。",
        "LaMP personalization benchmark",
        raw_intent,
    )

    assert normalized.action == "answer"
    assert normalized.domain == "teaching"
    assert normalized.needs_clarification is False
    assert normalized.retrieval_scopes == ["courseware"]
    assert normalized.exclude_scopes == ["publications"]


def test_normalize_interaction_intent_for_explicit_booking_request() -> None:
    client = object.__new__(VllmChatClient)
    raw_intent = InteractionIntent(
        action="answer",
        domain="research",
        retrieval_scopes=["publications", "profile"],
        exclude_scopes=["courseware"],
        confidence=0.5,
    )

    normalized = client._normalize_interaction_intent(
        "请帮我预约 2026-05-26 15:00 讨论论文进展",
        "科研指导",
        raw_intent,
    )

    assert normalized.action == "book_meeting"
    assert normalized.domain == "booking"
    assert normalized.retrieval_scopes == ["meeting_policy"]
    assert normalized.exclude_scopes == ["courseware", "publications"]


def test_normalize_interaction_intent_for_preparation_question() -> None:
    client = object.__new__(VllmChatClient)
    raw_intent = InteractionIntent(
        action="ask_followup",
        domain="advising",
        retrieval_scopes=["preparation"],
        exclude_scopes=["courseware"],
        needs_clarification=True,
        clarification_message="你是想问研究方向还是预约流程？",
        confidence=0.6,
    )

    normalized = client._normalize_interaction_intent(
        "和老师约时间前，我应该先准备什么？",
        "科研指导",
        raw_intent,
    )

    assert normalized.action == "answer"
    assert normalized.domain == "advising"
    assert normalized.needs_clarification is False
    assert normalized.retrieval_scopes == ["preparation", "meeting_policy", "profile"]


def test_normalize_interaction_intent_for_office_hour_information_query() -> None:
    client = object.__new__(VllmChatClient)
    raw_intent = InteractionIntent(
        action="book_meeting",
        domain="booking",
        retrieval_scopes=["meeting_policy"],
        exclude_scopes=["courseware", "publications"],
        decision_mode="review_queue",
        confidence=0.7,
    )

    normalized = client._normalize_interaction_intent(
        "能否告诉我张老师这周的 office hours？我想了解以便预约。",
        "科研指导",
        raw_intent,
    )

    assert normalized.action == "answer"
    assert normalized.domain == "advising"
    assert normalized.decision_mode == "direct_answer"
    assert normalized.retrieval_scopes == ["meeting_policy", "profile"]
    assert normalized.exclude_scopes == ["courseware"]


def test_joining_preparation_question_is_advising_not_review_queue() -> None:
    client = object.__new__(VllmChatClient)
    raw_intent = InteractionIntent(
        action="review_queue",
        domain="advising",
        retrieval_scopes=["meeting_policy", "profile"],
        exclude_scopes=["courseware"],
        decision_mode="review_queue",
        confidence=0.7,
    )

    normalized = client._normalize_interaction_intent(
        "张老师您好，请用两三句话介绍一下您目前主要研究什么，并说明学生如果想加入课题组应该提前准备什么？",
        "科研指导",
        raw_intent,
    )

    assert normalized.action == "answer"
    assert normalized.domain == "advising"
    assert normalized.decision_mode == "advise_only"
    assert normalized.retrieval_scopes == ["preparation", "meeting_policy", "profile"]


@pytest.mark.parametrize(
    "question",
    (
        "张老师，我想加入课题组，可以吗？",
        "老师能收我吗？",
        "能不能批准我延期？",
    ),
)
def test_faculty_commitment_requests_still_require_review(question: str) -> None:
    client = object.__new__(VllmChatClient)
    normalized = client._normalize_interaction_intent(
        question,
        "科研指导",
        InteractionIntent(action="answer", domain="advising", confidence=0.7),
    )

    assert normalized.action == "review_queue"
    assert normalized.decision_mode == "review_queue"


def test_normalize_interaction_intent_for_project_scoping_guidance() -> None:
    client = object.__new__(VllmChatClient)
    raw_intent = InteractionIntent(
        action="ask_followup",
        domain="general",
        retrieval_scopes=[],
        exclude_scopes=[],
        needs_clarification=True,
        clarification_message="请先说明是哪一类项目。",
        confidence=0.45,
    )

    normalized = client._normalize_interaction_intent(
        "老师，我现在想做一个跟推理系统有关的项目。如果题目太大，您一般会建议怎么收窄？",
        "科研指导",
        raw_intent,
    )

    assert normalized.action == "answer"
    assert normalized.domain == "advising"
    assert normalized.decision_mode == "advise_only"
    assert normalized.needs_clarification is False


def test_normalize_interaction_intent_for_draft_preparation_question() -> None:
    client = object.__new__(VllmChatClient)
    raw_intent = InteractionIntent(
        action="book_meeting",
        domain="booking",
        retrieval_scopes=["meeting_policy"],
        exclude_scopes=["courseware", "publications"],
        decision_mode="review_queue",
        confidence=0.55,
    )

    normalized = client._normalize_interaction_intent(
        "老师，我下周想带一个初稿来请您看。在您看之前，我自己最好先整理哪些信息，能让反馈更集中？",
        "科研指导",
        raw_intent,
    )

    assert normalized.action == "answer"
    assert normalized.domain == "advising"
    assert normalized.decision_mode == "advise_only"
    assert normalized.needs_clarification is False


def test_normalize_interaction_intent_for_email_vs_meeting_boundary_question() -> None:
    client = object.__new__(VllmChatClient)
    raw_intent = InteractionIntent(
        action="book_meeting",
        domain="booking",
        retrieval_scopes=["meeting_policy"],
        exclude_scopes=["courseware", "publications"],
        confidence=0.5,
    )

    normalized = client._normalize_interaction_intent(
        "老师，如果只是两个比较具体的小问题，您会更希望我直接发邮件，还是等有更多内容再约时间？",
        "科研指导",
        raw_intent,
    )

    assert normalized.action == "answer"
    assert normalized.domain == "advising"
    assert normalized.decision_mode == "advise_only"
    assert normalized.needs_clarification is False


def test_normalize_interaction_intent_for_mixed_course_research_boundary_question() -> None:
    client = object.__new__(VllmChatClient)
    raw_intent = InteractionIntent(
        action="ask_followup",
        domain="general",
        retrieval_scopes=[],
        exclude_scopes=[],
        needs_clarification=True,
        clarification_message="你是想问课程还是研究？",
        confidence=0.4,
    )

    normalized = client._normalize_interaction_intent(
        "老师，我既想问课程作业，也想顺便聊下研究。这种情况您一般建议一次都问完，还是分开准备？",
        "科研指导",
        raw_intent,
    )

    assert normalized.action == "answer"
    assert normalized.domain == "advising"
    assert normalized.decision_mode == "advise_only"
    assert normalized.needs_clarification is False


def test_normalize_interaction_intent_for_progress_followup_lamp_question() -> None:
    client = object.__new__(VllmChatClient)
    scenario = next(
        item
        for item in load_local_lamp_scenarios(
            Path("tests/data/lamp_personalization_weak_subset.json")
        )
        if item.scenario_id == "memory-followup-personalization"
    )
    request = build_lamp_request(scenario, task_name=scenario.task_name)
    raw_intent = InteractionIntent(
        action="ask_followup",
        domain="general",
        retrieval_scopes=[],
        exclude_scopes=[],
        needs_clarification=True,
        clarification_message="你是想发邮件还是补实验？",
        confidence=0.4,
    )

    normalized = client._normalize_interaction_intent(
        request.question,
        request.course_context,
        raw_intent,
    )

    assert normalized.action == "answer"
    assert normalized.domain == "advising"
    assert normalized.decision_mode == "advise_only"
    assert normalized.needs_clarification is False


def test_coerce_interaction_intent_uses_internal_confidence_policy() -> None:
    client = object.__new__(VllmChatClient)
    payload = client._parse_interaction_intent_payload(
        """
        {
            "action": "book_meeting",
            "domain": "booking",
            "retrieval_scopes": ["meeting_policy"],
            "exclude_scopes": ["courseware"],
            "decision_mode": "review_queue",
            "needs_clarification": false,
            "clarification_message": null,
            "escalation_reason": null,
            "confidence": "high"
        }
        """
    )

    intent = client._coerce_interaction_intent(payload)

    assert intent.action == "book_meeting"
    assert intent.domain == "booking"
    assert intent.confidence == 0.85


def test_parse_interaction_intent_payload_repairs_invalid_shape() -> None:
    client = object.__new__(VllmChatClient)
    repair_calls: list[tuple[str, str]] = []

    def answer_question_sync(
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = 256,
    ) -> str:
        repair_calls.append((system_prompt, user_prompt))
        return json.dumps(
            {
                "action": "book_meeting",
                "domain": "booking",
                "retrieval_scopes": ["meeting_policy"],
                "exclude_scopes": ["courseware"],
                "decision_mode": "review_queue",
                "needs_clarification": False,
                "clarification_message": None,
                "escalation_reason": None,
            }
        )

    client.answer_question_sync = answer_question_sync

    payload = client._parse_interaction_intent_payload(
        '{"action":"book_meeting","domain":"booking","retrieval_scopes":"meeting_policy"}'
    )

    assert payload.action == "book_meeting"
    assert payload.retrieval_scopes == ["meeting_policy"]
    assert len(repair_calls) == 1


def test_classify_booking_intent_sync_returns_false_on_classifier_failure() -> None:
    client = object.__new__(VllmChatClient)

    def classify_interaction_intent_sync(
        question: str, course_context: str | None = None
    ) -> InteractionIntent:
        raise RuntimeError("llm unavailable")

    client.classify_interaction_intent_sync = classify_interaction_intent_sync

    assert client.classify_booking_intent_sync("帮我预约明天上午讨论论文", "科研指导") is False


class _FakeChatCompletionResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


class _SequencedChatCompletionResponse:
    def __init__(
        self,
        content: str,
        *,
        finish_reason: str | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        self._content = content
        self._finish_reason = finish_reason
        self._reasoning_content = reasoning_content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        message = {"content": self._content}
        if self._reasoning_content is not None:
            message["reasoning_content"] = self._reasoning_content
        payload = {"message": message}
        if self._finish_reason is not None:
            payload["finish_reason"] = self._finish_reason
        return {"choices": [payload]}


class _FailingChatCompletionResponse:
    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError(
            "server error",
            request=httpx.Request("POST", "http://test.local/v1/chat/completions"),
            response=httpx.Response(500),
        )


class _SequencedHttpxClient:
    def __init__(
        self,
        responses: list[_SequencedChatCompletionResponse | _FailingChatCompletionResponse],
    ) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def post(
        self,
        path: str,
        json: dict,
    ) -> _SequencedChatCompletionResponse | _FailingChatCompletionResponse:
        self.calls.append((path, dict(json)))
        if not self._responses:
            raise RuntimeError("no more sequenced responses")
        return self._responses.pop(0)

    def close(self) -> None:
        return None


class _CapturingIntentClient:
    def __init__(self, content: str = '{"action":"answer","domain":"general","retrieval_scopes":[],"exclude_scopes":[],"decision_mode":"direct_answer","needs_clarification":false,"clarification_message":null,"escalation_reason":null}') -> None:
        self._content = content
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, json: dict) -> _FakeChatCompletionResponse:
        self.calls.append((path, json))
        return _FakeChatCompletionResponse(self._content)

    def close(self) -> None:
        return None


class _FlakyHttpxClient:
    def __init__(self, failures_before_success: int, *, content: str = "ok") -> None:
        self._failures_before_success = failures_before_success
        self._content = content
        self.calls = 0

    def post(self, path: str, json: dict) -> _FakeChatCompletionResponse:
        self.calls += 1
        if self.calls <= self._failures_before_success:
            raise httpx.ReadTimeout("timed out")
        return _FakeChatCompletionResponse(self._content)

    def close(self) -> None:
        return None


def _build_retry_test_client(settings: AppSettings, transport) -> VllmChatClient:
    client = object.__new__(VllmChatClient)
    client._settings = settings
    client._client = transport
    client._cache_lock = threading.Lock()
    client._response_cache = OrderedDict()
    client._metrics_lock = threading.Lock()
    client._request_count = 0
    client._success_count = 0
    client._error_count = 0
    client._cache_hit_count = 0
    client._last_request_at = None
    client._last_success_at = None
    client._last_error_at = None
    client._last_error_message = None
    return client


def _build_intent_test_client(settings: AppSettings, transport: _CapturingIntentClient) -> VllmChatClient:
    client = object.__new__(VllmChatClient)
    client._settings = settings
    client._client = None
    client._intent_client = transport
    client._intent_model_name = settings.intent_model_name or settings.model_name
    client._cache_lock = threading.Lock()
    client._response_cache = OrderedDict()
    client._metrics_lock = threading.Lock()
    client._request_count = 0
    client._success_count = 0
    client._error_count = 0
    client._cache_hit_count = 0
    client._last_request_at = None
    client._last_success_at = None
    client._last_error_at = None
    client._last_error_message = None
    return client


def test_request_intent_classification_disables_thinking_even_when_answer_llm_can_think() -> None:
    settings = AppSettings(model_name="Qwen3-32B", intent_model_name="Qwen3-8B")
    transport = _CapturingIntentClient()
    client = _build_intent_test_client(settings, transport)

    content = client._request_intent_classification("system", "user")

    assert content
    assert len(transport.calls) == 1
    path, payload = transport.calls[0]
    assert path == "/chat/completions"
    assert payload["model"] == "Qwen3-8B"
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["temperature"] == 0.0


def test_request_chat_completion_retries_timeout_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("sage_faculty_twin.llm_client.time.sleep", sleep_calls.append)
    settings = AppSettings(
        llm_cache_ttl_seconds=0,
        llm_cache_max_entries=0,
        llm_retry_attempts=2,
        llm_retry_backoff_seconds=0.25,
    )
    transport = _FlakyHttpxClient(failures_before_success=2, content="retried ok")
    client = _build_retry_test_client(settings, transport)

    answer = client._request_chat_completion_sync({"model": "demo", "messages": []})

    assert answer == "retried ok"
    assert transport.calls == 3
    assert sleep_calls == [0.25, 0.5]
    snapshot = client.runtime_snapshot()
    assert snapshot["llm_request_count"] == "1"
    assert snapshot["llm_success_count"] == "1"
    assert snapshot["llm_error_count"] == "0"
    assert snapshot["llm_last_error"] == ""


def test_request_chat_completion_never_uses_private_reasoning_as_answer() -> None:
    settings = AppSettings(
        llm_cache_ttl_seconds=0,
        llm_cache_max_entries=0,
        llm_retry_attempts=0,
    )
    transport = _SequencedHttpxClient(
        [_SequencedChatCompletionResponse("", reasoning_content="reasoning fallback")]
    )
    client = _build_retry_test_client(settings, transport)

    with pytest.raises(RuntimeError, match="empty chat message"):
        client._request_chat_completion_sync({"model": "demo", "messages": []})
    assert len(transport.calls) == 1


def test_app_settings_defaults_to_lower_llm_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DIGITAL_TWIN_LLM_TIMEOUT_SECONDS", raising=False)
    settings = AppSettings(_env_file=None)

    # Chat Latency Optimizations Task 1 lowered the default LLM timeout to
    # 60s so the request budget (80s) surfaces 504 well below Cloudflare's
    # 100s edge cap. ge=1, le=300 still allows operator overrides.
    assert settings.llm_timeout_seconds == 60


def test_app_settings_does_not_load_sibling_sage_env(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DIGITAL_TWIN_API_KEY", raising=False)

    settings = AppSettings(_env_file=None)

    # When no env file and no env var, the default api_key is "EMPTY".
    assert settings.api_key == "EMPTY"


def test_request_chat_completion_raises_after_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("sage_faculty_twin.llm_client.time.sleep", sleep_calls.append)
    settings = AppSettings(
        llm_cache_ttl_seconds=0,
        llm_cache_max_entries=0,
        llm_retry_attempts=1,
        llm_retry_backoff_seconds=0.1,
    )
    transport = _FlakyHttpxClient(failures_before_success=5)
    client = _build_retry_test_client(settings, transport)

    with pytest.raises(httpx.ReadTimeout):
        client._request_chat_completion_sync({"model": "demo", "messages": []})

    assert transport.calls == 2
    assert sleep_calls == [0.1]
    snapshot = client.runtime_snapshot()
    assert snapshot["llm_request_count"] == "1"
    assert snapshot["llm_success_count"] == "0"
    assert snapshot["llm_error_count"] == "1"
    assert "timed out" in snapshot["llm_last_error"]


def test_request_chat_completion_continues_when_finish_reason_is_length() -> None:
    settings = AppSettings(
        llm_cache_ttl_seconds=0,
        llm_cache_max_entries=0,
        llm_retry_attempts=0,
    )
    transport = _SequencedHttpxClient(
        [
            _SequencedChatCompletionResponse("第一段回答未完", finish_reason="length"),
            _SequencedChatCompletionResponse("第二段续写完成", finish_reason="stop"),
        ]
    )
    client = _build_retry_test_client(settings, transport)

    answer = client._request_chat_completion_sync(
        {
            "model": "demo",
            "messages": [{"role": "user", "content": "请给建议"}],
            "max_tokens": 256,
        }
    )

    assert answer == "第一段回答未完\n第二段续写完成"
    assert len(transport.calls) == 2
    _, second_payload = transport.calls[1]
    assert second_payload["messages"][-1]["role"] == "user"
    assert "继续" in second_payload["messages"][-1]["content"]


def test_request_chat_completion_rejects_bounded_truncation_without_retry_or_cache() -> None:
    settings = AppSettings(
        llm_cache_ttl_seconds=0,
        llm_cache_max_entries=0,
        llm_retry_attempts=0,
    )
    transport = _SequencedHttpxClient(
        [_SequencedChatCompletionResponse("已经包含足够信息。", finish_reason="length")]
    )
    client = _build_retry_test_client(settings, transport)

    with pytest.raises(IncompleteCompletionError):
        client._request_chat_completion_sync(
            {
                "model": "demo",
                "messages": [{"role": "user", "content": "请简要回答"}],
                "max_tokens": 256,
            },
            continue_on_length=False,
        )
    assert len(transport.calls) == 1
    assert not client._response_cache


def test_request_chat_completion_rejects_truncation_when_continuation_fails() -> None:
    settings = AppSettings(
        llm_cache_ttl_seconds=0,
        llm_cache_max_entries=0,
        llm_retry_attempts=0,
    )
    transport = _SequencedHttpxClient(
        [
            _SequencedChatCompletionResponse("第一段回答未完", finish_reason="length"),
        ]
    )
    client = _build_retry_test_client(settings, transport)

    with pytest.raises(IncompleteCompletionError):
        client._request_chat_completion_sync(
            {
                "model": "demo",
                "messages": [{"role": "user", "content": "请给建议"}],
                "max_tokens": 256,
            }
        )
    assert not client._response_cache


def test_stream_length_finish_cannot_be_cached_as_success():
    class Response:
        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"未完成"}}]}'
            yield 'data: {"choices":[{"delta":{},"finish_reason":"length"}]}'
            yield 'data: [DONE]'

    class Transport:
        def stream(self, *args, **kwargs):
            return nullcontext(Response())

    client = _build_retry_test_client(AppSettings(_env_file=None, llm_retry_attempts=0), Transport())
    client._ensure_runtime_state()
    client._request_completion_client = lambda: nullcontext(client._client)
    with pytest.raises(IncompleteCompletionError):
        client._request_chat_completion_stream({"model": "test", "messages": [], "stream": True}, lambda _: None)
    assert not client._response_cache


def test_cancellation_during_continuation_is_not_a_length_repair(monkeypatch):
    from sage_faculty_twin.request_context import RequestCancelledError

    client = _build_retry_test_client(AppSettings(_env_file=None), None)

    def cancel(**kwargs):
        raise RequestCancelledError("cancelled")

    monkeypatch.setattr("sage_faculty_twin.llm_client.raise_if_request_cancelled", cancel)
    with pytest.raises(RequestCancelledError):
        client._continue_truncated_answer({"messages": [{"role": "user", "content": "test"}]}, "partial")


def test_cache_namespace_scopes_responses_per_conversation() -> None:
    """Different cache_namespace values must produce distinct cache keys,
    preventing cross-user cache pollution when two students ask similar
    questions.
    """
    client = object.__new__(VllmChatClient)
    client._cache_lock = threading.Lock()
    client._response_cache = OrderedDict()
    client._metrics_lock = threading.Lock()
    client._cache_hit_count = 0
    client._exact_cache_hit_count = 0
    client._semantic_cache_hit_count = 0
    client._cache_lookup_count = 0

    settings = AppSettings(
        llm_cache_ttl_seconds=300,
        llm_cache_max_entries=100,
    )
    client._settings = settings

    payload_user_a = {
        "model": "demo",
        "messages": [{"role": "user", "content": "Ascend NPU内存管理怎么优化？"}],
        "temperature": 0.2,
        "max_tokens": 1024,
    }
    payload_user_b = dict(payload_user_a)

    # Store a response for user A
    client._store_cached_response(
        client._cache_key(payload_user_a, namespace="conv-alice"),
        "Alice's personalised answer",
        client._semantic_cache_key(payload_user_a, namespace="conv-alice"),
    )

    # User B should NOT get Alice's cached response
    cache_key_b = client._cache_key(payload_user_b, namespace="conv-bob")
    semantic_key_b = client._semantic_cache_key(payload_user_b, namespace="conv-bob")
    cached, hit_type = client._get_cached_response(cache_key_b, semantic_key_b)
    assert cached is None, (
        "User B should not receive User A's cached response even when "
        "the question is identical"
    )

    # User A should still get a cache hit
    cache_key_a = client._cache_key(payload_user_a, namespace="conv-alice")
    semantic_key_a = client._semantic_cache_key(payload_user_a, namespace="conv-alice")
    cached_a, hit_type_a = client._get_cached_response(cache_key_a, semantic_key_a)
    assert cached_a == "Alice's personalised answer"
    assert hit_type_a == "exact"


def test_namespaced_cache_never_fuzzy_matches_long_similar_prompts() -> None:
    client = object.__new__(VllmChatClient)
    client._cache_lock = threading.Lock()
    client._response_cache = OrderedDict()
    client._metrics_lock = threading.Lock()
    client._cache_hit_count = 0
    client._exact_cache_hit_count = 0
    client._semantic_cache_hit_count = 0
    client._cache_lookup_count = 0
    client._settings = AppSettings(
        llm_cache_ttl_seconds=300,
        llm_cache_max_entries=100,
    )
    shared_system = "你是中文学术助理。" * 120
    payload = {
        "model": "demo",
        "messages": [
            {"role": "system", "content": shared_system},
            {"role": "user", "content": "如何优化大模型推理？"},
        ],
    }
    client._store_cached_response(
        client._cache_key(payload, namespace="conversation-a"),
        "stale answer",
        client._semantic_cache_key(payload, namespace="conversation-a"),
    )

    cache_key = client._cache_key(payload, namespace="conversation-b")
    semantic_key = client._semantic_cache_key(payload, namespace="conversation-b")

    assert client._get_cached_response(cache_key, semantic_key) == (None, None)


def test_namespaced_cache_does_not_fuzzy_match_a_followup_in_same_conversation() -> None:
    client = object.__new__(VllmChatClient)
    client._cache_lock = threading.Lock()
    client._response_cache = OrderedDict()
    client._metrics_lock = threading.Lock()
    client._cache_hit_count = 0
    client._exact_cache_hit_count = 0
    client._semantic_cache_hit_count = 0
    client._cache_lookup_count = 0
    client._settings = AppSettings(
        llm_cache_ttl_seconds=300,
        llm_cache_max_entries=100,
    )
    first = {
        "model": "demo",
        "messages": [{"role": "user", "content": "医学影像分割有哪些核心挑战？"}],
    }
    followup = {
        "model": "demo",
        "messages": [{"role": "user", "content": "把上面的核心挑战归纳成三个优先级。"}],
    }
    namespace = "conversation-medical"
    client._store_cached_response(
        client._cache_key(first, namespace=namespace),
        "first answer",
        client._semantic_cache_key(first, namespace=namespace),
    )

    assert client._get_cached_response(
        client._cache_key(followup, namespace=namespace),
        client._semantic_cache_key(followup, namespace=namespace),
    ) == (None, None)


def test_cache_key_includes_namespace() -> None:
    """The namespace must appear in the cache key to guarantee isolation."""
    client = object.__new__(VllmChatClient)
    payload = {"model": "demo", "messages": [{"role": "user", "content": "hi"}]}

    key_a = client._cache_key(payload, namespace="conv-1")
    key_b = client._cache_key(payload, namespace="conv-2")
    key_none = client._cache_key(payload)

    assert key_a != key_b
    assert key_a != key_none
    assert key_a.startswith("conv-1::")
    assert key_b.startswith("conv-2::")


def test_fixed_prefix_materialization_hints_are_feature_gated() -> None:
    client = object.__new__(VllmChatClient)
    client._settings = AppSettings(kv_fixed_prefix_materialization_enabled=False)
    client.model_name = "demo-model"
    payload = {"model": "demo-model", "messages": []}

    annotated = client.annotate_request_with_fixed_prefix_hints(
        dict(payload),
        system_prompt="stable system prompt",
        logical_request_id="conv-1",
    )

    assert annotated == payload


def test_fixed_prefix_materialization_hints_use_stable_anchor() -> None:
    client = object.__new__(VllmChatClient)
    client._settings = AppSettings(
        kv_fixed_prefix_materialization_enabled=True,
        kv_fixed_prefix_anchor_prefix="twin-fixed",
        kv_fixed_prefix_anchor_version="v2",
    )
    client.model_name = "demo-model"
    payload = {
        "model": "demo-model",
        "messages": [],
        "kv_transfer_params": {"remote_engine_id": "prefill-0"},
    }

    first = client.annotate_request_with_fixed_prefix_hints(
        dict(payload),
        system_prompt="stable system prompt",
        logical_request_id="conv-1",
    )
    second = client.annotate_request_with_fixed_prefix_hints(
        dict(payload),
        system_prompt="stable system prompt",
        logical_request_id="conv-1",
    )
    changed_prompt = client.annotate_request_with_fixed_prefix_hints(
        dict(payload),
        system_prompt="changed system prompt",
        logical_request_id="conv-1",
    )

    assert first["cache_salt"] == second["cache_salt"]
    assert first["cache_salt"].startswith("kvmat:anchor:twin-fixed:v2:")
    assert changed_prompt["cache_salt"] != first["cache_salt"]
    assert first["kv_transfer_params"] == {"remote_engine_id": "prefill-0"}


def test_segment_reuse_hints_are_feature_gated() -> None:
    client = object.__new__(VllmChatClient)
    client._settings = AppSettings(segment_reuse_hints_enabled=False)
    client.model_name = "demo-model"
    payload = {"model": "demo-model", "messages": []}

    annotated = client.annotate_request_with_segment_reuse_hints(
        dict(payload),
        reusable_body_text="stable reusable body",
        scope="lab_member",
        logical_request_id="conv-1",
    )

    assert annotated == payload


def test_segment_reuse_hints_attach_extra_key_without_leading_token_guess() -> None:
    client = object.__new__(VllmChatClient)
    client._settings = AppSettings(
        segment_reuse_hints_enabled=True,
        segment_reuse_namespace_prefix="twin",
        segment_reuse_boundary_class="control-only",
        segment_reuse_max_leading_tokens=8192,
    )
    client.model_name = "demo-model"
    payload = {
        "model": "demo-model",
        "messages": [],
        "cache_salt": "kvmat:anchor:abc",
    }

    annotated = client.annotate_request_with_segment_reuse_hints(
        dict(payload),
        reusable_body_text="stable reusable body",
        scope="lab_member",
        logical_request_id="conv 1/with spaces",
    )

    assert annotated["cache_salt"] == "kvmat:anchor:abc"
    assert annotated["extra_key"].startswith("twin:lab_member:")
    assert "||segreuse:v1;" in annotated["extra_key"]
    assert "mode=leading-envelope" in annotated["extra_key"]
    assert "tokens=8192" in annotated["extra_key"]
    assert "leading_tokens=;" in annotated["extra_key"]
    assert "boundary_class=control-only" in annotated["extra_key"]
    assert "attention_contract=control-envelope-excluded-from-model-body" in annotated["extra_key"]
    assert "rid=conv-1-with-spaces" in annotated["extra_key"]


def test_segment_reuse_hints_partition_by_visitor_profile_scope() -> None:
    client = object.__new__(VllmChatClient)
    client._settings = AppSettings(
        segment_reuse_hints_enabled=True,
        segment_reuse_namespace_prefix="twin",
    )
    client.model_name = "demo-model"
    body = "stable reusable skills and public KB context"

    guest = client.annotate_request_with_segment_reuse_hints(
        {"model": "demo-model", "messages": []},
        reusable_body_text=body,
        scope="general_visitor",
    )
    lab = client.annotate_request_with_segment_reuse_hints(
        {"model": "demo-model", "messages": []},
        reusable_body_text=body,
        scope="lab_member",
    )

    assert guest["extra_key"].startswith("twin:general_visitor:")
    assert lab["extra_key"].startswith("twin:lab_member:")
    assert guest["extra_key"] != lab["extra_key"]


def test_segment_reuse_hints_preserve_runtime_leading_token_count() -> None:
    client = object.__new__(VllmChatClient)
    client._settings = AppSettings(
        segment_reuse_hints_enabled=True,
        segment_reuse_max_leading_tokens=4096,
    )
    client.model_name = "demo-model"

    annotated = client.annotate_request_with_segment_reuse_hints(
        {"model": "demo-model", "messages": []},
        reusable_body_text="stable reusable body",
        scope="lab_member",
        leading_token_count=384,
    )

    assert "tokens=4096" in annotated["extra_key"]
    assert "leading_tokens=384;" in annotated["extra_key"]


def test_segment_reuse_masked_envelope_advertises_hidden_body_contract() -> None:
    client = object.__new__(VllmChatClient)
    client._settings = AppSettings(
        segment_reuse_hints_enabled=True,
        segment_reuse_boundary_class="masked-envelope",
    )
    client.model_name = "demo-model"

    annotated = client.annotate_request_with_segment_reuse_hints(
        {"model": "demo-model", "messages": []},
        reusable_body_text="stable reusable body",
        scope="lab_member",
    )

    assert "boundary_class=masked-envelope" in annotated["extra_key"]
    assert "attention_contract=masked-envelope-hidden-from-body" in annotated["extra_key"]


def test_vllm_metrics_url_can_be_configured_separately_from_proxy_base_url() -> None:
    client = object.__new__(VllmChatClient)
    client._settings = AppSettings(
        llm_base_url="http://127.0.0.1:18001/v1",
        vllm_metrics_url="http://127.0.0.1:18080/metrics",
    )

    assert client._resolve_vllm_metrics_url() == "http://127.0.0.1:18080/metrics"


def test_vllm_metrics_url_defaults_to_llm_base_url_metrics_endpoint() -> None:
    client = object.__new__(VllmChatClient)
    client._settings = AppSettings(
        llm_base_url="http://127.0.0.1:18001/v1",
        vllm_metrics_url="",
    )

    assert client._resolve_vllm_metrics_url() == "http://127.0.0.1:18001/metrics"


def test_fixed_prefix_warmup_uses_materialization_anchor() -> None:
    transport = _SequencedHttpxClient([_SequencedChatCompletionResponse("OK")])
    client = _build_retry_test_client(
        AppSettings(
            kv_fixed_prefix_materialization_enabled=True,
            kv_fixed_prefix_warmup_on_startup=True,
            kv_fixed_prefix_warmup_max_tokens=1,
            kv_fixed_prefix_anchor_prefix="twin-fixed",
            kv_fixed_prefix_anchor_version="v3",
        ),
        transport,
    )
    client.model_name = "demo-model"

    assert client.warm_fixed_prefix_cache_sync("stable system prompt") is True

    assert len(transport.calls) == 1
    path, payload = transport.calls[0]
    assert path == "/chat/completions"
    assert payload["max_tokens"] == 1
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["cache_salt"].startswith("kvmat:anchor:twin-fixed:v3:")
    assert payload["messages"][0] == {
        "role": "system",
        "content": "stable system prompt",
    }


def test_fixed_prefix_warmup_failure_does_not_mark_llm_unhealthy() -> None:
    transport = _SequencedHttpxClient([_FailingChatCompletionResponse()])
    client = _build_retry_test_client(
        AppSettings(
            kv_fixed_prefix_materialization_enabled=True,
            kv_fixed_prefix_warmup_on_startup=True,
        ),
        transport,
    )
    client.model_name = "demo-model"

    assert client.warm_fixed_prefix_cache_sync("stable system prompt") is False

    assert len(transport.calls) == 1
    assert client._request_count == 0
    assert client._success_count == 0
    assert client._error_count == 0
    assert client._last_error_message is None


def test_answer_question_can_attach_prefix_and_segment_reuse_hints_together() -> None:
    transport = _SequencedHttpxClient([_SequencedChatCompletionResponse("OK")])
    client = _build_retry_test_client(
        AppSettings(
            kv_fixed_prefix_materialization_enabled=True,
            kv_fixed_prefix_anchor_prefix="twin-fixed",
            kv_fixed_prefix_anchor_version="v4",
            segment_reuse_hints_enabled=True,
            segment_reuse_namespace_prefix="twin",
        ),
        transport,
    )
    client.model_name = "demo-model"

    answer = client.answer_question_sync(
        "stable system prompt",
        "dynamic user prompt",
        enable_thinking=False,
        max_tokens=8,
        cache_namespace="conv-1",
        segment_reuse_scope="lab_member",
    )

    assert answer == "OK"
    assert len(transport.calls) == 1
    _, payload = transport.calls[0]
    assert payload["cache_salt"].startswith("kvmat:anchor:twin-fixed:v4:")
    assert payload["extra_key"].startswith("twin:lab_member:")
    assert "||segreuse:v1;" in payload["extra_key"]
    assert "leading_tokens=;" in payload["extra_key"]


def test_answer_question_can_disable_reuse_hints_for_recovery_retry() -> None:
    transport = _SequencedHttpxClient([_SequencedChatCompletionResponse("OK")])
    client = _build_retry_test_client(
        AppSettings(
            kv_continuity_enabled=True,
            kv_fixed_prefix_materialization_enabled=True,
            segment_reuse_hints_enabled=True,
        ),
        transport,
    )
    client.model_name = "demo-model"

    answer = client.answer_question_sync(
        "stable system prompt",
        "dynamic user prompt",
        enable_thinking=False,
        max_tokens=8,
        cache_namespace="conv-1",
        use_reuse_hints=False,
    )

    assert answer == "OK"
    assert len(transport.calls) == 1
    _, payload = transport.calls[0]
    assert "cache_salt" not in payload
    assert "extra_key" not in payload
    assert "kv_transfer_params" not in payload


def test_glm4_answer_uses_neutral_sampling_penalties() -> None:
    transport = _SequencedHttpxClient([_SequencedChatCompletionResponse("OK")])
    client = _build_retry_test_client(
        AppSettings(
            llm_answer_frequency_penalty=0.35,
            llm_answer_presence_penalty=0.2,
            llm_answer_repetition_penalty=1.08,
        ),
        transport,
    )
    client.model_name = "zai-org/GLM-4-32B-0414"

    answer = client.answer_question_sync(
        "system",
        "user",
        enable_thinking=False,
        max_tokens=8,
        use_reuse_hints=False,
    )

    assert answer == "OK"
    _, payload = transport.calls[0]
    assert payload["frequency_penalty"] == 0.0
    assert payload["presence_penalty"] == 0.0
    assert payload["repetition_penalty"] == 1.0


def test_non_glm_answer_keeps_configured_sampling_penalties() -> None:
    transport = _SequencedHttpxClient([_SequencedChatCompletionResponse("OK")])
    client = _build_retry_test_client(
        AppSettings(
            llm_answer_frequency_penalty=0.35,
            llm_answer_presence_penalty=0.2,
            llm_answer_repetition_penalty=1.08,
        ),
        transport,
    )
    client.model_name = "Qwen/Qwen3-32B"

    client.answer_question_sync(
        "system",
        "user",
        enable_thinking=False,
        max_tokens=8,
        use_reuse_hints=False,
    )

    _, payload = transport.calls[0]
    assert payload["frequency_penalty"] == 0.35
    assert payload["presence_penalty"] == 0.2
    assert payload["repetition_penalty"] == 1.08


def test_answer_question_retries_without_thinking_budget_after_server_error() -> None:
    transport = _SequencedHttpxClient(
        [
            _FailingChatCompletionResponse(),
            _SequencedChatCompletionResponse("OK"),
        ]
    )
    client = _build_retry_test_client(
        AppSettings(model_name="demo-model", llm_cache_ttl_seconds=0),
        transport,
    )
    client.model_name = "demo-model"
    client._supports_thinking_budget = True

    answer = client.answer_question_sync(
        "system",
        "user",
        enable_thinking=True,
        thinking_token_budget=256,
        max_tokens=8,
        use_reuse_hints=False,
    )

    assert answer == "OK"
    assert client._supports_thinking_budget is False
    assert len(transport.calls) == 2
    assert transport.calls[0][1]["thinking_token_budget"] == 256
    assert "thinking_token_budget" not in transport.calls[1][1]


def test_thinking_budget_is_an_explicit_server_capability_not_a_model_name() -> None:
    client = object.__new__(VllmChatClient)
    client._settings = AppSettings(
        model_name="zai-org/GLM-4-32B-0414",
        llm_base_url="http://127.0.0.1:18001/v1",
    )
    client.model_name = "zai-org/GLM-4-32B-0414"

    assert client._model_supports_thinking_budget() is False

    client._settings = AppSettings(
        model_name="Qwen/Qwen3-32B",
        llm_base_url="http://127.0.0.1:18001/v1",
    )
    client.model_name = "Qwen/Qwen3-32B"

    assert client._model_supports_thinking_budget() is False
    client._settings.llm_thinking_budget_supported = True
    assert client._model_supports_thinking_budget() is True


def test_native_tool_calling_capability_is_explicit() -> None:
    client = object.__new__(VllmChatClient)

    client._settings = AppSettings(_env_file=None)
    assert client.supports_native_tool_calling is False

    client._settings = AppSettings(llm_tool_calling_mode="native", _env_file=None)
    assert client.supports_native_tool_calling is True
