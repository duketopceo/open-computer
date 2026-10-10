# Determinism: rules and tests

These rules are non-negotiable and enforced without LLM judgment. Every rule
has a deterministic test. A build that fails any of these is not shippable.

## Rules

1. **Fail-closed.** If the sandbox cannot start, tool execution is disabled —
   never silently downgraded.
2. **Egress deny-by-default.** A task's allowlist is explicit; everything else
   is refused at the network boundary.
3. **No credential material inside the VM, ever.** Secrets are substituted at
   the boundary (OpenShell-style); the agent only ever sees scoped placeholders.
4. **Sub-agents coordinate via the filesystem protocol only.** No direct API
   calls between sub-agents. Inbox/outbox dirs, lease files, tombstones.
5. **Frontier-model escalation requires explicit per-step approval.** The
   Portable Computer gate, applied to cloud: the PII classifier scans, the human
   approves exactly what leaves.
6. **Every policy change produces a dry-run diff and requires human approval.**
   Agents cannot approve their own policy expansions.
7. **Dependency gaps queue, never hallucinate.** A consumer sub-agent waits for
   its producer's tombstone; it does not invent interim results.

## Test matrix

| Component | Test | Pass criterion |
|---|---|---|
| Router | Extend wisp-route-fuzz golden set (180 utterances) to model selection | exact expected route per fixture |
| Router | orchestral bench: Jev vs pplx-decider-v1-27b | quality-per-dollar ranking, reproducible |
| CubeVM | Cold boot timing | <100ms from snapshot template |
| CubeVM | Snapshot → clone → rollback round-trip | byte-identical filesystem state |
| CubeVM | Egress default | outbound connection refused with empty allowlist |
| OpenShell | Policy dry-run diff | diff lists every newly granted host/API/credential path |
| OpenShell | Self-approval rejection | agent-proposed policy change without human approval is denied |
| OpenShell | Credential substitution | agent-visible token ≠ real secret; real secret works at boundary |
| PII-Tracer gate | Labeled PII fixtures | flagged before any cloud call; approval recorded in audit log |
| Sub-agents | Spawn → write → parent collect | parent reads exactly the child's outbox output |
| Sub-agents | Dependency ordering | consumer queues (does not hallucinate) until producer's tombstone lands |
| Sub-agents | Lease expiry | orphaned lease is reclaimed; task re-queued, not lost |
| jev-compact | Round-trip fidelity | compact → restore preserves all golden facts |
| Escalation gate | Unapproved cloud call | blocked before any token leaves |
| Numbat | CEL rule violation | monitor records; enforce mode blocks pre-action |
| Bumblebee | Known-compromised fixture | flagged in scan output |
| Browsesafe | browsesafe-bench subset | meets published baseline; documented bypass caveat acknowledged |
