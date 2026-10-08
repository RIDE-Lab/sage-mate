from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from sage_faculty_twin import api as api_module
from sage_faculty_twin.conversation_trace_store import ConversationTraceStore
from sage_faculty_twin.models import ChatResponse, WorkflowTraceStep


def test_trace_store_persists_full_request_response_and_events(tmp_path: Path) -> None:
    db_path = tmp_path / "traces" / "traces.sqlite3"
    store = ConversationTraceStore(db_path, retention_days=365)
    store.begin(
        trace_id="trace-1",
        request_id="request-1",
        conversation_id="conversation-1",
        student_name="Alice",
        student_email="alice@example.com",
        source="http_sse",
        request_payload={"question": "完整问题"},
    )
    store.append_event("trace-1", "trace-step", {"key": "intent"})
    store.set_response("trace-1", {"answer": "完整回答", "conversation_id": "conversation-1"})
    store.finish("trace-1", status="completed")

    trace = store.get_trace("trace-1")
    assert trace is not None
    assert trace["request"]["question"] == "完整问题"
    assert trace["response"]["answer"] == "完整回答"
    assert [event["event_type"] for event in trace["events"]] == ["request", "trace-step"]
    assert store.stats()["completed_traces"] == 1
    assert os.stat(db_path.parent).st_mode & 0o777 == 0o700
    assert os.stat(db_path).st_mode & 0o777 == 0o600


def test_completed_trace_is_replicated_and_startup_backfills(tmp_path: Path) -> None:
    primary_path = tmp_path / "primary" / "traces.sqlite3"
    archive_path = tmp_path / "workstation" / "traces.sqlite3"
    primary = ConversationTraceStore(primary_path)
    primary.begin(
        trace_id="trace-archive",
        request_id=None,
        conversation_id="conversation-archive",
        student_name="Alice",
        student_email=None,
        source="http",
        request_payload={"question": "archive me"},
    )
    primary.append_event("trace-archive", "model_request", {"messages": ["full"]})
    primary.set_response("trace-archive", {"answer": "archived"})
    primary.finish("trace-archive", status="completed")

    mirrored = ConversationTraceStore(primary_path, archive_db_path=archive_path)
    archived = ConversationTraceStore(archive_path)
    assert mirrored.archive_db_path == archive_path.resolve()
    assert archived.get_trace("trace-archive") == primary.get_trace("trace-archive")
    assert os.stat(archive_path.parent).st_mode & 0o777 == 0o700
    assert os.stat(archive_path).st_mode & 0o777 == 0o600


def test_plain_chat_returns_trace_header_and_persists_trace(
    monkeypatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "traces.sqlite3"
    store = ConversationTraceStore(db_path)
    monkeypatch.setattr(api_module, "get_conversation_trace_store", lambda: store)

    async def fake_answer(request, admin_session_token=None):
        return ChatResponse(
            answer="实验回答",
            owner_name="Twin",
            used_model="test-model",
            conversation_id=request.conversation_id,
            workflow_trace=[
                WorkflowTraceStep(
                    key="response_render",
                    title="render",
                    summary="rendered",
                    detail="rendered response",
                    duration_ms=3,
                )
            ],
        )

    monkeypatch.setattr(api_module.service, "answer", fake_answer)
    response = TestClient(api_module.app).post(
        "/chat",
        json={
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "conversation_id": "conv-http",
            "question": "实验问题",
        },
    )

    assert response.status_code == 200
    trace_id = response.headers["x-trace-id"]
    trace = store.get_trace(trace_id)
    assert trace is not None
    assert trace["status"] == "completed"
    assert trace["request"]["question"] == "实验问题"
    assert trace["response"]["answer"] == "实验回答"
    assert "trace-step" in [event["event_type"] for event in trace["events"]]


def test_sse_chat_persists_stream_chunks_and_background_steps(
    monkeypatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "traces.sqlite3"
    store = ConversationTraceStore(db_path)
    monkeypatch.setattr(api_module, "get_conversation_trace_store", lambda: store)
    monkeypatch.setattr(api_module, "STREAM_CHAT_ANSWER", True)

    async def fake_answer(
        request,
        admin_session_token=None,
        trace_callback=None,
        on_post_answer_complete=None,
        answer_chunk_callback=None,
    ):
        step = WorkflowTraceStep(
            key="llm_answer",
            title="answer",
            summary="answered",
            detail="model answered",
            duration_ms=5,
        )
        trace_callback(step)
        answer_chunk_callback("流式")
        answer_chunk_callback("回答")
        on_post_answer_complete()
        return ChatResponse(
            answer="流式回答",
            owner_name="Twin",
            used_model="test-model",
            conversation_id=request.conversation_id,
            workflow_trace=[step],
        )

    monkeypatch.setattr(api_module.service, "answer", fake_answer)
    response = TestClient(api_module.app).post(
        "/chat?request_id=req-sse",
        json={
            "student_name": "Alice",
            "conversation_id": "conv-sse",
            "question": "流式问题",
        },
    )

    assert response.status_code == 200
    trace = store.get_trace(response.headers["x-trace-id"])
    assert trace is not None
    assert trace["status"] == "completed"
    stream_event = next(
        event for event in trace["events"] if event["event_type"] == "answer_stream"
    )
    assert stream_event["payload"]["chunks"] == ["流式", "回答"]
    assert trace["events"][-1]["event_type"] == "complete"
