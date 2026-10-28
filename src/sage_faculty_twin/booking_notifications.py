"""Booking notification port over SAGE work admission and recovery.

SMTP has no idempotent receipt API: ambiguous delivery stops for reconciliation. The worker
never regenerates a booking or asks a model to recover its immutable intent.
"""
from sage.runtime.external_effect import ExternalEffects, EffectOutcomeUnknown
from sage.runtime.work_dispatcher import WorkFailure
from sage.runtime.work_journal import WorkJournal
from sage.runtime.work_service import WorkService

from .models import BookingRecord


class BookingNotifications:
    def __init__(self, state):
        self.journal = WorkJournal(state, namespace="twin-booking-notifications-v1")
        self.service = None
        self.effects = ExternalEffects(state, namespace="twin-booking-email-v1")

    def prepare(self, operation_id, *, action, booking=None, recipient=None):
        return self.journal.prepare_commit(operation_id, result={"action": action}, tasks={
            "notify": {"action": action, "booking": booking, "recipient": recipient}})

    def start(self, notifier, *, admin_recipient, retry_delay=5):
        if self.service is None:
            def handle(lease):
                payload = lease.payload
                if payload["booking"] is None:
                    return {"status": "skipped"}
                booking = BookingRecord.model_validate(payload["booking"])
                action = payload["action"]
                # Validate the immutable destination before invoking any external
                # transport; checking its returned receipt is too late to prevent
                # a misdirected message from inconsistent persisted intent.
                if action in {"confirm", "reject"} and payload["recipient"] != booking.student_email.strip():
                    raise ValueError("booking_notification_destination_mismatch")
                if action == "book" and payload["recipient"] != admin_recipient:
                    raise ValueError("booking_notification_destination_changed")
                methods = {"book": "send_booking_request_notification",
                           "confirm": "send_booking_approved_notification",
                           "reject": "send_booking_rejected_notification"}
                if action not in methods:
                    raise ValueError("invalid_booking_notification_action")
                def deliver():
                    recipient = getattr(notifier, methods[action])(booking)
                    if recipient != payload["recipient"]:
                        raise ValueError("booking_notification_receipt_destination_mismatch")
                    return {"status": "sent", "recipient": recipient}
                # A pre-upgrade attempt may already have sent without a durable
                # effect claim. Never infer non-delivery from a missing receipt.
                if lease.attempt > 1 and self.journal._store.peek(
                        ("sage-external-effects-v1", "twin-booking-email-v1", lease.request_id)) is None:
                    raise EffectOutcomeUnknown("legacy_notification_outcome_unknown")
                return self.effects.execute(lease.request_id, payload, deliver,
                                            guard=self.journal.prepare_guard(lease))

            def failure(lease, error):
                if isinstance(error, EffectOutcomeUnknown):
                    return WorkFailure("notification_outcome_unknown")
                if isinstance(error, ValueError):
                    return WorkFailure("invalid_notification_intent")
                if lease.attempt >= 5:
                    return WorkFailure("notification_retry_exhausted")
                return WorkFailure("notification_delivery_failed", min(60, retry_delay * 2 ** (lease.attempt - 1)))

            self.service = WorkService(self.journal, handle, max_workers=1,
                                       poll_interval=0.05, failure_policy=failure)
        self.service.start()

    def close(self):
        if self.service is not None:
            self.service.close()

    def work_status(self):
        return self.service.status() if self.service is not None else None
