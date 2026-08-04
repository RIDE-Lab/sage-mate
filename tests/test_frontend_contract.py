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


def test_interface_icons_share_one_accessible_svg_system() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert 'class="ui-icon-sprite"' in html
    for symbol in (
        "menu", "companion", "dice", "brain", "globe-search", "mic",
        "send", "workflow", "copy", "retry", "sparkle", "bean",
    ):
        assert f'id="icon-{symbol}"' in html
    assert 'href="#icon-brain"' in html
    assert 'href="#icon-globe-search"' in html
    assert 'href="#icon-dice"' in html
    assert "function uiIconSvg(name, className = \"\")" in js
    assert 'return uiIconSvg(symbolName);' in js
    assert ".ui-icon {" in css
    assert ".ui-icon-dot {" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "🧠 深度思考" not in html
    assert "🌐 联网检索" not in html
    assert "🎲" not in html
    assert "👤" not in js


def test_sage_companion_is_local_accessible_and_lifecycle_driven() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    companion_css = (WEB_DIR / "companion.css").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    companion_js = (WEB_DIR / "companion.js").read_text(encoding="utf-8")

    assert 'id="sage-companion"' in html
    assert 'id="sage-companion-toggle"' in html
    assert 'aria-controls="sage-companion-panel"' in html
    assert 'id="sage-companion-message"' in html
    assert 'aria-live="polite"' in html
    assert "名字、外观与互动进度仅保存在当前浏览器" in html
    assert 'id="open-companion-settings"' in html
    assert 'id="sage-companion-name-input"' in html
    assert 'id="sage-companion-temperament"' in html
    assert 'id="sage-companion-sound"' in html
    assert 'id="sage-companion-answers"' in html
    assert 'id="sage-companion-streak"' in html
    assert 'id="sage-companion-quest-text"' in html
    assert 'data-companion-action="complete-quest"' in html
    assert "sage-companion-accessory-crown" in html
    assert 'role="tablist"' in html
    assert 'data-companion-tab="companion"' in html
    assert 'data-companion-tab="customize"' in html
    assert 'id="sage-companion-scroll-cue"' in html

    assert '<script src="./companion.js" defer></script>' in html
    assert '<link rel="stylesheet" href="./companion.css" />' in html
    assert 'const STORAGE_KEY = "sageMateCompanion:v3";' in companion_js
    assert 'Object.freeze(["sageMateCompanion:v2", "sageMateCompanion:v1"])' in companion_js
    assert "localStorage.setItem(STORAGE_KEY" in companion_js
    assert "globalThis.SageCompanion = Object.freeze" in companion_js
    assert "${message}。点击" not in companion_js
    assert "${message} 点击${action}伙伴面板" in companion_js
    assert 'sageCompanionController?.setState("thinking"' in js
    assert js.count("sageCompanionController?.recordAnswerCompleted();") == 2
    assert 'sageCompanionController?.setState("worried"' in js
    persist_block = companion_js[companion_js.index("persist() {"):companion_js.index("idleMessage() {")]
    assert "question:" not in persist_block
    assert "fetch(" not in companion_js
    assert "XMLHttpRequest" not in companion_js
    assert "question:" not in companion_js
    assert "answer:" not in companion_js

    assert ".sage-companion-toggle" not in css
    assert "body.onboarding-active .sage-companion" in companion_css
    assert ".chat-shell.view-active .sage-companion" in companion_css
    assert '@media (prefers-reduced-motion: reduce)' in companion_css
    assert '.sage-companion[data-appearance="mint"]' in companion_css
    assert ".sage-companion-tabs" in companion_css
    assert "handleTabKeydown(event)" in companion_js
    assert "prefers-reduced-motion: reduce" in companion_css
    assert "sage-companion-panel-enter" in companion_css
    assert "sage-companion-bond-shine" in companion_css
    assert '.sage-companion[data-stage="confidant"]' in companion_css
    assert "@keyframes sage-companion-spark" in companion_css
    assert ".sage-companion-footprint" in companion_css
    assert ".sage-companion-quest" in companion_css


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
    assert "width: min(100%, var(--content-column-max));" in css


def test_active_onboarding_keeps_chat_stream_visible() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    selector = "body.onboarding-active .chat-shell .chat-stream"
    block_match = re.search(rf"{re.escape(selector)} \{{\n(.*?)\n\}}", css, re.S)

    assert block_match, f"Missing CSS block for {selector}"
    assert "display: none;" not in block_match.group(1)
    assert "display: flex;" in block_match.group(1)


def test_active_onboarding_uses_left_column_even_after_chat_starts() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    shell_selector = "body.onboarding-active .chat-shell"
    selector = "body.onboarding-active .chat-shell .onboarding-card"
    shell_block = re.search(rf"{re.escape(shell_selector)} \{{\n(.*?)\n\}}", css, re.S)
    block_match = re.search(rf"{re.escape(selector)} \{{\n(.*?)\n\}}", css, re.S)

    assert shell_block, f"Missing CSS block for {shell_selector}"
    assert "display: grid;" in shell_block.group(1)
    assert "grid-template-columns:" in shell_block.group(1)
    assert block_match, f"Missing CSS block for {selector}"
    assert "grid-column: 1;" in block_match.group(1)
    assert "grid-row: 1 / 3;" in block_match.group(1)


def test_active_onboarding_collapses_to_viewport_safe_mobile_column() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    responsive_start = css.index(
        "/* Keep guided onboarding inside tablet and phone visual viewports. */"
    )
    responsive_end = css.index("/* --- Onboarding guided question card --- */")
    responsive_css = css[responsive_start:responsive_end]

    assert "@media (max-width: 920px)" in responsive_css
    assert "display: flex;" in responsive_css
    assert "flex-direction: column;" in responsive_css
    assert "flex: 0 0 auto;" in responsive_css
    assert "width: min(100%, var(--content-column-max));" in responsive_css
    assert "padding: var(--mobile-panel-padding);" in responsive_css
    assert "overflow-x: hidden;" in responsive_css
    assert "position: relative;" in responsive_css
    assert "left: auto;" in responsive_css
    assert "right: auto;" in responsive_css
    assert "bottom: auto;" in responsive_css
    assert "minmax(280px" not in responsive_css
