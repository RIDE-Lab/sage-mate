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
    assert "以下方操作状态为准" in draft.message
    assert "待你确认" not in draft.message  # historical body must not contradict later cancellation
    assert not meeting.list_bookings() and not outbox(meeting)
    action = draft.proposal["action_id"]
    with pytest.raises(ConfirmationError, match="not_found"):
        meeting.decide_proposal("guest:b", action, decision="confirm")
    assert meeting.decide_proposal("guest:a", action, decision="cancel").proposal["status"] == "cancelled"
    with pytest.raises(ConfirmationError, match="cancelled"):
        meeting.decide_proposal("guest:a", action, decision="confirm")
    assert not meeting.list_bookings() and not outbox(meeting)


def test_read_cancelled_and_confirmed_restart_never_replays(meeting, monkeypatch):
    cancelled = meeting.prepare_proposal("guest:a", request()).proposal["action_id"]
    meeting.decide_proposal("guest:a", cancelled, decision="cancel")
    accepted = meeting.prepare_proposal("guest:a", request()).proposal["action_id"]
    meeting.decide_proposal("guest:a", accepted, decision="confirm")
    waiting = meeting.prepare_proposal("guest:a", request()).proposal["action_id"]
    # Reproduce approval committed but the effect was not started: GET must not resume it.
    from sage.runtime.action_confirmation import ActionConfirmations
    ActionConfirmations(meeting._storage(), namespace="twin-booking").resolve("guest:a", waiting, decision="confirm")
    settings = meeting._settings
    meeting.close()
    restarted = MeetingService(settings)
    try:
        monkeypatch.setattr(restarted, "book", lambda *args, **kwargs: pytest.fail("GET must not execute"))
        for _ in range(3):
            assert restarted.read_proposal("guest:a", cancelled).proposal["status"] == "cancelled"
            result = restarted.read_proposal("guest:a", accepted)
            assert result.accepted and result.proposal["execution_status"] == "committed"
            assert restarted.read_proposal("guest:a", waiting).proposal["execution_status"] == "awaiting_result"
        with pytest.raises(ConfirmationError, match="not_found"):
            restarted.read_proposal("guest:b", accepted)
        assert len(restarted.list_bookings()) == len(outbox(restarted)) == 1
    finally:
        restarted.close()


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
    sent = []
    class CounterOnly:
        def send_booking_request_notification(self, booking):
            sent.append(booking.booking_id)
            return meeting._settings.booking_notification_email.strip()
    meeting.start_notifications(CounterOnly())
    meeting._notifications.service.wait("confirmed-booking:" + action, timeout=5)
    for _ in range(3):
        inspected = meeting.read_proposal("guest:a", action)
        assert inspected.booking.booking_id == results[0].booking.booking_id
        assert inspected.notification.status == "sent"
        meeting.decide_proposal("guest:a", action, decision="confirm")
    assert sent == [results[0].booking.booking_id]
    assert len(outbox(meeting)) == 1
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


def test_two_connections_stale_cancel_confirm_and_expired_replay(meeting, monkeypatch):
    from sage.runtime.action_confirmation import ActionConfirmations
    import sage_faculty_twin.meeting as module
    now = [100]
    monkeypatch.setattr(module, "ActionConfirmations", lambda store, **kwargs:
        ActionConfirmations(store, clock=lambda: now[0], **kwargs))
    sibling = MeetingService(meeting._settings)
    try:
        action = meeting.prepare_proposal("guest:a", request()).proposal["action_id"]
        assert sibling.read_proposal("guest:a", action).proposal["status"] == "pending"
        meeting.decide_proposal("guest:a", action, decision="cancel")
        with pytest.raises(ConfirmationError, match="already_cancelled"):
            sibling.decide_proposal("guest:a", action, decision="confirm")
        assert not sibling.list_bookings() and not outbox(meeting)
        action = meeting.prepare_proposal("guest:a", request()).proposal["action_id"]
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda service:
                service.decide_proposal("guest:a", action, decision="confirm"), [meeting, sibling]))
        assert results[0].booking.booking_id == results[1].booking.booking_id
        with pytest.raises(ConfirmationError, match="already_confirmed"):
            sibling.decide_proposal("guest:a", action, decision="cancel")
        expired = meeting.prepare_proposal("guest:a", request(), operation_id="expires").proposal
        now[0] = expired["expires_at"] + 1
        assert sibling.read_proposal("guest:a", expired["action_id"]).proposal["status"] == "expired"
        assert sibling.prepare_proposal("guest:a", request(), operation_id="expires").proposal["status"] == "expired"
        with pytest.raises(ConfirmationError, match="expired"):
            sibling.decide_proposal("guest:a", expired["action_id"], decision="confirm")
        assert sibling.read_proposal("guest:a", action).proposal["execution_status"] == "committed"
        assert len(sibling.list_bookings()) == len(outbox(meeting)) == 1
    finally:
        sibling.close()


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
