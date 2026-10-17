"""Twin event schemas and SSE framing over SAGE durable delivery."""

import json
from uuid import uuid4

from sage.runtime.event_journal import EventJournal
from sage.runtime.event_subscription import EventSubscription

from .chat_delivery import DeliveredChatResponse


class WorkflowEventBroker:
    def __init__(self, state_provider, *, heartbeat_provider=lambda: 15.0):
        self._state_provider = state_provider
        self._heartbeat_provider = heartbeat_provider

    def _journal(self):
        return EventJournal(self._state_provider(), namespace="twin-workflow-events-v1")

    def validate_cursor(self, request_id, after):
        self._journal().read(request_id, after=after, limit=1)

    async def stream(self, request_id, *, after=0):
        subscription = EventSubscription(self._journal(), heartbeat_interval=self._heartbeat_provider())
        iterator = subscription.stream(request_id, after=after)
        try:
            async for event in iterator:
                if event is None:
                    yield 'data: {"type": "keepalive"}\n\n'
                else:
                    yield f'id: {event["sequence"]}\ndata: {json.dumps(event["payload"], ensure_ascii=False)}\n\n'
        finally:
            await iterator.aclose()

    def publish_step(self, request_id, step):
        self._journal().append(request_id, event_id=uuid4().hex,
            payload={"type": "trace-step", "step": step.model_dump(mode="json")})

    def publish_answer_done(self, request_id, response):
        if not isinstance(response, DeliveredChatResponse):
            raise TypeError("answer_done requires a DeliveredChatResponse")
        self._journal().append(request_id, event_id="answer_done", payload={
            "type": "answer_done", "response": response.model_dump(mode="json")})

    def publish_error(self, request_id, message):
        journal = self._journal()
        prepared = journal.prepare_append(request_id, event_id="terminal", terminal=True,
                                           payload={"type": "error", "message": message})

        def apply(old):
            # Transport failure cannot revoke an admitted durable turn. This
            # check shares the append transaction, including racing admissions.
            if old and any(event["id"] == "answer_done" for event in old["events"]):
                return old
            return prepared.transform(old)

        self._state_provider().update(prepared.slot, apply)

    def finish_immediate(self, request_id, response, *, guard=None):
        """Finish a validated early return, or leave an admitted turn to its worker."""
        if not isinstance(response, DeliveredChatResponse):
            raise TypeError("answer_done requires a DeliveredChatResponse")
        journal = self._journal()
        answer = journal.prepare_append(request_id, event_id="answer_done", payload={
            "type": "answer_done", "response": response.model_dump(mode="json")})
        terminal = journal.prepare_append(request_id, event_id="terminal", terminal=True,
                                          payload={"type": "complete"})

        def apply(old):
            existing = next((event for event in (old or {}).get("events", [])
                             if event["id"] == "answer_done"), None)
            if existing is not None:
                saved = existing["payload"]["response"]
                if saved["answer"] != response.answer or saved.get("exchange_id") != response.exchange_id:
                    raise ValueError("delivery_answer_identity_conflict")
                return old
            return terminal.transform(answer.transform(old))

        state = self._state_provider()
        if guard is not None:
            guard.validate_store(state)

        def commit(values):
            updates = guard.apply(values) if guard is not None else {}
            updates[answer.slot] = apply(values[answer.slot])
            return updates

        state.update_many((answer.slot, *((guard.slot,) if guard is not None else ())), commit)

    def publish_complete(self, request_id):
        self._journal().append(request_id, event_id="terminal", terminal=True,
                               payload={"type": "complete"})
