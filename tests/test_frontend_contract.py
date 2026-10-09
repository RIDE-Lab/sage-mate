"""Frontend-to-API contract test.

Validates that every DOM element ID referenced inside an API POST body
construction in app.js actually exists in index.html. This catches the
exact class of bug where a required Pydantic field (e.g. visitor_profile)
sends an empty/null value because the DOM element was removed from HTML,
resulting in a 422 Unprocessable Entity error.
"""
from __future__ import annotations

import re
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1] / "src" / "sage_faculty_twin" / "web"

# Each entry: (endpoint, list_of_dom_ids_used_in_payload)
# These IDs are read by app.js to construct JSON POST bodies.
API_PAYLOAD_CONTRACTS = [
    ("/auth/admin/login", ["admin-username", "admin-password"]),
    ("/auth/user/register", [
        "user-register-name",
        "user-register-email",
        "user-register-profile",
        "user-register-password",
    ]),
    ("/auth/user/login", ["user-login-email", "user-login-password"]),
    ("/chat", [
        "chat-question",
        "student-name",
        "student-email",
        "course-context",
        "visitor-profile",
        "deep-thinking-checkbox",
    ]),
    ("/knowledge", [
        "knowledge-title",
        "knowledge-source",
        "knowledge-content",
        "knowledge-tags",
    ]),
    ("/bookings", [
        "booking-student-name",
        "booking-email",
        "booking-topic",
        "booking-start",
        "booking-end",
    ]),
    ("/suggestions", ["suggestion-message", "suggestion-category"]),
]

# Known missing IDs that exist in app.js but not in index.html.
# These use optional chaining in JS and do NOT affect API payloads.
# This count must only decrease over time (ratchet).
KNOWN_MISSING_ID_CEILING = 43


def _extract_html_ids(html: str) -> set[str]:
    return set(re.findall(r'id="([^"]+)"', html))


def _extract_js_element_ids(js: str) -> set[str]:
    return set(re.findall(r'getElementById\("([^"]+)"\)', js))


def test_api_payload_dom_ids_exist_in_html() -> None:
    """Every DOM ID used to build an API POST payload must exist in HTML."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    html_ids = _extract_html_ids(html)

    missing: list[tuple[str, str]] = []
    for endpoint, dom_ids in API_PAYLOAD_CONTRACTS:
        for dom_id in dom_ids:
            if dom_id not in html_ids:
                missing.append((endpoint, dom_id))

    assert not missing, (
        "DOM IDs used in API payloads are missing from index.html:\n"
        + "\n".join(f"  {endpoint}: #{dom_id}" for endpoint, dom_id in missing)
    )


def test_missing_element_ids_do_not_grow() -> None:
    """The number of JS getElementById refs missing from HTML must not increase."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    html_ids = _extract_html_ids(html)
    js_ids = _extract_js_element_ids(js)
    missing = js_ids - html_ids

    assert len(missing) <= KNOWN_MISSING_ID_CEILING, (
        f"Missing ID count grew to {len(missing)} (ceiling is {KNOWN_MISSING_ID_CEILING}).\n"
        f"New missing IDs:\n"
        + "\n".join(f"  #{mid}" for mid in sorted(missing))
    )


def test_conversation_history_storage_is_scoped_per_account() -> None:
    """Local conversation history must be partitioned by guest/user scope."""
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "function resolveConversationHistoryStorageScope()" in js
    assert "function switchConversationHistoryScope(nextScope" in js
    assert "return `${CHAT_HISTORY_STORAGE_KEY}:${scope || \"guest\"}`;" in js
    assert "return `${CHAT_HISTORY_META_STORAGE_KEY}:${scope || \"guest\"}`;" in js


def test_history_sync_uses_authenticated_session_email_only() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "let currentUserAccountEmail = \"\";" in js
    assert "currentUserAccountEmail = authenticated ? String(session.account?.email || \"\").trim().toLowerCase() : \"\";" in js
    assert "return currentUserAccountEmail || \"\";" in js
    assert "student_email" not in js[js.index("async function syncConversationHistoryFromServer"):js.index("function setHistoryRailCollapsed")]


def test_send_button_uses_animation_state() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "send-button-label" in html
    assert "send-button-icon" in html
    assert "send-button-spinner" in html
    assert "function setChatSubmitLoading(isLoading)" in js
    assert "chatSubmitButton.classList.toggle(\"is-sending\", loading)" in js
    assert "chatSubmitButton.textContent = \"发送中\"" not in js
    assert ".send-button.is-sending" in css
    assert "@keyframes send-spinner-spin" in css
    assert "@keyframes send-button-pulse" in css


def test_local_code_setup_does_not_block_or_auto_open_profile_modal() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    setup_fn = js[js.index("function shouldShowSageMateSetup"):js.index("async function maybeOpenSageMateSetup")]

    assert "if (!isLocalCodeSetupUrl) return false;" in setup_fn
    assert "return !data.api_key_set" not in setup_fn
    assert "const localCodeConfigPromise = maybeOpenSageMateSetup();" in js
    assert "await maybeOpenSageMateSetup();" not in js
    assert "initializePage().catch(" in js


def test_auto_scientist_keeps_research_profile_in_frontend_payloads() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    onboarding_fn = js[js.index("function currentOnboardingProfile"):js.index("function currentChatVisitorProfile")]
    visitor_fn = js[js.index("function currentChatVisitorProfile"):js.index("function hasCompletedOnboarding")]

    assert 'if (profile === "auto_scientist") return "auto_scientist";' in onboarding_fn
    assert 'if (profile === "auto_scientist") return "lab_member";' in visitor_fn
    assert "visitor_profile: currentChatVisitorProfile()," in js


def test_account_entry_stays_in_sidebar_not_topbar() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="sidebar-user-icon"' in html
    assert 'id="topbar-user-badge" class="topbar-user-badge hidden"' in html
    assert 'topbarUserBadge.classList.remove("hidden"' not in js
    assert 'topbarUserBadge.classList.add("hidden")' in js


def test_empty_chat_onboarding_sits_before_chat_stream() -> None:
    """Faculty Twin onboarding uses the left empty column during guided mode."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    empty_chat_blocks = re.findall(r"(?m)^\.chat-shell\.chat-empty \{\n(.*?)\n\}", css, re.S)

    assert empty_chat_blocks
    assert html.index('id="onboarding-card"') < html.index('id="chat-stream"')
    assert all("justify-content: center;" not in block for block in empty_chat_blocks)
    assert all("align-items: center;" not in block for block in empty_chat_blocks)
    assert ".chat-shell.chat-empty .onboarding-card" in css
    assert ".chat-shell.chat-empty .composer-shell" in css
    assert "width: min(100%, 720px);" in css


def test_active_onboarding_keeps_chat_stream_visible() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    selector = "body.onboarding-active .chat-shell .chat-stream"
    block_match = re.search(rf"{re.escape(selector)} \{{\n(.*?)\n\}}", css, re.S)

    assert block_match, f"Missing CSS block for {selector}"
    assert "display: none;" not in block_match.group(1)
    assert "display: flex;" in block_match.group(1)


def test_active_onboarding_uses_one_centered_column_even_after_chat_starts() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    shell_selector = "body.onboarding-active .chat-shell"
    selector = "body.onboarding-active .chat-shell .onboarding-card"
    shell_block = re.search(rf"{re.escape(shell_selector)} \{{\n(.*?)\n\}}", css, re.S)
    block_match = re.search(rf"{re.escape(selector)} \{{\n(.*?)\n\}}", css, re.S)

    assert shell_block, f"Missing CSS block for {shell_selector}"
    assert "display: flex;" in shell_block.group(1)
    assert "flex-direction: column;" in shell_block.group(1)
    assert "grid-template-columns:" not in shell_block.group(1)
    assert block_match, f"Missing CSS block for {selector}"
    assert "width: min(100%, 720px);" in block_match.group(1)
    assert "margin: clamp(16px, 4vh, 36px) auto 12px;" in block_match.group(1)
    assert "grid-column:" not in block_match.group(1)


def test_restoring_a_conversation_hides_the_empty_state_onboarding() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    restore_fn = js[
        js.index("async function restoreConversationFromHistory"):
        js.index("async function fetchConversationTranscript")
    ]
    transcript_fn = js[
        js.index("function renderConversationTranscript"):
        js.index("function ", js.index("function renderConversationTranscript") + 1)
    ]

    assert 'chatStream.querySelector(".message-user")' in restore_fn
    assert "hideOnboardingCard();" in restore_fn
    assert "hideOnboardingCard();" in transcript_fn

    hide_fn = js[js.index("function hideOnboardingCard"):js.index("let onboardingHintTimer")]
    assert 'document.getElementById("seed-chips")?.classList.add("hidden");' in hide_fn


def test_default_chat_is_quiet_and_guidance_is_opt_in() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    initialize_fn = js[js.index("async function initializePage"):js.index("initializePage().catch")]
    session_fn = js[js.index("function applyUserSession"):js.index("async function loadManagedServices")]
    restart_fn = js[js.index("function restartOnboarding"):js.index("// --- iOS Safari")]

    assert "startOnboarding(" not in initialize_fn
    assert "startOnboarding(" not in session_fn
    assert "showDefaultLandingContent();" in initialize_fn
    assert "startOnboarding(profile, { force: true });" in restart_fn


def test_advanced_composer_controls_are_collapsed_into_one_tools_disclosure() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    assert '<details class="composer-tools-disclosure">' in html
    assert 'class="composer-tools-summary"' in html
    assert html.index('class="composer-tools-summary"') < html.index('class="composer-tools-row"')
    assert ".composer-tools-disclosure:not([open]) .composer-tools-row" in css
    assert ".composer-tools-disclosure:has(input:checked) .composer-tools-active-label" in css


def test_diagnostics_do_not_compete_with_the_core_chat_surface() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    assert re.search(
        r"\.app-stack-banner,\s*\n\.app-version-badge \{\s*\n\s*display: none;",
        css,
    )


def test_completed_answers_keep_a_collapsed_inline_process_summary() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "const INLINE_WORKFLOW_TRACE_ENABLED = true;" in js
    summary_fn = js[
        js.index("function buildWorkflowStatusSummaryHtml"):
        js.index("function buildWorkflowPhaseRailHtml")
    ]
    assert 'class="message-workflow-summary message-section"' in summary_fn
    assert 'aria-expanded="false"' in summary_fn
    assert '<strong class="message-section-title">处理过程</strong>' in summary_fn
    assert '<div class="message-section-content" hidden>' in summary_fn
    assert summary_fn.index('<div class="message-section-content" hidden>') < summary_fn.index(
        "buildWorkflowPhaseRailHtml"
    )


def test_live_progress_uses_one_compact_surface_inside_the_message() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    pending_fn = js[
        js.index("function renderPendingAssistantMessage"):
        js.index("function updatePendingAssistantMessage")
    ]
    compact_fn = js[
        js.index("function buildPendingWorkflowTraceCompactHtml"):
        js.index("function buildWorkflowChipRowHtml")
    ]
    assert 'aria-label="实时处理进度"' in pending_fn
    assert "thinking-phase-rail" not in pending_fn
    assert "thinking-trace-chip-row" not in compact_fn
    assert "已完成 ${completedSteps.length} 步" in compact_fn


def test_compact_process_summary_hides_details_until_requested() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    assert ".message-workflow-summary .message-section-content[hidden]" in css
    assert ".message-workflow-summary .message-inline-process-preview" in css
    assert "text-overflow: ellipsis;" in css
    assert ".message-process-icon" in css
