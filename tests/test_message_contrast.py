"""Guard the paired message palette; browser acceptance checks the cascade."""
import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "src/sage_faculty_twin/web/minimal.1009.css"


def luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return sum(c * w for c, w in zip(linear, (.2126, .7152, .0722)))


def test_message_palette_contrast():
    css = CSS.read_text()
    for selector in (":root", 'html[data-theme="dark"]'):
        block = css.split(selector + " {", 1)[1].split("}", 1)[0]
        tokens = dict(re.findall(r"(--[\w-]+):\s*(#[\da-f]{6});", block))
        for foreground in ("--ink", "--muted"):
            for background in ("--user", "--bg", "--surface-soft"):
                light, dark = sorted((luminance(tokens[foreground]), luminance(tokens[background])), reverse=True)
                assert (light + .05) / (dark + .05) >= 4.5, (selector, foreground, background)


def test_message_bubble_uses_theme_tokens_not_light_literal():
    css = CSS.read_text()
    block = css.split('html[data-theme] .message-user .message-bubble {', 1)[1].split("}", 1)[0]
    assert "background: var(--user);" in block
    assert "color: var(--ink);" in block
    assert "#f1f1ee" not in block
    assert 'html[data-theme="dark"] .message-section-chevron,' in css


def test_error_messages_use_semantic_surface():
    js = CSS.with_name("app.js").read_text()
    assert 'container.style.background = "var(--error-surface)";' in js
    assert 'var(--error)' not in js
    css = CSS.read_text()
    assert '--error:' not in css
    assert 'background: var(--error-surface);' in css
