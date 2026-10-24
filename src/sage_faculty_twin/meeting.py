from __future__ import annotations

from datetime import date, datetime, timedelta
import threading
import weakref
from uuid import uuid4
from zoneinfo import ZoneInfo

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.operation_receipts import OperationReceipts
from sage.runtime.effect_cancellation import EffectFences
from sage.runtime.action_confirmation import ActionConfirmations

from .availability import WeeklyAvailabilityStore
from .booking_notifications import BookingNotifications
from .booking_workflow import BookingWorkflows
from .config import AppSettings
from .models import AvailabilitySchedule, BookingRecord, BookingRequest, BookingResponse, NotificationDeliveryStatus


def _retire_booking_resources(state, notifications):
    if notifications.service is not None:
        notifications.service.retire(state.close)
    else:
        state.close()


class MeetingService:
    def read_proposal(self, owner_id, action_id):
        """Inspect only; neither approval nor effect replay belongs in GET."""
        state = self._storage()
        proposal = ActionConfirmations(state, namespace="twin-booking").read(owner_id, action_id)
        view = {**proposal, "payload": proposal["payload"]["request"]}
        if proposal["status"] == "confirmed":
            saved = OperationReceipts(state, namespace="twin-booking-effects-v1").read("confirmed-booking:" + action_id)
            if saved is not None:
                view["execution_status"] = "committed"
                result = BookingResponse.model_validate(saved)
                result.proposal = view
                if result.booking:
                    current = next((row for row in self.list_bookings() if row.booking_id == result.booking.booking_id), None)
                    if current is not None:
                        result.booking = current
                        result.message = "已核对原预约申请；当前状态：" + current.status + "。不会因此新建预约或重新发送通知。"
                if result.notification is not None:
                    work = self._notifications.journal.read("confirmed-booking:" + action_id)
                    task = work["tasks"]["notify"] if work else None
                    if task and task["status"] == "done" and (task.get("result") or {}).get("status") == "sent":
                        result.notification = result.notification.model_copy(update={"status": "sent",
                            "summary": "通知工作已完成，邮件服务已接受；不代表收件人已阅读。"})
                    elif task and task["status"] in {"failed", "blocked"}:
                        result.notification = result.notification.model_copy(update={"status": "failed",
                            "summary": "通知工作失败；查询不会重新发送邮件。", "detail": task.get("failure_code")})
                return result
            view["execution_status"] = "awaiting_result"
            return BookingResponse(accepted=False, proposal=view,
                message="已收到确认，但执行结果尚未核验；请重试同一申请，不要新建预约。")
        messages = {"pending": "草稿尚未提交；请核对后再明确确认。",
                    "cancelled": "已放弃预约草稿；未提交预约、未发送邮件。",
                    "expired": "草稿已过期，未提交；请重新准备。"}
        return BookingResponse(accepted=False, proposal=view, message=messages[proposal["status"]])

    def prepare_proposal(self, owner_id, request, *, operation_id=None, cancellation_scope=None):
        proposal = ActionConfirmations(self._storage(), namespace="twin-booking").prepare(
            owner_id, {"request": request.model_dump(mode="json"), "workflow_operation_id": operation_id}, operation_id=operation_id,
            guard=self.effect_fences.prepare_guard(cancellation_scope) if cancellation_scope else None)
        view = {**proposal, "payload": proposal["payload"]["request"]}
        messages = {"pending": "预约草稿已生成（生成时未提交预约、未发送邮件）。后续提交、放弃及失败结果请以下方操作状态为准；明确确认后才会提交，提交后仍需老师审核。",
                    "cancelled": "草稿已经放弃，未提交；请重新准备预约。",
                    "confirmed": "此草稿已经确认提交，请核对申请状态；不能据此认定会议或邮件已确认。"}
        return BookingResponse(accepted=False, proposal=view, message=messages[proposal["status"]])

    def decide_proposal(self, owner_id, action_id, *, decision):
        proposal = ActionConfirmations(self._storage(), namespace="twin-booking").resolve(
            owner_id, action_id, decision=decision)
        if decision == "cancel":
            if proposal["payload"]["workflow_operation_id"]:
                self.workflows.discard(owner_id, proposal["payload"]["workflow_operation_id"])
            return BookingResponse(accepted=False, proposal={**proposal, "payload": proposal["payload"]["request"]},
                message="已放弃预约草稿；未提交预约、未发送邮件。这不是取消一条已提交的预约。")
        result = self.book(BookingRequest.model_validate(proposal["payload"]["request"]),
                           operation_id="confirmed-booking:" + proposal["action_id"])
        if proposal["payload"]["workflow_operation_id"]:
            self.workflows.settle(owner_id, proposal["payload"]["workflow_operation_id"], result)
        return result

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._state = None
        self._state_lock = threading.RLock()
        self._closed = False
        self._notifications = None
        self._effect_fences = None
        self._slot = ("twin-bookings-v1", "calendar", "records")
        self._availability_store = WeeklyAvailabilityStore(settings)
        self.workflows = BookingWorkflows(self._storage)

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

    def notification_work_status(self):
        with self._state_lock:
            return self._notifications.work_status() if self._notifications is not None else None

    @property
    def effect_fences(self):
        with self._state_lock:
            state = self._storage()
            if self._effect_fences is None:
                self._effect_fences = EffectFences(state, namespace="twin-preanswer-effects-v1")
            return self._effect_fences

    def _commit(self, operation_id, inputs, decide, *, cancellation_scope=None):
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
            guard = self.effect_fences.prepare_guard(cancellation_scope) if cancellation_scope is not None else None
            saved = receipts.commit(operation_id, inputs=inputs,
                                    slots=(self._slot, *prepared.slots), transition=apply, guard=guard)
            return BookingResponse.model_validate(saved)

    def book(self, request: BookingRequest, *, operation_id: str | None = None,
             cancellation_scope: str | None = None) -> BookingResponse:
        if cancellation_scope is not None and not operation_id:
            raise ValueError("cancellable_booking_requires_operation_identity")
        # Calendar policy and historical records use wall time in the configured
        # booking timezone. Browser datetime-local inputs arrive as UTC instants;
        # never compare those UTC hours with local opening hours or naive records.
        zone = ZoneInfo(self._settings.booking_timezone)
        request = request.model_copy(update={name: (
            getattr(request, name).astimezone(zone).replace(tzinfo=None)
            if getattr(request, name).tzinfo is not None else getattr(request, name))
            for name in ("preferred_start", "preferred_end")})
        # Availability is a captured file-policy snapshot. It is not claimed to
        # be transactionally coupled to concurrent calendar-policy edits.
        slot_start = request.preferred_start.replace(second=0, microsecond=0)
        slot_end = slot_start + timedelta(minutes=self._settings.meeting_duration_minutes)
        available = self._availability_store.is_available(slot_start, slot_end)
        suggestions = self._suggest_slots(slot_start)
        return self._commit(operation_id, {"action": "book", "request": request.model_dump(mode="json")},
            lambda bookings: self._decide_booking(request, bookings, available, suggestions),
            cancellation_scope=cancellation_scope)

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
