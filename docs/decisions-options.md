# OC-002 decisions: options for the owner

Not decisions. Each section lists the options, what the code and evidence say, and a recommendation. Record the
chosen option, the date and who signed off in `docs/decisions.md` (does not exist yet) before OC-058/OC-059.

## 1. Name

Registry and plugin names are effectively permanent once published (MCP Registry, plugin marketplaces).

Evidence: unrelated projects already use "open-computer" or "Open Computer Use" (e.g. an e2b-dev repo and an MCP
launched 2026-05-14; see docs/FEATURES.md, conflicts section; secondary sources, details unverified).

| Option | For | Against |
|---|---|---|
| A. Keep `open-computer` | matches repo, README, all docs; descriptive | collides in search and registries; confusion with other projects |
| B. Keep the repo name, publish under a qualified name (`duketopceo/open-computer`, `oc-gate`) | no rename cost; namespace is the owner's | two names to explain |
| C. Rename before first publish | clean slate, best time to do it | touches repo, imports (`oc/`), docs, logo |

Recommendation: B for now (nothing is published yet), decide C only if a trademark or search check finds a real
conflict. Needed from the owner: whether "Computer" in the name is wanted given the Perplexity comparison.

## 2. Router service: keep, shrink or delete

Evidence: bench (#2) says the hosted decider wins on labels (86.7% route and tier) at about $0.000004 per decision
and a self-hosted router did not stay up. PR #6 said "do not build a router service"; `services/router/` landed
anyway (about 200 lines, tested, budget-gated, decider -> jev -> table chain) and placement/orchestrator depend on it.

| Option | Effect |
|---|---|
| A. Keep as is | works, tested; one more service surface |
| B. Shrink to the engine layer behind `place()` (recommended) | the router stays a library; no separate process, no HTTP surface; ledger unified with the OC-035 cost ledger |
| C. Delete | placement would call the decider directly; loses the offline table fallback and budget gate, needs rework of dispatch |

Recommendation: B. The code is already shaped that way (no server); the work is merging its decision `Budget` with
the run cost ledger.

## 3. fsproto: keep or defer behind the agentflow journal

Evidence: PR #6 proposed deferring `oc/fsproto/` until a real multi-writer failure appears. The code exists, has a
spec (v0.2), 16 conformance tests including a lease-race proof, and the orchestrator runs on it (#14-#16, #20).

| Option | Effect |
|---|---|
| A. Keep (recommended) | fixes already landed; mailbox is the only multi-process coordination primitive tested |
| B. Defer behind agentflow's journal | one less module to maintain; orchestrator rework and loss of tombstone/lease semantics |

Recommendation: A. Cost of keeping is low now that the deviations are closed (#20); the remaining work is property
and crash-point tests.

## 4. Premium models

The spend rule says no premium model as a default. Today: no tier names one (enforced by
`tests/unit/test_spend_rule.py`); ceiling is Grok 4.7 (about $1.60/$4.80 per Mtok); orchestrator default
`deepseek-v4-pro`.

| Option | Effect |
|---|---|
| A. Keep the rule as is (recommended) | frontier tier means "Grok 4.7" |
| B. Allow a premium model as an explicit per-goal opt-in, always through the gate | adds a tier above frontier; each use needs a recorded approval and shows its cost before it runs |
| C. Allow as a configurable default | contradicts the rule; not recommended |

Recommendation: A now; revisit B only if a golden task fails on every allowed model.

## 5. Also waiting on the owner

- Logo sign-off (OC-051), and whether the Silver Lining console remains the admin surface while the chat UI is built.
- Whether the first public listing should wait for a recorded (not synthetic) cassette set and a live R1-R3 run (OC-029/OC-031).
- Run `scripts/record_cube.py` against the local CubeSandbox once (a human decision: it boots real microVMs).
