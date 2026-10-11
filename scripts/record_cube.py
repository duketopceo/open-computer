"""Record the R1-R3 sandbox scenarios from a REAL CubeSandbox into tests/fixtures/cube/ (live, by hand only).

    CUBE_API_URL=... CUBE_API_KEY=... uv run python scripts/record_cube.py tests/fixtures/cube/r1_r3_recorded.json

Boots real microVMs: never run in CI. The API key is scrubbed from the output; commit only after reading the diff.
Needs a sentinel for R3 only as a fake string, so no real secret is ever sent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "conformance")]

from checks import execplane_checks as ec  # noqa: E402
from services.execplane.cassette import RecordingSDK  # noqa: E402
from services.execplane.client import ExecplaneAdapter  # noqa: E402


def main(out: str) -> int:
    adapter = ExecplaneAdapter(Path("/tmp/oc-record"))
    if not adapter.sandboxes_available():
        print("CubeSandbox unreachable: set CUBE_API_URL/CUBE_API_KEY and install the SDK", file=sys.stderr)
        return 2
    rec = RecordingSDK(adapter._sdk, out, scrub=[os.environ.get("CUBE_API_KEY", "")])
    adapter._sdk = rec
    for fn in (ec.empty_allowlist_blocks_egress, ec.allowlist_is_exact, ec.no_credential_material_in_guest):
        fn(adapter.make_sandbox)
    rec.save()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/cube/r1_r3_recorded.json"))
