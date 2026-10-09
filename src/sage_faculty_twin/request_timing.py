"""Small monotonic timing ledger for the public chat request boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

from .models import ChatRequestTiming, ChatResponse
from .request_context import RequestRuntimeDiagnostics


@dataclass(slots=True)
class RequestTimingLedger:
    trace_id: str
    budget_seconds: float
    started_at: float = field(default_factory=perf_counter)
    stage_durations_ms: dict[str, float] = field(default_factory=dict)
    runtime_diagnostics: RequestRuntimeDiagnostics = field(
        default_factory=RequestRuntimeDiagnostics
    )

    @property
    def deadline_at(self) -> float:
        return self.started_at + self.budget_seconds

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_at - perf_counter())

    def record(self, stage: str, started_at: float) -> None:
        elapsed_ms = max(0.0, (perf_counter() - started_at) * 1000.0)
        self.stage_durations_ms[stage] = round(
            self.stage_durations_ms.get(stage, 0.0) + elapsed_ms,
            3,
        )

    def snapshot(self, response: ChatResponse, *, route: str) -> ChatRequestTiming:
        total_ms = max(0.0, (perf_counter() - self.started_at) * 1000.0)
        trace_ms = sum(
            step.duration_ms or 0 for step in response.workflow_trace
        )
        accounted_ms = min(total_ms, sum(self.stage_durations_ms.values()))
        runtime = self.runtime_diagnostics.snapshot()
        return ChatRequestTiming(
            trace_id=self.trace_id,
            route=route,
            total_duration_ms=round(total_ms, 3),
            budget_ms=round(self.budget_seconds * 1000.0, 3),
            remaining_budget_ms=round(self.remaining_seconds() * 1000.0, 3),
            stage_durations_ms=dict(self.stage_durations_ms),
            workflow_trace_reported_ms=trace_ms,
            accounted_duration_ms=round(accounted_ms, 3),
            unattributed_duration_ms=round(max(0.0, total_ms - accounted_ms), 3),
            **runtime,
        )

    def attach(self, response: ChatResponse, *, route: str) -> ChatResponse:
        return response.model_copy(update={"request_timing": self.snapshot(response, route=route)})
