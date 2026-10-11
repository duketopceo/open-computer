"""Token gates (section 13): token-only styling and contrast floors.

`test_contrast_*` computes WCAG 2.2 ratios from silver-lining.css itself, so it
keeps working when the hand-ported file is replaced by the generated one."""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "services/opsview/static"
TOKENS = (STATIC / "silver-lining.css").read_text()
RAW_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(")


def block(selector_start: str) -> dict[str, str]:
    i = TOKENS.index(selector_start)
    body = TOKENS[TOKENS.index("{", i) + 1: TOKENS.index("}", i)]
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))


DARK = block(":root {")
LIGHT = block(':root[data-theme="light"]')
SYSTEM_LIGHT = block(':root:not([data-theme="dark"])')


def lum(hexv: str) -> float:
    h = hexv.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# surface-4 is hover/pressed fill only; no text rests on it, so it is outside the text gate.
SURFACES = ["canvas", "surface-1", "surface-2", "surface-3"]
FLOORS = {"ink": 12, "ink-muted": 7, "ink-subtle": 4.5, "ink-tertiary": 4.5}


def pairs(theme):
    for t, floor in FLOORS.items():
        for s in SURFACES:
            yield t, s, floor, ratio(theme[f"--color-{t}"], theme[f"--color-{s}"])


@pytest.mark.parametrize("name,theme", [("dark", DARK), ("light", LIGHT)])
def test_contrast_floors_on_every_surface(name, theme):
    bad = [(t, s, round(r, 2), f) for t, s, f, r in pairs(theme) if r < f]
    assert not bad, f"{name}: below floor {bad}"


def test_dark_ink_stays_under_the_glow_ceiling():
    assert max(ratio(DARK["--color-ink"], DARK[f"--color-{s}"]) for s in SURFACES) <= 16.0


def test_control_edges_and_status_glyphs_meet_3_to_1():
    for theme in (DARK, LIGHT):
        for s in ("canvas", "surface-1"):
            assert ratio(theme["--color-control"], theme[f"--color-{s}"]) >= 3
            for st in ("urgent", "attention", "healthy", "neutral", "accent"):
                assert ratio(theme[f"--color-{st}"], theme[f"--color-{s}"]) >= 3, (st, s)


def test_buttons_text_contrast():
    for theme in (DARK, LIGHT):
        assert ratio(theme["--color-on-accent"], theme["--color-accent"]) >= 4.5
        assert ratio(theme["--color-on-accent"], theme["--color-accent-hover"]) >= 4.5


def test_no_pure_black_or_white_text_or_canvas():
    for theme in (DARK, LIGHT):
        assert theme["--color-ink"].lower() not in ("#fff", "#ffffff", "#000", "#000000")
    assert DARK["--color-canvas"].lower() != "#000000"


def test_light_theme_is_defined_three_ways_identically():
    assert LIGHT == SYSTEM_LIGHT or {k: v for k, v in LIGHT.items()} == {k: v for k, v in SYSTEM_LIGHT.items()}
    assert set(DARK) - {"--shadow-overlay"} <= set(LIGHT) | {k for k in DARK if not k.startswith("--color")}


def test_tokens_file_is_marked_replace_with_generated():
    assert "REPLACE WITH GENERATED" in TOKENS


@pytest.mark.parametrize("f", ["console.css", "console.js", "theme-init.js"])
def test_no_raw_colors_in_component_code(f):
    text = (STATIC / f).read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    assert not RAW_COLOR.search(text), f"{f}: raw color; use a --color-* token"


def test_no_raw_colors_in_templates_or_login_page():
    root = STATIC.parent
    for f in ("views.py", "registry.py", "webauthn_page.py"):
        text = (root / f).read_text()
        assert not RAW_COLOR.search(text), f"{f}: raw color in a template"


def test_font_sizes_come_from_the_scale():
    css = (STATIC / "console.css").read_text()
    for m in re.finditer(r"font-size:\s*([^;}]+)", css):
        assert m.group(1).strip().startswith("var(--text-"), m.group(0)
    scale = {"11px", "12px", "13px", "14px", "15px", "17px", "24px"}
    assert {v for k, v in DARK.items() if k.startswith("--text-")} == scale


def test_weights_are_400_500_600_only():
    css = (STATIC / "console.css").read_text()
    assert set(re.findall(r"font-weight:\s*(\d+)", css)) <= {"400", "500", "600"}


def test_no_motion_at_rest():
    css = (STATIC / "console.css").read_text()
    assert "@keyframes" not in css and "animation:" not in css and "infinite" not in css
    assert "prefers-reduced-motion" in css
