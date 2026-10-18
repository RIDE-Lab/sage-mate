"""Isolated fault qualification, not a live write or a passing quality test.

Run with the release's Python and PYTHONPATH. Real native HTTP retry and local
knowledge storage are used; generation is replaced by an injected failure after
the write. Everything is confined to a fresh temporary directory. No model,
email, Slack, or production knowledge call is made. Exit 1 means a required
side-effect invariant is not satisfied; do not count the observation as a pass.
"""
import asyncio
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.http_authority import HttpAuthority
from sage_faculty_twin.http_workflow import HttpIngress, HttpWorkflow
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.meeting import MeetingService
from sage_faculty_twin.models import BookingRequest, ChatRequest, ChatResponse, KnowledgeDocumentCreate


async def qualify(root):
    settings = AppSettings(runtime_dir=root, knowledge_base_dir=root / "knowledge",
                           knowledge_backend="local", sagevdb_embedding_backend="hash")
    knowledge = LocalKnowledgeStore(settings)
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(root / "ingress.sqlite3"))
    attempts = []
    async def authorize(item):
        return True  # Isolated authority; never used against a serving endpoint.
    async def recover(item):
        return None  # The failure occurs before canonical answer commit.
    async def answer(item, *, admission_guard):
        record = knowledge.add_document(KnowledgeDocumentCreate(
            title="Isolated retry fixture", content="Synthetic public test content",
            source_name="isolated-retry-fixture"),
            operation_id=f"knowledge-write:{item.delivery.stream_id}")
        attempts.append(record.document_id)
        if len(attempts) == 1:
            raise ConnectionError("injected after destination commit, before answer commit")
        return ChatResponse(answer="fixture complete", owner_name="test", used_model="not-called")
    workflow = HttpWorkflow(state, authorize=authorize, recover=recover, answer=answer,
                            retry_delay=0)
    try:
        # This fixture bypasses HTTP authentication only at its injected port;
        # no user identity or credential is loaded.
        ingress = HttpIngress(request_id="fixture", request=ChatRequest(
            student_name="Fixture", owner_id="guest:fixture", question="fixture"),
            authority=HttpAuthority(kind="guest", digest="0" * 64))
        await workflow.admit(ingress)
        await workflow.wait(ingress, timeout=10)
        documents = [r for r in knowledge.list_documents() if r.source_name == "isolated-retry-fixture"]
        result = {"producer_attempts": len(attempts), "destination_records": len(documents),
                  "one_write_per_request": len(documents) == 1}
    finally:
        await workflow.aclose()
        state.close()
        knowledge.close()
    meeting = MeetingService(settings)
    request = BookingRequest(student_name="Fixture", student_email="fixture@example.test",
        topic="Isolated fixture", preferred_start=datetime(2030, 1, 7, 10),
        preferred_end=datetime(2030, 1, 7, 11))
    first = meeting.book(request)
    result["booking_initially_accepted"] = first.accepted
    result["booking_survives_reconstruction"] = bool(first.booking and any(
        b.booking_id == first.booking.booking_id for b in MeetingService(settings).list_bookings()))
    return result


if __name__ == "__main__":
    with TemporaryDirectory(prefix="twin-effect-qualification-") as directory:
        result = asyncio.run(qualify(Path(directory)))
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if result["one_write_per_request"] and result["booking_survives_reconstruction"] else 1)
