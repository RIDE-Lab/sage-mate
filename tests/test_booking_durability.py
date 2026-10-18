from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import subprocess
import sys

import pytest
from sage.runtime.operation_receipts import OperationConflict

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.meeting import MeetingService
from sage_faculty_twin.models import BookingRequest


def settings(tmp_path):
    return AppSettings(runtime_dir=tmp_path, booking_state_path=tmp_path / "booking.sqlite3")


def request(name="Fixture"):
    return BookingRequest(student_name=name, student_email="fixture@example.test", topic="Synthetic",
        preferred_start=datetime(2030, 1, 7, 10), preferred_end=datetime(2030, 1, 7, 11))


def test_restart_and_replay_preserve_booking_and_later_confirmation(tmp_path):
    first = MeetingService(settings(tmp_path))
    original = first.book(request(), operation_id="owner-request")
    assert original.accepted
    first.close()
    second = MeetingService(settings(tmp_path))
    try:
        assert second.list_bookings() == [original.booking]
        second.confirm_booking(original.booking.booking_id)
        assert second.book(request(), operation_id="owner-request") == original
        assert second.list_bookings()[0].status == "已确认"
        with pytest.raises(OperationConflict):
            second.book(request("Different"), operation_id="owner-request")
    finally:
        second.close()


@pytest.mark.parametrize("same_request", [True, False])
def test_concurrent_connections_never_double_book(tmp_path, same_request):
    stores = [MeetingService(settings(tmp_path)) for _ in range(2)]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(store.book, request(), operation_id="same" if same_request else str(i))
                       for i, store in enumerate(stores)]
            responses = [future.result() for future in futures]
        assert len(stores[0].list_bookings()) == 1
        if same_request:
            assert responses[0] == responses[1]
        else:
            assert sum(response.accepted for response in responses) == 1
    finally:
        for store in stores:
            store.close()


def test_rejected_booking_and_reason_survive_restart_and_release_slot(tmp_path):
    store = MeetingService(settings(tmp_path))
    created = store.book(request())
    store.reject_booking(created.booking.booking_id, "Synthetic reason")
    store.close()
    store = MeetingService(settings(tmp_path))
    try:
        assert store.list_bookings()[0].rejection_reason == "Synthetic reason"
        assert store.book(request("Second")).accepted
    finally:
        store.close()


@pytest.mark.parametrize("committed", [False, True])
def test_transaction_failure_or_lost_ack_can_retry_once(tmp_path, monkeypatch, committed):
    store = MeetingService(settings(tmp_path))
    state = store._storage()
    update = state.update_many
    def fail(slots, transition):
        if committed:
            update(slots, transition)
            raise OSError("lost acknowledgement")
        def rollback(values):
            transition(values)
            raise OSError("before commit")
        return update(slots, rollback)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(state, "update_many", fail)
            with pytest.raises(OSError):
                store.book(request(), operation_id="same")
        assert len(store.list_bookings()) == int(committed)
        assert store.book(request(), operation_id="same").accepted
        assert len(store.list_bookings()) == 1
    finally:
        store.close()


def test_confirm_reject_race_has_one_terminal_state(tmp_path):
    stores = [MeetingService(settings(tmp_path)) for _ in range(2)]
    booking = stores[0].book(request()).booking
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            confirm = pool.submit(stores[0].confirm_booking, booking.booking_id)
            reject = pool.submit(stores[1].reject_booking, booking.booking_id, "Synthetic")
            responses = [confirm.result(), reject.result()]
        assert sum(response.accepted for response in responses) == 1
        assert stores[0].list_bookings() == stores[1].list_bookings()
    finally:
        for store in stores:
            store.close()


@pytest.mark.parametrize("after_commit", [False, True])
def test_process_death_on_both_sides_of_commit(tmp_path, after_commit):
    script = '''
import os, sys
from pathlib import Path
from datetime import datetime
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.meeting import MeetingService
from sage_faculty_twin.models import BookingRequest
root = Path(sys.argv[1])
store = MeetingService(AppSettings(runtime_dir=root, booking_state_path=root / 'booking.sqlite3'))
state = store._storage()
update = state.update_many
def crash(slots, transition):
    if sys.argv[2] == 'after':
        update(slots, transition)
        os._exit(23)
    def before(values):
        transition(values)
        os._exit(23)
    return update(slots, before)
state.update_many = crash
store.book(BookingRequest(student_name='Fixture', student_email='fixture@example.test', topic='Synthetic',
    preferred_start=datetime(2030, 1, 7, 10), preferred_end=datetime(2030, 1, 7, 11)),
    operation_id='same')
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path),
                             "after" if after_commit else "before"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 23, result.stderr
    store = MeetingService(settings(tmp_path))
    try:
        assert len(store.list_bookings()) == int(after_commit)
        assert store.book(request(), operation_id="same").accepted
        assert len(store.list_bookings()) == 1
    finally:
        store.close()
