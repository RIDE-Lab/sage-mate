from datetime import datetime

import pytest
from sage.runtime.operation_receipts import OperationReceipts

from sage_faculty_twin.booking_migration import BOOKING_SLOT, NAMESPACE, migrate_booking_receipts
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.meeting import MeetingService
from sage_faculty_twin.models import BookingRecord, BookingRequest, BookingResponse


def test_legacy_receipt_migration_replays_without_email_or_state_rollback(tmp_path):
    settings = AppSettings(runtime_dir=tmp_path, booking_state_path=tmp_path / "booking.db")
    service = MeetingService(settings)
    request = BookingRequest(student_name="Fixture", student_email="fixture@example.test", topic="Synthetic",
                             preferred_start=datetime(2030, 1, 7, 10), preferred_end=datetime(2030, 1, 7, 11))
    booking = BookingRecord(booking_id="historical", student_name=request.student_name,
        student_email=request.student_email, topic=request.topic, start_at=request.preferred_start,
        end_at=request.preferred_end, status="待确认")
    original = BookingResponse(accepted=True, message="historical canonical response", booking=booking)
    state = service._storage()
    OperationReceipts(state, namespace=NAMESPACE).commit("old-request",
        inputs={"action": "book", "request": request.model_dump(mode="json")}, slots=(BOOKING_SLOT,),
        transition=lambda _: (original.model_dump(mode="json"),
                               {BOOKING_SLOT: [booking.model_dump(mode="json")]}))
    state.update(BOOKING_SLOT, lambda rows: [{**rows[0], "status": "已确认"}])
    assert migrate_booking_receipts(state) == {"legacy": 1, "current": 0, "migrated": 0}
    assert migrate_booking_receipts(state, apply=True) == {"legacy": 1, "current": 0, "migrated": 1}
    assert migrate_booking_receipts(state, apply=True) == {"legacy": 0, "current": 1, "migrated": 0}
    service.close()
    service = MeetingService(settings)
    try:
        assert service.book(request, operation_id="old-request") == original
        assert service.list_bookings()[0].status == "已确认"
        assert service._notifications.journal.records()[0] == []
        audits, _ = service._state.scan_stage_entries(stage_id="sage-operation-migrations-v1", namespace=NAMESPACE)
        assert len(audits) == 1
        assert audits[0]["value"]["evidence"]["historical_smtp_outcome"] == "unknown"
    finally:
        service.close()


def test_unknown_receipt_schema_is_not_guessed(tmp_path):
    service = MeetingService(AppSettings(runtime_dir=tmp_path, booking_state_path=tmp_path / "booking.db"))
    state = service._storage()
    state.update(("sage-operation-receipts-v1", NAMESPACE, "unknown"), lambda _: {
        "version": 1, "input_digest": "fixture", "destinations": [["other", "n", "k"]], "result": {}})
    try:
        with pytest.raises(ValueError, match="unknown_booking_receipt_destination_schema"):
            migrate_booking_receipts(state, apply=True)
    finally:
        service.close()
