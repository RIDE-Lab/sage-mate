from datetime import datetime

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.meeting import MeetingService
from sage_faculty_twin.models import BookingRequest


def config(root):
    return AppSettings(runtime_dir=root, booking_state_path=root / "booking.db",
                       booking_notification_email="admin@example.test")


def request():
    return BookingRequest(student_name="Synthetic", student_email="student@example.test", topic="Fixture",
        preferred_start=datetime(2030, 1, 7, 10), preferred_end=datetime(2030, 1, 7, 11))


class Notifier:
    def __init__(self, failures=0):
        self.calls = []
        self.failures = failures

    def send_booking_request_notification(self, booking):
        self.calls.append(booking.booking_id)
        if len(self.calls) <= self.failures:
            raise OSError("synthetic transport failure")
        return "admin@example.test"


def test_reopen_recovers_notice_without_repeating_booking_producer(tmp_path):
    store = MeetingService(config(tmp_path))
    response = store.book(request(), operation_id="fixture")
    assert response.notification.status == "queued"
    store.close()
    notifier = Notifier()
    store = MeetingService(config(tmp_path))
    try:
        store.start_notifications(notifier)
        store._notifications.service.wait("fixture", timeout=5)
        assert notifier.calls == [response.booking.booking_id]
        assert store.book(request(), operation_id="fixture") == response
        assert len(store.list_bookings()) == 1
        assert store._notifications.journal.read("fixture")["tasks"]["notify"]["result"]["status"] == "sent"
    finally:
        store.close()


def test_uncertain_transport_stops_without_resending_or_rebooking(tmp_path):
    store = MeetingService(config(tmp_path))
    response = store.book(request(), operation_id="fixture")
    notifier = Notifier(failures=1)
    try:
        store._notifications.start(notifier, admin_recipient="admin@example.test", retry_delay=0)
        from sage.runtime.work_service import WorkFailedError
        with pytest.raises(WorkFailedError):
            store._notifications.service.wait("fixture", timeout=5)
        assert store._notifications.journal.read("fixture")["tasks"]["notify"]["failure_code"] == "notification_outcome_unknown"
        assert notifier.calls == [response.booking.booking_id]
        assert len(store.list_bookings()) == 1
    finally:
        store.close()


def test_notification_admission_failure_rolls_back_booking_and_receipt(tmp_path, monkeypatch):
    store = MeetingService(config(tmp_path))
    store._storage()
    prepare = store._notifications.prepare
    def broken(*args, **kwargs):
        if kwargs.get("booking") is not None:
            raise OSError("synthetic outbox admission failure")
        return prepare(*args, **kwargs)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(store._notifications, "prepare", broken)
            with pytest.raises(OSError):
                store.book(request(), operation_id="fixture")
        assert store.list_bookings() == []
        assert store._notifications.journal.records()[0] == []
        assert store.book(request(), operation_id="fixture").accepted
    finally:
        store.close()


def test_changed_admin_destination_fails_before_sending(tmp_path):
    from sage.runtime.work_service import WorkFailedError

    store = MeetingService(config(tmp_path))
    store.book(request(), operation_id="fixture")
    store.close()
    changed = config(tmp_path).model_copy(update={"booking_notification_email": "other@example.test"})
    store = MeetingService(changed)
    notifier = Notifier()
    try:
        store.start_notifications(notifier)
        with pytest.raises(WorkFailedError):
            store._notifications.service.wait("fixture", timeout=5)
        assert notifier.calls == []
        task = store._notifications.journal.read("fixture")["tasks"]["notify"]
        assert task["failure_code"] == "invalid_notification_intent"
        assert len(store.list_bookings()) == 1
    finally:
        store.close()


@pytest.mark.parametrize("action", ["confirm", "reject"])
def test_inconsistent_student_destination_never_calls_transport(tmp_path, action):
    from sage.runtime.work_service import WorkFailedError

    store = MeetingService(config(tmp_path))
    booking = store.book(request(), operation_id="create").booking
    outbox = store._notifications
    prepared = outbox.prepare("inconsistent", action=action,
        booking=booking.model_dump(mode="json"), recipient="different@example.test")
    store._state.update_many(prepared.slots, prepared.apply)
    notifier = Notifier()
    notifier.student_calls = []
    def unexpected(record):
        notifier.student_calls.append(record.booking_id)
        return record.student_email
    notifier.send_booking_approved_notification = unexpected
    notifier.send_booking_rejected_notification = unexpected
    try:
        store.start_notifications(notifier)
        with pytest.raises(WorkFailedError):
            outbox.service.wait("inconsistent", timeout=5)
        assert notifier.student_calls == []
        assert outbox.journal.read("inconsistent")["tasks"]["notify"]["failure_code"] == "invalid_notification_intent"
    finally:
        store.close()


@pytest.mark.parametrize('decision', ['confirm', 'reject'])
def test_admin_decision_retry_has_one_notification_intent(tmp_path, decision):
    store = MeetingService(config(tmp_path))
    try:
        created = store.book(request(), operation_id='new-booking')
        def decide():
            return (store.confirm_booking(created.booking.booking_id) if decision == 'confirm'
                    else store.reject_booking(created.booking.booking_id, 'Synthetic'))
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda _: decide(), range(8)))
        assert all(r == results[0] for r in results)
        rows, _ = store._notifications.journal.records()
        assert len(rows) == 2  # one new-booking mail and one admin-decision mail
        assert len(store.list_bookings()) == 1
    finally: store.close()
