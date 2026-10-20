---
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan user-directed
external_source: cloudflare/computer (github.com/cloudflare/computer) — 9.7k★, MIT, TypeScript, pushed 2026-10-19
---

# cloudflare/computer design-diff — "give your agent a computer" vs open-computer

## Goal Capsule

Compare Cloudflare's `computer` (browser + code-exec + storage bundled as one agent tool surface on Workers) against open-computer's model (ops-side console, oc.shippedit.dev, host :8791). Deliverable: a design-diff doc naming what their tool-shape does that ours doesn't — and what ours gives them nothing for.

## Product Contract

### The axis of comparison

cloudflare/computer is the *agent-facing* side: bundle a browser, filesystem, and code execution into a single sandboxed tool an agent calls. open-computer is the *operator-facing* side: visibility/control console over what agents do on a real machine. They're the same problem from opposite ends — the interesting question is where the tool surface vs the ops surface should meet.

### Scope boundaries

- **In:** read their API surface and architecture, map capabilities (browser, exec, fs, sessions, snapshots) onto open-computer's model, decision doc on borrowable primitives.
- **Out:** deploying anything on Workers; porting open-computer to Cloudflare; implementing borrowed primitives (follow-on work).

### Requirements

1. Enumerate their agent-facing primitives (exact tool calls an agent gets) vs open-computer's operator-facing primitives (what oc.shippedit.dev exposes).
2. Diff tables: session model, sandboxing story, persistence/snapshots, per-action cost (Workers pricing model vs local host), what the *agent* sees vs what the *operator* sees.
3. Pay attention to their constraint choice: ephemeral Workers isolates vs a persistent real machine — that's the core architectural fork.
4. Output `docs/decisions/cloudflare-computer-diff.md`: borrow/adapt/reject per primitive.

### Acceptance examples

- The doc answers concretely: "open-computer should adopt X shape for Y" or "nothing to take — divergent problems" with evidence.

## Repo Touchpoints

- `docs/decisions/cloudflare-computer-diff.md` (new)
- `ARCHITECTURE_MAP.md`, `DESIGN.md` — referenced for our side of the diff

## Test Scenarios

None — analysis artifact.

## Risks / Honest Notes

- MIT + 9.7k★ + 4 months old = actively interesting, unproven. Workers-first means their sandbox guarantees come free from the platform — we can't copy that property onto a real host; the diff must be honest about which parts don't transfer.
- Read for the *tool-shape* vocabulary: "a computer" as one bundled tool surface is a framing worth stealing even if no code moves.
