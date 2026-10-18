"""Continuous native worker failures cannot masquerade as HTTP readiness."""

import asyncio
import json

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin import api
from sage_faculty_twin.service import DigitalTwinService
from test_http_workflow_integration import http_client


@pytest.mark.parametrize("kind", ["post_answer", "feedback", "knowledge_index", "booking_notifications"])
def test_failed_native_worker_degrades_public_health_and_reconstruction_recovers(tmp_path, monkeypatch, kind):
    instance = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
    instance.start_background_work()
    workers = instance.background_work_status()
    assert workers["post_answer"].ready and workers["feedback"].ready
    assert all(status.ready for status in workers.values() if status is not None)
    assert instance.health()["background_readiness"] == "ready"
    sources = {
        "post_answer": instance._post_answer_runtime._service,
        "feedback": instance._feedback_runtime._service,
        "knowledge_index": instance._knowledge_store._catalog._index_service,
        "booking_notifications": instance._meeting_service._notifications.service,
    }
    native = sources[kind]

    def fail(**kwargs):
        raise OSError("private storage diagnostic fixture")

    try:
        with http_client(instance, monkeypatch, close_service=False) as client:
            proxy = api.LazyDigitalTwinService()
            proxy._instance = instance  # Exercise the real production readiness proxy.
            monkeypatch.setattr(api, "service", proxy)
            assert client.get("/health").status_code == 200
            with monkeypatch.context() as patch:
                patch.setattr(native.journal, "active_records", fail)
                assert native._stopped.wait(5), "native supervisor did not observe failure"
            snapshot = instance.health()
            assert snapshot["status"] == "degraded" and snapshot["background_readiness"] == "degraded"
            states = json.loads(snapshot["background_work_states"])
            assert states[kind]["phase"] == "failed" and states[kind]["error_type"] == "OSError"
            assert not states[kind]["ready"] and not states[kind]["accepting"]
            assert "private storage" not in snapshot["background_work_states"]
            public = client.get("/health")
            assert public.status_code == 503, public.text
            assert public.json()["readiness"] == "degraded"
            assert public.json()["background_readiness"] == "degraded"
            assert "background_work_states" not in public.json()
            assert "OSError" not in public.text and "private storage" not in public.text
            with pytest.raises(RuntimeError, match="work_service_failed"):
                native.close()
    finally:
        asyncio.run(instance.aclose())

    # Replacement reconstructs native services over the existing stores. This
    # is not resetting a failed thread/error flag or creating another executor.
    replacement = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
    try:
        replacement.start_background_work()
        assert replacement.health()["status"] == "ok"
        assert replacement.health()["background_readiness"] == "ready"
    finally:
        asyncio.run(replacement.aclose())


@pytest.mark.parametrize("kind", ["ingress", "cancellation"])
def test_http_native_driver_failure_is_independent_of_healthy_application_workers(tmp_path, monkeypatch, kind):
    instance = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
    instance.start_background_work()
    try:
        with http_client(instance, monkeypatch, close_service=False) as client:
            proxy = api.LazyDigitalTwinService()
            proxy._instance = instance
            monkeypatch.setattr(api, "service", proxy)
            native = (api.http_workflow._work._service if kind == "ingress"
                      else api.http_workflow._cancellations._work)

            def fail(**kwargs):
                raise OSError("private fixture diagnostic")

            with monkeypatch.context() as patch:
                patch.setattr(native.journal, "active_records", fail)
                assert native._stopped.wait(5)
            response = client.get("/health")
            assert response.status_code == 503
            assert response.json()["http_readiness"] == "degraded"
            assert response.json()["background_readiness"] == "ready"
            assert response.json()["readiness"] == "degraded"
            assert "private" not in response.text and "OSError" not in response.text
            with pytest.raises(RuntimeError, match="work_service_failed"):
                native.close()
    finally:
        asyncio.run(instance.aclose())
