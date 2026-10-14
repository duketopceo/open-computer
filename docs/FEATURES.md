# open-computer: feature inventory (verified against code)

> **Snapshot notice.** Written 2026-10-11 against `de1440b`/`1288ce4`. Landed on `main` since: F-AUTH passkey gate (#11), workplace shell (#12), agentflow audit fixes (#14), placement rule table (#15, rows 20 now partial), orchestrator decompose/dispatch/collect (#16, row 19 now partial), and the Silver Lining console with a hash-bound approval queue in `services/gate/queue.py` (#17). PRs #2 and #6 are merged. Rows below are otherwise unchanged; `ROADMAP.md` is the live status board and each PR that closes a gap edits the row. The "README is stale" remarks were fixed by OC-001.


As of 2026-10-11. Source of truth: `origin/main` at `de1440b`/`1288ce4` (U0 #3, spec #4, U1 #5, U3 #7, U2 #8, ops view #9, U4 #10). I extracted `origin/main` to a scratch dir and ran the suite there; the repo was not touched. Open PRs #2 (router bench) and #6 (program roadmap, design, logo) are docs/bench only and are **not** in main.

Status key: **shipped+tested** (code and a passing check that would fail if the code were wrong), **shipped untested** (code exists, no meaningful test), **partial** (code exists but misses part of its own spec/contract), **spec only**, **missing**.

## 1. What open-computer is for, and who uses it

**One line:** a self-hosted, cost-capped control plane that runs an agent's subtasks in disposable sandboxes with deny-by-default egress, and lets nothing leave for a cloud model without a recorded human approval.

**Who:** one operator first (Luke, on his own M1 Max / Hetzner / CubeSandbox box) who wants Perplexity-Computer-style "goal in, artifact out" without runaway spend or data leaving unreviewed. Second audience: people who run coding agents (Claude Code, Codex, Cursor, Devin) and want a safe execution + approval + cost layer behind them. It is not a hosted SaaS, not a connector hub (Perplexity's 400+ connectors), not a memory product. The landscape doc on the program branch reaches the same position (`docs/research/2026-10-10-landscape.md`, PR #6).

**Honest status:** it is a set of tested safety primitives (protocol, policy engine, gate, router, ledger) plus a live CubeSandbox adapter. **It cannot yet take a goal and produce an artifact.** There is no orchestrator, no CLI, no placement function, no credential injection, no snapshot API. `README.md` on main still says "Research phase ... No runtime code yet", which is stale.

## 2. Inventory

| # | Feature | Component / path | Status | Gap (verified) |
|---|---|---|---|---|
| 1 | Determinism rules R1-R7 as executable checks | `tests/conformance/{harness,checks,rules}` | shipped+tested | 17 registered checks, 17 bound tests. Two checks are weak (see 2a). |
| 2 | Harness meta-test (positive + negative control) | `tests/conformance/test_meta.py`, `fakes.py` | shipped+tested | 12 cases. Covers fsproto, execplane, policy, gate. **No negative control for router checks.** |
| 3 | fs protocol spec v0.1 | `docs/fs-protocol-spec.md` | shipped (spec) | Implementation deviates in 6 places (2b). |
| 4 | fs protocol reference impl: send/collect/claim/expire/enqueue/ready/verify | `oc/fsproto/__init__.py` (228 lines) | partial; deviations resolved (OC-019, spec v0.2) | (mostly fixed; remaining: bind-mount isolation, property/crash tests) was: Passes 4 checks. Missing: `.bad/` quarantine (§6), per-task `deps` (§1, impl stores per-agent), hard-linked inbox, `ttl` (always `None`), immutability of `result.md`, lease-holder check on write, race-safe lease reclaim, bind-mount isolation. |
| 5 | Sandbox create / exec on CubeSandbox, deny-by-default egress | `services/execplane/client.py` | partial | Only `make_sandbox` + `exec`. No timeout on exec, no teardown/kill, no pause/resume/snapshot/clone/rollback, no file I/O. U2 plan listed all of these. |
| 6 | Fail-closed boot (R1) | `client.py` `_FailedSandbox` | shipped+tested (live only) | Row is PENDING without CubeSandbox. Any exception, including misconfig, silently becomes `_FailedSandbox` with no logged reason. |
| 7 | Egress allowlist (R2) | `client.py` `allow_out` | shipped; test fixed (OC-015) | (fixed: positive control added, mutants fail it) was: `empty_allowlist_blocks_egress` passes on *any* non-zero curl exit (DNS failure, no route) so it cannot distinguish "denied" from "broken". `allowlist_is_exact` never asserts the allowlisted host **succeeds**. |
| 8 | No credentials in VM (R3) | `client.py` placeholders | partial; test fixed (non-vacuous) | (fixed: guest must hold a placeholder, host `substitute()` resolves it; mutants fail) was: The secret is simply never sent, so the check passes trivially. **Nothing maps a placeholder back to the real secret at a boundary.** U2's `creds.py` does not exist. |
| 9 | Policy engine: propose / dry-run / human-only apply (R6) | `services/execplane/policy.py` | partial | Tested. But applied policy is only written to `policy.json`; nothing reads it when creating a sandbox. `_items` handles egress/cred/fs-write only (no process, no revoke). Non-atomic write. `policy_agent_proposals.jsonl` fixture (4 rows) is **unused by any test**. Its row `benign-egress` (`expect_denied_without_human: false`) contradicts R6. |
| 10 | PII prefilter (rules) | `services/gate/pii.py` | shipped+tested | 5 regex classes, Luhn for cards, 8 labelled fixtures. No names, no phone, no DOB, no IPs. PII-Tracer model stage is a comment, not code. Recall on real data unmeasured. |
| 11 | Escalation gate: block unapproved cloud call, zero bytes (R5) | `services/gate/gate.py` | partial; approval fixed (OC-020) | (fixed: approval bound to payload hash, single use, expiring, forgery refused, `sender` is the only egress) was: `approved` is a bare bool from the caller. Approval is not bound to a payload hash, so it is replayable. `approved=True` with PII flagged is allowed (only logged). `bytes_sent` is `len(str(payload))`, a number, not evidence: **the gate never sends anything**, so "zero bytes leave" is by construction of the stub, not enforced egress. |
| 12 | Append-only audit log | `services/gate/audit.py` | partial | `delete()` raises, but the file is a plain NDJSON that anything can edit. No hash chain, no fsync, no file mode. Timestamps are float epoch. |
| 13 | Approval console (TUI) | `services/gate/console.py` | partial | Pure render + `a`/`r` keys, injectable I/O. No `d`/`i`/`?`/`:` keys the DESIGN.md promises, no diff-vs-redacted, no state list, no EOF/Ctrl-C handling (EOF raises, which is fail-safe by accident). **Zero tests** (the roadmap said "console renders every gate state deterministically"). |
| 14 | Router: decider -> jev -> table chain, budget gate | `services/router/{router,decide,policy_table,budget}.py` | partial | Chain, malformed-reject, budget-halt tested (4 checks). Gaps: (a) the 180-fixture corpus is **never replayed through the service** (U4 acceptance said exact-match >= 86.7%); (b) `RouteDecision.ms` holds **seconds** (`decide.py` `time.time()-t0`), the ops view papers over it with `if ms<10: *1000`; (c) `budget.check()` is called with est_cost 0 so the pre-flight cap is inert; `task_spend` is per-instance memory; (d) `candidates()` is dead code; (e) `TIER_MODELS` lists `anthropic/claude-*` which the spend rule excludes by default; (f) no key -> silently table-only; (g) "jev" is the hosted OpenRouter `typesafe/jev-1.13`, not local jev-shim, so there is no local/offline path. |
| 15 | Cost ledger + daily/per-task cap | `services/router/budget.py` | partial | Covers decision spend only. **Worker/model spend is not recorded anywhere**; the thing PR #6 calls the headline feature (per-task cost ledger and cap) does not cover the work itself. Day boundary uses local time (midnight flake risk). |
| 16 | Ops view (web, `:8791`) | `services/opsview/server.py` (261 lines) | shipped untested | Renders matrix, sandboxes, audit tail, route tester. Zero tests. Bugs seen live: audit timestamps print as raw epoch (`1791704867.1325488`); empty `href` when `CUBE_API_URL` unset; hard-coded `~/src/cubesandbox/sdk/python`; audit path `/tmp/oc-audit/` differs from where `Gate` writes unless env set; `/route` makes a paid call if `OPENROUTER_API_KEY` is set and has no confirm; `/run` boots real microVMs from a GET; PENDING rows show as absent. |
| 17 | Bench: 180-fixture routing corpus + 3-leg results | `bench/router/*` (PR #2, **not merged**) | shipped (branch) | Reproducible: deterministic corpus builder. Labels are one reviewer's judgment; loop leg n=9. |
| 18 | `oc` CLI (`open-computer run`) | none | missing | DESIGN.md (PR #6) specifies it. No `pyproject.toml`, no entrypoint. |
| 19 | Orchestrator: goal -> subtask DAG -> dispatch -> collect | none | missing | PR #6 says compose orchestral + agentflow instead of building. Neither is wired. |
| 20 | Placement policy (local vs cloud, PII -> never cloud, over budget -> local) | none | missing | U5 never started. This is the glue that makes router + gate + budget one decision. |
| 21 | Credential boundary (substitute at proxy) | none | missing | See row 8. |
| 22 | Snapshot / clone / rollback / pause / resume | none | missing | Determinism table row "byte-identical rollback" has no check. |
| 23 | Cold-boot timing (<100 ms) | none | missing | No check, no measurement in repo. |
| 24 | PII-Tracer model stage | none | missing | |
| 25 | Input screening (browsesafe) | none | missing | PR #6 recommends dropping (36% bypass, 30B model). Decide and record. |
| 26 | Sandbox bind-mount isolation of `agents/<id>/` | none | spec only | Spec §1 says agents see only their dir. No code. |
| 27 | MCP server exposing the stack | none | missing | `services/orchestrator/mcp_server.py` was planned; also the artifact every marketplace needs. |
| 28 | Khan adapter / Temporal durability | none | missing | |
| 29 | Browser / computer-use worker | none | missing | |
| 30 | jev-compact, knowledge index, numbat/bumblebee wiring, DRACO/WANDR eval | none | missing | All "Later" in both roadmaps. |
| 31 | CI | none | missing | No `.github/`. Repo is public (MIT), so standard GitHub-hosted runners are free ([GitHub docs](https://docs.github.com/en/billing/concepts/product-billing/github-actions)). |
| 32 | Packaging (`pyproject.toml`, lockfile, pinned Python) | none | missing | Tests run via `uv run --with pytest`; conftest does `sys.path.insert`. |

### 2a. Weak checks (they pass when the code could be wrong)

1. `empty_allowlist_blocks_egress` (`execplane_checks.py`): pass = `returncode != 0`. A guest with no DNS passes. Needs a positive control (same command succeeds with the host allowlisted) and an error-class assertion.
2. `allowlist_is_exact`: asserts only the negative. Passes if the allowlist feature is entirely broken and everything is blocked.
3. `no_credential_material_in_guest`: vacuous until a real substitution path exists; it cannot fail on the current adapter.
4. `unapproved_cloud_call_blocked`: tests only `approved=False`. No test for `approved=True` + PII, replayed approval, or approval for a different payload.
5. Router checks have no meta negative control (e.g. a router that accepts malformed output).

### 2b. fs protocol deviations from its own spec (code read, not guessed)

| Spec | Code |
|---|---|
| `tasks/<id>/deps` per task | `agents/<consumer>/deps` per agent; `ready()` and `enqueue()` take an agent name |
| `result.md` immutable once written | `_write_record` renames over an existing file; a second `outbox_write` silently replaces a tombstoned result |
| lease claim atomic, races lose cleanly | `claim()` does `exists -> valid? -> unlink -> O_EXCL create`. Two claimers who both see an expired lease can each unlink the other's fresh lease. TOCTOU. |
| corrupted record quarantined to `.bad/` | `verify()` returns False; nothing is moved |
| `ttl` in envelope | always `None` |
| inbox entries are hard links | plain files containing the id |
| `claim` only on non-final tasks | `claim()` on a tombstoned task succeeds and flips status to `leased` |
| `collect(parent, ...)` | `parent` is unused |

## 3. Missing features, ranked

Ranking = (does it block "goal in, artifact out") x (does it make an existing safety claim true) / effort.

1. **Gate enforcement that is real** (rows 11, 12): one function that is the only code path to a cloud call, approval bound to a payload hash, PII + approve handled explicitly, hash-chained audit. Today R5 is satisfied by a stub that cannot send. Medium.
2. **Credential boundary** (rows 8, 21): without it R3 is untestable and any real task that needs an API key either leaks the key into the VM or cannot run. Hard; cheap v1 (PR #6): pass no secrets into sandboxes, workers return data, host calls authenticated APIs.
3. **Placement policy** (row 20): one pure function tying PII flag, budget, latency, tier to {local, cheap, mid, cloud+gate}. Pure table, fully testable, no infra. Easy.
4. **Worker cost ledger + hard cap** (row 15): extend the ledger to every model call, per subtask, per run, cap that halts the run. This is the product's selling point against Perplexity's credit burn. Easy to medium.
5. **Execplane completion** (rows 5, 22, 23): exec timeout, teardown, snapshot/clone/rollback, cold-boot measurement, aarch64 verdict. Medium.
6. **`oc` CLI + vertical slice** (rows 18, 19): one command, three golden tasks, replay mode for determinism. Medium (glue; compose orchestral/agentflow).
7. **Policy -> sandbox binding** (row 9): approved policy actually changes the allowlist; every apply writes an audit row. Easy to medium.
8. **fsproto spec conformance** (rows 4, 26): fix the 8 deviations or amend the spec. Easy to medium. Note PR #6 argues to defer fsproto behind agentflow's journal; the code exists and is tested, so the cheap choice is to keep it and fix the lease race, not delete it.
9. **Ops view hardening and tests** (row 16). Easy.
10. **MCP server** (row 27): unlocks every agent marketplace. Medium.
11. PII-Tracer stage, Khan adapter, browser worker, jev-compact, enforcement wiring, eval suite, knowledge index (rows 24, 28-30). Later; knowledge index is a candidate to cut.

## 4. Conflicts between the plans (resolve first)

- **Unit numbering collides.** Merged PRs use U0-U4 from PR #1 ("U1 fsproto", "U2 execplane", "U3 gate", "U4 router"). PR #6 renumbers to U1-U18 ("U3 = conformance", "U4 = cost ledger", "U6 = sandbox adapter", "U7 = gate", "U9 = placement"). Same labels, different meanings. This backlog uses `OC-nnn` ids and cross-references both.
- **PR #2/#6 say "do not build a router service" and "defer fsproto and the gate console".** Main already contains `services/router/`, `oc/fsproto/`, `services/gate/console.py`. The code landed after the recommendation. Decision needed (backlog `OC-001`): keep and shrink (router becomes the placement function's engine layer; recommended, it is 200 lines and tested), or delete.
- **README says "no runtime code"**; main has ~1,300 lines of runtime code.
- **`ROADMAP.md`** was rewritten on main (MIT decided, Phase 1 = conformance harness) and rewritten differently on the program branch; PR #6 will conflict.
- **Spend rule vs code:** `TIER_MODELS` names Anthropic models; `ARCHITECTURE_MAP.md` names "Opus 5" as orchestrator.
- **Name collision:** several unrelated projects use "open-computer"/"Open Computer Use" (e.g. [e2b-dev/open-computer-use](https://gittrend.io/repo/e2b-dev/open-computer-use), an MCP "Open Computer Use" launched on Product Hunt 2026-05-14, and an "OpenComputer" VM product; search results, unverified detail). Matters for marketplace listings (see MARKETPLACES.md). Decide the listing name before any registry publish, because MCP Registry and plugin names are effectively permanent.
