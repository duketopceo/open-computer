"""Every desktop screen renders (headless) and uses only black, white and grey: no hue, no purple."""

from __future__ import annotations

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
    # Anti-aliased text only ever produces greys; a coloured pixel would mean a hue crept in.
    worst = max((max(rgb) - min(rgb) for _, rgb in colours), default=0)
    assert worst <= 6, f"{screen} {theme}: a pixel has colour (spread {worst})"
    background = img.getpixel((2, 2))
    assert (sum(background) / 3 < 20) == (theme == "dark"), f"{screen}: wrong background for {theme}"
