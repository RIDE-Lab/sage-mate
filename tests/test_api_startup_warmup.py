"""Model-side cache warmup must not keep Twin's health endpoint offline."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sage_faculty_twin import api as api_module


@pytest.fixture(autouse=True)
def http_lifecycle_port(monkeypatch):
    flow = SimpleNamespace(start=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(api_module, "_create_http_workflow", lambda _: flow)
    monkeypatch.setattr(api_module, "http_workflow", None)
    yield flow


def test_slow_cache_warmup_does_not_block_startup(monkeypatch, http_lifecycle_port) -> None:
    release_warmup = threading.Event()
    warmup_started = threading.Event()
    shared_state = object()
    migrated = []
    monkeypatch.setattr(api_module, "SLACK_TWIN_TEAM_ID", "")
    monkeypatch.setattr(api_module, "slack_link_store", SimpleNamespace(migrate_to=lambda state: migrated.append(state)))

    def warmup() -> bool:
        warmup_started.set()
        return release_warmup.wait(timeout=1.0)

    monkeypatch.setattr(api_module.settings, "warm_service_on_startup", True)
    monkeypatch.setattr(api_module, "STARTUP_WARMUP_MAX_WAIT_SECONDS", 0.02)
    monkeypatch.setattr(
        api_module,
        "service",
        SimpleNamespace(ensure_initialized=lambda: SimpleNamespace(
            warm_fixed_prefix_cache=warmup, start_background_work=lambda: None,
            _conversation_store=SimpleNamespace(state_store=shared_state))),
    )

    async def verify() -> None:
        started = time.monotonic()
        await api_module.startup_event()
        elapsed = time.monotonic() - started
        assert warmup_started.is_set()
        assert migrated == [shared_state]
        assert elapsed < 0.2
        release_warmup.set()

    asyncio.run(verify())
    http_lifecycle_port.start.assert_awaited_once()


def test_recovery_starts_even_when_model_warmup_is_disabled(monkeypatch, http_lifecycle_port):
    calls = []
    monkeypatch.setattr(api_module, "SLACK_TWIN_TEAM_ID", "")
    monkeypatch.setattr(api_module, "slack_link_store", SimpleNamespace(migrate_to=lambda _: calls.append("migrate")))
    monkeypatch.setattr(api_module.settings, "warm_service_on_startup", False)
    monkeypatch.setattr(api_module, "service", SimpleNamespace(
        ensure_initialized=lambda: SimpleNamespace(start_background_work=lambda: calls.append("recover"),
            _conversation_store=SimpleNamespace(state_store=object()))
    ))
    asyncio.run(api_module.startup_event())
    assert calls == ["migrate", "recover"]
    http_lifecycle_port.start.assert_awaited_once()


def test_partial_startup_failure_drains_http_before_service(monkeypatch):
    calls = []

    async def close_flow():
        calls.append("http-close")

    async def close_service():
        calls.append("service-close")

    async def startup():
        api_module.http_workflow = SimpleNamespace(aclose=close_flow)
        raise RuntimeError("later startup failure")

    monkeypatch.setattr(api_module, "startup_event", startup)
    monkeypatch.setattr(api_module, "slack_workflow", None)
    monkeypatch.setattr(api_module, "service", SimpleNamespace(
        is_initialized=lambda: True, aclose=close_service))

    async def verify():
        with pytest.raises(RuntimeError, match="later startup failure"):
            async with api_module._app_lifespan(api_module.llm_app):
                pytest.fail("failed startup must not accept requests")
        assert api_module.http_workflow is None
        assert calls == ["http-close", "service-close"]

    asyncio.run(verify())
