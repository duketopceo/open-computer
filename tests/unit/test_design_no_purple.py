"""The design rule: black and white only, no purple, violet or indigo anywhere in product colours."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-no-purple.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _violations(text: str) -> list[dict]:
    code = (
        "import('./scripts/check-no-purple.mjs').then(m => "
        "console.log(JSON.stringify(m.findViolations(process.argv[1]))))"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", code, "--", text],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_repo_has_no_purple():
    result = subprocess.run(["node", str(SCRIPT)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "snippet",
    [
        "color: #6d28d9;",
        "color: #7c3aed",
        "background: rgb(99, 102, 241)",
        "fill: hsl(270, 60%, 50%)",
        "Color32::from_rgb(139, 92, 246)",
        "Color::rgb8(139, 92, 246)",
        "hex(\"#8b5cf6\")",
        "--accent: indigo;",
        "stroke: Violet",
        "color: #818cf8",
    ],
)
def test_purple_is_caught(snippet: str):
    assert _violations(snippet), snippet


@pytest.mark.parametrize(
    "snippet",
    ["color: #000000;", "color: #ffffff", "background: #3a3a3a", "color: rgb(200, 200, 200)", "fill: hsl(0, 0%, 50%)", "color: #1a1a1a"],
)
def test_black_white_and_grey_pass(snippet: str):
    assert _violations(snippet) == []
