# FS Protocol Spec — v0.1

Sub-agents coordinate **through the filesystem only** (`docs/determinism.md`
R4: no direct API calls between sub-agents; R7: dependency gaps queue, never
hallucinate).

Derived from OACP (Apache-2.0, oacp.dev) — mailbox envelopes, human-readable
records. This spec adds the semantics OACP leaves unspecified: **leases,
tombstones, dependency queueing, and fail-closed integrity**.

Conformance contract: `tests/conformance/checks/__init__.py`. Every method
below maps to an executable check; the adapter is done when R4 + R7 are green.

## 1. Layout

```
<run>/
  tasks/<task_id>/
    task.md         # work envelope — immutable after creation
    status          # queued | leased | done   (plain text, single word)
    lease           # exists only while claimed: front-matter {holder, deadline}
    deps            # producer task_ids, one per line (absent = no deps)
    result.md       # child's output — immutable once written
    tombstone       # zero-byte marker; EXISTS == result is final
  agents/<agent_id>/
    inbox/          # task_ids offered to this agent (hard links to task dirs)
    outbox/         # records emitted by this agent
```

One `<run>` root per orchestrated goal. A sandboxed sub-agent is bind-mounted
only its own `agents/<id>/` dir plus the `tasks/` entries it is offered — it
cannot see siblings it has no dependency on.

## 2. Envelope format

`task.md` and `result.md` are YAML front-matter + markdown body:

```markdown
---
id: <task_id>
from: <agent_id>
to: <agent_id | broadcast>
kind: task | result | ack | nack
deps: [<task_id>...]
ttl: <seconds>
created: <unix_ts>
sha256: <hex digest of body bytes>
---

<body: what to do / what was produced>
```

`sha256` covers the body bytes only (front-matter excluded). Any reader MUST
verify before trusting a record (§6).

## 3. Lifecycle

| Step | File transitions | Check |
|---|---|---|
| Spawn | create `tasks/<id>/task.md`, `status=queued`, hard-link into `agents/<child>/inbox/` | R4 spawn |
| Claim | atomic `lease` create (`O_EXCL`) `{holder, deadline=now+ttl}`; `status=leased` | R4 lease |
| Complete | write `result.md` → fsync → **`tombstone` created last** → `status=done` | R4 collect |
| Requeue | expired `lease` deleted; `status=queued`; task NOT lost | R4 lease-expiry |
| Collect | parent reads `result.md` iff `tombstone` exists | R4/R7 |

Atomicity: every write is tmpfile + `rename()` on the same filesystem.
Readers never observe a partial record.

## 4. Leases

- `claim(agent, task_id, ttl)` creates `lease` via `O_EXCL` — races lose
  cleanly: second claimant gets "already leased," not a corrupt file.
- A lease is valid iff `deadline > clock()`. Clock is injectable
  (`adapter(tmp_path, clock=fn)`) for deterministic tests.
- `expire(lease_id)` (or deadline passing) → lease invalid → `pending(task)`
  is true → dispatcher requeues. **A requeued task is never deleted** — the
  task record outlives any number of leases.
- `status` is advisory; `lease` file existence + deadline is authoritative.

## 5. Tombstones and dependencies

- `tombstone` is the completion signal, created **only after** `result.md`
  is fully flushed. Ordering is the entire guarantee: no tombstone ⇒ the
  result bytes may not exist yet.
- `enqueue(consumer, depends_on=producer)` writes `deps` on the consumer's
  task. `ready(consumer)` is true iff every dep has a tombstone.
- The dispatcher only *offers* ready tasks. `collect()` on an un-tombstoned
  task raises — it never returns a placeholder. (R7: queue, don't fabricate.)
- `collect(parent, task_id)` returns the **exact** bytes of `result.md`.
  Parent-child fidelity is byte-exact, no summarization, no mediation.

## 6. Integrity (fail-closed)

- `verify(record)` recomputes `sha256` over the body and compares to
  front-matter. Mismatch ⇒ false ⇒ caller raises — the record is quarantined
  (moved to `tasks/<id>/.bad/`), never silently accepted.
- Tamper attempts via `write_raw` (the low-level hook tests use) must be
  detectable by the very next `verify`.
- Malformed front-matter, unknown `kind`, or missing required fields are all
  verify-failures: fail closed, never guess.

## 7. Adapter contract → spec map

| Method | Spec |
|---|---|
| `send(dst, msg)` → task_id | §3 spawn |
| `outbox_write(agent, tid, payload)` | §3 complete |
| `collect(parent, tid)` → exact payload | §5 |
| `enqueue(consumer, depends_on)` / `ready(consumer)` | §5 deps |
| `claim(agent, tid, ttl)` → lease_id | §4 |
| `expire(lease_id)` / `pending(tid)` | §4 requeue |
| `record_path(agent, tid)` / `write_raw` / `verify` | §6 |

## 8. Non-goals

- No network transport, no ordering across agents beyond deps, no consensus.
- No LLM judgment anywhere in the protocol — all semantics are filesystem
  operations a shell script could implement.
- Cross-run durability/history is orchestrator state (Khan/Temporal), not
  this protocol's problem.
