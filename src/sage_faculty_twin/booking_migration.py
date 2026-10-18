"""Offline receipt-schema migration, never a second booking execution path."""
from sage.runtime.operation_receipts import OperationReceipts

from .booking_notifications import BookingNotifications

BOOKING_SLOT = ("twin-bookings-v1", "calendar", "records")
NAMESPACE = "twin-booking-effects-v1"
MIGRATION_ID = "booking-notification-outbox-v1"
EVIDENCE = {
    "historical_smtp_outcome": "unknown",
    "automatic_historical_resend": False,
    "canonical_response": "preserved",
    "current_booking_state": "preserved",
}


def migrate_booking_receipts(state, *, apply=False):
    """Inspect or migrate a quiescent store; caller must stop its writers first.

    Atomic per receipt, restartable across pages. Unknown schemas fail closed.
    Counts contain no booking payloads, identities or email addresses.
    """
    receipts = OperationReceipts(state, namespace=NAMESPACE)
    outbox = BookingNotifications(state)
    counts = {"legacy": 0, "current": 0, "migrated": 0}
    cursor = None
    while True:
        rows, cursor = state.scan_stage_entries(stage_id="sage-operation-receipts-v1",
                                                namespace=NAMESPACE, after_key=cursor, limit=100)
        for row in rows:
            operation_id = row["key"]
            receipt = row["value"]
            if (not isinstance(receipt, dict) or receipt.get("version") != 1
                    or set(receipt) != {"version", "input_digest", "destinations", "result"}):
                raise ValueError("invalid_booking_operation_receipt")
            prepared = outbox.prepare(operation_id, action="historical_unknown")
            new_slots = (BOOKING_SLOT, *prepared.slots)
            current_destinations = [list(slot) for slot in sorted(new_slots)]
            if receipt["destinations"] == current_destinations:
                counts["current"] += 1
            elif receipt["destinations"] == [list(BOOKING_SLOT)]:
                counts["legacy"] += 1
                if apply:
                    receipts.migrate_destinations(operation_id, migration_id=MIGRATION_ID,
                        old_slots=(BOOKING_SLOT,), new_slots=new_slots, evidence=EVIDENCE)
                    counts["migrated"] += 1
            else:
                raise ValueError("unknown_booking_receipt_destination_schema")
        if cursor is None:
            return counts
