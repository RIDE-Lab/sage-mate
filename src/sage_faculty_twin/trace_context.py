from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


TraceEventSink = Callable[[str, Any], None]
_trace_event_sink: ContextVar[TraceEventSink | None] = ContextVar(
    "sage_mate_trace_event_sink", default=None
)


@contextmanager
def bind_trace_event_sink(sink: TraceEventSink) -> Iterator[None]:
    token = _trace_event_sink.set(sink)
    try:
        yield
    finally:
        _trace_event_sink.reset(token)


def emit_trace_event(event_type: str, payload: Any) -> None:
    sink = _trace_event_sink.get()
    if sink is not None:
        sink(event_type, payload)
