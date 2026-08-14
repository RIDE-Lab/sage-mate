"""Request-scoped cancellation and deadline propagation.

This module deliberately has no service/API dependencies so synchronous work
running through ``asyncio.to_thread`` can observe the same absolute deadline as
the HTTP request that started it.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
import threading


_cancel_event: ContextVar[threading.Event | None] = ContextVar(
    "sage_mate_request_cancel_event", default=None
)
_deadline_at: ContextVar[float | None] = ContextVar(
    "sage_mate_request_deadline_at", default=None
)


class RequestCancelledError(RuntimeError):
    """Raised after disconnect, explicit cancellation, or deadline expiry."""


@contextmanager
def request_cancellation_scope(
    event: threading.Event,
    *,
    deadline_at: float | None = None,
):
    cancel_token = _cancel_event.set(event)
    deadline_token = _deadline_at.set(deadline_at)
    try:
        yield
    finally:
        _deadline_at.reset(deadline_token)
        _cancel_event.reset(cancel_token)


def request_remaining_seconds(*, reserve_seconds: float = 0.0) -> float | None:
    """Return this request's remaining wall-clock budget, if one is active."""

    deadline_at = _deadline_at.get()
    if deadline_at is None:
        return None
    return max(0.0, deadline_at - perf_counter() - max(0.0, reserve_seconds))


def request_was_cancelled() -> bool:
    event = _cancel_event.get()
    if event is not None and event.is_set():
        return True
    remaining = request_remaining_seconds()
    return remaining is not None and remaining <= 0.0


def raise_if_request_cancelled(*, minimum_remaining_seconds: float = 0.0) -> None:
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise RequestCancelledError("chat request cancelled by client")
    remaining = request_remaining_seconds()
    if remaining is not None and remaining <= max(0.0, minimum_remaining_seconds):
        raise RequestCancelledError("chat request deadline exhausted")


def bounded_request_timeout(
    configured_seconds: float,
    *,
    reserve_seconds: float = 0.25,
    minimum_seconds: float = 0.1,
) -> float:
    """Cap an I/O timeout by the active request's remaining budget."""

    remaining = request_remaining_seconds(reserve_seconds=reserve_seconds)
    if remaining is None:
        return configured_seconds
    if remaining <= 0.0:
        raise RequestCancelledError("chat request deadline exhausted before I/O")
    return max(minimum_seconds, min(configured_seconds, remaining))
