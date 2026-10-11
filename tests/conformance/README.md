# Conformance harness

Executable spec for `docs/determinism.md`. Runs before any feature code —
a unit is only done when its matrix rows flip from PENDING to PASS.

    uv run --with pytest pytest tests/conformance -v

With a local CubeSandbox SDK checkout (same pattern as opsview matrix run):

    uv run --with pytest --with-editable ~/src/cubesandbox/sdk/python pytest tests/conformance -v

## Layout

- `checks/` — pure check functions over adapter objects. This is the spec:
  read `checks/__init__.py` for the adapter contract each unit must expose.
- `rules/` — pytest bindings, one file per determinism.md rule (R1–R7) plus ROUTER and AUTH.
- `fakes.py` — in-memory reference semantics (Good*) plus single-violation
  stubs (Broken-ish names). Meta-test only; never shipped adapters.
- `test_meta.py` — positive control (all checks pass on Good fakes) and
  negative control (every check raises CheckFailed on its violating fake).
- `fixtures/` — golden corpora: labeled PII, agent policy-change proposals.
- `conftest.py` — binds real unit adapters via `pytest.importorskip`
  (`oc.fsproto`, `services.execplane`, `services.gate`, `services.router`,
  `services.opsview`) and prints the per-rule matrix at session end.

## Activating rows

When a unit lands, expose `adapter(tmp_path)` in its package (or fixtures below):

- U1 `oc/fsproto/` → rows R4, R7
- U2 `services/execplane/` → rows R1, R2, R3, R6 *(PENDING if CubeSandbox unreachable)*
- U3 `services/gate/` → rows R5 (+ audit rows under R5)
- U4 `services/router/` → row **ROUTER** (`rules/test_router.py` → `checks/router_checks.py`)
- F-AUTH `services/opsview/` → row **AUTH** (`rules/test_auth.py` → `checks/auth_checks.py`; `ops` fixture spins ephemeral server)

Until then the row reports PENDING — which is the statement "unverified,"
not "passing."
