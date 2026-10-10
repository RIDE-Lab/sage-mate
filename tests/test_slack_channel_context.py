from __future__ import annotations

import json
import urllib.parse

import pytest

from sage_faculty_twin import slack_channel_context as module
from sage_faculty_twin.models import ChatRequest


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


def test_fetch_slack_context_is_channel_local_bounded_and_chronological(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _FakeResponse(
            {
                "ok": True,
                "messages": [
                    {"user": "U22222222", "ts": "1760079060.0", "text": "debin 单独开会"},
                    {"bot_id": "B123", "ts": "1760079000.0", "text": "ignore bot"},
                    {"user": "U11111111", "ts": "1760078940.0", "text": "hongyi 开会 xoxb-secret123"},
                ],
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    context = module.fetch_slack_channel_context(
        bot_token="test-token", channel_id="C123456789", latest_ts="1760079120"
    )

    query = urllib.parse.parse_qs(urllib.parse.urlparse(requests[0][0].full_url).query)
    assert query == {"channel": ["C123456789"], "latest": ["1760079120"], "limit": ["30"]}
    assert requests[0][0].get_header("Authorization") == "Bearer test-token"
    assert context.message_count == 2
    assert context.text.index("hongyi") < context.text.index("debin")
    assert "xoxb-secret123" not in context.text
    assert "[REDACTED]" in context.text
    assert "ignore bot" not in context.text


def test_fetch_slack_context_reports_missing_scope(monkeypatch):
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse({"ok": False, "error": "missing_scope"}),
    )
    with pytest.raises(module.SlackHistoryUnavailable) as exc:
        module.fetch_slack_channel_context(
            bot_token="test-token", channel_id="C123456789", latest_ts="1760079120"
        )
    assert exc.value.code == "missing_scope"


def test_fetch_slack_context_rejects_bad_channel_without_network(monkeypatch):
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("network must not be called"),
    )
    with pytest.raises(module.SlackHistoryUnavailable) as exc:
        module.fetch_slack_channel_context(
            bot_token="test-token", channel_id="C123/other", latest_ts="1760079120"
        )
    assert exc.value.code == "invalid_channel"


def test_slack_context_is_transient_not_serialized():
    request = ChatRequest(
        student_name="test",
        question="总结前文",
        slack_channel_context="private channel text",
    )
    assert request.slack_channel_context == "private channel text"
    assert "slack_channel_context" not in request.model_dump()
    assert module.asks_about_prior_context(request.question)
    assert not module.asks_about_prior_context("介绍你的研究方向")
