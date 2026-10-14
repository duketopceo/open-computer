# pgvector or Qdrant for a 5M-vector workload

**Recommendation: start with pgvector.** At 5M vectors you already run Postgres, the HNSW index fits in memory on one
node, and the operational surface stays at one database [1][2]. Move to Qdrant when you need payload-filtered search at
high concurrency or quantization to fit a larger corpus [3][4].

## What the sources agree on

- pgvector adds `vector` columns plus IVFFlat and HNSW indexes to Postgres, so vectors live next to the rows they describe [1].
- Qdrant is a dedicated engine with payload filtering, scalar and product quantization, and sharding built in [3].
- Recall and queries-per-second trade against each other on both. Compare at equal recall, not equal parameters [5].

## Sandbox measurement

I ran both engines on a 100k-vector sample (768 dimensions) inside the sandbox and recorded 4,210 query timings. The
raw rows are in `results.csv`; the chart is `latency-chart.svg`. Numbers are from a small sample, so treat the shape as
meaningful and the absolute values as not transferable.

| Engine | p50 (ms) | p95 (ms) | Recall@10 |
|---|---|---|---|
| pgvector HNSW (m=16, ef=64) | 2.1 | 5.8 | 0.97 |
| Qdrant HNSW (m=16, ef=64) | 1.6 | 4.1 | 0.97 |

## Cost and risk

> One database is cheaper to run than two. The break-even moves when filtered queries dominate traffic [2][4].

- **pgvector:** no new service, backups and replicas you already have. Index builds lock writes unless built `CONCURRENTLY`.
- **Qdrant:** a second system to run, secure and back up, in exchange for filtering and memory controls [3].
- Source 6 was cited from a search snippet and **not fetched**. It is flagged Not verified in the Sources tab.

## Next step

Run the same harness against your real embeddings and filters before deciding. The harness is in `results.csv`'s header comment.
