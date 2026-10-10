# Router bench results — Phase 0 E2

Date: 2026-10-10 · Branch: `eval/router-bench` · Spend: ~$0.09 on the
`orchestral-eval-v3` key (all calls via `orch`)

**Question:** does any self-managed routing beat hosted baselines on our
workloads?

**Short answer: no self-managed arm wins.** `pplx-decider-v1.1-27b` —
itself a hosted Decisions API call — is the exact-match winner; the
self-managed arm (Jev, run hosted after the local backend failed
availability) tied it on routes and lost on tier selection, and the
fully-local arm could not complete the bench. `openrouter/auto` trails on
headline label accuracy but its *actual routing decisions* are
competitive, and in the loop leg every worker pick passed regardless of
who chose it. **Recommendation: no self-hosted router.** Per the
decision tree a decider win licenses a *thin router service wrapping the
winning backend* — and the winning backend is already a hosted API, so
the thinnest honest implementation of that is calling the Decisions API
from inside the placement policy (R4). Nothing else needs building.

## Candidates

| candidate | kind | endpoint | cost/decision | p50 latency |
|---|---|---|---|---|
| `pplx-decider` | hosted | OpenRouter `POST /api/alpha/decisions`, `perplexity/pplx-decider-v1.1-27b` (resolves `-20261006`) | $4.1e-6 | 331 ms |
| `jev` | hosted | same endpoint, `typesafe/jev-1.13` (resolves `jev-1.13-20260917`) | $1.9e-5 | 248 ms |
| `auto` | hosted | OpenRouter `openrouter/auto` chat completions | ~$1.9e-4/call | 1285 ms |
| ~~jev-local~~ | self-managed | jev-shim `:8931` → llama.cpp qwen3-4b `:8091` | $0 | 8.5 s |
| ~~vLLM-SR~~ | self-managed | — | — | — |

**Two arms dropped, documented:**

- **jev-local (the spec'd arm).** Attempted first per spec. The llama.cpp
  `qwen3-4b` backend is OOM-killed by systemd-oomd under bench load
  (`app.slice` memory-pressure kill — known machine issue); 149/180 and
  166/180 calls returned 502 across two runs before abandonment. Replaced
  with `typesafe/jev-1.13` on the hosted Decisions API — same decision
  model family, same `{state, questions}→{answers}` contract. This is
  itself evidence: *the self-hosted path failed the availability test.*
- **vLLM Semantic Router (stretch).** Omitted: no GPU headroom on this
  host, the local llama.cpp service it would sit beside was already being
  OOM-killed, and it duplicates the Decisions API contract the other
  candidates already cover. Cost of trying it exceeded its information
  value.

## Dataset — `bench/router/corpus.jsonl` (180 fixtures)

The plan referenced a "wisp-route-fuzz golden set (~180 utterances)".
**No such corpus exists** — not in wisp, open-computer, or anywhere on
this machine. It was constructed for this bench; this is the provenance:

| source | n | what |
|---|---|---|
| `wisp-case` | 27 | wisp `tests/test_route.py` heuristic coverage table, verbatim |
| `wisp-trace` | 59 | real fleet transcripts from wisp `decisions.jsonl`/`route_ab.jsonl` logs |
| `synthetic` | 94 | authored utterances covering thin routes and adversarial cases |

Real-trace scrubbing: dictation payloads and personal/legal/financial
utterances were **excluded**, not paraphrased — paraphrasing private
speech still leaks it. What remains is command-shaped traffic (app
control, clicks, workspace ops, screen questions) plus STT noise.

Labels (two axes per fixture):

- `expected_route` — wisp's production taxonomy (`launch`, `tool`,
  `agent`, `learn`, `act`, `dictation`, `answer`, `clarify`), criteria
  copied from `wisp/wisp/stage_route.py JEV_QUESTIONS`.
- `expected_tier` — placement policy: `local` (trivial), `cheap`
  (flash-class), `mid` (strong mid-tier), `frontier` (reserved; 0 fixtures
  need it — nothing in voice-command traffic justifies frontier).

Distribution — routes: act 62, answer 29, launch 21, clarify 20, agent
14, tool 13, dictation 11, learn 10. Tiers: local 134, cheap 38, mid 8.
The corpus is deliberately hard on the `act`/`tool`/`clarify` boundaries
and on compound utterances ("open firefox and go to github") that broke
wisp's heuristic router in production.

Rebuild: `python3 bench/router/build_corpus.py` (deterministic).

## Leg 1 — exact-match on the golden set

Method: decisions candidates receive the fixture utterance as `state`
plus a `choice` question per axis (same payload shape wisp sends in
production). `auto` gets the same classification task as a chat prompt
(route word parsed from the reply) and a second bare-utterance call whose
`response.model` — the model auto actually routed to — is mapped to a
tier via `MODEL_TIER_MAP`/price bands in `run_bench.py`.

| candidate | route acc | tier acc | invalid | errors | spend |
|---|---|---|---|---|---|
| **pplx-decider** | **156/180 (86.7%)** | **156/180 (86.7%)** | 0 | 0 | $0.002 |
| jev | 156/180 (86.7%) | 142/180 (78.9%) | 0 | 0 | $0.006 |
| auto | 147/180 (81.7%) | 38/180 (21.1%) | 20 | 0 | $0.035 |

Top confusions — jev: `act→tool` ×5 (over-routes screen ops to the tool
bus); decider: `clarify→answer` ×4 (answers things it should bounce).

**Winner by margin: pplx-decider** — ties jev on routes, beats it +7.8pp
on tiers, cheapest per decision, and returns calibrated per-option
probabilities (free confidence gates).

### The `auto` numbers need a footnote — read before deciding

- **20/180 invalids are empty completions, not wrong answers** —
  `openrouter/auto` routed 158/180 classification calls to
  `deepseek-v4.1-flash`, a reasoning model that intermittently returns
  `content: ""` after spending the token budget on reasoning. On the 160
  answered items auto's route accuracy is **91.9% — the best of any
  candidate.** Its apparent lag is a reliability/format issue, not a
  classification error.
- **Tier 21.1% is structural, not a quality gap.** `openrouter/auto` can
  only choose among hosted models — `local` (134/180 fixtures) is
  unreachable by construction. On the 46 hosted-reachable fixtures its
  tier accuracy is **82.6%**, within 4pp of decider's 86.7%.

So the honest read: on the label metric `auto` loses the headline, but
its underlying routing choices are roughly at parity with the winner.

## Leg 2 — quality-per-dollar through the real loop

Method (`bench/router/run_loop_leg.py`): three orchestral tasks spanning
difficulty (`code-fizzbuzz` easy, `bugfix-lru-evict` medium,
`multi-file-site` hard). Each router reads a one-line task brief and
picks a tier → concrete worker (local/cheap→`deepseek-v4.1-flash`,
mid→`gpt-5.4-mini`, frontier→`grok-4.7`); `auto` routes the brief itself
and its `response.model` is the pick. Each (router, task) runs the full
Plan→Delegate→Assemble→Validate→Judge loop, fixed orchestrator
`deepseek-v4-pro`, judge `~typesafe/jev-latest`, execution inside
CubeSandbox microVMs. Runs dir: `/tmp/routerbench-runs` (not committed —
ephemeral).

| router pick | fizzbuzz (easy) | bugfix (med) | multi-file (hard) | spend |
|---|---|---|---|---|
| jev | local→flash: **pass** $0.0017 | mid→mini: **pass** $0.0035 | mid→mini: **fail** $0.0086 | $0.0138 |
| decider | local→flash: **pass** $0.0037 | mid→mini: **pass** $0.0031 | mid→mini: **pass** $0.0044 | $0.0112 |
| auto | flash: **pass** $0.0054 | flash: **pass** $0.0040 | flash: **pass** $0.0155 | $0.0249 |

**Every pick passed except one** (jev's mid-tier pick failed
multi-file-site on a flaky subtask; decider's identical pick passed).
Two observations worth more than the pass counts:

- auto's uniform-cheap strategy passed all three including the hard task
  — it spent 3.4× on the hard task (retries) but never failed.
- n=9 runs is **underpowered**: single run per cell, no replicates. This
  leg demonstrates the plumbing and gives a directional signal ("router
  choice didn't move pass/fail"), not a powered quality-per-dollar
  comparison. Treat it as inconclusive evidence, honestly labeled.

## Spend ledger

| leg | amount |
|---|---|
| leg 1 (all candidates, 180 fixtures) | $0.043 |
| leg 2 (9 loop runs + router calls) | $0.048 |
| probes/debug | ~$0.001 |
| **total** | **~$0.09** |

## Recommendation

**Winner by margin: `pplx-decider`** — but what it wins *is* the case
against building anything. The plan's decision tree maps a decider win
to "thin router service wrapping the winning backend"; the winning
backend is a hosted OpenRouter endpoint, so the thin service is one HTTP
call embedded in the placement policy (R4). Build that and nothing more.

The evidence, honestly stacked:

1. **Decider won the label metric** (86.7% both axes) with the cheapest
   decisions and calibrated per-option probabilities — the only arm that
   gives you a usable confidence gate for free.
2. **No self-managed arm won anywhere.** Hosted Jev tied routes, lost
   tiers; local Jev could not stay alive for 180 calls (OOM-killed —
   an availability failure mode self-hosting adds for nothing).
3. **But `auto` underperforms its own decisions.** Its classification
   headline is dragged down by empty responses (91.9% on answered items)
   and a tier axis it structurally can't win (82.6% on reachable
   options); in the loop leg its picks passed 3/3. A user who only ever
   calls `openrouter/auto` loses little — the case for a decider call
   inside the placement policy is the +5pp route accuracy and the
   calibrated confidence signal, not survival.

So: **no self-hosted router; placement policy calling hosted
`/api/alpha/decisions` (pplx-decider) when it wants a decider.** This is
the thinnest valid form of the decision tree's decider-win branch — the
"service" exists already; what we write is policy.

## Limitations

- Golden set is constructed, not the spec'd wisp-route-fuzz — it did not
  exist. Labels are one reviewer's judgment; route/tier boundaries are
  debatable at the `act`/`tool` and `clarify`/`answer` edges (confusions
  cluster exactly there, for all candidates).
- Tier labels encode *my* placement policy; a different policy changes
  the tier metric. The corpus builder documents the policy inline.
- Loop leg is 9 runs, no replicates — directional only.
- `auto`'s invalid rate (11%) is a real reliability datapoint for
  auto-as-classifier but irrelevant to auto-as-router (its actual job).
- No fleet task traces beyond wisp voice logs — open-computer has no
  production traffic yet, so "our workloads" = wisp's voice-command
  distribution plus synthetic agent-shaped tasks. Re-run when Khan/docket
  real traces exist.

## Reproduce

```bash
cd open-computer
python3 bench/router/build_corpus.py                      # -> corpus.jsonl (180)
orch python3 bench/router/run_bench.py                    # leg 1, all candidates
cd ../orchestral && source scripts/cube-env.sh
orch python3 ../open-computer/bench/router/run_loop_leg.py \
     --runs-dir /tmp/routerbench-runs                     # leg 2
```

Committed artifacts: `bench/router/{build_corpus.py,corpus.jsonl,
run_bench.py,run_loop_leg.py,results-*.jsonl,loop-runs.jsonl,
summary.json}`.
