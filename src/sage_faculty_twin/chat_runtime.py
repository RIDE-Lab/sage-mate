"""Persistent SAGE chat graph; application ports are scoped to each request.

The graph has no automatic replay: booking/knowledge writes require destination
idempotency before they may be replayed. SAGE owns execution and cancellation.
"""

from contextvars import ContextVar

from sage.runtime.cancellation import RequestCancellation, bind_request_cancellation
from sage.runtime.flownet import (
    FlowRequestOutcomeError, bootstrap_runtime, build_runtime_client, flow, stateless,
)

from .request_context import register_request_cancel_callback, raise_if_request_cancelled


_ports = ContextVar("twin_chat_ports")


@stateless(uri="stateless://twin/chat/bootstrap", namespace="twin")
def bootstrap(request):
    return _ports.get()["bootstrap"].execute(request)


@stateless(uri="stateless://twin/chat/understand", namespace="twin")
def understand(context):
    return _ports.get()["understand"].execute(context)


@stateless(uri="stateless://twin/chat/prepare-booking", namespace="twin")
def prepare_booking(context):
    return _ports.get()["prepare_booking"].execute(context)


@stateless(uri="stateless://twin/chat/execute-booking", namespace="twin")
def execute_booking(context):
    return _ports.get()["execute_booking"].execute(context)


def _booking_requested(context):
    # Intent is captured by understand and is not modified by booking stages.
    # Both native guards read the same decision, never mutable route/state.
    intent = context.interaction_intent
    if intent is None:
        raise ValueError("chat_branch_requires_captured_intent")
    return intent.action == "book_meeting"


@stateless(uri="stateless://twin/chat/booking-requested", namespace="twin")
def booking_requested(context):
    return _booking_requested(context)


@stateless(uri="stateless://twin/chat/booking-not-requested", namespace="twin")
def booking_not_requested(context):
    return not _booking_requested(context)


def _booking_ready(context):
    # The captured decision remains stable after settlement and across guards.
    decision = context.booking_decision
    return decision is not None and decision.request is not None


@stateless(uri="stateless://twin/chat/booking-ready", namespace="twin")
def booking_ready(context):
    return _booking_ready(context)


@stateless(uri="stateless://twin/chat/booking-incomplete", namespace="twin")
def booking_incomplete(context):
    return not _booking_ready(context)


@stateless(uri="stateless://twin/chat/write-knowledge", namespace="twin")
def write_knowledge(context):
    return _ports.get()["write_knowledge"].execute(context)


@stateless(uri="stateless://twin/chat/retrieve", namespace="twin")
def retrieve(context):
    return _ports.get()["retrieve"].execute(context)


@stateless(uri="stateless://twin/chat/prompt", namespace="twin")
def prompt(context):
    return _ports.get()["prompt"].execute(context)


@stateless(uri="stateless://twin/chat/answer", namespace="twin")
def answer(context):
    return _ports.get()["answer"].execute(context)


@stateless(uri="stateless://twin/chat/render", namespace="twin")
def render(context):
    return _ports.get()["render"].execute(context), context


@flow(uri="flow://twin/chat", namespace="twin")
def chat_flow(init_stream):
    understood = init_stream.map(bootstrap, operator_key="bootstrap").map(understand, operator_key="understand")
    prepared = understood.filter(booking_requested).map(prepare_booking, operator_key="prepare-booking")
    booking = prepared.filter(booking_ready).map(execute_booking, operator_key="execute-booking").connect(
        prepared.filter(booking_incomplete))
    selected = booking.connect(understood.filter(booking_not_requested))
    return (selected.map(write_knowledge, operator_key="write-knowledge").map(retrieve, operator_key="retrieve")
            .map(prompt, operator_key="prompt").map(answer, operator_key="answer").map(render, operator_key="render"))


class ChatRuntime:
    def __init__(self):
        self._bootstrap = bootstrap_runtime(node_mode="local")
        try:
            client = build_runtime_client(owner="twin", runtime_host=self._bootstrap.runtime_host)
            self._endpoint = chat_flow.bind("twin-chat-in", "twin-chat-out").endpoint(
                client=client, reuse_existing=False,
            )
        except BaseException:
            self._bootstrap.shutdown()
            raise

    def answer(self, request, ports, *, timeout):
        cancellation = RequestCancellation()
        unregister = register_request_cancel_callback(cancellation.cancel)
        token = _ports.set(ports)
        try:
            raise_if_request_cancelled()
            with bind_request_cancellation(cancellation):
                outputs = self._endpoint.call(request, timeout=timeout)
        except FlowRequestOutcomeError:
            raise_if_request_cancelled()
            raise
        finally:
            _ports.reset(token)
            unregister()
        if len(outputs) != 1:
            raise RuntimeError("invalid_chat_output_count")
        return outputs[0]

    def close(self):
        self._endpoint.close()
        self._bootstrap.shutdown()
