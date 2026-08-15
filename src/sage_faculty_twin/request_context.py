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
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field


_cancel_event: ContextVar[threading.Event | None] = ContextVar(
    "sage_mate_request_cancel_event", default=None
)
_deadline_at: ContextVar[float | None] = ContextVar(
    "sage_mate_request_deadline_at", default=None
)


class RequestCancelledError(RuntimeError):
    """Raised after disconnect, explicit cancellation, or deadline expiry."""


@dataclass(slots=True)
class RequestRuntimeDiagnostics:
    """Mutable request-local model metrics shared across worker contexts."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    llm_call_count: int = 0
    llm_retry_count: int = 0
    llm_ttft_ms: float | None = None
    llm_total_duration_ms: float = 0.0
    llm_cache_hits: int = 0
    llm_cache_misses: int = 0

    def record_llm_call(self) -> None:
        with self._lock:
            self.llm_call_count += 1

    def record_llm_retry(self) -> None:
        with self._lock:
            self.llm_retry_count += 1

    def record_llm_ttft(self, elapsed_ms: float) -> None:
        with self._lock:
            if self.llm_ttft_ms is None:
                self.llm_ttft_ms = max(0.0, elapsed_ms)

    def record_llm_complete(self, elapsed_ms: float) -> None:
        with self._lock:
            self.llm_total_duration_ms += max(0.0, elapsed_ms)

    def record_cache_lookup(self, *, hit: bool) -> None:
        with self._lock:
            if hit:
                self.llm_cache_hits += 1
            else:
                self.llm_cache_misses += 1

    def snapshot(self) -> dict[str, int | float | None]:
        with self._lock:
            return {
                "llm_call_count": self.llm_call_count,
                "llm_retry_count": self.llm_retry_count,
                "llm_ttft_ms": (
                    round(self.llm_ttft_ms, 3) if self.llm_ttft_ms is not None else None
                ),
                "llm_total_duration_ms": round(self.llm_total_duration_ms, 3),
                "llm_cache_hits": self.llm_cache_hits,
                "llm_cache_misses": self.llm_cache_misses,
            }


class RequestCancellationController:
    """Thread-safe cancellation signal that can interrupt active request I/O."""

    def __init__(
        self,
        event: threading.Event | None = None,
        *,
        diagnostics: RequestRuntimeDiagnostics | None = None,
    ) -> None:
        self.event = event or threading.Event()
        self.diagnostics = diagnostics or RequestRuntimeDiagnostics()
        self._lock = threading.Lock()
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._next_callback_id = 0

    def is_cancelled(self) -> bool:
        return self.event.is_set()

    def cancel(self) -> None:
        self.event.set()
        with self._lock:
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                # One broken closer must not prevent the remaining resources
                # from being reclaimed.
                continue

    def register(self, callback: Callable[[], None]) -> Callable[[], None]:
        with self._lock:
            if self.event.is_set():
                callback_id = None
            else:
                callback_id = self._next_callback_id
                self._next_callback_id += 1
                self._callbacks[callback_id] = callback
        if callback_id is None:
            callback()
            return lambda: None

        def unregister() -> None:
            with self._lock:
                self._callbacks.pop(callback_id, None)

        return unregister


_cancel_controller: ContextVar[RequestCancellationController | None] = ContextVar(
    "sage_mate_request_cancel_controller", default=None
)


@contextmanager
def request_cancellation_scope(
    cancellation: threading.Event | RequestCancellationController,
    *,
    deadline_at: float | None = None,
) -> Iterator[RequestCancellationController]:
    controller = (
        cancellation
        if isinstance(cancellation, RequestCancellationController)
        else RequestCancellationController(cancellation)
    )
    cancel_token = _cancel_event.set(controller.event)
    controller_token = _cancel_controller.set(controller)
    deadline_token = _deadline_at.set(deadline_at)
    try:
        yield controller
    finally:
        _deadline_at.reset(deadline_token)
        _cancel_controller.reset(controller_token)
        _cancel_event.reset(cancel_token)


def register_request_cancel_callback(callback: Callable[[], None]) -> Callable[[], None]:
    """Register an active-I/O closer and return its unregister callback."""

    controller = _cancel_controller.get()
    if controller is None:
        return lambda: None
    return controller.register(callback)


def request_has_cancellation_controller() -> bool:
    return _cancel_controller.get() is not None


def request_runtime_diagnostics() -> RequestRuntimeDiagnostics | None:
    controller = _cancel_controller.get()
    return controller.diagnostics if controller is not None else None


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
