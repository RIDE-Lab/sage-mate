from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage_faculty_twin.api import app, service
from sage_faculty_twin.config import settings
from sage_faculty_twin.online_presence_store import OnlinePresenceStore


client = TestClient(app)


@pytest.fixture
def isolated_online_presence_store(tmp_path: Path):
    original_store = service._online_presence_store
    service._online_presence_store = OnlinePresenceStore(
        settings.model_copy(
            update={
                "online_presence_dir": tmp_path / "online-presence",
                "online_presence_window_seconds": 300,
                "online_presence_retention_seconds": 172800,
            }
        )
    )
    try:
        yield
    finally:
        service._online_presence_store = original_store


def test_presence_heartbeat_counts_online_visitors(isolated_online_presence_store) -> None:
    client.cookies.clear()

    first = client.post(
        "/presence/heartbeat",
        json={
            "client_id": "client-a",
            "conversation_id": "conv-a",
            "is_authenticated": False,
        },
    )
    assert first.status_code == 200
    assert first.json()["online_visitors"] == 1
    assert first.json()["online_authenticated_users"] == 0

    second = client.post(
        "/presence/heartbeat",
        json={
            "client_id": "client-b",
            "conversation_id": "conv-b",
            "student_email": "alice@example.com",
            "is_authenticated": True,
        },
    )
    assert second.status_code == 200

    third = client.post(
        "/presence/heartbeat",
        json={
            "client_id": "client-c",
            "conversation_id": "conv-c",
            "student_email": "alice@example.com",
            "is_authenticated": True,
        },
    )
    assert third.status_code == 200

    payload = third.json()
    assert payload["window_seconds"] == 300
    assert payload["online_visitors"] == 3
    assert payload["online_authenticated_users"] == 1
    assert payload["active_conversations"] == 3


def test_health_includes_online_presence_metrics(isolated_online_presence_store) -> None:
    client.cookies.clear()

    heartbeat = client.post(
        "/presence/heartbeat",
        json={
            "client_id": "client-health",
            "conversation_id": "conv-health",
            "student_email": "health@example.com",
            "is_authenticated": True,
        },
    )
    assert heartbeat.status_code == 200

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200
    health_response = client.get("/admin/health")
    assert health_response.status_code == 200
    payload = health_response.json()

    assert payload["online_window_seconds"] == "300"
    assert payload["online_visitors"] == "1"
    assert payload["online_authenticated_users"] == "1"
    assert payload["online_active_conversations"] == "1"
