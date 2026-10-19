from types import SimpleNamespace

import httpx
import pytest

from tools import accept_evidence_workflow as qa


def test_queued_and_timeout_observations_preserve_the_same_request_and_body(monkeypatch):
    monkeypatch.setattr(qa.time, "sleep", lambda _: None)
    responses = iter([httpx.Response(429), httpx.Response(504), httpx.Response(200)])
    calls, body = [], {"question":"public test"}
    cookie = SimpleNamespace(secure=True)
    def post(path, **kwargs):
        calls.append((path, kwargs))
        return next(responses)
    client = SimpleNamespace(post=post, cookies=SimpleNamespace(jar=[cookie]))
    response, polls = qa.observe_owned_chat(client, body, "same-request")
    assert response.status_code == 200 and polls == 2
    assert len(calls) == 3 and all(path == "/chat" and args["params"] == {"request_id":"same-request"}
        and args["json"] is body for path, args in calls)
    assert cookie.secure is False


def test_capacity_rejection_is_not_misreported_as_an_enqueued_request():
    response = httpx.Response(429, headers={"X-Sage-Error-Code":"work_capacity_exhausted"})
    client = SimpleNamespace(post=lambda *a, **kw: response, cookies=SimpleNamespace(jar=[]))
    assert qa.observe_owned_chat(client, {}, "request") == (response, 0)


def test_observer_deadline_does_not_cancel_or_resubmit_producer(monkeypatch):
    ticks = iter([0, 2])
    monkeypatch.setattr(qa.time, "monotonic", lambda: next(ticks))
    client = SimpleNamespace(post=lambda *a, **kw: pytest.fail("expired observer must not post"))
    with pytest.raises(TimeoutError, match="producer was not cancelled"):
        qa.observe_owned_chat(client, {}, "same-request", timeout=1)
