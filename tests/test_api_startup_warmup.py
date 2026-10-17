"""Model-side cache warmup must not keep Twin's health endpoint offline."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from sage_faculty_twin import api as api_module


def test_slow_cache_warmup_does_not_block_startup(monkeypatch) -> None:
    release_warmup = threading.Event()
    warmup_started = threading.Event()

    def warmup() -> bool:
        warmup_started.set()
        return release_warmup.wait(timeout=1.0)

    monkeypatch.setattr(api_module.settings, "warm_service_on_startup", True)
    monkeypatch.setattr(api_module, "STARTUP_WARMUP_MAX_WAIT_SECONDS", 0.02)
    monkeypatch.setattr(
        api_module,
        "service",
        SimpleNamespace(ensure_initialized=lambda: SimpleNamespace(
            warm_fixed_prefix_cache=warmup, start_background_work=lambda: None)),
    )

    async def verify() -> None:
        started = time.monotonic()
        await api_module.startup_event()
        elapsed = time.monotonic() - started
        assert warmup_started.is_set()
        assert elapsed < 0.2
        release_warmup.set()

    asyncio.run(verify())


def test_recovery_starts_even_when_model_warmup_is_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(api_module.settings, "warm_service_on_startup", False)
    monkeypatch.setattr(api_module, "service", SimpleNamespace(
        ensure_initialized=lambda: SimpleNamespace(start_background_work=lambda: calls.append("recover"))
    ))
    asyncio.run(api_module.startup_event())
    assert calls == ["recover"]
