---
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan user-directed
external_source: cloudflare/computer (github.com/cloudflare/computer) @ b6291175 (commit, 2026-10-19) — 9.7k★, MIT, TypeScript
---

# cloudflare/computer design-diff — "give your agent a computer" vs open-computer

## Goal Capsule

Compare Cloudflare's `computer` (browser + code-exec + storage bundled as one agent tool surface on Workers) against open-computer's model (ops-side console, oc.example.com, host :8791). Deliverable: a design-diff doc naming what their tool-shape does that ours doesn't — and what ours gives them nothing for.

## Product Contract

### The axis of comparison

cloudflare/computer is the *agent-facing* side: bundle a browser, filesystem, and code execution into a single sandboxed tool an agent calls. open-computer is the *operator-facing* side: visibility/control console over what agents do on a real machine. They're the same problem from opposite ends — the interesting question is where the tool surface vs the ops surface should meet.

### Scope boundaries

- **In:** read their API surface and architecture, map capabilities (browser, exec, fs, sessions, snapshots) onto open-computer's model, decision doc on borrowable primitives.
- **Out:** deploying anything on Workers; porting open-computer to Cloudflare; implementing borrowed primitives (follow-on work).

### Requirements

1. Enumerate their agent-facing primitives (exact tool calls an agent gets) vs open-computer's operator-facing primitives (what oc.example.com exposes).
2. Diff tables: session model, sandboxing story, persistence/snapshots, per-action cost (Workers pricing model vs local host), what the *agent* sees vs what the *operator* sees.
3. Policy boundary diff: egress control, credential handling (where secrets live, whether guests ever see them), human approval, and audit — vs our PolicyEngine/AuditLog/deny-by-default model. Their platform isolates; ours gates. Name the difference explicitly.
4. Pay attention to their constraint choice: ephemeral Workers isolates vs a persistent real machine — that's the core architectural fork.
5. Output `docs/decisions/cloudflare-computer-diff.md`: borrow/adapt/reject per primitive.

### Acceptance examples

- The doc answers concretely: "open-computer should adopt X shape for Y" or "nothing to take — divergent problems" with evidence.
- Each required comparison (tool calls, session model, sandboxing, persistence/snapshots, cost, agent-vs-operator visibility, policy boundary) has its own section with a per-primitive verdict — a single generic paragraph does not satisfy it.
- Every claim about their API cites a file, doc page, or commit in cloudflare/computer @ b6291175 — no vibes.

## Repo Touchpoints

- `docs/decisions/cloudflare-computer-diff.md` (new)
- `ARCHITECTURE_MAP.md`, `DESIGN.md` — referenced for our side of the diff

## Test Scenarios

Review-only artifact — no automated tests. Completeness check instead:

- Every scope-boundary item under "In" appears in the output doc; every "Out" item does not.
- Every requirement (1–5) maps to a section of the output doc.
- Each verdict in the diff table names the primitive, the verdict (borrow/adapt/reject), and the evidence line.
- The reviewer can answer from the doc alone: which primitives transfer, which don't, and why (platform-bound vs borrowable).

## Risks / Honest Notes

- MIT + 9.7k★ + 4 months old = actively interesting, unproven. Workers-first means their sandbox guarantees come free from the platform — we can't copy that property onto a real host; the diff must be honest about which parts don't transfer.
- Read for the *tool-shape* vocabulary: "a computer" as one bundled tool surface is a framing worth stealing even if no code moves.
