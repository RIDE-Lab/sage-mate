"""Theme-token checks supplement, not replace, browser visual acceptance."""
import re
from pathlib import Path

THEME = Path(__file__).resolve().parents[1] / "src/sage_faculty_twin/web/minimal.1009.css"


def test_shared_accent_is_readable_in_both_themes():
    css = THEME.read_text()
    for selector in (":root", 'html[data-theme="dark"]'):
        block = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css).group(1)
        tokens = dict(re.findall(r"--([\w-]+):\s*#([\da-f]{6});", block))

        def luminance(value):
            channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
            channels = [v / 12.92 if v <= 0.04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
            return sum(v * w for v, w in zip(channels, (.2126, .7152, .0722)))

        for fg in ("ink", "muted", "brand-ink"):
            for bg in ("bg", "surface", "brand-soft"):
                high, low = sorted((luminance(tokens[fg]), luminance(tokens[bg])), reverse=True)
                assert (high + .05) / (low + .05) >= 4.5, (selector, fg, bg)


def test_visual_refresh_preserves_native_states_and_accessible_focus():
    css = THEME.read_text()
    assert ":focus-visible" in css
    assert "outline: 2px solid var(--brand-ink)" in css
    assert ".message-retry-button" in css
    html = (THEME.parent / "index.html").read_text()
    assert "twin-ws-visual-20261020" in html
