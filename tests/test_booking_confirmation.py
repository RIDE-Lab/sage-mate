from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from sage.runtime.action_confirmation import ConfirmationError

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.meeting import MeetingService
from sage_faculty_twin.models import BookingRequest


@pytest.fixture
def meeting(tmp_path):
    service = MeetingService(AppSettings(runtime_dir=tmp_path,
        booking_state_path=tmp_path / "book.sqlite3", availability_schedule_path=tmp_path / "availability.json"))
    yield service
    service.close()


def request():
    return BookingRequest(student_name="Synthetic", student_email="fixture@example.test", topic="Synthetic only",
                          preferred_start=datetime(2030, 1, 7, 10), preferred_end=datetime(2030, 1, 7, 11))


def outbox(meeting):
    return meeting._notifications.journal.records()[0]


def test_prepare_cancel_never_creates_or_notifies(meeting):
    draft = meeting.prepare_proposal("guest:a", request())
    assert draft.proposal["status"] == "pending"
    assert not meeting.list_bookings() and not outbox(meeting)
    action = draft.proposal["action_id"]
    with pytest.raises(ConfirmationError, match="not_found"):
        meeting.decide_proposal("guest:b", action, decision="confirm")
    assert meeting.decide_proposal("guest:a", action, decision="cancel").proposal["status"] == "cancelled"
    with pytest.raises(ConfirmationError, match="cancelled"):
        meeting.decide_proposal("guest:a", action, decision="confirm")
    assert not meeting.list_bookings() and not outbox(meeting)


def test_explicit_confirmation_is_replay_safe_after_lost_ack(meeting, monkeypatch):
    draft = meeting.prepare_proposal("guest:a", request())
    action = draft.proposal["action_id"]
    original = meeting.book
    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("synthetic lost acknowledgement")
    monkeypatch.setattr(meeting, "book", lost_ack)
    with pytest.raises(ConnectionError):
        meeting.decide_proposal("guest:a", action, decision="confirm")
    monkeypatch.setattr(meeting, "book", original)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: meeting.decide_proposal("guest:a", action, decision="confirm"), range(2)))
    assert results[0] == results[1]
    assert results[0].booking.status == "待确认"
    assert len(meeting.list_bookings()) == len(outbox(meeting)) == 1
    with pytest.raises(ConfirmationError, match="already_confirmed"):
        meeting.decide_proposal("guest:a", action, decision="cancel")


def test_rejected_confirmed_request_does_not_notify(meeting):
    invalid = request().model_copy(update={"preferred_end": datetime(2030, 1, 7, 10, 1)})
    draft = meeting.prepare_proposal("guest:a", invalid)
    result = meeting.decide_proposal("guest:a", draft.proposal["action_id"], decision="confirm")
    assert not result.accepted and result.booking is None
    assert not meeting.list_bookings()
    key = "confirmed-booking:" + draft.proposal["action_id"]
    class ForbiddenNotifier:
        def send_booking_request_notification(self, booking):
            raise AssertionError("rejected request must never send")
    meeting.start_notifications(ForbiddenNotifier())
    meeting._notifications.service.wait(key, timeout=5)
    assert meeting._notifications.journal.read(key)["tasks"]["notify"]["result"]["status"] == "skipped"


def test_browser_utc_input_uses_calendar_timezone_and_conflicts_with_local_record(meeting):
    utc = request().model_copy(update={
        "preferred_start": datetime.fromisoformat("2030-01-07T02:00:00+00:00"),
        "preferred_end": datetime.fromisoformat("2030-01-07T03:00:00+00:00")})
    proposal = meeting.prepare_proposal("guest:a", utc).proposal
    result = meeting.decide_proposal("guest:a", proposal["action_id"], decision="confirm")
    assert result.accepted and result.booking.start_at == datetime(2030, 1, 7, 10)
    conflicting = meeting.prepare_proposal("guest:b", request()).proposal
    rejected = meeting.decide_proposal("guest:b", conflicting["action_id"], decision="confirm")
    assert not rejected.accepted and len(meeting.list_bookings()) == 1
