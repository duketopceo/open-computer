"""Every desktop screen renders (headless) and uses only black, white and grey, plus the one orange-to-red accent: no other hue, no purple."""

from __future__ import annotations

import colorsys
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "desktop" / "target" / "debug" / "open-computer"
SCRIPT = ROOT / "desktop" / "scripts" / "screens.sh"

PIL = pytest.importorskip("PIL.Image", reason="pillow is not installed")
pytestmark = pytest.mark.skipif(not BIN.exists() or shutil.which("bash") is None, reason="the desktop binary is not built")

SCREENS = ["onboarding-connect", "chat", "settings", "diagnostics"]


def _is_accent(rgb: tuple[int, int, int]) -> bool:
    """True for a pixel on the amber-to-red accent: hue between 5 and 45 degrees (blends with black or white keep it)."""
    h, _s, _v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    return 5 <= h * 360 <= 45


@pytest.fixture(scope="module")
def shots(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("screens")
    env = {k: v for k, v in os.environ.items() if k not in {"OC_CLOUD_URL", "OC_CLOUD_TOKEN"}}
    subprocess.run(["bash", str(SCRIPT), str(out), str(BIN)], check=True, env=env, capture_output=True, timeout=240)
    return out


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("screen", SCREENS)
def test_screen_renders_and_has_no_colour(shots: Path, screen: str, theme: str):
    path = shots / f"{screen}-{theme}.png"
    assert path.exists(), path
    img = PIL.open(path).convert("RGB")
    assert img.width >= 400 and img.height >= 400
    colours = img.getcolors(maxcolors=1 << 20) or []
    # Anti-aliased text only ever produces greys. The one accent (an orange-to-red gradient, hue 5 to 45 degrees, also
    # when blended with black or white) is allowed; any other coloured pixel means a hue crept in.
    stray = [rgb for _, rgb in colours if max(rgb) - min(rgb) > 6 and not _is_accent(rgb)]
    assert not stray, f"{screen} {theme}: a pixel has a colour that is not the accent, for example {stray[0]}"
    background = img.getpixel((2, 2))
    assert (sum(background) / 3 < 20) == (theme == "dark"), f"{screen}: wrong background for {theme}"
