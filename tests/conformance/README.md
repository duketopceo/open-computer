# Conformance harness

Executable spec for `docs/determinism.md`. Runs before any feature code —
a unit is only done when its matrix rows flip from PENDING to PASS.

    uv run --with pytest pytest tests/conformance -v

## Layout

- `checks/` — pure check functions over adapter objects. This is the spec:
  read `checks/__init__.py` for the adapter contract each unit must expose.
- `rules/` — pytest bindings, one file per determinism.md rule (R1–R7).
- `fakes.py` — in-memory reference semantics (Good*) plus single-violation
  stubs (Broken-ish names). Meta-test only; never shipped adapters.
- `test_meta.py` — positive control (all checks pass on Good fakes) and
  negative control (every check raises CheckFailed on its violating fake).
- `fixtures/` — golden corpora: labeled PII, agent policy-change proposals.
- `conftest.py` — binds real unit adapters via `pytest.importorskip`
  (`oc.fsproto`, `services.execplane`, `services.gate`) and prints the
  per-rule matrix at session end.

## Activating rows

When a unit lands, expose `adapter(tmp_path)` in its package:

- U1 `oc/fsproto/` → rows R4, R7
- U2 `services/execplane/` → rows R1, R2, R3, R6
- U3 `services/gate/` → rows R5 (+ audit rows under R5)

Until then the row reports PENDING — which is the statement "unverified,"
not "passing."

## Row to unit map (OC numbering; see ROADMAP.md)

R1-R3, R6 `services/execplane/` (OC-009); R4, R7 `oc/fsproto/` (OC-008); R5 `services/gate/` (OC-010);
ROUTER `services/router/` (OC-011); PLACE `services/placement/` (OC-036); ORCH `services/orchestrator/`
(OC-040); AUTH `services/opsview/auth.py` (OC-012).
