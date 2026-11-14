---
title: Skill Catalog, Run-Side Resolution and Provisioning Integrity - Plan
type: feat
date: 2026-11-13
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
depth: standard
related: ~/research/PPX-TO-OC.md (the transfer research this plan executes); docs/plans/2026-11-05-feat-pplx-computer-parity-plan.md (U9 MCP client and Plugins page; this plan does not renumber it); ROADMAP.md (OC-075 and OC-082 stay unclaimed)
---

# Skill Catalog, Run-Side Resolution and Provisioning Integrity - Plan

**Target repo:** open-computer. Paths are repo-relative and were read from `main`. Ids: **U** is plan-local, **P0-P2** is priority, **S/M/L** is effort.

## Goal Capsule

- **Objective:** A skill becomes a declared thing that a run resolves and enforces before it dispatches, and the packages installed before any sandbox exists are verifiable.
- **Means:** A loader that refuses an invalid declaration, a run-side resolver that makes that refusal load-bearing, and a `/skills` page that reports what is on disk. Then pin the registry packages that CI and the image install.
- **Authority:** `services/router/model_catalog.py` is the structural precedent for load-and-refuse-on-invalid (`CatalogError(ValueError)` at `:38`, validation helpers `:115-171`, invariants `:183-197`). Atomic writes follow `services/device/registry.py:103`. The spend rule, approval gate and deny-by-default egress are untouched.
- **Stop conditions:** Stop and report if a unit needs a live model call, widens egress, touches the approval gate, adds a paid dependency, or if a step requires resolving a new package version to make it work.
- **Execution profile:** Python plus one TSX page, one PR per unit, based on `main`. U1 → U2 → U3 stack. U4, U5 and U6 are independent.
- **Who finishes:** Each unit's own PR. The owner merges.

---

## Product Contract

### Summary

Open Computer has no skill system. `services/opsview/capabilities.py` returns a hardcoded list of live-health rows (`[{name, kind, status, detail}]`) probing Temporal on `:7233`, llama.cpp on `:8080`, ollama on `:11434`, `CUBE_API_URL` and the OpenRouter key. That list is served as "skills" by `g_api_skills` (`services/opsview/server.py:1158`) and rendered by `ui/src/pages/Skills.tsx`. There is no skill directory, no frontmatter, no loader, and no way for a skill to declare what it needs.

This plan builds the loader, adds the run-side resolver that makes validation matter, points `/skills` at real content, and separately pins the registry packages that get installed before any sandbox exists.

### Problem Frame

**`/skills` is a misnomer, and the page that looks like it is not the page users reach.** `server.py:407` routes `/skills` to `g_spa_route`, so `g_skills` (`:1144`) and `views.page_skills` (`:879`) are unreachable in production. The live surface is `Skills.tsx` reading `/api/skills`, which `g_api_skills` (`:1158`) answers from `capabilities.collect()`. The health probe itself is real information; the problem is only that it is labelled as skills and that there is no skill content anywhere to label.

**Nothing validates a skill's requirements, because nothing loads skills for a run.** `grep -rn skills services/agents/ services/orchestrator/ services/durable/` returns zero hits. A skill naming a tool the platform lacks has no path by which to fail early. That makes a loader alone cosmetic: it would guard a page list. The resolver in U2 is what turns the declaration into something a run is held to.

**The install runs before the sandbox exists.** `deploy/server-001/Dockerfile:26-27` installs `uv sync --locked --no-dev --no-install-project --extra space` and then `uv pip install "temporalio==1.35.0"` outside the lock. That is the image every worker runs from, so a modified registry artifact would execute with host privilege, outside the sandbox and outside egress policy. Two measured facts constrain the fix: `uv export` already emits `--hash=sha256:` lines for every registry package (642 of them with `--no-dev --extra space`), and it cannot emit a hash for a version-control dependency at all.

### Requirements

**Skills**

- R1. A skill is a directory with a `SKILL.md` whose frontmatter declares `name`, `description` and `pulls_tools`, and the loader refuses an invalid declaration with an error that names the file and the offending token.
- R2. `pulls_tools` resolves against a declared capability vocabulary, so a skill demanding a capability the platform cannot provide is refused at load rather than at first use.
- R3. A run resolves its skills before dispatch and reports what it resolved, so a skill that cannot be honoured stops the run rather than surfacing mid-flight.
- R4. `/skills` reports real skills read from disk, and the existing live-health probe keeps working under a name that says health.

**Integrity**

- R5. Every registry package installed in the server image is verified by hash, and the one version-control dependency that cannot be hash-verified is named as a documented exception with its commit pin as the substitute guarantee.
- R6. Reading an archive's entry listing is bounded, so a crafted archive cannot cause unbounded reads.
- R7. `ROADMAP.md`, `docs/FEATURES.md` and `ARCHITECTURE_MAP.md` describe the shipped state truthfully after each unit.

### Success Criteria

- A skill naming a capability outside the vocabulary makes the loader raise, and a test asserts the error names the token and the file.
- A run whose resolved skill demands an unavailable capability fails before the first dispatch, with the reason in the run's audit trail.
- `/skills` renders rows read from disk; adding a `SKILL.md` adds a row with no code change, and the health probe still answers from its own path.
- `pip install --require-hashes -r requirements.txt` succeeds on a clean venv and fails when one `--hash` line is removed. `jev-compact` is installed by its commit pin under a named exception.
- `uv run pytest -m "not live"` and `uv run ruff check .` are green after every unit.

### Scope Boundaries

**Not built:**

- No skill manifest, no `schema_version`, no `retired_entries`. See "Considered and not built" for why.
- No skill marketplace, install-by-URL, or remote registry.
- No Office or PDF text extraction. R6 bounds the listing that exists; there is no parser in this tree and this plan adds none.
- No Windows support (`fcntl.flock` at `services/trail.py:35`, `os.killpg` at `services/relay.py:77`, the Unix socket in `services/actuator/wisp_actuator.py:24`).
- No change to the eight-tool MCP surface in `services/mcp/server.py:162`. U1 reads it; nothing here adds to it.

**Deferred to follow-up:**

- Skill authoring through the UI. Skills are files on disk in this plan.
- Skill ranking or retrieval. U2 resolves what is declared; ordering quality is a later problem.
- Pinning the Cube sandbox template. `make_sandbox` uses `CUBE_TEMPLATE_ID` (default `code-interpreter`), an image this repo does not define, and `SandboxLauncher._seed` asserts the guest already has `git`, `python3` and `ori`. That supply chain is unpinned and is not made pinned by R5, which covers the server image only.

### Considered and not built

- **A versioned skill manifest with `retired_entries`.** Rejected: skills are repo directories shipped by reviewed pull requests, so `git log -- skills/` is already the audit record the manifest would provide, and no reader of `schema_version` exists. The mechanism would add a new module, a committed artifact, five test scenarios and a hard load-time error for a condition no user has hit — including an outage where retiring a skill and restarting takes `/skills` down until someone deletes files by hand. If a second consumer appears, revisit.
- **`uv sync --require-hashes`.** Rejected: it is not a flag. `uv sync` exits 2 on it. Hash enforcement lives on `uv pip install`.

### Dependencies / Assumptions

- `uv export` emits hashes from `uv.lock` without a flag; this was measured on uv 0.12.17 while `deploy/server-001/Dockerfile:20` uses `uv:0.11`. U4 pins one uv version in CI so a generated file is byte-stable.
- Per-step placement flags are booleans in `services/orchestrator/dispatch.py:116-137` (`needs_sandbox`, `needs_egress`, `needs_web`, `needs_apps`) and in `services/orchestrator/engines.py:477-488`. They are not tool names, and U1 must not treat them as one.
- The MCP `TOOLS` dict at `services/mcp/server.py:162` holds control-plane verbs (`list_runs`, `get_run`, `list_approvals`, `request_approval`, `approval_status`, `cost`, `audit_tail`, `place`) and is a real registry. It is not the skill vocabulary.
- `tests/mcp/test_server.py:52` fails on any MCP tool addition unless `OC_UPDATE_GOLDEN=1`. No unit here adds a tool.
- The runtime image copies only `oc`, `services`, `pyproject.toml`, `README.md` and `ui/dist` (`Dockerfile:38-41`). A skills tree outside those paths does not ship, so U1 must place it inside `services/` and U3 must confirm it reaches the image.

---

## Planning Contract

### Key Technical Decisions

- **KTD1. A new `services/skills/` package, peer to `services/connectors/`, with `/skills` moved off the health probe.** Rationale: `opsview` is an observability surface; skills are a loadable content type with validation. Mixing them keeps the health probe and the skill inventory in one module, which is the confusion R4 removes. *(session-settled: user-directed — chosen over a loader-only build and over a minimum-viable parse inside `model_catalog.py`: the owner wants the surface honest, not partial.)* Governs R1–R4; U1, U3.
- **KTD2. `pulls_tools` names capabilities from a declared vocabulary in `services/skills/inventory.py`, not tool names discovered from a registry.** Rationale: the two candidate registries are unusable as a skill vocabulary — MCP `TOOLS` is eight control-plane verbs, and the placement flags are booleans. A declared constant that maps each capability token to its enforcement (a placement flag or a tool registry lookup) is honest, testable, and stable under registry churn. The plan states that this is a naming convention with an enforcement mapping, not a discovery. Governs R1, R2; U1.
- **KTD3. The loader validates and refuses; a separate resolver in the run path consumes it.** Rationale: nothing under `services/agents`, `services/orchestrator` or `services/durable` loads a skill today, so a loader alone would guard only a page list. Splitting them keeps the loader pure and makes the run-side contract explicit and testable. Governs R3; U2.
- **KTD4. `/skills` is re-pointed at the loader from `g_api_skills`, and the health probe moves to `/api/health`.** Rationale: `/skills` and `/api/skills` are different paths and only the second is live; `g_skills`/`page_skills` are unrouted. Separating the paths is what lets both surfaces keep their current tests with an address change rather than a rewrite. Governs R4; U3.
- **KTD5. Hash pinning is generated by `uv export` and enforced by `uv pip install --require-hashes`, with the VCS dependency carved out as a named exception.** Rationale: measured, not assumed — `uv export` writes hash lines for every registry package, and both `uv export --require-hashes` and `uv sync --require-hashes` do not exist as flags. `--require-hashes` cannot verify a git dependency at all: pip refuses with "Can't verify hashes for these requirements because we don't have a way to hash version control repositories". So R5's guarantee is registry-package integrity plus a commit pin. *(session-settled: user-directed — chosen over keeping `uv sync --locked` alone: the owner wants the install that runs before the sandbox to be verifiable.)* Governs R5; U4.
- **KTD6. The credential-boundary constraint is written as documentation, not implemented.** Rationale: placeholders exist in `services/execplane/client.py:158-167` and `_CubeSandbox.substitute` (`:56-65`) exists, but no production caller passes `secrets=`, so the substitution branch has never run outside cassettes and `ROADMAP.md:88` says no real worker uses it. Writing it as a requirement no unit delivers would be a false claim, so it lands as a documented constraint with its non-functioning state stated plainly. Governs R7; U6.

### High-Level Technical Design

**Skill loading and resolution.**

```
services/skills/__init__.py     load_all(root) -> list[Skill]; SkillError
services/skills/schema.py       parse_frontmatter(text); problems(entry, caps) -> list[str]
services/skills/inventory.py    CAPABILITIES: dict[str, str]  token -> enforcement site
services/skills/resolve.py      resolve_for_run(run) -> Resolved; raises SkillUnavailable
skills.ndjson                   one sidecar row per run: what it resolved, or why it refused
```

`CAPABILITIES` is the whole vocabulary, written out as a constant so a reader can see the boundary:

| token | enforcement today |
|---|---|
| `sandbox` | placement flag `needs_sandbox` |
| `egress` | placement flag `needs_egress` + `egress_allowed` |
| `web` | dispatch body `needs_web` |
| `apps` | dispatch body `needs_apps` |
| `memory` | `services/memory/__init__.py` `MemoryClient.call` |
| `mcp:<verb>` | any key of the MCP `TOOLS` dict |

Validation order, each step able to refuse:

1. Frontmatter parses into a flat string map.
2. `name` and `description` are present and non-empty.
3. `name` matches its directory name.
4. `pulls_tools` is optional; absent or empty means the skill requires nothing. Present means every token splits cleanly and appears in `CAPABILITIES` (with `mcp:<verb>` checked against `TOOLS`'s keys).
5. The body is within `MAX_BODY` bytes.

`load_all` collects every problem across every file and raises once. A missing root is an empty set, not an error.

**Provisioning.**

```
requirements.txt   uv export --no-dev --extra space --no-emit-project -o requirements.txt
                   (registry packages only, each with --hash=sha256:...)
jev-compact        installed separately from its commit pin; documented exception
temporalio         either folded into the export or kept as a second named exception
```

The image installs with `pip install --require-hashes -r requirements.txt`; the Dockerfile gains a `COPY requirements.txt` and that install in place of `uv sync`. CI keeps its own `uv sync --locked` (it runs pytest, which the runtime export excludes) and gains one job that runs the same pinned install on a clean venv. A freshness test runs the exporter into a temp file and asserts byte equality, which is what makes a lock bump without a re-export fail.

### Alternative Approaches Considered

| Approach | Why rejected |
|---|---|
| Extend `services/opsview/capabilities.py` with a skill branch | Keeps the health probe and the skill inventory in one module, which is the confusion R4 exists to remove. |
| Validate `pulls_tools` against the MCP `TOOLS` dict directly | That dict is eight control-plane verbs; a realistic skill naming `memory` or `sandbox` would be refused, inverting R2's purpose. |
| A loader with no run-side consumer | Validation would guard a page list while the run path stays unchanged, which is the cosmetic outcome this plan exists to avoid. |
| Keep `uv sync --locked` and add a lockfile check | Verifies the lockfile, not the install. The install is what runs with host privilege. |
| `uv export --require-hashes` / `uv sync --require-hashes` | Neither flag exists; both exit 2. |
| A versioned skill manifest | Git history already is the audit trail, and no reader of a schema version exists. See "Considered and not built". |

---

## Owner Decisions Resolved This Session

- **Skills scope:** a full `services/skills/` service with `pulls_tools` validation and `/skills` moved off the health list. This plan delivers that as a catalog **plus** the run-side resolver (U2) that makes validation load-bearing.
- The Wisp-side decisions are recorded in the companion plan at `wisp/docs/plans/2026-11-13-2336-feat-wisp-framing-and-job-memory-plan.md`.

---

## Implementation Units

### U1. Skill loader with a declared capability vocabulary (P0, M)

- **Goal:** A skill directory loads, and an invalid declaration is refused with a named error. (R1, R2; KTD1, KTD2)
- **Dependencies:** none.
- **Files:** `services/skills/__init__.py`, `services/skills/schema.py`, `services/skills/inventory.py` (all new), `services/skills/catalog/<name>/SKILL.md` (new seeds), `tests/unit/test_skills_loader.py` (new).
- **Approach:**
  1. `inventory.CAPABILITIES` is a module-level dict literal mapping each token to its enforcement site, as the table above shows. It is a declared vocabulary, not a discovery, and the docstring says so.
  2. `schema.parse_frontmatter(text)` reads the leading `---` block into a flat `dict[str, str]`, tolerating CRLF.
  3. `schema.problems(entry, caps, mcp_verbs)` returns `list[str]` naming the file, the field and the bad token. It never raises.
  4. `load_all(root)` returns every skill or raises `SkillError(problems)` with all problems. A missing root returns an empty list.
  5. `MAX_BODY = 64 * 1024` sits beside `CAPABILITIES`, following the `MAX_*` convention at `services/opsview/artifacts.py:17-18`. It bounds the one file the loader reads wholesale.
  6. The skills tree lives at `services/skills/catalog/` so it ships inside the `services` tree the image already copies (`Dockerfile:40`); confirm with a test that the constant path is under `services/`.
- **Patterns to follow:** `services/router/model_catalog.py` for the raise-on-invalid shape; carry the offending value, not just a message.
- **Test scenarios:**
  - A skill with `pulls_tools: memory, sandbox` loads and exposes both tokens (both are in `CAPABILITIES`).
  - `pulls_tools: memory, teleport` raises `SkillError` naming `teleport` and the file path.
  - Missing `description` raises, naming the key.
  - `name` differing from its directory is rejected.
  - Absent `pulls_tools` is valid and yields an empty requirement list.
  - A `pulls_tools` written as `[a, b]` is rejected naming the bracketed tokens rather than loading as one token.
  - CRLF frontmatter parses identically to LF.
  - A missing root returns `[]`, not an error.
  - A body over `MAX_BODY` is refused naming the limit.
  - Three skills, two invalid, raise once and report both files.
- **Verification:** `uv run pytest tests/unit/test_skills_loader.py -v`; `uv run ruff check services/skills/`.

### U2. Run-side resolution before dispatch (P0, M)

- **Goal:** A run is held to what its skills declared, before it dispatches anything. (R3; KTD3)
- **Dependencies:** U1.
- **Files:** `services/skills/resolve.py` (new), `services/orchestrator/__init__.py` (`submit` `:106-124` is where a run is accepted and `_submit` decomposes and dispatches; the resolver call goes before that), `services/orchestrator/skills.py` (new, the sidecar writer), `tests/unit/test_skills_resolve.py` (new), `tests/unit/test_agents.py` (extend).
- **Approach:**
  1. `resolve_for_run(run)` loads the catalog, selects the skills applicable to the run, and checks each declared token against what this run's placement actually provides. A token whose enforcement is a placement flag that the run did not set is a hard refusal.
  2. The resolved set and any refusal reason are recorded before the first dispatch, so the decision is auditable after the fact. `ThinkingLog` (`services/orchestrator/thinking.py:33-47`) is the pattern to copy: one NDJSON sidecar at the run root, `O_APPEND` single-write, and a write failure logs and is dropped rather than failing the run. Use `skills.ndjson`, keyed by `run`. Do not write into `<root>/workspaces/<job>`: that is the seeded file tree the sandbox mounts (`services/agents/workspace.py:137-138`), not the run record store.
  3. A refusal surfaces as the run's own failure reason, not as an exception escaping the scheduler.
  4. If the catalog is empty, resolution succeeds with an empty set. Absence of skills is not an error.
  5. "Applicable" needs a deterministic rule, because there is no per-run skill selection today. Read `pulls_tools` plus one optional frontmatter field, `applies_to`, and load every skill whose `applies_to` is empty or names the run's origin. Say the rule in the module docstring so a later selector replaces it rather than being guessed at.
- **Patterns to follow:** `ThinkingLog` (`services/orchestrator/thinking.py`) for the sidecar; the failure-path conventions in `services/orchestrator/dispatch.py`.
- **Test scenarios:**
  - A run with a skill requiring `egress` and a placement without `needs_egress` fails before the first dispatch, and the reason names the skill and the token.
  - The same run with `needs_egress` set proceeds, and `skills.ndjson` records the resolved set.
  - A run with no applicable skills proceeds with an empty resolved set.
  - A run whose skill is invalid in the catalog fails with the loader's problems, not a stack trace.
  - `skills.ndjson` is written before the first dispatch (assert write ordering against the dispatch log).
- **Verification:** `uv run pytest tests/unit/test_skills_resolve.py tests/unit/test_agents.py -v`.

### U3. `/skills` reports real skills (P1, M)

- **Goal:** The page stops being a health dashboard wearing the name. (R4; KTD4)
- **Dependencies:** U1, U2.
- **Files:** `services/opsview/server.py` (`g_api_skills` `:1158`, add the health route beside it), `services/opsview/capabilities.py` (unchanged behaviour, docstring corrected), `services/opsview/views.py:879` and `g_skills` `:1144` (delete — unreachable), `ui/src/pages/Skills.tsx`, `ui/src/lib/api.ts` (`Skill` `:681`, `getSkills` `:692`), `ui/src/demo/fixtures.ts` (`skills()` `:285-291`), `ui/src/demo/routes.ts` (`:104`, add the health route), `ui/src/demo/demo.test.ts` (`:46`), `tests/opsview/test_automations.py`, `tests/unit/test_opsview_skills.py` (new).
- **Approach:**
  1. `g_api_skills` returns the loaded catalog: `{name, description, pulls_tools, path, status}` where status reports a skill whose declared tokens are unavailable as degraded and names the token.
  2. Add `/api/health` serving `capabilities.collect()`, so the probe keeps a reachable address.
  3. Delete `g_skills` and `views.page_skills`. `/skills` stays on `g_spa_route` (`:407`), which is what actually renders the SPA.
  4. `Skills.tsx` renders the real fields and drops the health-shaped columns. `Skill` is currently `{[k: string]: unknown}` (`ui/src/lib/api.ts:681`), so the row is untyped and every field is coerced; give it real fields in the same edit. `detail()` at `Skills.tsx:48-54` exists only to compensate for that and goes with the columns.
  5. The health rows move to fixtures shaped like `capabilities.collect()`; `ui/src/demo/fixtures.ts:285-291` currently ships `{name, state, detail}` and would render an empty Skills page. `ui/src/demo/routes.ts:104` gains the `/api/health` route, and `demo.test.ts:46` asserts against whichever endpoint it now calls.
  6. Move `test_skills_page_lists_real_capabilities` onto `/api/health` and leave `test_capabilities_report_live_not_static` on `capabilities.collect()`, so the health contract keeps its coverage.
- **Execution note:** read `tests/opsview/test_automations.py` before editing; repointing `/api/skills` without moving those assertions turns the suite red on the first commit. Its fixture list is `OpenRouter, llama, Ollama, Sandbox, gate` plus the four status words (`:9-16`).
- **Patterns to follow:** the existing page/API pairing in `server.py`; do not introduce a new routing style. `pnpm check:demo` (`ci.yml:57`) asserts the fixtures still match the typed API.
- **Test scenarios:**
  - Adding a `SKILL.md` under the catalog makes a row appear with no code change.
  - A skill declaring an unavailable token renders as degraded and names the token.
  - `/api/health` returns the same rows `capabilities.collect()` returned before the move, and the moved assertions pass against it.
  - An empty or absent catalog renders an empty state, not an error page.
  - The demo fixtures satisfy the page's typed fields, and `pnpm check:demo` stays green.
  - `/skills` still renders the SPA.
- **Verification:** `uv run pytest -m "not live"`; the page verified with `tests/e2e` Playwright if the `ui/` toolchain builds in this environment, otherwise the API half lands in its own commit with the page change reviewed.

### U4. Hash-pinned provisioning (P0, M)

- **Goal:** Every registry package in the server image is hash-verified, and the one package that cannot be is named. (R5; KTD5)
- **Dependencies:** none.
- **Files:** `requirements.txt` (new, generated), `.github/workflows/ci.yml` (two `uv sync --locked` call sites, `:27` and `:47`), `deploy/server-001/Dockerfile` (`:23` COPY, `:26-27` install), `tests/unit/test_provisioning_pins.py` (new).
- **Approach:**
  1. Generate with exactly `uv export --no-dev --extra space --no-emit-project -o requirements.txt`. Those flags are not optional: a bare export ships pytest and ruff into the runtime image, and omitting `--extra space` drops `e2b`, which the Dockerfile's `--extra space` currently installs.
  2. Carve out `jev-compact`: its line carries no hash because `uv export` cannot hash a VCS requirement, and `--require-hashes` refuses to install when it is present. Install it from its commit pin in a separate step, and record in the Dockerfile comment that the commit SHA is the integrity guarantee for that one package. R5 states exactly this.
  3. Decide `temporalio`: it is not in the lock (`Dockerfile:24-25` says so). Fold it into the install or keep it as a second documented exception. Do not leave it silently unhashed.
  4. Add `COPY requirements.txt` to the `py` stage (`:23` sits beside `COPY pyproject.toml uv.lock ./`) and switch the install to `uv pip install --require-hashes -r requirements.txt`.
  5. Do not switch CI's own install to `requirements.txt`: both `python` and `console-e2e` run `uv run --locked pytest` (`ci.yml:33`), and the runtime export excludes pytest and ruff by design, so that change would break the job it appears to harden. CI's relationship to the file is the freshness test plus one job that runs `pip install --require-hashes` on a clean venv, which proves the install the image depends on.
  6. The freshness test runs `uv export` into a temp file via subprocess and asserts byte equality with the committed file. Pin one uv version in CI so the header is stable; this box has 0.12.17 and the Dockerfile uses 0.11.
- **Execution note:** run the actual `pip install --require-hashes` against a clean venv during the unit, not only in CI. It is what establishes the `jev-compact` exception is real rather than theoretical.
- **Test scenarios:**
  - `pip install --require-hashes -r requirements.txt` on a clean venv succeeds after the `jev-compact` carve-out.
  - Removing one `--hash` line makes that install fail.
  - The freshness test fails after a `uv.lock` change and names the regeneration command.
  - The freshness test passes on an unchanged tree.
  - `requirements.txt` contains no `pytest` or `ruff` line and does contain `e2b`.
  - The container builds, and its install log shows every registry package verified.
- **Verification:** `uv run pytest tests/unit/test_provisioning_pins.py -v`; a real `deploy/server-001` image build.

### U5. Bounded archive listing (P1, S)

- **Goal:** A crafted archive cannot cause an unbounded read through the one place the tree reads archive metadata. (R6)
- **Dependencies:** none.
- **Files:** `services/workspace/routes.py:298-303` (the uncapped `ZipFile` listing), `services/workspace/caps.py` (new), `tests/unit/test_archive_caps.py` (new), fixtures under `tests/fixtures/archives/`.
- **Approach:**
  1. Constants at module top following the `MAX_*` naming at `services/opsview/artifacts.py:17-18`: `MAX_INPUT = 10 MB`, `MAX_ENTRIES = 4096`, `MAX_LISTING = 1 MB`.
  2. Reject an archive whose byte size exceeds `MAX_INPUT` before opening it, whose entry count exceeds `MAX_ENTRIES`, or whose accumulated entry names and metadata exceed `MAX_LISTING` while iterating `infolist()`.
  3. Do not add `pypdf`, `python-docx` or any extractor. Nothing in this tree expands an archive entry, so a decompression bound would guard a code path that does not exist.
  4. State in the module docstring that this bounds the listing only, and that any future extractor inherits the same constants.
- **Patterns to follow:** `services/opsview/artifacts.py:17-18` for constant naming and bounds.
- **Test scenarios:**
  - An archive larger than `MAX_INPUT` is rejected before being opened.
  - An archive with 4097 entries is rejected, naming the entry cap.
  - An archive whose accumulated listing exceeds `MAX_LISTING` is rejected mid-iteration rather than after building the full list.
  - A legitimate 5 MB archive lists normally.
  - A truncated or non-zip file fails with a named error, not a traceback.
- **Verification:** `uv run pytest tests/unit/test_archive_caps.py -v`.

### U6. Docs sync, including the credential constraint (P2, S)

- **Goal:** The status tables tell the truth, and the credential boundary is recorded with its current non-functioning state stated. (R7; KTD6)
- **Dependencies:** U1 through U5.
- **Files:** `ROADMAP.md`, `docs/FEATURES.md`, `ARCHITECTURE_MAP.md`, `docs/CREDENTIALS.md` (new).
- **Approach:**
  1. Add rows for the skill catalog, run-side resolution, provisioning pins and archive bounds, with real paths.
  2. Correct `docs/FEATURES.md:53`, which is already stale.
  3. Note in `ARCHITECTURE_MAP.md` that `services/skills/` is a peer to `services/connectors/`.
  4. In `docs/CREDENTIALS.md`, state the boundary and name the enforcement sites — placeholders at `services/execplane/client.py:158-167`, substitution in `_CubeSandbox.substitute` (`:56-65`), token minting from `services/device/protocol.py:35-36` — **and state plainly that no production caller passes `secrets=`, so the substitution branch has never run and the boundary is not yet enforced.** A constraint that names its own inert state cannot be mistaken for a shipped guarantee. Do not put this in `docs/determinism.md`: `tests/conformance/test_meta.py` binds determinism rules to tests, and there is no test here.
- **Test scenarios:** none; no executable behaviour. Verification is that every cited path and line exists at write time.
- **Verification:** `ls` each cited path; read each cited line to confirm it still says what is claimed.

---

## Verification Contract

Run from the repo root, after every unit:

```sh
uv run pytest -m "not live"      # testpaths: tests/{conformance,fsproto,opsview,unit,mcp}
uv run ruff check .
```

| Requirement | Command | Pass condition |
|---|---|---|
| R1, R2 | `uv run pytest tests/unit/test_skills_loader.py -v` | An out-of-vocabulary token raises `SkillError` naming the token and the file. |
| R3 | `uv run pytest tests/unit/test_skills_resolve.py -v` | A run lacking a declared capability fails before the first dispatch, with the reason in `skills.ndjson`. |
| R4 | `uv run pytest tests/unit/test_opsview_skills.py tests/opsview/test_automations.py -v` | Adding a `SKILL.md` adds a row; `/api/health` keeps the health assertions green. |
| R5 | `uv run pytest tests/unit/test_provisioning_pins.py -v` plus a real image build | Removing one `--hash` line fails the install; the build verifies every registry package. |
| R6 | `uv run pytest tests/unit/test_archive_caps.py -v` | An oversized archive is rejected before its entry list is built. |
| R7 | `ls` each path named in the new doc rows | Every cited path exists. |

`tests/e2e` is Playwright and outside `testpaths`; it runs for U3's page half only. `tests/conformance/` is untouched: no determinism rule is added.

## Definition of Done

Globally:

- `uv run pytest -m "not live"` and `uv run ruff check .` are green.
- `tests/mcp/test_server.py:52`'s golden file is unmodified.
- No new `# type: ignore`, no bare `except`, type hints on every new signature.
- No unit adds a paid dependency or widens egress.
- Abandoned approaches are deleted from the diff, not commented out.

Per unit:

- U1: `services/skills/` imports nothing from `services.opsview`; the catalog path is inside the tree the image copies.
- U2: `skills.ndjson` is written before the first dispatch, and an empty catalog is not an error.
- U3: `/skills` renders from disk; `g_skills` and `page_skills` are gone; `/api/health` carries the old assertions.
- U4: the install genuinely runs with `--require-hashes`; `jev-compact`'s exception is documented where it is installed, not only in the plan.
- U5: no extractor dependency is added.
- U6: every cited line still exists, and `docs/CREDENTIALS.md` states the boundary is not yet enforced.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `pulls_tools` validated against a static constant drifts from real dispatch as the orchestrator gains flags | High | U2 checks the token against the run's actual placement, not only the constant, so drift surfaces as a refusal rather than a false pass. |
| The declared capability vocabulary is wrong on its first draft | Moderate | Six tokens with a one-line enforcement mapping each; a wrong mapping fails U2's refusal tests rather than silently permitting. |
| `--require-hashes` breaks on `jev-compact` in a way the carve-out does not cover (a transitive VCS dep appearing later) | Moderate | The unit's execution note requires a real clean-venv install, so the failure surfaces in the PR rather than in a deploy. |
| The runtime export and CI's test dependency set drift apart silently | Moderate | The file serves the image only; CI proves it installs on a clean venv and never installs from it, so a missing test dependency shows up as a CI sync failure rather than an image failure. |
| `uv` version drift makes the freshness test flaky | Moderate | Pin one uv version in CI; the test compares exporter output to the committed file rather than to `uv.lock`. |
| The `ui/` toolchain does not build here, leaving U3's page half unverified | Moderate | Split U3 into API-then-page commits; the page half is verified by Playwright separately. |

## What This Plan Deliberately Leaves Unclaimed

- OC-075 and OC-082 keep their current status. U6 records a constraint; it implements nothing.
- The MCP golden file is untouched; a later tool addition is a reviewed change, not a side effect here.
- The Cube sandbox template's supply chain stays unpinned and is named as out of scope rather than left implied.
