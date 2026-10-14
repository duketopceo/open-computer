# FS Protocol Spec — v0.2

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
    status          # queued | leased | done | failed   (plain text, single word)
    lease           # exists only while claimed: front-matter {holder, deadline}
    result.md       # child's output — immutable once tombstoned
    tombstone       # zero-byte marker; EXISTS == result is final
    .lock           # flock target serialising claim / expire / final result write
    .bad/           # quarantined records that failed verification (§6)
  agents/<agent_id>/
    deps            # producer task_ids this consumer waits for, one per line (absent = none)  [FS-1]
    inbox/<task_id> # hard link to tasks/<task_id>/task.md (same inode)                        [FS-6]
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
ttl: <seconds | null>   # default lease duration for claim()
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
| Claim | under the task lock, atomic `lease` create (tmpfile + `link()`, complete content or nothing) `{holder, deadline=now+ttl}`; `status=leased`; refused on final tasks | R4 lease |
| Complete | write `result.md` → fsync → **`tombstone` created last** → `status=done`; refused once a tombstone exists | R4 collect |
| Requeue | expired `lease` deleted; `status=queued`; task NOT lost | R4 lease-expiry |
| Collect | parent reads `result.md` iff `tombstone` exists | R4/R7 |

Atomicity: every write is tmpfile + `rename()` on the same filesystem.
Readers never observe a partial record.

## 4. Leases

- `claim(agent, task_id, ttl=None)` runs under the per-task lock (`flock` on `.lock`) and creates `lease`
  with complete content via tmpfile + `link()`. Races lose cleanly: the second claimant gets "already
  leased," never a corrupt file and never a second holder. (v0.1 relied on `O_EXCL` alone; two claimers
  that both saw an expired lease could each unlink the other's fresh lease. The lock closes that window
  because "is it expired?" and "reclaim it" are one critical section. [FS-3])
- `ttl` defaults to the envelope's `ttl`; with neither it is an error. [FS-5]
- `claim` on a tombstoned or `failed` task raises: final tasks are never leased. [FS-7]
- An unreadable lease file counts as held (fail closed), never as free.
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
  `parent` names the caller for the audit trail. It is **not** an access check: a path-level argument
  cannot enforce isolation, and the protocol does not pretend to. Isolation is the sandbox bind-mount
  (§1, OC-041). [FS-8]
- `result.md` is immutable once its tombstone exists: a second `outbox_write` raises and changes nothing.
  Before the tombstone (crash between the two writes) a retry may rewrite it. [FS-2]
- Dependencies are recorded per **consumer agent** in `agents/<consumer>/deps`, and `ready(consumer)` is
  true iff every listed producer task has a tombstone. (v0.1 said `tasks/<id>/deps`; the adapter contract
  addresses consumers by agent name, and an agent waits on several tasks. The envelope's `deps` field is
  informational.) [FS-1]

## 6. Integrity (fail-closed)

- `verify(record)` recomputes `sha256` over the body and compares to
  front-matter. Mismatch ⇒ false ⇒ `collect` raises `IntegrityError` and quarantines the record: it is
  moved to `tasks/<id>/.bad/`, the tombstone is withdrawn and the task requeued (not lost), never silently
  accepted. [FS-4]
- Tamper attempts via `write_raw` (the low-level hook tests use) must be
  detectable by the very next `verify`.
- Malformed front-matter, unknown `kind`, or missing required fields are all
  verify-failures: fail closed, never guess.

## 7. Adapter contract → spec map

| Method | Spec |
|---|---|
| `send(dst, msg, ttl=None)` → task_id | §3 spawn |
| `outbox_write(agent, tid, payload)` | §3 complete |
| `collect(parent, tid)` → exact payload | §5 |
| `enqueue(consumer, depends_on)` / `ready(consumer)` | §5 deps |
| `claim(agent, tid, ttl=None)` → lease_id | §4 |
| `expire(lease_id)` / `pending(tid)` | §4 requeue |
| `record_path(agent, tid)` / `write_raw` / `verify` | §6 |

## 8. Non-goals

- No network transport, no ordering across agents beyond deps, no consensus.
- No LLM judgment anywhere in the protocol — all semantics are filesystem
  operations a shell script could implement.
- Cross-run durability/history is orchestrator state (Khan/Temporal), not
  this protocol's problem.

## 9. Numbered rules and their tests

Each rule below was a deviation between v0.1 and the implementation. Resolution says which side moved.

| Rule | Statement | Resolution | Test |
|---|---|---|---|
| FS-1 | Dependencies are per consumer agent, `agents/<id>/deps` | spec amended | `tests/fsproto/test_spec_conformance.py::test_fs1_*` |
| FS-2 | A tombstoned `result.md` is immutable | code fixed | `::test_fs2_*` |
| FS-3 | Lease claim is atomic; races never yield two holders | code fixed (flock) | `::test_fs3_*`, `tests/fsproto/test_lease_race.py` |
| FS-4 | A record failing verification is quarantined to `.bad/` | code fixed | `::test_fs4_*` |
| FS-5 | `ttl` is in the envelope and is the default lease duration | code fixed | `::test_fs5_*` |
| FS-6 | Inbox entries are hard links (to `task.md`; directories cannot be hard-linked) | code fixed, spec reworded | `::test_fs6_*` |
| FS-7 | `claim` only on non-final tasks | code fixed | `::test_fs7_*` |
| FS-8 | `collect`'s `parent` is audit-only | spec amended | `::test_fs8_*` |

`test_lease_race.py` keeps the v0.1 algorithm as a mutant (`NaiveClaimFS`) and forces the losing schedule to
prove the test can fail; the real `claim` is then run through the identical schedule and a multi-process
stress.
