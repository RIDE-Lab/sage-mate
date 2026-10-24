"""Owner-scoped booking decisions committed through SAGE operation receipts.

Parsing belongs before capture. Capturing a turn atomically saves its immutable
decision and the new draft. Completing an effect only settles that turn's draft;
an older retry cannot erase a later turn. This adapter does not own a worker or
coordinate another database's ingress lease.
"""

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sage.runtime.operation_receipts import OperationReceipts

from .models import BookingRequest, BookingResponse


class BookingWorkflowState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_name: str
    student_email: str | None = None
    topic: str | None = None
    preferred_start: datetime | None = None
    preferred_end: datetime | None = None


class BookingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str
    state: BookingWorkflowState
    pending_fields: list[str]
    request: BookingRequest | None


class BookingWorkflows:
    def __init__(self, state_provider):
        self._state_provider = state_provider

    @staticmethod
    def _identity(owner_id, operation_id):
        if not isinstance(owner_id, str) or not owner_id or owner_id != owner_id.strip():
            raise ValueError("booking_workflow_requires_verified_owner")
        if not isinstance(operation_id, str) or not operation_id or operation_id != operation_id.strip():
            raise ValueError("booking_workflow_requires_operation_identity")
        return hashlib.sha256(json.dumps([owner_id, operation_id], separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _draft_slot(owner_id, conversation_id):
        if not isinstance(conversation_id, str) or not conversation_id or conversation_id != conversation_id.strip():
            raise ValueError("booking_workflow_requires_conversation")
        return ("twin-booking-drafts-v1", owner_id, conversation_id)

    def peek(self, owner_id, conversation_id):
        # Unattributed internal/legacy requests cannot discover a verified
        # owner's draft, nor claim it by a name or email address.
        if owner_id is None:
            return None
        self._identity(owner_id, "read")
        saved = self._state_provider().peek(self._draft_slot(owner_id, conversation_id))
        return BookingWorkflowState.model_validate(saved["state"]) if saved else None

    def decision(self, owner_id, operation_id):
        if owner_id is None:
            return None
        identity = self._identity(owner_id, operation_id)
        saved = self._state_provider().peek(("twin-booking-plans-v1", owner_id, identity))
        return BookingDecision.model_validate(saved) if saved else None

    def capture(self, owner_id, conversation_id, operation_id, *, inputs, fields, topic_default=None, guard=None):
        identity = self._identity(owner_id, operation_id)
        draft_slot = self._draft_slot(owner_id, conversation_id)
        plan_slot = ("twin-booking-plans-v1", owner_id, identity)
        # Detach parser output before entering the pure state transition.
        fields = dict(fields)

        def decide(values):
            previous = values[draft_slot]
            state = BookingWorkflowState.model_validate({
                **(previous["state"] if previous else {}), **fields,
            })
            # Suggested defaults never replace a previously captured value,
            # including a draft changed after the caller's preliminary read.
            if not state.topic and topic_default:
                state.topic = topic_default
            missing = [name for name in ("student_email", "preferred_start", "topic")
                       if not getattr(state, name)]
            request = None if missing else BookingRequest.model_validate(state.model_dump())
            plan = BookingDecision(conversation_id=conversation_id, state=state,
                                   pending_fields=missing, request=request).model_dump(mode="json")
            return plan, {plan_slot: plan,
                          draft_slot: {"operation_id": identity, "state": state.model_dump(mode="json")}}

        result = OperationReceipts(self._state_provider(), namespace="twin-booking-plans-v1").commit(
            identity, inputs={"conversation_id": conversation_id, "request": inputs},
            slots=(draft_slot, plan_slot), transition=decide, guard=guard,
        )
        return BookingDecision.model_validate(result)

    def settle(self, owner_id, operation_id, response: BookingResponse, *, guard=None):
        identity = self._identity(owner_id, operation_id)
        plan = self.decision(owner_id, operation_id)
        if plan is None or plan.request is None:
            raise ValueError("booking_workflow_requires_captured_executable_plan")
        draft_slot = self._draft_slot(owner_id, plan.conversation_id)
        next_state = None if response.accepted else plan.state.model_copy(
            update={"preferred_start": None, "preferred_end": None}).model_dump(mode="json")

        def finish(values):
            current = values[draft_slot]
            owned = bool(current and current["operation_id"] == identity)
            replacement = ({"operation_id": identity, "state": next_state}
                           if next_state is not None else None) if owned else current
            return {"settled_current_draft": owned}, {draft_slot: replacement}

        return OperationReceipts(self._state_provider(), namespace="twin-booking-draft-settle-v1").commit(
            identity, inputs=response.model_dump(mode="json"), slots=(draft_slot,), transition=finish, guard=guard,
        )

    def discard(self, owner_id, operation_id):
        identity = self._identity(owner_id, operation_id)
        plan = self.decision(owner_id, operation_id)
        if plan is None:
            return
        slot = self._draft_slot(owner_id, plan.conversation_id)
        def discard(values):
            current = values[slot]
            return {slot: None if current and current["operation_id"] == identity else current}
        self._state_provider().update_many((slot,), discard)
