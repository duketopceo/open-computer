# Encoded lessons

Rule: a correction made a second time becomes a CI check, not more prose. Run all checks with `make lint-lessons`
(`scripts/check-encoded-lessons.sh`); CI runs them as the `Encoded lesson guards` step of the `python` job, after lint and
before the tests. `scripts/check-*` is in `.github/CODEOWNERS`, so weakening a check needs the owner's review.

| Check | Lesson it enforces |
| --- | --- |
| `check-merge-conflict-markers.sh` | Conflict markers must not be committed |
| `check-client-secret-mirrors.sh` | No `VITE_*`/`NEXT_PUBLIC_*`-style variable that mirrors a server secret |
| `check-workflow-pipe-masking.sh` | A `| tail` in a workflow step must not hide a failing command |
| `check-open-source-hygiene.sh` | Public repo: no operator names or Cloudflare ids; a 32-hex test fixture looks like an account id (runs `tests/unit/test_obs_open_source_hygiene.py`) |
| `check-no-purple.mjs` | No purple, violet or indigo in product colours |
| `check-wizard-fresh.sh` | The generated web setup wizard page is regenerated after a source change |
| `check-403-not-key-invalid.sh` | HTTP 403 is "forbidden", not "key invalid" (ratchet: existing sites are listed in the script, new ones fail) |
| `check-desktop-packaging.sh` | The menu entry runs the launcher, which sets the PATH (`~/.local/bin`, mise shims) and logs to `~/.local/state/open-computer/desktop.log` |
| `check-desktop-logging.sh` | The app starts its own file log (`applog::init()`), redacts it, starts children with the extended PATH, and keeps the transport error reason |
| `check-workflow-hardening.sh` | No `pull_request_target`; every action pinned to a commit SHA |

To add one: write `scripts/check-<topic>.sh` that prints `check-<topic>: ok` or the offending lines plus a "Fix:" paragraph,
register it in `scripts/check-encoded-lessons.sh`, and show it failing on a bad fixture before it passes.
