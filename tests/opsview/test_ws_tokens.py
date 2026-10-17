"""Design gates for the workspace (silver-lining.md section 13): token-only styling, type scale, weights, motion.

Same rules test_tokens.py applies to console.css, applied to everything the workspace ships. The only animation
allowed is the single `shimmer` on the one running plan step, and prefers-reduced-motion must switch it off."""

import re
from pathlib import Path

import pytest

WS = Path(__file__).resolve().parents[2] / "services/workspace"
STATIC = WS / "static"
RAW_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(")
SCALE = {
    "var(--text-micro)",
    "var(--text-caption)",
    "var(--text-body)",
    "var(--text-body-lg)",
    "var(--text-title)",
    "var(--text-headline)",
    "var(--text-display)",
}


def strip_comments(t: str) -> str:
    return re.sub(r"/\*.*?\*/|(?<!:)//[^\n]*", "", t, flags=re.S)


CODE = sorted(STATIC.glob("*.js")) + [STATIC / "workspace.css", WS / "page.py", WS / "routes.py", WS / "replay.py"]


@pytest.mark.parametrize("f", CODE, ids=lambda p: p.name)
def test_no_raw_colors_in_workspace_code(f):
    assert not RAW_COLOR.search(strip_comments(f.read_text())), f"{f.name}: raw color; use a --color-* token"


def test_workspace_css_font_sizes_come_from_the_scale():
    css = (STATIC / "workspace.css").read_text()
    sizes = re.findall(r"font-size:\s*([^;}]+)", css)
    assert sizes and all(s.strip() in SCALE for s in sizes), [s for s in sizes if s.strip() not in SCALE]


def test_no_px_font_sizes_anywhere_in_js():
    for f in STATIC.glob("*.js"):
        assert not re.search(r"fontSize|font-size\s*[:=]", f.read_text()), f.name


def test_weights_are_400_500_600_only():
    css = (STATIC / "workspace.css").read_text()
    assert set(re.findall(r"font-weight:\s*(\d+)", css)) <= {"400", "500", "600"}


def test_spacing_uses_the_4px_scale_tokens():
    css = strip_comments((STATIC / "workspace.css").read_text())
    for prop, val in re.findall(r"\b(padding|margin|gap)(?:-[a-z]+)?:\s*([^;}]+)", css):
        for part in val.split():
            if re.fullmatch(r"-?\d+(px)?", part) and part not in ("0",):
                raise AssertionError(f"{prop}: {val}: use a --space-* token")


def test_only_one_animation_and_it_respects_reduced_motion():
    css = (STATIC / "workspace.css").read_text()
    assert re.findall(r"@keyframes\s+(\w+)", css) == ["shimmer"]
    uses = re.findall(r"([^{}]+)\{[^{}]*animation:\s*([^;]*);", css)
    assert [(sel.strip(), a.split()[0]) for sel, a in uses if "none" not in a] == [
        (".plan-running::after", "shimmer")
    ], uses
    reduced = css[css.index("prefers-reduced-motion: reduce") :]
    assert ".plan-running::after { animation: none; display: none; }" in reduced


def test_no_third_party_or_remote_loads_in_the_workspace():
    for f in [*STATIC.glob("*"), WS / "page.py"]:
        text = strip_comments(f.read_text())
        assert not re.search(
            r"(src|href)=[\"']https?://|@import\s+url\(|\bimport\s*\(\s*[\"']https?:|fetch\(\s*[\"']https?:", text
        ), f.name
        assert "cdn." not in text.lower() and "googleapis" not in text.lower(), f.name


def test_no_inline_event_handlers_or_eval_in_js():
    for f in STATIC.glob("*.js"):
        text = strip_comments(f.read_text())
        assert not re.search(r"\beval\(|new Function\(|\binnerHTML\s*=|insertAdjacentHTML|document\.write", text), (
            f.name
        )


def test_icon_only_controls_have_names_in_the_template():
    from services.workspace import page

    for sym in ("panel-left", "panel-right", "plus", "chev-left", "stop", "send"):
        assert sym in page.EXTRA_ICONS


def test_js_bundle_budget_is_far_under_180kb_gzipped():
    import gzip

    total = sum(len(gzip.compress(f.read_bytes())) for f in STATIC.glob("*.js")) + len(
        gzip.compress((STATIC / "workspace.css").read_bytes())
    )
    assert total < 70_000, (
        f"{total} bytes gzipped: no build step, no dependencies, and well inside the 180kB route budget"
    )
