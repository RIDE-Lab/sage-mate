from pathlib import Path

from fastapi.testclient import TestClient

from sage_faculty_twin.api import app


client = TestClient(app)
REPO_ROOT = Path(__file__).resolve().parents[1]
NGINX_TEMPLATE = REPO_ROOT / "tools" / "nginx-local.conf"
LOCAL_PROXY_SCRIPTS = [
    REPO_ROOT / "tools" / "run_local_proxy.sh",
]


def test_chat_shell_exposes_topbar_action_entries() -> None:
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'id="lucky-question-button"' in html
    assert 'id="open-suggestions"' in html
    assert 'id="suggestion-modal"' in html
    assert 'id="homepage-link" href="/home/"' in html
    assert 'id="knowledge-feedback-web-list"' in html
    assert "联网资料审查区" in html


def test_frontend_script_uses_optional_overlay_modal_registry() -> None:
    response = client.get("/app.js")

    assert response.status_code == 200
    script = response.text
    assert 'const luckyQuestionButton = document.getElementById("lucky-question-button");' in script
    assert 'const knowledgeFeedbackWebList = document.getElementById("knowledge-feedback-web-list");' in script
    assert 'luckyQuestionButton?.addEventListener("click", handleLuckyQuestionClick);' in script
    assert "function applyLuckyQuestionPreferences(selection)" in script
    assert "function isFeedbackWebKnowledgeRecord(record)" in script
    assert "function handleFeedbackWebKnowledgeAction(event)" in script
    assert 'apiRequest("/knowledge/reviews/summary")' in script
    assert "data-feedback-web-review" in script
    assert "/knowledge/${encodeURIComponent(documentId)}/review" in script
    assert "const luckyEntries = RANDOM_CHAT_QUESTION_BANKS[profile] || RANDOM_CHAT_QUESTION_BANKS.general_visitor;" in script
    assert "const overlayModals = [" in script
    assert "].filter(Boolean);" in script
    assert "function hasVisibleOverlayModal()" in script
    assert "overlayModals.forEach((element) => {" in script
    assert (
        'return overlayModals.some((element) => !element.classList.contains("hidden"));'
        in script
    )
    assert "[identityModal, knowledgeModal, bookingModal, suggestionModal" not in script
    assert 'identityModal.classList.contains("hidden") &&' not in script


def test_general_visitor_onboarding_preserves_the_typed_question() -> None:
    response = client.get("/app.js")

    assert response.status_code == 200
    script = response.text
    assert "step.question.replace(/_+/g, question)" not in script
    assert "onboardingWrappedQuestion = effectiveQuestion;" in script


def test_local_site_proxy_routes_home_to_embedded_app() -> None:
    nginx_template = NGINX_TEMPLATE.read_text(encoding="utf-8")

    assert "location /home/ {" in nginx_template
    assert "proxy_pass http://__APP_HOST__:__APP_PORT__/home/;" in nginx_template
    assert "example.invalid" not in nginx_template
    assert "__HOMEPAGE_UPSTREAM_HOST__" not in nginx_template
    assert "__HOMEPAGE_UPSTREAM_SCHEME__" not in nginx_template

    for script_path in LOCAL_PROXY_SCRIPTS:
        script = script_path.read_text(encoding="utf-8")
        assert "HOMEPAGE_UPSTREAM_HOST" not in script
        assert "HOMEPAGE_UPSTREAM_SCHEME" not in script
        assert "example.invalid" not in script
