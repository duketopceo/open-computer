"""The server image installs hash-verified packages, and the one exception is named.

Two things are proven here, and they are different claims:

  * `requirements.txt` is what `uv export` produces from `uv.lock`, so a lock bump without a
    re-export fails this test rather than reaching a deploy (the freshness check).
  * The file actually installs under `--require-hashes`, and removing a hash breaks it (the integrity
    check). That one runs a real pip install into a throwaway venv and is marked `slow`; the default run
    covers the freshness half.

`jev-compact` is a version-control dependency. pip cannot hash a VCS source at all — it refuses with "Can't
verify hashes for these requirements because we don't have a way to hash version control repositories" — so it
lives in `deploy/server-001/requirements-vcs.txt`, installed in a separate step, with its 40-character commit pin
as the integrity guarantee. That carve-out is asserted here: if the VCS line ever reappears in the hashed file,
every `--require-hashes` install of the image breaks, and this test says so first.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "requirements.txt"
VCS = ROOT / "deploy" / "server-001" / "requirements-vcs.txt"
DOCKERFILE = ROOT / "deploy" / "server-001" / "Dockerfile"

EXPORT_CMD = (
    "uv",
    "export",
    "--no-dev",
    "--extra",
    "space",
    "--extra",
    "durable",
    "--no-emit-project",
)


def _uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH; the freshness check needs the exporter")
    return uv


def _exported() -> list[str]:
    """Regenerate into a scratch name, then drop the header line that echoes the output path.

    `uv export` writes its own `-o` target into a header comment, so exporting to a temp path can never compare
    equal to the committed file. The path-bearing header is stripped from both sides before comparing; everything
    that decides what installs is compared verbatim.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        out_file = Path(d) / "requirements.txt"
        out = subprocess.run(
            [_uv(), *EXPORT_CMD[1:], "-o", str(out_file)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert out.returncode == 0, out.stderr
        return _drop_path_header(out_file.read_text().splitlines(keepends=True))


def _drop_path_header(lines: list[str]) -> list[str]:
    return [ln for ln in lines if not ln.startswith("#    uv export ")]


def _committed() -> list[str]:
    return REQUIREMENTS.read_text().splitlines(keepends=True)


def _split_vcs(lines):
    """The exporter emits the VCS requirement inline; the committed files carry it separately."""
    kept, carved = [], []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("jev-compact @ "):
            carved.append(ln)
            i += 1
            while i < len(lines) and lines[i].lstrip().startswith("#"):
                carved.append(lines[i])
                i += 1
            continue
        kept.append(ln)
        i += 1
    return kept, carved


def test_requirements_txt_matches_the_lock():
    """A lock change without a re-export fails here, naming the command that fixes it."""
    want, _ = _split_vcs(_exported())
    got = _drop_path_header(_committed())
    if want != got:
        added = [ln for ln in want if ln not in got][:5]
        gone = [ln for ln in got if ln not in want][:5]
        pytest.fail(
            "requirements.txt is stale against uv.lock.\n"
            f"  + {added}\n  - {gone}\n"
            "Regenerate: uv export --no-dev --extra space --extra durable --no-emit-project -o requirements.txt"
            " (then move the bare jev-compact line into deploy/server-001/requirements-vcs.txt)"
        )


def test_every_registry_package_carries_a_hash():
    text = REQUIREMENTS.read_text()
    pinned = [ln for ln in text.splitlines() if "==" in ln and not ln.lstrip().startswith("#")]
    assert pinned, "requirements.txt has no pinned packages"
    for ln in pinned:
        block_has_hash = "hash=sha256" in text.split(ln, 1)[1].split("\n\n", 1)[0]
        assert block_has_hash, f"no hash for: {ln}"


def test_the_vcs_dependency_is_not_in_the_hashed_file():
    assert "jev-compact" not in REQUIREMENTS.read_text(), (
        "jev-compact is back in requirements.txt; pip refuses to install that file with --require-hashes"
    )
    assert "jev-compact @ git+" in VCS.read_text()


def test_the_vcs_pin_is_a_full_commit_sha():
    lines = [ln for ln in VCS.read_text().splitlines() if ln.startswith("jev-compact @ ")]
    assert lines, "requirements-vcs.txt has no jev-compact line"
    ref = lines[0].rsplit("@", 1)[-1]
    assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), f"not a full SHA: {ref!r}"


def test_the_runtime_export_excludes_the_test_toolchain():
    text = REQUIREMENTS.read_text()
    assert "pytest" not in text.lower() or "pytest==" not in text
    assert "ruff==" not in text


def test_the_export_carries_both_extras():
    text = REQUIREMENTS.read_text()
    assert "e2b==" in text, "the space extra (e2b) is missing, so the SPACE backend would not install"
    assert "temporalio==" in text, "the durable extra (temporalio) is missing"


def test_the_dockerfile_installs_from_the_hashed_file_and_the_carve_out():
    text = DOCKERFILE.read_text()
    assert "--require-hashes -r requirements.txt" in text
    assert "requirements-vcs.txt" in text
    assert 'uv pip install --python /opt/venv/bin/python "temporalio==1.35.0"' not in text, (
        "temporalio is installed outside the lock again"
    )
    assert "COPY pyproject.toml requirements.txt ./" in text, "the py stage does not copy the hashed file in"


def test_the_dockerfile_documents_the_regeneration_command():
    text = DOCKERFILE.read_text()
    assert "uv export --no-dev --extra space --extra durable --no-emit-project" in text


@pytest.mark.slow
def test_a_clean_install_verifies_every_hash_and_a_removed_hash_fails(tmp_path):
    """Runs real pip. Marked slow: it resolves ~60 packages into a throwaway venv."""
    uv = _uv()
    good = tmp_path / "venv-good"
    assert subprocess.run([uv, "venv", str(good), "--seed", "-q"], timeout=120).returncode == 0
    py = good / "bin" / "python"
    ok = subprocess.run(
        [str(py), "-m", "pip", "install", "-q", "--require-hashes", "-r", str(REQUIREMENTS)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert ok.returncode == 0, ok.stdout[-2000:] + ok.stderr[-2000:]

    tampered = tmp_path / "tampered.txt"
    lines = _committed()
    dropped = 0
    for i, ln in enumerate(lines):
        if "hash=sha256" in ln and i > 50 and dropped == 0:
            lines[i] = ln.split("--hash")[0] + "\n"
            dropped = 1
    assert dropped, "no hash line was available to remove"
    tampered.write_text("".join(lines))

    bad = tmp_path / "venv-bad"
    assert subprocess.run([uv, "venv", str(bad), "--seed", "-q"], timeout=120).returncode == 0
    bad_run = subprocess.run(
        [str(bad / "bin" / "python"), "-m", "pip", "install", "-q", "--require-hashes", "-r", str(tampered)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert bad_run.returncode != 0
    assert "DO NOT MATCH THE HASHES" in (bad_run.stdout + bad_run.stderr)
