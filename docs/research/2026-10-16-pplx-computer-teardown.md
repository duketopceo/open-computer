# Perplexity Computer vs open-computer: teardown and gap map

Date: 2026-10-16. Supersedes the "Perplexity part / open equivalent" column of
`ARCHITECTURE_MAP.md` and the "Still proprietary" section of
`docs/perplexity-open-catalog.md` (dated 2026-10-10).

## Provenance and boundaries

Every claim below carries one of four labels:

- **source-assisted**: read from `github.com/perplexityai/SPACE` at clone time (Apache-2.0).
- **documentation-based**: Perplexity's own blog posts, help center, or public reference material.
- **observed-behavior**: what a Computer user sees in the product UI (activity timeline, approval cards, artifacts, Tasks page).
- **third-party**: press or community write-ups. Treated as unverified.

Not done, by design: no decompilation of Perplexity's web or desktop client. The
client bundle is a third-party artifact under Perplexity's terms, and nothing
below needed it. The orchestrator, router weights for routing in production,
connector hub, and memory system remain closed. Claims about them stay at the
documentation level.

## 1. The headline change: SPACE is open

On 2026-10-15 Perplexity released SPACE (Sandboxed Platform for Agentic Code
Execution) under Apache-2.0
([announcement](https://www.perplexity.ai/hub/blog/open-sourcing-space),
[repo](https://github.com/perplexityai/SPACE)). It has run every Computer
session since July 2026 per
[press coverage](https://tech.yahoo.com/ai/perplexity-ai/articles/perplexity-ships-search-agent-sandbox-181042782.html)
(third-party).

This invalidates the premise in `ARCHITECTURE_MAP.md` that SPACE must be
approximated with CubeVM. The sandbox layer that this repo was reproducing is
now downloadable. What remains closed is the harness above it.

### 1a. What SPACE is (source-assisted)

Repository facts from the clone (~988k lines of Rust across `crates/`, 18 app
binaries, 26 libraries):

| Concept | Meaning in SPACE |
|---|---|
| Template | Starting filesystem and runtime config; a digest reference pins exact content |
| Sandbox | One Firecracker microVM with its own writable (btrfs copy-on-write) rootfs |
| Command session | Authorization for one command; attaching starts the process |
| Operation | Async record for create / pause / suspend / wake / delete; the client polls it |
| Pause | Execution stops, VM memory stays resident on the worker. Not durable. |
| Suspend | Snapshot of memory, runtime state, and rootfs to object storage; VM resources released |
| Fork | A new sandbox from a snapshot; carries the saved egress policy |

Source: `docs/architecture.md`. Control plane records desired state in
PostgreSQL; controllers reconcile. Templates and snapshots live in S3-compatible
object storage. Default idle-to-suspend is six hours.

### 1b. The network and credential boundary (source-assisted)

`proto/net/policy/v1/policy.proto` defines `NetworkPolicyV1`:

- `egress_default_action`: `ALLOW` or `DENY`.
- `allow` / `deny` lists of `ip`, `cidr`, `domain`, or `wildcard_domain`.
- `tls_interception`: `ON` or `OFF`, with per-destination exemptions.
- `network_credential_rules`: reference-only. Each rule binds an opaque
  `credential_reference_id` to an HTTP header name, scoped to an exact host,
  optional port (default 443), optional segment-aware path prefix, and only
  `GET`/`POST`. Every other method fails closed. The proto comment states:
  "Resolved secret values are never part of network policy and are delivered
  to a capable gateway separately."

This is rule R3 of `docs/determinism.md` ("substitution at the boundary only")
implemented as a per-sandbox TLS-terminating gateway that injects headers on
egress. Operator-level named egress profiles are additive and immutable per
sandbox lifetime (`docs/egress-profiles.md`). Wildcard domains are rejected in
profiles.

### 1c. Deployment reality (source-assisted)

`docs/installation.md`: "Use an apt-based Linux x86_64 host with KVM ... these
instructions do not run directly on macOS." Needs `/dev/kvm`, `/dev/net/tun`,
loop devices, btrfs, and a Docker daemon that permits privileged containers.
Reference deployment is Kubernetes via Kind, with PostgreSQL and a Versity S3
gateway. A direct (non-Kubernetes) adapter exists at
`crates/libs/api/src/controllers/substrate/direct.rs`, but the published
guides do not exercise it.

An E2B Compatibility Wrapper (`docs/ecw.md`) accepts the E2B Python SDK
(`e2b==2.21.1`) as a client. ECW "pause" maps to SPACE suspend.

### 1d. Known weaknesses (documentation-based)

[Escaping SPACE: Part I](https://www.perplexity.ai/hub/blog/escaping-space-part-i):
nine frontier-model configurations, 108 attempts, zero VM-to-host escapes.
The same report records successful network-policy bypasses under partial
access conditions. Treat the egress gateway as strong but not proven complete.

## 2. The layer above SPACE: what stays closed

Documentation-based, from
[The AI is the Computer](https://www.perplexity.ai/hub/blog/the-ai-is-the-computer),
[How We Built Security Into Computer](https://www.perplexity.ai/hub/blog/how-we-built-security-into-computer),
[How we engineer safer agents](https://www.perplexity.ai/hub/blog/how-we-engineer-safer-agents),
and the public capability reference.

| Layer | What Perplexity publishes | Open? |
|---|---|---|
| Planner / orchestrator | Breaks a goal into subtasks, assigns agents, enforces rules, manages execution | No |
| Model fleet | "15+" ([Computer for Builders](https://www.perplexity.ai/hub/blog/computer-for-builders)) to "over 20" ([knowledge work post](https://www.perplexity.ai/en-GB/hub/blog/how-computer-is-reshaping-knowledge-work)) frontier models; users can direct model choice | Weights no; decider model yes |
| Subagents | Own tools and context window each; shared workspace filesystem; parent synthesizes; per-agent model choice | No |
| Skills | 50+ domain playbooks loaded on demand, plus user-installed skills | Format observable; runtime no |
| Connectors | 400+ managed; OAuth tokens held server-side; sub-agents get short-lived proxy tokens through an authenticated gateway | No |
| Memory | Cross-session semantic recall over user facts and history; "Brain" wiki | No |
| Scheduling | Durable one-time and recurring automations; connector-triggered runs; Tasks page at `/computer/tasks` | No |
| Privacy Gate | Detected sensitive values stay on the user's Mac; the thread collects the share decision | No |
| Containment | SPACE | **Yes, as of 2026-10-15** |
| Input screening | BrowseSafe | Yes (36% bypass caveat stands) |
| Monitoring | Numbat | Yes |

Observed-behavior notes relevant to this repo's console design:

- Consequential actions (send, publish, delete, spend) surface as an approval
  card inside the thread. The user answers in place. This is the same shape as
  `services/gate/queue.py`, but bound to the specific outbound payload shown.
- Model choice is user-directable per session and per subagent. The repo's
  spend rule (no premium default) is a stricter stance than the product's.
- Computer does not expose credit cost to the agent. The product's cost
  control is at the account level, not per task. A per-task hard cap is a
  genuine difference, not a catch-up item.

Third-party, unverified: claims that sub-agents "communicate via the
filesystem, not APIs" and run in "2 vCPU / 8 GB" VMs
([lablab](https://github.com/lablab-ai/community-content/blob/main/blog/en/perplexity-computer.mdx)).
The official line is only that subagents share a workspace filesystem. Do not
cite the former as fact in `fs-protocol-spec.md`.

## 3. Component map, corrected

| open-computer component | Status (FEATURES.md) | Perplexity equivalent | Verdict |
|---|---|---|---|
| `services/execplane/` CubeSandbox adapter | partial: make_sandbox + exec only | SPACE native API or ECW | **Replace.** Two calls drive execution (`make_sandbox`, `exec`); `_CubeSandbox.close()` also does a best-effort `_sb.kill()`, though no caller is shown to use it. The surface is still tiny, so switching cost is low and only grows as callers adopt more of it. |
| OC-030 pause/resume/snapshot/clone/rollback | open | SPACE pause / suspend / wake / fork, `rollback` named in egress docs | **Adopt.** Stop designing; wrap. |
| OC-031 cold-boot + aarch64 verdict | open | SPACE: x86_64 + KVM only, no macOS | **Decide.** personal dev machine is out for SPACE. Keep CubeVM only if an aarch64 dev loop matters more than parity. |
| OC-032/033 credential boundary v1/v2 | partial / open | `network_credential_rules` + gateway header injection | **Adopt v2 directly.** v1 placeholder scheme becomes unnecessary. |
| R2 egress deny-by-default | shipped, test fixed | `egress_default_action = DENY`, exact domains, no wildcards in profiles | **Keep rule, swap enforcement.** Conformance checks should target SPACE's policy objects. |
| OpenShell (Landlock + seccomp + credential substitution) | research/adopt | Overlaps SPACE gateway and VM isolation | **Drop or defer.** Two policy engines for one boundary is more surface, not more safety. |
| `services/router/` pplx-decider chain | benched 86.7% | Decider is open; production routing is not | **Keep.** Already best-in-class for the price. |
| `services/gate/` PII prefilter + approval queue + audit | partial | Privacy Gate (local) + in-thread approval cards | **Keep; this is the product.** Hash-chained audit (row 12) is still the gap. |
| `services/placement/` | partial | Not public | **Keep.** Local-vs-cloud by PII and budget is yours alone. |
| Worker cost ledger + hard cap (OC-035) | partial | Account-level credits only | **Keep; lead with it.** |
| `oc/fsproto/` subagent filesystem protocol | partial | Shared workspace filesystem (official); inbox/outbox detail unverified | **Keep but shrink.** Do not claim parity with Perplexity's protocol. |
| Temporal durability (OC-044) | open | SPACE operations are async records in PostgreSQL; sandboxes survive restarts | **Re-scope.** Durability of the sandbox is SPACE's. Durability of the plan is still yours. |
| Browser worker (OC-045) | open | Cloud browser for login-gated sites | Later, unchanged. |
| Connectors, memory, skills marketplace | not attempted | Core of the product | Correctly out of scope. |

## 4. What this means for the thesis

The README says the moat is "SPACE, the orchestrator, Model Council, audit
pipeline." Strike SPACE. The moat is now: the harness, the connector hub with
server-side token custody, memory, and the model fleet. None of that is
sandboxing. open-computer's remaining differentiation is exactly what the
FEATURES.md ranking already put first: a real approval gate, a per-task cost
cap, and a placement function that keeps PII local.

Strongest counterargument to adopting SPACE: it is production-scale infra.
Kubernetes, PostgreSQL, S3, KVM on x86_64, ~1M lines of Rust. For one operator
on a laptop that is a heavy dependency. The direct adapter is undocumented. If
the Hetzner box is x86_64 with nested virtualization, it is the only viable
host in the current fleet.

Resolution: make open-computer a SPACE *application*, not a SPACE *alternative*.
The repo's own README already says SPACE-style sandboxes are a commodity; act
on it. Keep `execplane` as an interface with two backends (SPACE via ECW,
CubeVM for aarch64 dev) and stop investing in sandbox primitives.

## 5. Proposed roadmap deltas

1. **OC-009/OC-030**: add a SPACE backend to `execplane`. Start with ECW and
   the E2B Python SDK since it is the tested client. Native API later.
2. **OC-033**: implement credential boundary v2 as `network_credential_rules`
   emission. Delete the v1 placeholder path once a worker uses v2.
3. **OC-031**: record the verdict now: SPACE on x86_64 Hetzner; CubeVM only if
   aarch64 dev loop is required. Measure SPACE cold boot there, not on M1.
4. **ARCHITECTURE_MAP.md**: remove the OpenShell row or mark "superseded by
   SPACE gateway". Update "Still proprietary" in `perplexity-open-catalog.md`.
5. **Conformance (R1–R7)**: re-target execplane checks at SPACE policy objects.
   R2 positive control: allowlisted exact domain succeeds, everything else is
   refused by the gateway. R3: assert no credential bytes in guest env or
   filesystem after an injected call succeeds.
6. **fs-protocol-spec.md**: remove any implication of parity with Perplexity's
   subagent protocol. Cite only "shared workspace filesystem".
7. **README "Why"**: rewrite the moat sentence. Lead with cost cap and gate.

## Sources

- SPACE repository (Apache-2.0): https://github.com/perplexityai/SPACE
- SPACE hosted guide: https://space.docs.perplexity.ai/guide/index.html
- Open-sourcing SPACE: https://www.perplexity.ai/hub/blog/open-sourcing-space
- Escaping SPACE: Part I: https://www.perplexity.ai/hub/blog/escaping-space-part-i
- How We Built Security Into Computer: https://www.perplexity.ai/hub/blog/how-we-built-security-into-computer
- How we engineer safer agents: https://www.perplexity.ai/hub/blog/how-we-engineer-safer-agents
- The AI is the Computer: https://www.perplexity.ai/hub/blog/the-ai-is-the-computer
- Computer for builders: https://www.perplexity.ai/hub/blog/computer-for-builders
- How Computer is reshaping knowledge work: https://www.perplexity.ai/en-GB/hub/blog/how-computer-is-reshaping-knowledge-work
- What is Computer (Help Center): https://www.perplexity.ai/help-center/en/articles/13837784-what-is-computer
- Press on SPACE release (third-party): https://tech.yahoo.com/ai/perplexity-ai/articles/perplexity-ships-search-agent-sandbox-181042782.html
- Community architecture claims (third-party, unverified): https://github.com/lablab-ai/community-content/blob/main/blog/en/perplexity-computer.mdx
