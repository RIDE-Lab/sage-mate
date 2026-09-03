"""Native/application reasoning contracts without model-family heuristics."""

import json

import httpx
import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.evidence_policy import (
    comparison_subjects,
    is_research_hit,
    rank_comparison_evidence,
)
from sage_faculty_twin.llm_client import VllmChatClient
from sage_faculty_twin.models import ChatRequest, KnowledgeSearchHit, InteractionIntent
from sage_faculty_twin.service import ChatWorkflowContext, FacultyTwinWorkflowSupport
from sage_faculty_twin.thinking_policy import (
    VisibleAnswerFilter,
    capability_from_tokenization,
)


def rendered(text, tokens=None):
    return {"tokens": tokens or list(text.encode()), "token_strs": [text]}


@pytest.mark.parametrize(
    "off,on,supported,switchable",
    [
        ("assistant<think></think>", "assistant<think>", True, True),
        ("assistant<think>", "assistant<think>", True, False),
        ("assistant", "assistant", False, False),
        ("assistant", "some other protocol", None, False),
    ],
)
def test_actual_template_capabilities(off, on, supported, switchable):
    capability = capability_from_tokenization(rendered(off), rendered(on))
    assert capability.supported is supported
    assert capability.switchable is switchable


@pytest.mark.parametrize(
    "bad", [{}, {"tokens": []}, {"tokens": [1], "token_strs": [123]}]
)
def test_invalid_template_is_unknown_not_unsupported(bad):
    assert capability_from_tokenization(bad, rendered("<think>")).supported is None


@pytest.fixture
def client_factory(monkeypatch):
    clients = []

    def build(
        *, mode="native", streaming=False, reasoning_only=False, probe_fails=False
    ):
        calls = []

        def handler(request):
            payload = json.loads(request.content) if request.content else {}
            calls.append((request.url.path, payload))
            if request.url.path.endswith("models"):
                return httpx.Response(200, json={"data": [{"id": "arbitrary-model"}]})
            if request.url.path.endswith("tokenize"):
                if probe_fails:
                    return httpx.Response(503)
                enabled = payload["chat_template_kwargs"]["enable_thinking"]
                return httpx.Response(
                    200, json=rendered("<think>" if enabled else "<think></think>")
                )
            if streaming:
                chunks = [
                    {"delta": {"reasoning": "PRIVATE-A"}},
                    {"delta": {"reasoning_content": "PRIVATE-B"}},
                ]
                if not reasoning_only:
                    chunks.append({"delta": {"content": "最终回答"}})
                chunks.append({"delta": {}, "finish_reason": "stop"})
                body = "".join(
                    "data: " + json.dumps({"choices": [c]}) + "\n\n" for c in chunks
                )
                return httpx.Response(
                    200,
                    text=body + "data: [DONE]\n\n",
                    headers={"content-type": "text/event-stream"},
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "" if reasoning_only else "最终回答",
                                "reasoning": "PRIVATE-A",
                                "reasoning_content": "PRIVATE-B",
                            },
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

        transport = httpx.Client(
            base_url="http://engine.test/v1", transport=httpx.MockTransport(handler)
        )
        monkeypatch.setattr(
            VllmChatClient, "_build_completion_client", lambda _: transport
        )
        settings = AppSettings(
            _env_file=None,
            model_name="arbitrary-model",
            llm_thinking_mode=mode,
            llm_base_url="http://engine.test/v1",
            llm_cache_ttl_seconds=0,
            llm_retry_attempts=0,
        )
        client = VllmChatClient(settings)
        clients.append(client)
        return client, calls

    yield build
    for client in clients:
        client.close()


def test_probe_once_per_model_not_per_question(client_factory):
    client, calls = client_factory(mode="auto")
    for _ in range(5):
        assert client.supports_native_thinking
    assert len([c for c in calls if c[0].endswith("tokenize")]) == 2
    client.model_name = "another-model"
    assert client.supports_native_thinking
    assert len([c for c in calls if c[0].endswith("tokenize")]) == 4


def test_unknown_does_not_silently_select_application(client_factory):
    client, _ = client_factory(mode="auto", probe_fails=True)
    with pytest.raises(RuntimeError, match="capability is unknown"):
        _ = client.supports_native_thinking


@pytest.mark.parametrize("enabled", [False, True])
def test_transport_explicit_switch_and_preserves_budget(client_factory, enabled):
    client, calls = client_factory()
    assert (
        client.answer_question_sync(
            "system",
            "user",
            enable_thinking=enabled,
            max_tokens=3072,
            use_reuse_hints=False,
        )
        == "最终回答"
    )
    payload = calls[-1][1]
    assert payload["chat_template_kwargs"]["enable_thinking"] is enabled
    assert payload["max_tokens"] == 3072
    assert "thinking_token_budget" not in payload
    if enabled:
        assert payload["chat_template_kwargs"]["reasoning_effort"] == "medium"


def test_streaming_excludes_both_reasoning_fields(client_factory):
    client, _ = client_factory(streaming=True)
    visible = []
    answer = client.answer_question_sync(
        "system",
        "user",
        token_callback=visible.append,
        enable_thinking=True,
        use_reuse_hints=False,
    )
    assert answer == "最终回答"
    assert visible == ["最终回答"]


def test_reasoning_only_is_never_success(client_factory):
    client, _ = client_factory(reasoning_only=True)
    with pytest.raises(RuntimeError, match="empty chat message"):
        client.answer_question_sync(
            "system", "user", enable_thinking=True, use_reuse_hints=False
        )


@pytest.mark.parametrize(
    "supported,requested,expected",
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_service_routing_budget_and_deep_streaming(supported, requested, expected):
    class Adapter:
        supports_native_thinking = supported

        def answer_question_sync(
            self,
            system,
            user,
            *,
            enable_thinking,
            max_tokens,
            token_callback=None,
            **kwargs,
        ):
            self.call = (enable_thinking, max_tokens, token_callback)
            return "最终回答"

    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(_env_file=None)
    service._llm_client = Adapter()
    context = ChatWorkflowContext(
        request=ChatRequest(
            student_name="test",
            question="解释混合缓存",
            deep_thinking=requested,
            deep_thinking_explicit=True,
        ),
        conversation_id="test",
        owner_name="owner",
        used_model="arbitrary-model",
    )

    def callback(text):
        return None

    service._call_answer_question_sync(
        "system",
        "user",
        context=context,
        enable_thinking=requested,
        token_callback=callback,
    )
    thinking, budget, used_callback = service._llm_client.call
    assert thinking is expected
    if requested:
        assert budget == (3072 if expected else 1024)
    assert used_callback is callback


def test_native_repair_does_not_downgrade(client_factory):
    client, calls = client_factory()
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = client._settings
    service._llm_client = client
    service._call_compact_retry_sync(
        system_prompt="system",
        user_prompt="user",
        temperature=0.2,
        max_tokens=3072,
        cache_namespace="repair",
        enable_thinking=True,
    )
    assert calls[-1][1]["chat_template_kwargs"]["enable_thinking"] is True


@pytest.mark.parametrize(
    "chunks",
    [
        ["<think>PRIVATE</think>answer"],
        ["<thi", "nk>", "PRIVATE", "</thi", "nk>", "answer"],
        ["answer", "<think>PRIVATE"],
    ],
)
def test_inline_thinking_never_reaches_answer_callback(chunks):
    guard = VisibleAnswerFilter()
    assert "".join(guard.feed(chunk) for chunk in chunks) + guard.finish() == "answer"


def test_fuzzy_cache_cannot_cross_thinking_modes(client_factory):
    client, calls = client_factory()
    client._settings.llm_cache_ttl_seconds = 60
    client._settings.llm_cache_max_entries = 10
    for enabled in (False, True, True):
        client.answer_question_sync(
            "system",
            "same question",
            enable_thinking=enabled,
            max_tokens=100,
            use_reuse_hints=False,
        )
    assert len([c for c in calls if c[0].endswith("completions")]) == 2


def test_native_sampling_is_separate_from_prose_penalties(client_factory):
    client, calls = client_factory()
    client._settings.llm_thinking_temperature = 1.0
    client._settings.llm_thinking_top_p = 0.95
    client._settings.llm_thinking_top_k = 20
    client.answer_question_sync(
        "system", "user", enable_thinking=True, use_reuse_hints=False
    )
    payload = calls[-1][1]
    assert payload["frequency_penalty"] == payload["presence_penalty"] == 0.0
    assert payload["repetition_penalty"] == 1.0
    assert (payload["temperature"], payload["top_p"], payload["top_k"]) == (
        1.0,
        0.95,
        20,
    )


def test_comparison_coverage_is_not_a_product_allowlist():
    assert comparison_subjects("比较 AlphaDB 与 BetaFlow 的定位") == [
        "alphadb",
        "betaflow",
    ]
    assert comparison_subjects("介绍 AlphaDB") == []
    assert comparison_subjects("Compare alphadb and betaflow") == ["alphadb", "betaflow"]
    assert comparison_subjects("比较两种方法") == []
    assert is_research_hit(
        KnowledgeSearchHit(
            document_id="repo",
            title="AlphaDB",
            excerpt="system",
            score=1,
            tags=["github", "public-repository"],
        )
    )


def test_comparison_ranking_prefers_evidence_covering_both_subjects():
    paper = KnowledgeSearchHit(
        document_id="a", title="AlphaDB paper", excerpt="AlphaDB experiment", score=99
    )
    overview = KnowledgeSearchHit(
        document_id="b",
        title="System overview",
        excerpt="AlphaDB is storage; BetaFlow is execution.",
        score=10,
    )
    assert (
        rank_comparison_evidence([paper, overview], ["alphadb", "betaflow"])[0]
        is overview
    )


def test_native_budget_not_squeezed_to_prose_cap_under_load(
    client_factory, monkeypatch
):
    client, calls = client_factory()
    monkeypatch.setattr(client, "_is_high_congestion", lambda _: True)
    client.answer_question_sync(
        "system", "user", enable_thinking=True, max_tokens=3072, use_reuse_hints=False
    )
    assert calls[-1][1]["max_tokens"] == 3072


def test_comparison_supplement_preserves_visitor_permissions():
    class Store:
        def __init__(self):
            self.calls = []

        def search(self, query, **kwargs):
            self.calls.append(kwargs)
            return []

    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(_env_file=None, web_search_enabled=False)
    service._knowledge_store = Store()
    service._trace_callback = None
    service._resolve_admin_role = lambda: None
    context = ChatWorkflowContext(
        request=ChatRequest(
            student_name="test",
            question="比较 AlphaDB 与 BetaFlow",
            visitor_profile="general_visitor",
        ),
        conversation_id="permissions",
        owner_name="owner",
        used_model="model",
        interaction_intent=InteractionIntent(action="answer", domain="research"),
    )
    service.retrieve_knowledge(context)
    assert len(service._knowledge_store.calls) == 3
    assert all(
        call["visitor_profile"] == "general_visitor" and call["admin_role"] is None
        for call in service._knowledge_store.calls
    )
