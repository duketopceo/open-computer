---
title: Orchestrator Slice 2 - Sandbox Launcher
type: feat
date: 2026-10-30
artifact_contract: ce-unified-plan/v1
execution: code
origin: docs/plans/2026-10-29-001-feat-agent-orchestrator-plan.md (Roadmap item 1), residuals of PR #81
---

# Orchestrator Slice 2 - Sandbox Launcher

## Goal Capsule

- **Objective:** a worker step placed `cloud-sandbox` runs its ACP agent harness (`ori opencode acp`) inside a microVM instead of failing closed, with the job's repo seeded into the VM, deny-by-default egress, and every egress host human-approved before it is granted. The host's real repo is never written by the sandbox.
- **Authority:** owner decisions D1-D9 of the parent plan are settled (see below). No new product decision is introduced.
- **Execution profile:** code, tests only. No live model call, no Firecracker/e2b/SPACE launch, no real egress. A fake ACP agent (`tests/fakes/acp_agent.py`) runs under a fake sandbox that executes the VM commands in a temp directory.
- **Stop conditions:** a settled decision invalidated; any test needing the network or a credential file.
- **Finishes the work:** `ce-work` implements; Luke merges.

## Product Contract

### Summary

Slice 1 left sandbox-placed steps failing closed (the parent plan's KTD7). This slice builds the launcher the parent plan's Roadmap item 1 describes, and folds in the PR #81 residuals that live on the same code path. It deliberately does not include job resume, console POST routes, the React trail view, production planner wiring, or the live acceptance run.

### Settled decisions (user-directed, Luke)

| # | Decision | Rejected alternative |
|---|---|---|
| D2 | Workers are real harnesses behind a pluggable ACP interface, OpenCode first | OpenCode-only forever |
| D4 | Placement per task: private data local, heavy/untrusted in cloud microVMs, deny-by-default egress | fixed placement |
| D5 | Plan approval up front, then only risky actions ask: push, deploy, new egress host, spend over 80% | milestone check-ins |
| D6 | $25 per working day default budget, Grok 4.7-class ceiling, default orchestrator model deepseek-v4-pro, enforced by `tests/unit/test_spend_rule.py` | premium models |
| D7 | Data: read prod, write staging | prod writes |
| Parent KTD7 | Sandbox placement fails closed unless the launcher is fully available | run the harness on the host |

### Requirements

- R1. With a launcher configured, a `cloud-sandbox` task runs the same ACP session (initialize, session/new, model option, prompt, permissions) against a harness process inside a VM. With none configured, or any launcher failure, it still fails closed as `WorkerUnavailable`.
- R2. The VM boots with an empty egress list unless hosts were approved. The step result names each approved host. Every host in `egress_hosts` passes one shared shape validator (strict lowercase dotted hostname labels; no IP literal, `localhost`, `.local`/`.internal` suffix, wildcard, scheme, port or trailing dot; the owner sees the punycode form), a loop-level owner approval (`egress` ask, trail rows), and the execplane S5 binding (`bind_egress` refuses hosts no human applied). A shape, S5 or launcher refusal fails the step closed with the reason; an owner denial ends the job `stalled` before any spawn (R9). The validator is one function in `services/agents/worker.py`, run in the loop (so the owner is never asked about a malformed host) and again in the launcher. Turning an owner approval into an S5 grant is NOT built here: S5 grants are made by the existing human policy-apply flow, so a host the owner approves but that has no grant fails closed with the reason `no execplane grant for host`; wiring approval to grant creation belongs with the console approve routes (deferred).
- R3. The repo reaches the VM as a seeded copy of a per-job host workspace; the host repo at `task.cwd` is read-only input. Paths matching one shared, case-insensitive credential denylist (matched on every path component: `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `id_*`, `auth.json`, `.netrc`, `.npmrc`, `.pypirc`, `.git-credentials`, `.aws`, `.ssh`, `.docker`, `kubeconfig`, `*.tfstate`, `*credentials*`, `*secret*`, `service-account*.json`) and `.git/`, `.gitattributes`, `.gitmodules` never enter the VM. Only regular files are seeded (symlinks and special files are skipped, never dereferenced) and every path must resolve under the source root. Seed size is capped at 25 MB. The same denylist is the patch deny list.
- R4. After the run the launcher collects a `git diff --binary` patch from the VM and applies it to the per-job workspace only after verifying it two ways. Before apply: `git apply --numstat -z --summary` plus a header scan reject any path component equal (case-insensitive) to `.git`, `.gitattributes` or `.gitmodules`, mode 120000 or 160000, absolute, `..` or rename/copy paths outside the workspace, denylist paths and size over the cap. Because a header scan can disagree with what `git apply` does, the patch is then applied to a scratch copy and the resulting tree is checked (no `.git` component under any casing, no symlink, no denylist name, nothing outside the root) before the same patch is applied to the workspace. Git runs with `-c core.hooksPath=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `--no-ext-diff --no-textconv`. The next sandbox round seeds from the updated workspace. Nothing is applied to `task.cwd`.
- R5. The VM is always closed (success, timeout, exception). Its backend lifetime is seed budget (120 s) + `task.timeout_s` + collect budget (60 s); seed and collect execs carry their own deadlines. If the VM expires or collect fails, the step result says `work lost` (never a successful empty diff), and on a worker timeout a best-effort collect still runs. Each task gets its own VM: no cross-step egress union and no shared-TTL race (PR #81 sandbox residual, for agent workers).
- R6. The in-VM harness environment is an allowlist (the existing `scrub_env` keys with a VM home). No secret is passed to the VM in this slice (no `secrets` plumbing: the SPACE backend refuses any, and placeholder substitution is OC-033). The OpenCode config and home written into the VM contain no secret material.
- R7. Local harness children are killed as a process group on close and on timeout, so grandchildren cannot outlive a worker and race the next round in the same cwd (PR #81 residual).
- R8. Exception text, ACP stderr tails and `job_end` summaries are redacted before truncation, so a secret cut mid-pattern by a slice cannot survive (PR #81 residual: trail redaction).
- R9. The loop passes the planner's `egress` list into `WorkerTask.egress_hosts` and asks the owner once per distinct host; a denied host ends the job `stalled` like any declined risky action.

### Acceptance

- A1. A fake-sandbox job: the planner spawns a `cloud-sandbox` step with one approved host; the fake agent edits a file in the VM; the host job workspace contains the edit, `task.cwd` is untouched, and the VM was created with exactly that host and closed.
- A2. Unapproved (S5) host, wildcard or IP host, no launcher, VM boot failure: each yields a failed step with a stated reason and no process running. A denied owner approval ends the job `stalled` with no spawn (A9 below).
- A9. A denied owner egress approval ends the job `stalled` and spawns nothing.
- A3. A `.env` file and `.git/config` in the repo are absent from the VM tree; a patch that adds `.env` or edits `.git/hooks/x` or a symlink is rejected and the workspace is unchanged.
- A4. A permission request for `rm -rf` or a credential read inside the VM is refused by `policy.classify`; a risky one reaches the approval hook.
- A5. A fake agent that spawns a sleeping grandchild leaves no live process after a timeout.
- A3b. A hostile patch (`.gitattributes`, gitlink, `.GIT/x`, rename out of tree, header mismatch) and a repo symlink to a credential file are rejected or skipped.
- A2b. An owner-approved host with no S5 grant fails closed with `no execplane grant for host`.
- A6. A secret inside an exception message straddling the 300-char detail cut is redacted in the trail.

## Planning Contract

### Key Technical Decisions

- KTD1. **In-VM relay over request/response exec.** Launch form: `setsid nohup python3 relay.py serve ... >/dev/null 2>&1 &` with every fd detached, then a handshake: the host polls for a `ready` marker (3 tries) and otherwise fails closed with `relay did not survive exec`. The relay refreshes a heartbeat file and `poll` returns liveness; a relay with no `rc` and a stale heartbeat for 5 polls, or 5 consecutive exec failures, is EOF with a nonzero rc (fail fast, not a full-timeout hang). execplane `exec(cmd) -> ExecResult` has no streaming or stdin (both adapters wrap `commands.run`). Rather than extend two SDK wrappers we cannot test live, a small stdlib `relay.py` runs in the VM, owns the harness process, and exposes it through append-only files: `in` (host-to-agent bytes), `out`, `err`, `rc`. The host drives it with short execs: `send` (base64 payload appended to `in`) and `poll` (returns new `out`/`err` bytes past given offsets as base64 JSON, plus `rc`). Rejected: a streaming exec (needs new SDK surface on both backends, cannot be verified without a live VM); a TCP/WebSocket relay (needs inbound network to the VM, which contradicts deny-by-default). Cost: one exec round trip per poll; the poll interval is 100 ms with backoff to 500 ms while idle. Live behavior of backgrounding a process through `commands.run` is unverified and is the first thing the live acceptance run must check (recorded as an open question).
- KTD2. **`AcpConnection` takes an injectable process object** (`proc=`), defaulting to `subprocess.Popen`. `SandboxProc` implements every member `AcpConnection` touches: `stdin.write/flush/close`, `stdout.readline(size)` and `stdout.close`, `stderr.read1(n)` and `stderr.close`, `wait(timeout=None)` (blocks until `rc` is known, raises `subprocess.TimeoutExpired` on timeout), an idempotent `kill()`, and `pid`. `kill()` terminates only the in-VM harness (it writes the relay `close` marker, which kills the harness process group); it never closes the VM, because `AcpConnection._fail` calls it on every connection death and the diff must still be collected afterwards. The VM is closed only by the launcher after collect. The reader thread, framing, permission and timeout logic stay shared and are covered once.
- KTD3. **One fresh VM per sandbox task; the host owns durable state.** The VM is disposable; the per-job host workspace carries state across rounds. This removes the run-shared union/TTL class of bug for agent workers by construction. The run-shared registry in `engines.py` was not reused for exactly that reason (egress union, a fixed-from-creation TTL race); the price is a full seed every round, accepted for now. `services/orchestrator/engines.py` run-shared sandbox code is not touched (it serves the older script worker).
- KTD4. **Egress needs two independent approvals.** The owner approves a host in the loop (D5: new egress host is a risky action) and the execplane policy must already hold a human-applied grant for it (S5, `bind_egress`). Either alone is insufficient. Host shape is validated before either runs.
- KTD5. **Diff-back, never write-through.** Seeding copies, collection is `git diff --binary` against a baseline commit made at seed time, and apply targets only the per-job workspace after path and mode checks. Promotion to the real repo is the later "finish in a PR" slice and stays an owner-approved risky action.
- KTD6. **Credentials in the VM are an open dependency, not solved here.** A real harness needs a model provider host and a credential. Placeholder substitution at the egress proxy is OC-033 (open) and must bind a placeholder to its destination host. This slice passes no secrets, ships the approved-host path, and records that live use waits on OC-033; tests use the fake agent only.
- KTD7. **Process-group kill for local children.** `start_new_session=True` and `os.killpg` on close for the local transport. The sandbox transport's equivalent is the relay `close` marker (KTD2); the VM is closed by the launcher after collect.
- KTD8. **Fail closed everywhere.** Missing launcher, `sandboxes_available()` false, boot failure, seed failure, relay start failure, collection failure, patch rejection: each is `WorkerUnavailable` or a failed result, never a silent local run.

### Scope Boundaries

Deferred, with where each goes next:

- Job resume (state row per round, restart picks up): independent of the launcher, belongs with the console submit/approve routes (parent plan Roadmap 3). Next slice.
- Console POST routes, approve view, React trail view, `since`/`limit` on the job API, `list_jobs` per-job re-read: Roadmap 3.
- Production planner wiring over `services.openrouter`, real `services/placement`, ledger cap before spawn: Roadmap 2.
- Per-tool flag allowlist in `services/agents/policy.py` (PR #81 P1): needs a design pass per allowed tool; a denylist stays until then. The in-VM policy is the same `classify`, so the VM boundary is additional containment, not a substitute.
- `touches_build_file` destination keys, case-insensitive `.git`, `ruff --fix` taint; `_tainted` pruning; trail failure inside `end()`; in-flight spend cap on a 900 s run (the VM lifetime bound in R5 limits wall time, not spend); `_run_sandbox` dead code, `join_thoughts`, duplicate `Approve`, `_RUN_GUARDS` growth, `_with_dep_results` stall: untouched files or unrelated to the launcher.
- OpenCode `session/request_permission` completeness (open question 9) and live relay behavior: need a live run.
- Promotion (the later PR slice) must treat diffs touching build, CI and git-attribute files as risky asks; owner approval creating an S5 grant; credential substitution at the egress proxy (OC-033), git/gh PR tooling, data-engineering guardrails, bundling pins.

## Implementation Units

### U1. ACP transport seam and process-group kill (R7)

- **Goal:** make `AcpConnection` transport-agnostic and stop leaking grandchildren.
- **Files:** `services/agents/acp.py`, `tests/fakes/acp_agent.py` (script key `spawn_grandchild`), `tests/unit/test_agents.py`.
- **Approach:** `AcpConnection(..., proc=None)`; when None, Popen with `start_new_session=True`; `close()` and `_fail` kill via `os.killpg` when the transport is a Popen. `AcpWorker` timeout path closes the group.
- **Test scenarios:** A5 (grandchild pid dead after timeout and after normal close); existing ACP tests unchanged and green; injected fake proc object drives a full initialize.

### U2. Relay and `SandboxProc` (R1, KTD1, KTD2)

- **Goal:** a bidirectional stdio bridge over request/response exec.
- **Files:** `services/agents/relay.py` (pure stdlib, runnable as a script and importable; `RELAY_SOURCE` read from its own file), `services/agents/sandbox.py` (`SandboxProc`), `tests/unit/test_sandbox_launcher.py`.
- **Approach:** relay `serve <dir> -- argv` spawns the harness with a sanitized env passed as JSON, pumps `in` to stdin, stdout and stderr to `out`/`err`, writes `rc` on exit and kills the harness process group when `in` is closed by a `close` marker. `SandboxProc` starts the relay with one exec, runs a poll thread feeding two os pipes that serve `stdout.readline` and `stderr.read1`, chunks `send` at 48 KB, and treats an exec error or a dead VM as EOF with a nonzero `rc`. VM output is untrusted: per-poll cap 256 KB (loop while more is pending), cumulative caps (64 MB stdout, 1 MB stderr) that end the step as `output flood`, a max line length, strict JSON/base64 parsing that fails closed, integer-only offsets, `shlex.quote` on every value placed in an exec command, and a permission request from the VM never auto-approves (it goes through `policy.classify`). Poll exec logging is sampled, not one info line per poll.
- **Test scenarios:** relay SIGKILLed mid-session fails fast; relay that does not survive exec fails the handshake; over-cap output and malformed poll JSON close the connection; line framing across polls; a 1 MB agent line; large prompt chunked send; harness exit code surfaced; exec failure mid-session becomes `AcpClosed`; kill terminates the harness.

### U3. Per-job workspace, seed and collect (R3, R4, KTD5)

- **Goal:** move repo state into and out of the VM safely.
- **Files:** `services/agents/workspace.py`, `tests/unit/test_sandbox_launcher.py`.
- **Approach:** `file_list(src)` from `git ls-files -co --exclude-standard` filtered by the credential patterns and a size cap; `Workspace.init(job, src)` copies (no dereference, regular files only) into `<root>/workspaces/<job>` once, where `<job>` is restricted to `[A-Za-z0-9_-]{1,64}`, the directory is mode 0700 and standalone (`git apply --directory` guards against an enclosing repo); `seed_commands(ws)` makes a tar, base64-chunked into execs, extracts under the VM work dir, then `git init` plus a baseline commit made with `-c user.name=oc -c user.email=oc@localhost`; `collect_commands()` returns the `git add -A && git diff --cached --binary HEAD` exec; `apply_patch(ws, patch)` validates headers (no `.git/`, no mode 120000, no credential-pattern path, size cap) then runs `git apply` in the workspace.
- **Test scenarios:** A3 in full; A3b; rename, mode-change, quoted-path and header-mismatch patches are rejected; a hostile job id is rejected; a rename and a binary file round trip; an oversized repo refuses to seed; a patch with a path escaping the workspace (`../x`) is rejected.

### U4. `SandboxLauncher` and worker integration (R1, R2, R5, R6, KTD3, KTD4, KTD8)

- **Goal:** run the ACP session in the VM and tie it to the egress and approval rules.
- **Files:** `services/agents/sandbox.py` (`SandboxLauncher`), `services/agents/worker.py` (`WorkerTask.egress_hosts`, sandbox branch, `opencode_worker(..., sandbox=)`), `services/agents/opencode.py` (VM home layout helper), `tests/unit/fake_sandbox.py`, `tests/unit/test_sandbox_launcher.py`, `tests/unit/test_agents.py`.
- **Approach:** `SandboxLauncher(plane, vm_dir="/work", ...)`. `launch(task)`: validate hosts; check `plane.sandboxes_available()`; `make_sandbox(egress_allowlist=sorted(hosts), timeout=<seed budget + timeout_s + collect budget>)` (a `_FailedSandbox` or `PolicyDenied` becomes `WorkerUnavailable`); seed; write the OpenCode config into the VM home; start the relay; hand back `(SandboxProc, collect, close)`. The worker uses the same session code with `proc=` and `cwd=vm_dir`, and runs collect and apply in a `finally` before closing the VM. `policy.classify` is called with the VM work dir as cwd. Collect runs after the connection (and so the relay) is closed and before the VM is closed.
- **Test scenarios:** A1, A2 (each refusal), A4, VM closed on timeout and on exception, exactly the approved hosts reach `make_sandbox`, a model not offered still fails closed, env allowlist inside the VM contains no secret.
- **VM template requirement:** the template must provide `git`, `python3` and the harness binary. The launcher runs a one-exec preflight (`git --version && python3 -V && command -v ori`) and fails closed with a precise reason if any is missing.
- **Fake sandbox:** `FakeSandbox.exec(cmd)` runs `bash -c` in a temp "VM root" with the work dir and relay dir mapped under it, so the real relay script and the fake ACP agent run for real; `FakeSandbox` is deliberately hostile like the real backends: a configurable exec timeout that kills the process tree when the exec returns, max command size, max stdout size, injected latency, raise-on-nonzero (cube-style), and an expiry clock; tests cover relay start killed at exec return, an oversized poll payload and a VM expiring before collect. `FakePlane` records `make_sandbox` calls, honors `sandboxes_available`, and can raise the S5 denial.

### U5. Loop egress approvals and redaction (R8, R9)

- **Goal:** owner approval per new host; redact before truncate.
- **Files:** `services/agents/loop.py`, `services/agents/worker.py`, `tests/unit/test_agents.py`.
- **Approach:** `dec["egress"]` validated to a list of host strings, deduplicated per job (asked once per host), each `_ask(job, "egress", {host (punycode), title, placement, seed file count})` keyed per (job, host) and never cached across jobs (an `egress:host` entry in the planner's `risky` list is folded into the same set so a host is never asked twice), denied ends `stalled`, approved set goes on `WorkerTask.egress_hosts`. Add `redact_str` ahead of every `[:N]` cut on `detail`, `job_end.summary` and the stderr tail. `PLANNER_PROMPT` documents the `egress` key.
- **Test scenarios:** A6; denied host stalls the job with no spawn; the same host in two rounds asks once; non-list or non-string egress is ignored as unusable and asks nothing.

### U6. Docs and roadmap

- **Goal:** the roadmap matches what shipped.
- **Files:** `ROADMAP.md` (the existing OC-040 row and its notes; main has no "Open work" section because it lives in open PR #78, which this PR does not touch), `docs/plans/2026-10-29-001-feat-agent-orchestrator-plan.md` (mark Roadmap item 1 as done in this slice with a pointer here).
- **Test scenarios:** none (docs). The PR body flags that the "Open work" entry in #78 needs a one-line follow-up after #78 merges.

## Verification Contract

| Check | Command | Applies to |
|---|---|---|
| Lint | `uv run --locked ruff check .` | all units |
| Tests | `uv run --locked pytest -m "not live" -p no:cacheprovider` | all units |
| Spend rule | `tests/unit/test_spend_rule.py` stays green and no model id above the ceiling is introduced | all units |
| Console build | `cd ui && pnpm build` only if `ui/` is touched (not planned) | n/a |

New tests are run against the origin/main code where meaningful (they must fail there). No live model, VM or network.

## Definition of Done

- This slice proves the launcher against the fake agent and fake sandbox only. Merging it does not claim a real harness reaches a model from a microVM: that needs OC-033 and a live check that a process started through `commands.run` survives the exec returning. The PR and ROADMAP say so.

- R1-R9 each have a passing test; A1-A6 pass.
- Lint and the non-live suite are green; test count recorded.
- `ROADMAP.md` and the parent plan reflect what shipped and what is deferred.
- The PR body lists the deferred residuals with the owner of each and the live-run open questions (relay backgrounding through `commands.run`, OC-033 credential substitution, OpenCode permission completeness).
