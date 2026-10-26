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
    revisions = re.findall(r'(?:styles\.4222\.css|minimal\.1009\.css|app\.4222\.js)\?rev=([^"\s]+)', html)
    assert len(revisions) == 3 and len(set(revisions)) == 1
    assert re.fullmatch(r"[a-z][a-z0-9-]+", revisions[0])


def test_composer_modes_share_compact_geometry_without_shrinking_forms():
    css = THEME.read_text()
    mode = re.search(r"\.composer-pill-toggle\s*\{([^}]+)\}", css).group(1)
    for declaration in (
        "height: var(--compact-control-height)",
        "padding: 0 var(--compact-control-padding)",
        "border-radius: var(--compact-control-radius)",
    ):
        assert declaration in mode
    select = re.search(r"\.composer-tools-row #web-search-mode\s*\{([^}]+)\}", css).group(1)
    assert "width: 6em" in select and "padding: 0" in select
    assert "appearance: auto" in select  # Keep a native dropdown affordance.
    assert "--compact-control-height: 44px" in css
    close = re.search(r"\.modal-head > \.icon-button\s*\{([^}]+)\}", css).group(1)
    assert "flex: 0 0 auto" in close and "white-space: nowrap" in close
    html = (THEME.parent / "index.html").read_text()
    assert '<option value="always">必须联网</option>' in html
    assert "任何模式都不能越过私密资料边界" in html


def test_companion_has_one_coordinate_space_across_responsive_breakpoint():
    css = (THEME.parent / "companion.css").read_text()
    js = (THEME.parent / "companion.js").read_text()
    assert ".sage-companion { position: fixed" not in css
    assert "translateY(-148px)" not in css
    assert "const layoutBottom = shellRect.bottom;" in js
    assert "const localTop = clamped.top - (shellRect?.top || 0);" in js
