# The credential boundary

Open Computer's design rule is that no credential material ever enters a sandbox VM. A secret becomes a
placeholder in the guest; the host substitutes the real value only in outbound text it is about to send. This file
records what exists and, plainly, what does not work yet.

## What the rule requires

1. The guest holds a placeholder, never the real secret.
2. The host keeps the placeholder-to-secret map in its own memory only.
3. Substitution happens at the boundary, on the host side, in text the host is about to send.

## What exists

| Piece | Where | State |
|---|---|---|
| Placeholder generation | `services/execplane/client.py` `_guest_env` (`:159-166`) | Works: each secret becomes `oc-placeholder-<12 hex>`, and the guest's env var holds that placeholder. |
| Substitution map | `services/execplane/client.py` `_CubeSandbox.__init__` (`:58`) | Exists: a `placeholder -> real` dict, documented as host-memory-only. |
| Substitution | `services/execplane/client.py` `_CubeSandbox.substitute` (`:60-64`) | The method exists and is correct for what it does. |
| Secret minting | `services/device/protocol.py` `mint_secret` (`:35-36`) | Works: 32 random bytes, hex. |

## What does not work

**The substitution branch has never run outside cassettes.** No production caller passes `secrets=` to
`make_sandbox`. The three call sites are `services/orchestrator/engines.py:272`, `services/orchestrator/engines.py:370`
and `services/agents/sandbox.py:398`, and none of them supplies a secrets argument. `ROADMAP.md` says no real worker
uses the boundary either.

So the boundary is **declared and inert**. Reading `_guest_env` and `substitute` in isolation suggests credentials
are handled; reading the call sites shows the branch is dead. This section exists so the first reading is not
mistaken for the second.

**Nothing enforces the rule at the sandbox.** `services/execplane/policy.py` writes an applied policy to
`policy.json`, and nothing reads it when a sandbox is created (`docs/FEATURES.md` row 9). A check that no
credential reaches a VM therefore cannot fail today: there is no code path that would put one there.

## What this means for a reviewer

- Do not cite this file as evidence that credentials are isolated. It records the intended design and its current
  reach.
- The claim "no credential material inside the VM, ever" is a design constraint that is **unexercised**, not a
  shipped guarantee.
- When a caller first passes `secrets=`, that PR is what makes the boundary load-bearing, and it should add a test
  that fails if the real value appears in guest-visible output.

## Why this is documented rather than implemented here

Implementing it needs a decision the code does not settle: which secret store feeds the map (OmaSeal, the host
keyring, an env file), and which outbound boundary performs substitution (the egress proxy, the host-side exec
wrapper). Those are product decisions, so this file records the constraint and its state instead of a plan unit
that would have had to guess. See `docs/plans/2026-11-13-2336-feat-oc-skill-catalog-and-provisioning-plan.md`
(KTD6).
