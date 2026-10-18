from __future__ import annotations

from datetime import date, datetime, timedelta
import threading
import weakref
from uuid import uuid4

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.operation_receipts import OperationReceipts

from .availability import WeeklyAvailabilityStore
from .booking_notifications import BookingNotifications
from .config import AppSettings
from .models import AvailabilitySchedule, BookingRecord, BookingRequest, BookingResponse, NotificationDeliveryStatus


def _retire_booking_resources(state, notifications):
    if notifications.service is not None:
        notifications.service.retire(state.close)
    else:
        state.close()


class MeetingService:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._state = None
        self._state_lock = threading.RLock()
        self._closed = False
        self._notifications = None
        self._slot = ("twin-bookings-v1", "calendar", "records")
        self._availability_store = WeeklyAvailabilityStore(settings)

    def _storage(self):
        with self._state_lock:
            if self._closed:
                raise RuntimeError("booking_store_closed")
            if self._state is None:
                path = self._settings.booking_state_path
                path.parent.mkdir(parents=True, exist_ok=True)
                self._state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(path))
                self._notifications = BookingNotifications(self._state)
                self._finalizer = weakref.finalize(self, _retire_booking_resources,
                                                 self._state, self._notifications)
            return self._state

    def close(self):
        if self._notifications is not None:
            self._notifications.close()
        with self._state_lock:
            if not self._closed:
                if self._state is not None:
                    self._finalizer()
                self._closed = True

    def start_notifications(self, notifier):
        with self._state_lock:
            self._storage()
            self._notifications.start(notifier, admin_recipient=self._settings.booking_notification_email.strip())

    def _commit(self, operation_id, inputs, decide):
        """The pure decision sees current bookings under the SAGE transaction.

        Authentication belongs to ingress on every invocation. Caller-supplied
        operation IDs must be captured, owner-scoped identities, not body fields.
        Receipts do not fence another database's lease or external email.
        """
        with self._state_lock:
            state = self._storage()
            receipts = OperationReceipts(state, namespace="twin-booking-effects-v1")
            operation_id = operation_id or str(uuid4())
            action = inputs["action"]
            prepared = self._notifications.prepare(operation_id, action=action)
            def apply(values):
                bookings = [BookingRecord.model_validate(row) for row in (values[self._slot] or [])]
                before = [row.model_dump(mode="json") for row in bookings]
                response = decide(bookings)
                after = [row.model_dump(mode="json") for row in bookings]
                changed = response.accepted and response.booking is not None and before != after
                booking = response.booking.model_dump(mode="json") if changed else None
                recipient = (self._settings.booking_notification_email.strip() if action == "book"
                             else response.booking.student_email.strip()) if changed else None
                work = self._notifications.prepare(operation_id, action=action,
                                                    booking=booking, recipient=recipient)
                if changed:
                    response.notification = NotificationDeliveryStatus(status="queued",
                        recipient=recipient, summary="邮件通知已持久化排队，尚未确认送达。")
                return response.model_dump(mode="json"), {
                    self._slot: after, **work.apply(values)}
            saved = receipts.commit(operation_id, inputs=inputs,
                                    slots=(self._slot, *prepared.slots), transition=apply)
            return BookingResponse.model_validate(saved)

    def book(self, request: BookingRequest, *, operation_id: str | None = None) -> BookingResponse:
        # Availability is a captured file-policy snapshot. It is not claimed to
        # be transactionally coupled to concurrent calendar-policy edits.
        slot_start = request.preferred_start.replace(second=0, microsecond=0)
        slot_end = slot_start + timedelta(minutes=self._settings.meeting_duration_minutes)
        available = self._availability_store.is_available(slot_start, slot_end)
        suggestions = self._suggest_slots(slot_start)
        return self._commit(operation_id, {"action": "book", "request": request.model_dump(mode="json")},
            lambda bookings: self._decide_booking(request, bookings, available, suggestions))

    def _decide_booking(self, request, bookings, available, suggestions):
        requested_minutes = (request.preferred_end - request.preferred_start).total_seconds() / 60
        if requested_minutes < self._settings.meeting_duration_minutes:
            return BookingResponse(
                accepted=False,
                message=(
                    f"预约时间窗口至少需要 {self._settings.meeting_duration_minutes} 分钟。"
                ),
            )

        slot_start = request.preferred_start.replace(second=0, microsecond=0)
        slot_end = slot_start + timedelta(minutes=self._settings.meeting_duration_minutes)

        if not self._is_within_booking_hours(slot_start, slot_end):
            return BookingResponse(
                accepted=False,
                message="所选时间不在可预约时段内。",
                alternative_slots=suggestions,
            )

        if not available:
            return BookingResponse(
                accepted=False,
                message="所选时间不在本周开放预约时段内。",
                alternative_slots=suggestions,
            )

        conflicting_booking = self._find_conflicting_booking(slot_start, slot_end, bookings)
        if conflicting_booking is not None:
            return BookingResponse(
                accepted=False,
                message=self._build_conflict_message(conflicting_booking),
                alternative_slots=suggestions,
            )

        booking = BookingRecord(
            booking_id=str(uuid4()),
            student_name=request.student_name,
            student_email=request.student_email,
            topic=request.topic,
            start_at=slot_start,
            end_at=slot_end,
            status="待确认",
            rejection_reason=None,
        )
        bookings.append(booking)
        return BookingResponse(
            accepted=True,
            message="预约请求已提交，等待管理员确认。",
            booking=booking,
        )

    def list_bookings(self, status: str | None = None) -> list[BookingRecord]:
        with self._state_lock:
            bookings = [BookingRecord.model_validate(row)
                        for row in (self._storage().peek(self._slot) or [])]
        if status is None:
            return bookings
        return [booking for booking in bookings if booking.status == status]

    def confirm_booking(self, booking_id: str, *, operation_id=None) -> BookingResponse:
        return self._commit(operation_id, {"action": "confirm", "booking_id": booking_id},
                            lambda bookings: self._confirm(booking_id, bookings))

    def _confirm(self, booking_id, bookings):
        booking = next((item for item in bookings if item.booking_id == booking_id), None)
        if booking is None:
            return BookingResponse(accepted=False, message="未找到对应的预约请求。")

        if booking.status == "已拒绝":
            return BookingResponse(
                accepted=False,
                message="该预约已被拒绝，不能再确认。",
                booking=booking,
            )

        if booking.status == "已确认":
            return BookingResponse(
                accepted=True,
                message="该预约已处于确认状态。",
                booking=booking,
            )

        booking.status = "已确认"
        return BookingResponse(
            accepted=True,
            message="预约已确认。",
            booking=booking,
        )

    def reject_booking(self, booking_id: str, rejection_reason: str | None = None,
                       *, operation_id=None) -> BookingResponse:
        return self._commit(operation_id, {"action": "reject", "booking_id": booking_id,
                            "rejection_reason": rejection_reason},
                            lambda bookings: self._reject(booking_id, rejection_reason, bookings))

    def _reject(self, booking_id, rejection_reason, bookings):
        booking = next((item for item in bookings if item.booking_id == booking_id), None)
        if booking is None:
            return BookingResponse(accepted=False, message="未找到对应的预约请求。")

        if booking.status == "已拒绝":
            return BookingResponse(
                accepted=True,
                message="该预约已处于拒绝状态。",
                booking=booking,
            )

        if booking.status == "已确认":
            return BookingResponse(
                accepted=False,
                message="该预约已经确认，不能再拒绝。",
                booking=booking,
            )

        booking.status = "已拒绝"
        booking.rejection_reason = rejection_reason.strip() if rejection_reason else None
        return BookingResponse(
            accepted=True,
            message="预约已拒绝。",
            booking=booking,
        )

    def get_availability_schedule(self) -> AvailabilitySchedule:
        return self._availability_store.load()

    def get_previous_week_availability_template(self, week_of: date | None = None) -> AvailabilitySchedule:
        return self._availability_store.load_previous_week_template(week_of)

    def update_availability_schedule(self, schedule: AvailabilitySchedule | dict) -> AvailabilitySchedule:
        normalized = AvailabilitySchedule.model_validate(schedule)
        return self._availability_store.save(normalized)

    def describe_current_availability(self) -> str:
        return self._availability_store.describe_for_prompt()

    def _is_within_booking_hours(self, start_at: datetime, end_at: datetime) -> bool:
        if start_at.date() != end_at.date():
            return False
        return (
            self._settings.booking_start_hour <= start_at.hour < self._settings.booking_end_hour
            and self._settings.booking_start_hour < end_at.hour <= self._settings.booking_end_hour
        )

    def _find_conflicting_booking(self, start_at: datetime, end_at: datetime,
                                  bookings: list[BookingRecord]) -> BookingRecord | None:
        for booking in bookings:
            if booking.status == "已拒绝":
                continue
            if start_at < booking.end_at and end_at > booking.start_at:
                return booking
        return None

    def _build_conflict_message(self, booking: BookingRecord) -> str:
        if booking.status == "待确认":
            return "所选时间已被其他学生提交预约申请，正在等待管理员确认，请更换时间。"
        return "所选时间已被其他学生预约，请更换时间。"

    def _suggest_slots(self, anchor: datetime) -> list[str]:
        bookings = [booking for booking in self.list_bookings() if booking.status != "已拒绝"]
        weekly_suggestions = self._availability_store.suggest_slots(
            anchor,
            self._settings.meeting_duration_minutes,
            [(booking.start_at, booking.end_at) for booking in bookings],
        )
        if weekly_suggestions:
            return weekly_suggestions

        suggestions: list[str] = []
        for offset in range(1, 4):
            candidate = anchor + timedelta(hours=offset)
            end_at = candidate + timedelta(minutes=self._settings.meeting_duration_minutes)
            if self._is_within_booking_hours(candidate, end_at) and self._find_conflicting_booking(
                candidate,
                end_at,
                bookings,
            ) is None:
                suggestions.append(candidate.isoformat())
        return suggestions
