from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage_faculty_twin.api import app, service
from sage_faculty_twin.config import settings
from sage_faculty_twin.suggestion_store import SuggestionBoardStore


client = TestClient(app)


@pytest.fixture
def isolated_suggestion_store(tmp_path: Path):
    original_store = service._suggestion_store
    service._suggestion_store = SuggestionBoardStore(
        settings.model_copy(update={"suggestion_board_dir": tmp_path / "suggestions"})
    )
    try:
        yield
    finally:
        service._suggestion_store = original_store


def test_anonymous_suggestion_board_accepts_guest_messages(isolated_suggestion_store) -> None:
    client.cookies.clear()

    response = client.post(
        "/suggestions",
        json={"message": "希望留言板支持匿名建议。", "category": "功能建议"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "***"
    assert payload["category"] == "功能建议"
    assert "student_name" not in payload
    assert "student_email" not in payload

    list_response = client.get("/suggestions")

    assert list_response.status_code == 200
    assert list_response.json()[0]["suggestion_id"] == payload["suggestion_id"]
    assert list_response.json()[0]["message"] == "***"

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200
    health_response = client.get("/admin/health")
    assert health_response.status_code == 200
    assert health_response.json()["suggestion_board_records"] == "1"


def test_admin_can_view_plaintext_anonymous_suggestions(isolated_suggestion_store) -> None:
    client.cookies.clear()

    create_response = client.post(
        "/suggestions",
        json={"message": "希望管理员看到完整留言。", "category": "课程反馈"},
    )
    assert create_response.status_code == 200
    assert create_response.json()["message"] == "***"

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    admin_list_response = client.get("/suggestions")

    assert admin_list_response.status_code == 200
    assert admin_list_response.json()[0]["message"] == "希望管理员看到完整留言。"


def test_anonymous_suggestion_board_returns_newest_first(isolated_suggestion_store) -> None:
    client.cookies.clear()

    first = client.post("/suggestions", json={"message": "第一条建议"}).json()
    second = client.post("/suggestions", json={"message": "第二条建议"}).json()

    response = client.get("/suggestions?limit=1")

    assert response.status_code == 200
    assert [item["suggestion_id"] for item in response.json()] == [second["suggestion_id"]]
    assert first["suggestion_id"] != second["suggestion_id"]
