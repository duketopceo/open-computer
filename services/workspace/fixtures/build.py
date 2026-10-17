"""Regenerates the replay fixtures: `python -I services/workspace/fixtures/build.py`.

The three streams are authored with the real Emitter on a fixed clock, so they are valid by
construction and byte-for-byte reproducible (a test regenerates them and diffs). They are scripted,
not captured from a live run: no orchestrator exists yet. Content is illustrative; numbers in the
benchmark artifacts are synthetic. When the orchestrator lands, record real runs into this format.

Outputs, per goal:  g_<name>.ndjson   the event stream
                    g_<name>.meta.json holds (where replay pauses for a human), deny path, artifact files
                    artifacts/<goal>/<file>
"""

from __future__ import annotations

import hashlib
import io
import os
import json
import struct
import sys
import zlib
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("OC_FIXTURE_OUT") or HERE)  # tests regenerate into a temp dir and diff
sys.path.insert(0, str(HERE.parents[2]))
from oc.events import Emitter, MemoryLog  # noqa: E402
from oc.events.log import line  # noqa: E402

T0 = 1_791_700_000.0


class Rec:
    def __init__(self, goal: str, actor: dict, t0: float):
        self.goal, self.files, self.holds, self.on_deny = goal, {}, {}, []
        self.now = t0
        self.log = MemoryLog()
        self.em = Emitter(self.log, goal, actor=actor, clock=lambda: self.now)
        self.user = {"name": "luke", "kind": "human"}

    def t(self, dt: float):
        self.now += dt
        return self.em

    def hold(self, gate: str):
        """Replay pauses before the NEXT event until a human releases `gate`."""
        self.holds[str(self.log.last_seq(self.goal) + 1)] = gate

    def file(self, art_id: str, name: str, content: str | bytes):
        data = content.encode() if isinstance(content, str) else content
        self.files[art_id] = name
        d = OUT / "artifacts" / self.goal
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
        return len(data)

    def write(self, meta: dict):
        evs = self.log.read(self.goal)
        (OUT / f"{self.goal}.ndjson").write_text("".join(line(e) + "\n" for e in evs))
        meta = {
            **meta,
            "id": self.goal,
            "holds": self.holds,
            "on_deny": self.on_deny,
            "files": self.files,
            "events": len(evs),
        }
        (OUT / f"{self.goal}.meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def models(rows):
    return [{"model": m, "in": i, "out": o, "cached": c, "usd": u} for m, i, o, c, u in rows]


# ============================================================ 1. research report

REPORT_MD = """# pgvector or Qdrant for a 5M-vector workload

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
"""

SUMMARY_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Summary</title>
<style>
body{font:14px/1.5 system-ui,sans-serif;margin:16px;color:#151923;background:#fff}
h1{font-size:16px;margin:0 0 8px}.card{border:1px solid #c9cdd4;border-radius:8px;padding:12px;margin:8px 0}
.k{color:#566070;font-size:12px}.big{font-size:22px;font-weight:600}
</style></head><body>
<h1>pgvector vs Qdrant: one-page summary</h1>
<div class="card"><div class="k">Recommendation</div><div class="big">Start with pgvector</div></div>
<div class="card"><div class="k">Network probe (this page tries to reach out and must fail)</div><div id="net">running</div></div>
<script>
(function(){
  var q=new URLSearchParams(location.search).get("probe")||"https://example.invalid/leak";
  var tried=0,blocked=0,out=document.getElementById("net");
  function done(){tried++;if(tried===4)out.textContent="network: blocked "+blocked+"/4";}
  function fail(){blocked++;done();}
  document.addEventListener("securitypolicyviolation",function(){});
  fetch(q+"?via=fetch",{mode:"no-cors"}).then(done,fail);
  var x=new XMLHttpRequest();x.open("GET",q+"?via=xhr");x.onerror=fail;x.onload=done;
  try{x.send();}catch(e){fail();}
  var i=new Image();i.onload=done;i.onerror=fail;i.src=q+"?via=img";
  try{var w=new WebSocket(q.replace(/^http/,"ws")+"?via=ws");w.onerror=fail;w.onopen=done;}catch(e){fail();}
})();
</script></body></html>
"""


def svg_chart() -> str:
    pg = [(0, 2.0), (1, 2.1), (2, 2.4), (3, 3.1), (4, 5.8)]
    qd = [(0, 1.5), (1, 1.6), (2, 1.8), (3, 2.4), (4, 4.1)]
    xs = ["p10", "p50", "p75", "p90", "p95"]
    W, H, L, B = 480, 260, 48, 40

    def pt(i, v):
        return L + i * 100, H - B - v * 30

    def path(rows):
        return " ".join(("M" if k == 0 else "L") + "%d %d" % pt(i, v) for k, (i, v) in enumerate(rows))

    grid = "".join(
        f'<line x1="{L}" y1="{H - B - v * 30}" x2="{W - 16}" y2="{H - B - v * 30}" stroke="#c9cdd4" stroke-width="1"/>'
        f'<text x="{L - 8}" y="{H - B - v * 30 + 4}" text-anchor="end" font-size="11" fill="#566070">{v}</text>'
        for v in range(0, 7, 2)
    )
    ticks = "".join(
        f'<text x="{L + i * 100}" y="{H - B + 18}" text-anchor="middle" font-size="11" fill="#566070">{x}</text>'
        for i, x in enumerate(xs)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
        f'aria-label="Query latency by percentile in milliseconds: pgvector rises from 2.0 to 5.8, Qdrant from 1.5 to 4.1">'
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>{grid}{ticks}'
        f'<path d="{path(pg)}" fill="none" stroke="#0b66cc" stroke-width="2"/>'
        f'<path d="{path(qd)}" fill="none" stroke="#047857" stroke-width="2" stroke-dasharray="6 4"/>'
        f'<text x="{L}" y="18" font-size="12" font-weight="600" fill="#151923">Query latency (ms) by percentile</text>'
        f'<text x="{W - 120}" y="{H - B - 5.8 * 30 - 8}" font-size="11" fill="#0b66cc">pgvector</text>'
        f'<text x="{W - 120}" y="{H - B - 4.1 * 30 + 14}" font-size="11" fill="#047857">Qdrant (dashed)</text></svg>'
    )


def results_csv() -> str:
    rows = [
        "# harness: 768-d, 100k vectors, HNSW m=16 ef=64, synthetic timings",
        "query_id,engine,latency_ms,recall_at_10,filtered",
    ]
    seed = 7
    for i in range(1, 4211):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        base = 2.0 if i % 2 else 1.5
        lat = base + (seed % 1000) / 1000 * (3.4 if i % 2 else 2.4)
        rows.append(
            f"{i},{'pgvector' if i % 2 else 'qdrant'},{lat:.2f},{0.95 + (seed % 50) / 1000:.3f},{'yes' if i % 5 == 0 else 'no'}"
        )
    return "\n".join(rows) + "\n"


def minimal_pdf(lines: list[str]) -> bytes:
    text = (
        "BT /F1 12 Tf 56 780 Td 16 TL "
        + " ".join(f"({s.replace('(', '[').replace(')', ']')}) Tj T*" for s in lines)
        + " ET"
    )
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(text)} >>\nstream\n{text}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return out


def png(w=360, h=200) -> bytes:
    rows = b""
    for y in range(h):
        r = b"\x00"
        for x in range(w):
            bar = 20 + (x // 36) * 4
            inside = y > h - 20 - (x * 7 % 120 + 20) and (x // 36) % 2 == 0
            r += bytes((11, 102, 204)) if inside else bytes((244, 245, 248)) if y < bar or True else b""
        rows += r

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def research() -> None:
    r = Rec("g_research01", {"name": "researcher", "session": "sess-4f21"}, T0)
    em = r.em
    goal = "Compare pgvector and Qdrant for a 5M-vector workload and write me a one-page recommendation with sources."
    r.now = T0
    em.run(
        "queued",
        title="Compare pgvector and Qdrant for a 5M-vector workload",
        goal_text=goal,
        cap_usd=2.0,
        model="deepseek/deepseek-v3.2",
        box={
            "template": "code-interpreter",
            "ttl_s": 3600,
            "cpu": 2,
            "mem_mb": 4096,
            "disk_mb": 8192,
            "network_allow": [
                "html.duckduckgo.com",
                "github.com",
                "qdrant.tech",
                "postgresql.org",
                "ann-benchmarks.com",
            ],
        },
    )
    r.t(0.2).note("note_u1", "user", goal, kind="message")
    r.t(1.1).run("planning")
    r.t(2.0).note(
        "note_a1",
        "agent",
        "I'll split this into four steps: collect sources, fetch the ones that matter, benchmark both engines on a small sample in the sandbox, then write the recommendation. Estimated cost is about $0.40 of your $2.00 cap.",
        kind="message",
    )
    for i, (sid, title) in enumerate(
        [
            ("step_plan", "Plan the research"),
            ("step_search", "Search for comparisons"),
            ("step_fetch", "Read the primary sources"),
            ("step_bench", "Benchmark both engines in the sandbox"),
            ("step_write", "Write the recommendation"),
        ]
    ):
        r.t(0.1).step(sid, title, "pending")
    r.t(0.4).run("running")
    r.t(0.2).step("step_plan", "Plan the research", "running")
    r.t(3.0).step("step_plan", "Plan the research", "done")
    r.t(0.3).cost(
        0.012,
        2.0,
        tokens={"in": 3100, "out": 620, "cached": 0},
        by_model=models([("deepseek/deepseek-v3.2", 3100, 620, 0, 0.012)]),
        context={"used": 3720, "window": 128000},
    )
    r.t(0.2).step("step_search", "Search for comparisons", "running")
    for k, q in enumerate(
        ["pgvector vs qdrant HNSW recall", "qdrant payload filtering quantization", "pgvector index build concurrently"]
    ):
        r.t(1.2).tool_call(
            f"tool_s{k}",
            "web.search",
            "running",
            step="step_search",
            summary=f'Search "{q}"',
            destination="html.duckduckgo.com",
        )
        r.t(1.8).tool_call(f"tool_s{k}", "web.search", "ok", duration_ms=1800, summary=f'Search "{q}": 8 results')
    srcs = [
        (
            "src_pgvector",
            1,
            "https://github.com/pgvector/pgvector",
            "pgvector: open-source vector similarity search for Postgres",
            "github.com",
            "Supports exact and approximate nearest neighbor search with IVFFlat and HNSW indexes, L2, inner product and cosine distance.",
            True,
        ),
        (
            "src_pgdocs",
            2,
            "https://www.postgresql.org/docs/current/indexes-types.html",
            "PostgreSQL documentation: index types",
            "postgresql.org",
            "Concurrent index builds avoid blocking writes but take longer and need two table scans.",
            True,
        ),
        (
            "src_qdrant",
            3,
            "https://qdrant.tech/documentation/overview/",
            "Qdrant documentation: overview",
            "qdrant.tech",
            "A vector database with filtering on payload, quantization options and distributed deployment.",
            True,
        ),
        (
            "src_qfilter",
            4,
            "https://qdrant.tech/documentation/concepts/filtering/",
            "Qdrant documentation: filtering",
            "qdrant.tech",
            "Filter conditions are applied during HNSW traversal so filtered queries keep their recall.",
            True,
        ),
        (
            "src_ann",
            5,
            "https://ann-benchmarks.com/",
            "ANN-Benchmarks",
            "ann-benchmarks.com",
            "Benchmarks approximate nearest neighbor libraries by plotting recall against queries per second.",
            True,
        ),
        (
            "src_blog",
            6,
            "https://example-vectors.dev/blog/pg-vs-dedicated",
            "Postgres or a dedicated vector database?",
            "example-vectors.dev",
            "Teams under 10M vectors often stay on Postgres until filtered query volume dominates.",
            False,
        ),
    ]
    r.t(0.5).step("step_search", "Search for comparisons", "done")
    r.t(0.2).step("step_fetch", "Read the primary sources", "running")
    for sid, n, url, title, dom, snip, fetched in srcs:
        if fetched:
            r.t(1.0).tool_call(
                f"tool_f{n}", "web.fetch", "running", step="step_fetch", summary=f"Fetch {dom}", destination=dom
            )
            r.t(1.6).tool_call(f"tool_f{n}", "web.fetch", "ok", duration_ms=1600)
            r.t(0.1).source(
                sid,
                n,
                url,
                title,
                domain=dom,
                snippet=snip,
                fetched=True,
                fetched_at=round(r.now, 3),
                content_hash=sha(url + snip),
                cited_by=["step_fetch"],
            )
        else:
            r.t(0.4).source(sid, n, url, title, domain=dom, snippet=snip, fetched=False, cited_by=["step_fetch"])
    r.t(0.6).cost(
        0.088,
        2.0,
        tokens={"in": 21800, "out": 1900, "cached": 4200},
        by_model=models([("deepseek/deepseek-v3.2", 21800, 1900, 4200, 0.088)]),
        context={"used": 23700, "window": 128000},
    )
    r.t(0.3).step("step_fetch", "Read the primary sources", "done")
    r.t(0.6).note(
        "note_a2",
        "agent",
        "Five primary sources agree on the shape of the trade: pgvector keeps vectors in Postgres [1][2], Qdrant adds filtering and quantization [3][4], and recall must be compared at equal recall [5]. One more source [6] came back only as a search snippet; I'll mark it unverified.",
        kind="message",
        cites=[
            {"n": 1, "quote": "IVFFlat and HNSW indexes, L2, inner product and cosine distance"},
            {"n": 2, "quote": "Concurrent index builds avoid blocking writes"},
            {"n": 3, "quote": "filtering on payload, quantization options"},
            {"n": 4, "quote": "applied during HNSW traversal so filtered queries keep their recall"},
            {"n": 5, "quote": "plotting recall against queries per second"},
            {"n": 6, "quote": "Teams under 10M vectors often stay on Postgres"},
        ],
    )
    r.t(0.8).step(
        "step_bench", "Benchmark both engines in the sandbox", "running", kind="sandbox", agent="sbx-7f3a91c2"
    )
    r.t(1.0).tool_call(
        "tool_pip",
        "shell.exec",
        "running",
        step="step_bench",
        summary="pip install pgvector qdrant-client psycopg",
        destination="sbx-7f3a91c2",
        output_tail=["Collecting pgvector", "Successfully installed pgvector-0.3 qdrant-client-1.12 psycopg-3.2"],
    )
    r.t(3.0).tool_call("tool_pip", "shell.exec", "ok", duration_ms=3000)
    r.t(4.0).tool_call(
        "tool_bench",
        "shell.exec",
        "running",
        step="step_bench",
        summary="python bench.py --vectors 100000 --dim 768",
        destination="sbx-7f3a91c2",
        output_tail=[
            "pgvector: p50 2.1ms p95 5.8ms recall@10 0.97",
            "qdrant:   p50 1.6ms p95 4.1ms recall@10 0.97",
            "wrote results.csv (4210 rows)",
        ],
    )
    r.t(0.4).tool_call("tool_bench", "shell.exec", "ok", duration_ms=52400)
    csv = results_csv()
    n = r.file("art_csv", "results.csv", csv)
    r.t(0.3).artifact(
        "art_csv",
        "results.csv",
        "csv",
        1,
        "/goals/g_research01/artifacts/art_csv",
        mime="text/csv",
        size=n,
        rows=4210,
        step="step_bench",
        summary="4,210 query timings, synthetic sample",
        export={"requires_approval": False},
    )
    n = r.file("art_chart", "latency-chart.svg", svg_chart())
    r.t(0.5).artifact(
        "art_chart",
        "latency-chart.svg",
        "svg",
        1,
        "/goals/g_research01/artifacts/art_chart",
        mime="image/svg+xml",
        size=n,
        step="step_bench",
    )
    r.t(0.3).step("step_bench", "Benchmark both engines in the sandbox", "done")
    r.t(0.4).cost(
        0.241,
        2.0,
        tokens={"in": 52100, "out": 6400, "cached": 11000},
        by_model=models([("deepseek/deepseek-v3.2", 52100, 6400, 11000, 0.241)]),
        context={"used": 58500, "window": 128000},
    )
    r.t(0.4).step("step_write", "Write the recommendation", "running")
    r.t(3.5).tool_call(
        "tool_w1",
        "fs.write",
        "ok",
        step="step_write",
        summary="Write report.md",
        destination="/work/report.md",
        duration_ms=40,
    )
    n = r.file("art_report", "report.md", REPORT_MD)
    r.t(0.2).artifact(
        "art_report",
        "report.md",
        "markdown",
        1,
        "/goals/g_research01/artifacts/art_report",
        mime="text/markdown",
        size=n,
        step="step_write",
        summary="One-page recommendation with six cited sources",
        export={"requires_approval": False},
    )
    n = r.file("art_html", "summary.html", SUMMARY_HTML)
    r.t(0.6).artifact(
        "art_html",
        "summary.html",
        "html",
        1,
        "/goals/g_research01/artifacts/art_html",
        mime="text/html",
        size=n,
        step="step_write",
        summary="One-page summary card. Runs sandboxed, no network.",
        export={"requires_approval": False},
    )
    n = r.file(
        "art_pdf",
        "report.pdf",
        minimal_pdf(
            [
                "pgvector or Qdrant for a 5M-vector workload",
                "",
                "Recommendation: start with pgvector.",
                "One database to run; HNSW fits in memory at this size.",
                "Move to Qdrant when filtered search dominates traffic.",
                "",
                "Sources: pgvector, PostgreSQL docs, Qdrant docs, ANN-Benchmarks.",
            ]
        ),
    )
    r.t(0.4).artifact(
        "art_pdf",
        "report.pdf",
        "pdf",
        1,
        "/goals/g_research01/artifacts/art_pdf",
        mime="application/pdf",
        size=n,
        step="step_write",
        summary="Printable copy",
        export={"requires_approval": True},
    )
    r.t(0.3).step("step_write", "Write the recommendation", "done")
    r.t(0.3).cost(
        0.397,
        2.0,
        tokens={"in": 71200, "out": 9100, "cached": 18400},
        by_model=models([("deepseek/deepseek-v3.2", 71200, 9100, 18400, 0.397)]),
        context={"used": 80300, "window": 128000},
    )
    r.t(0.5).note(
        "note_a3",
        "agent",
        "Done. **Start with pgvector.** At 5M vectors it's one database to run and the HNSW index fits in memory [1][2]; move to Qdrant when payload-filtered queries dominate or you need quantization [3][4]. Compare engines at equal recall, not equal parameters [5]. The sandbox sample puts Qdrant about 25% faster at the same recall, which is a small-sample result.\n\nOne source [6] was cited from a snippet and is marked Not verified. The report is `report.md`; raw timings are in `results.csv`.",
        kind="final",
        cites=[
            {"n": 1},
            {"n": 2},
            {"n": 3},
            {"n": 4},
            {"n": 5},
            {"n": 6, "quote": "Teams under 10M vectors often stay on Postgres"},
        ],
    )
    r.t(0.3).run("done", summary="Recommendation written. 6 sources, 4 artifacts, $0.40 of $2.00.")
    r.write(
        {
            "title": "Compare pgvector and Qdrant for a 5M-vector workload",
            "summary": "Research report with web sources, a markdown report and a csv of timings",
        }
    )


# ============================================================ 2. coding task

RATELIMIT_OLD = """import time


class TokenBucket:
    def __init__(self, rate: float, burst: int):
        self.rate, self.burst = rate, burst
        self.tokens = float(burst)
        self.stamp = time.time()

    def allow(self) -> bool:
        now = time.time()
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False
"""


def unified(path, old, new):
    import difflib

    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}", n=3))


RATELIMIT_NEW = """import time
from collections.abc import Callable


class TokenBucket:
    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic):
        self.rate, self.burst = rate, burst
        self.clock = clock
        self.tokens = float(burst)
        self.stamp = clock()

    def allow(self) -> bool:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + max(0.0, now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False
"""
TEST_OLD = """import time

from gateway.ratelimit import TokenBucket


def test_refills_after_wait():
    b = TokenBucket(rate=10, burst=1)
    assert b.allow()
    assert not b.allow()
    time.sleep(0.1)
    assert b.allow()
"""
TEST_NEW = """from gateway.ratelimit import TokenBucket


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_refills_after_wait():
    clock = FakeClock()
    b = TokenBucket(rate=10, burst=1, clock=clock)
    assert b.allow()
    assert not b.allow()
    clock.now += 0.1
    assert b.allow()
"""
MMD = """flowchart LR
  A[pytest x20] --> B{3 failures?}
  B -->|yes| C[time.time can jump]
  B -->|no| D[Done]
  C --> E[Inject a monotonic clock]
  E --> F[Fake clock in test]
  F --> G[pytest x50 green]
  G --> H[Push branch]
"""


def coding() -> None:
    r = Rec("g_coding01", {"name": "coder", "session": "sess-9ab0"}, T0 + 4000)
    em = r.em
    goal = "The test_refills_after_wait test in api-gateway is flaky. Find out why, fix it, and push the branch for review."
    em.run(
        "queued",
        title="Fix the flaky rate limiter test in api-gateway",
        goal_text=goal,
        cap_usd=1.5,
        model="deepseek/deepseek-v3.2",
        box={
            "template": "code-interpreter",
            "ttl_s": 3600,
            "cpu": 2,
            "mem_mb": 4096,
            "disk_mb": 8192,
            "network_allow": ["github.com", "api.github.com", "pypi.org"],
        },
    )
    r.t(0.2).note("note_u1", "user", goal, kind="message")
    r.t(1.0).run("planning")
    r.t(1.5).note(
        "note_a1",
        "agent",
        "Plan: reproduce the flake in the sandbox, find the cause, fix it with the smallest change, prove it with 50 runs, then ask you before pushing. I'll read from `github.com` and write nothing outside the box until you approve.",
        kind="message",
    )
    steps = [
        ("step_clone", "Clone api-gateway into the sandbox"),
        ("step_repro", "Reproduce the flake"),
        ("step_fix", "Fix the cause"),
        ("step_verify", "Prove it with 50 runs"),
        ("step_push", "Push the branch for review"),
    ]
    for sid, title in steps:
        r.t(0.1).step(sid, title, "pending")
    r.t(0.3).run("running")
    r.t(0.2).step("step_clone", steps[0][1], "running")
    # Approval 1: low-risk network read, allowed for this goal.
    pl1 = {
        "method": "GET",
        "url": "https://api.github.com/repos/duketopceo/api-gateway",
        "headers": {"Accept": "application/vnd.github+json"},
    }
    r.t(1.0)
    em.approval_request(
        "ap_clone01",
        kind="network",
        action="GET api.github.com/repos/duketopceo/api-gateway",
        destination="api.github.com",
        payload=pl1,
        payload_preview="GET /repos/duketopceo/api-gateway\nAccept: application/vnd.github+json\n(no body)",
        why="I need the default branch and clone URL before cloning.",
        risk="low",
        cost_usd=0.0,
        if_denied="I can't clone the repo, so the goal stops here.",
        scope_pattern="GET api.github.com/repos/duketopceo/*",
        step="step_clone",
    )
    r.hold("approval:ap_clone01")
    r.t(6.0).approval_resolve("ap_clone01", "approved", decision="goal", decided_by="luke")
    r.t(0.4).tool_call(
        "tool_clone",
        "git.clone",
        "running",
        step="step_clone",
        summary="git clone https://github.com/duketopceo/api-gateway",
        destination="github.com",
    )
    r.t(2.2).tool_call(
        "tool_clone",
        "git.clone",
        "ok",
        duration_ms=2200,
        output_tail=[
            "Cloning into 'api-gateway'...",
            "remote: Enumerating objects: 1842, done.",
            "Receiving objects: 100% (1842/1842), 612.40 KiB",
        ],
    )
    r.t(0.3).step("step_clone", steps[0][1], "done")
    r.t(0.3).cost(
        0.021,
        1.5,
        tokens={"in": 5200, "out": 900, "cached": 0},
        by_model=models([("deepseek/deepseek-v3.2", 5200, 900, 0, 0.021)]),
        context={"used": 6100, "window": 128000},
    )
    r.t(0.4).step("step_repro", steps[1][1], "running")
    r.t(1.0).tool_call(
        "tool_rep",
        "shell.exec",
        "running",
        step="step_repro",
        summary="pytest tests/test_ratelimit.py -x --count=20",
        destination="sbx-2c19d0aa",
        output_tail=[
            "tests/test_ratelimit.py ..F....F.....F....",
            "FAILED tests/test_ratelimit.py::test_refills_after_wait",
            "3 failed, 17 passed in 3.4s",
        ],
    )
    r.t(3.4).tool_call("tool_rep", "shell.exec", "error", duration_ms=3400)
    r.t(0.5).error(
        "err_flake",
        "tests_failed",
        "3 of 20 runs of test_refills_after_wait failed.",
        step="step_repro",
        recoverable=True,
        hint="Expected: this is the flake. The agent continues to the cause.",
    )
    r.t(1.0).note(
        "note_a2",
        "agent",
        "Reproduced: 3 of 20 runs fail. `TokenBucket.allow` reads `time.time()` at [src/gateway/ratelimit.py:8], which can step backwards or jump under NTP adjustment, and the test relies on a real `time.sleep(0.1)` at [tests/test_ratelimit.py:10]. A wall-clock jump makes the refill negative or too large.",
        kind="message",
    )
    r.t(0.3).step("step_repro", steps[1][1], "done")
    r.t(0.3).step("step_fix", steps[2][1], "running")
    d1 = unified("src/gateway/ratelimit.py", RATELIMIT_OLD, RATELIMIT_NEW)
    d2 = unified("tests/test_ratelimit.py", TEST_OLD, TEST_NEW)

    def counts(d):
        return sum(1 for ln in d.splitlines() if ln.startswith("+") and not ln.startswith("+++")), sum(
            1 for ln in d.splitlines() if ln.startswith("-") and not ln.startswith("---")
        )

    r.t(2.0).tool_call(
        "tool_e1",
        "fs.edit",
        "ok",
        step="step_fix",
        summary="Edit src/gateway/ratelimit.py",
        destination="/work/api-gateway/src/gateway/ratelimit.py",
        duration_ms=30,
    )
    a, d = counts(d1)
    r.t(0.1).change("chg_rl", "src/gateway/ratelimit.py", "modify", a, d, diff=d1, step="step_fix")
    r.t(1.4).tool_call(
        "tool_e2",
        "fs.edit",
        "ok",
        step="step_fix",
        summary="Edit tests/test_ratelimit.py",
        destination="/work/api-gateway/tests/test_ratelimit.py",
        duration_ms=28,
    )
    a, d = counts(d2)
    r.t(0.1).change("chg_test", "tests/test_ratelimit.py", "modify", a, d, diff=d2, step="step_fix")
    r.t(0.3).step("step_fix", steps[2][1], "done")
    r.t(0.4).step("step_verify", steps[3][1], "running")
    r.t(1.0).tool_call(
        "tool_v",
        "shell.exec",
        "running",
        step="step_verify",
        summary="pytest tests/test_ratelimit.py --count=50",
        destination="sbx-2c19d0aa",
        output_tail=["tests/test_ratelimit.py ..................................................", "50 passed in 0.9s"],
    )
    r.t(1.2).tool_call("tool_v", "shell.exec", "ok", duration_ms=1200)
    r.t(0.3).cost(
        0.064,
        1.5,
        tokens={"in": 16100, "out": 3100, "cached": 5200},
        by_model=models([("deepseek/deepseek-v3.2", 16100, 3100, 5200, 0.064)]),
        context={"used": 19200, "window": 128000},
    )
    r.t(0.3).step("step_verify", steps[3][1], "done")
    n = r.file("art_patch", "fix-flaky-ratelimit.patch", d1 + d2)
    r.t(0.4).artifact(
        "art_patch",
        "fix-flaky-ratelimit.patch",
        "diff",
        1,
        "/goals/g_coding01/artifacts/art_patch",
        mime="text/x-diff",
        size=n,
        step="step_fix",
        summary="Both files, ready to apply",
        export={"requires_approval": True},
    )
    n = r.file("art_code", "ratelimit.py", RATELIMIT_NEW)
    r.t(0.1).artifact(
        "art_code",
        "ratelimit.py",
        "code",
        1,
        "/goals/g_coding01/artifacts/art_code",
        mime="text/x-python",
        language="python",
        size=n,
        step="step_fix",
    )
    n = r.file("art_mmd", "flake-timeline.mmd", MMD)
    r.t(0.1).artifact(
        "art_mmd",
        "flake-timeline.mmd",
        "mermaid",
        1,
        "/goals/g_coding01/artifacts/art_mmd",
        mime="text/vnd.mermaid",
        size=n,
        step="step_repro",
        summary="From failure to push",
    )
    n = r.file("art_img", "coverage.png", png())
    r.t(0.1).artifact(
        "art_img",
        "coverage.png",
        "image",
        1,
        "/goals/g_coding01/artifacts/art_img",
        mime="image/png",
        size=n,
        step="step_verify",
        summary="Coverage sparkline",
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in [
            ("api-gateway/src/gateway/ratelimit.py", RATELIMIT_NEW),
            ("api-gateway/tests/test_ratelimit.py", TEST_NEW),
            ("api-gateway/pyproject.toml", "[project]\nname = 'api-gateway'\n"),
            ("api-gateway/README.md", "# api-gateway\n"),
        ]:
            zi = zipfile.ZipInfo(name, (2026, 10, 11, 4, 0, 0))
            z.writestr(zi, body)
    n = r.file("art_zip", "bundle.zip", buf.getvalue())
    r.t(0.1).artifact(
        "art_zip",
        "bundle.zip",
        "archive",
        1,
        "/goals/g_coding01/artifacts/art_zip",
        mime="application/zip",
        size=n,
        step="step_fix",
        export={"requires_approval": True},
    )
    n = r.file(
        "art_txt",
        "test-output.txt",
        "tests/test_ratelimit.py ..................................................\n50 passed in 0.9s\n",
    )
    r.t(0.1).artifact(
        "art_txt",
        "test-output.txt",
        "text",
        1,
        "/goals/g_coding01/artifacts/art_txt",
        mime="text/plain",
        size=n,
        step="step_verify",
    )
    r.t(0.8).step("step_push", steps[4][1], "running")
    push = {
        "remote": "origin",
        "url": "https://github.com/duketopceo/api-gateway.git",
        "branch": "fix/ratelimit-flake",
        "base": "main",
        "commits": [{"sha": "c0ffee1", "message": "ratelimit: inject a monotonic clock so refill can't go negative"}],
        "files": ["src/gateway/ratelimit.py", "tests/test_ratelimit.py"],
    }
    preview = "git push origin fix/ratelimit-flake\n\n c0ffee1 ratelimit: inject a monotonic clock so refill can't go negative\n\n 2 files changed, 13 insertions(+), 8 deletions(-)\n\nThis leaves the box and creates a new branch on github.com/duketopceo/api-gateway."
    r.t(1.0)
    em.approval_request(
        "ap_push01",
        kind="push",
        action="git push origin fix/ratelimit-flake",
        destination="github.com/duketopceo/api-gateway",
        payload=push,
        payload_preview=preview,
        why="You asked me to push the branch for review. Nothing else leaves the box.",
        risk="high",
        cost_usd=0.0,
        if_denied="The fix stays in the sandbox. You can export the patch from Artifacts instead.",
        scope_pattern="git push origin fix/ratelimit-flake",
        step="step_push",
    )
    r.hold("approval:ap_push01")
    r.t(9.0).approval_resolve("ap_push01", "approved", decision="once", decided_by="luke")
    r.t(0.5).tool_call(
        "tool_push",
        "git.push",
        "running",
        step="step_push",
        summary="git push origin fix/ratelimit-flake",
        destination="github.com",
    )
    r.t(1.8).tool_call(
        "tool_push",
        "git.push",
        "ok",
        duration_ms=1800,
        output_tail=[
            "To github.com:duketopceo/api-gateway.git",
            " * [new branch]      fix/ratelimit-flake -> fix/ratelimit-flake",
        ],
    )
    r.t(0.3).step("step_push", steps[4][1], "done")
    r.t(0.3).cost(
        0.081,
        1.5,
        tokens={"in": 19800, "out": 3500, "cached": 7100},
        by_model=models([("deepseek/deepseek-v3.2", 19800, 3500, 7100, 0.081)]),
        context={"used": 23300, "window": 128000},
    )
    r.t(0.5).note(
        "note_a3",
        "agent",
        "Pushed `fix/ratelimit-flake`. The cause was wall-clock time: `TokenBucket` now takes an injectable monotonic clock at [src/gateway/ratelimit.py:6], and the test uses a fake clock at [tests/test_ratelimit.py:5] instead of sleeping. 50 runs pass. Open the PR from the branch when you're ready.",
        kind="final",
    )
    r.t(0.3).run("done", summary="Fixed and pushed fix/ratelimit-flake. 2 files, 50/50 runs pass, $0.08 of $1.50.")
    r.on_deny = [
        ["step", {"id": "step_push", "title": steps[4][1], "status": "skipped"}],
        [
            "note",
            {
                "id": "note_a3",
                "role": "agent",
                "kind": "final",
                "text": "Understood, I did not push. The fix is in the sandbox: see Changes, or export `fix-flaky-ratelimit.patch` from Artifacts when you want it.",
            },
        ],
        ["run", {"status": "done", "summary": "Fix verified in the sandbox. Push was denied, nothing left the box."}],
    ]
    r.write(
        {
            "title": "Fix the flaky rate limiter test in api-gateway",
            "summary": "Coding task with changes, a diff and an approval to push",
        }
    )


# ============================================================ 3. spend cap

OUTLINE_MD = """# Competitive analysis: 12 vendors (draft outline)

1. Scope and method
2. Vendors reviewed: Acme, Borealis, Cobalt, Dyno, Ember, Fathom
3. Pricing and packaging
4. Integrations and lock-in
5. Gaps to fill after the cap is raised: Gantry, Helix, Ionic, Juno, Kestrel, Lumen
"""


def cap() -> None:
    r = Rec("g_cap01", {"name": "analyst", "session": "sess-77c3"}, T0 + 9000)
    em = r.em
    goal = "Do a deep competitive analysis of 12 vendors in the workflow-automation space: pricing, integrations, lock-in. Be thorough."
    em.run(
        "queued",
        title="Competitive analysis of 12 workflow vendors",
        goal_text=goal,
        cap_usd=1.0,
        model="deepseek/deepseek-v3.2",
        box={
            "template": "code-interpreter",
            "ttl_s": 3600,
            "cpu": 2,
            "mem_mb": 4096,
            "disk_mb": 8192,
            "network_allow": ["html.duckduckgo.com"],
        },
    )
    r.t(0.2).note("note_u1", "user", goal, kind="message")
    r.t(1.0).run("planning")
    r.t(1.6).note(
        "note_a1",
        "agent",
        "This is a wide task: 12 vendors times three questions. My estimate is $1.40, above your $1.00 cap, so I'll work in order of importance and stop at the cap rather than skim.",
        kind="message",
    )
    steps = [
        ("step_scope", "Define scope and method"),
        ("step_vendors_a", "Research vendors 1 to 6"),
        ("step_vendors_b", "Research vendors 7 to 12"),
        ("step_write", "Write the analysis"),
    ]
    for sid, t in steps:
        r.t(0.1).step(sid, t, "pending")
    r.t(0.3).run("running")
    r.t(0.2).step("step_scope", steps[0][1], "running")
    r.t(2.5).step("step_scope", steps[0][1], "done")
    r.t(0.2).step("step_vendors_a", steps[1][1], "running")
    spent = [(0.09, 8), (0.21, 20), (0.37, 36), (0.52, 51), (0.66, 63)]
    for k, (usd, ctx) in enumerate(spent):
        r.t(2.0).tool_call(
            f"tool_a{k}",
            "web.search",
            "ok",
            step="step_vendors_a",
            summary=f"Search pricing page for vendor {k + 1}",
            destination="html.duckduckgo.com",
            duration_ms=1700,
        )
        r.t(1.0).cost(
            usd,
            1.0,
            tokens={"in": int(usd * 220000), "out": int(usd * 18000), "cached": int(usd * 40000)},
            by_model=models([("deepseek/deepseek-v3.2", int(usd * 220000), int(usd * 18000), int(usd * 40000), usd)]),
            context={"used": ctx * 1000, "window": 128000},
        )
    r.t(0.4).step("step_vendors_a", steps[1][1], "done")
    n = r.file("art_outline", "outline.md", OUTLINE_MD)
    r.t(0.3).artifact(
        "art_outline",
        "outline.md",
        "markdown",
        1,
        "/goals/g_cap01/artifacts/art_outline",
        mime="text/markdown",
        size=n,
        step="step_scope",
        summary="Draft outline, vendors 1 to 6 researched",
        export={"requires_approval": False},
    )
    r.t(0.3).step("step_vendors_b", steps[2][1], "running")
    r.t(2.0).tool_call(
        "tool_b1",
        "web.search",
        "ok",
        step="step_vendors_b",
        summary="Search integrations for vendor 7",
        destination="html.duckduckgo.com",
        duration_ms=1900,
    )
    r.t(1.0).cost(
        0.81,
        1.0,
        tokens={"in": 178200, "out": 14600, "cached": 32400},
        by_model=models([("deepseek/deepseek-v3.2", 178200, 14600, 32400, 0.81)]),
        context={"used": 79000, "window": 128000},
    )
    r.t(0.2).note(
        "note_80", "system", "80% of the $1.00 cap is spent ($0.81). 6 of 12 vendors are done.", kind="threshold"
    )
    r.t(2.6).tool_call(
        "tool_b2",
        "web.search",
        "ok",
        step="step_vendors_b",
        summary="Search lock-in for vendor 7",
        destination="html.duckduckgo.com",
        duration_ms=2100,
    )
    r.t(1.0).cost(
        1.0,
        1.0,
        tokens={"in": 220000, "out": 18000, "cached": 40000},
        by_model=models([("deepseek/deepseek-v3.2", 220000, 18000, 40000, 1.0)]),
        context={"used": 96000, "window": 128000},
    )
    r.t(0.2).note(
        "note_100",
        "system",
        "The $1.00 cap is reached. The run is paused, not cancelled: the sandbox and everything gathered so far are kept.",
        kind="threshold",
    )
    r.t(0.1).run(
        "paused", reason="cap", summary="Paused at the $1.00 cap. Raise the cap to continue with vendors 7 to 12."
    )
    r.hold("resume")
    r.t(30.0).run("running", cap_usd=2.5)
    r.t(0.4).cost(
        1.0,
        2.5,
        tokens={"in": 220000, "out": 18000, "cached": 40000},
        by_model=models([("deepseek/deepseek-v3.2", 220000, 18000, 40000, 1.0)]),
        context={"used": 96000, "window": 128000},
    )
    r.t(2.0).tool_call(
        "tool_b3",
        "web.search",
        "ok",
        step="step_vendors_b",
        summary="Search pricing for vendor 8",
        destination="html.duckduckgo.com",
        duration_ms=1800,
    )
    r.t(1.0).cost(
        1.31,
        2.5,
        tokens={"in": 288000, "out": 23100, "cached": 53000},
        by_model=models([("deepseek/deepseek-v3.2", 288000, 23100, 53000, 1.31)]),
        context={"used": 104000, "window": 128000},
    )
    r.t(0.5).step("step_vendors_b", steps[2][1], "done")
    r.t(0.3).step("step_write", steps[3][1], "running")
    r.t(3.0).tool_call(
        "tool_w",
        "fs.write",
        "ok",
        step="step_write",
        summary="Write analysis.md",
        destination="/work/analysis.md",
        duration_ms=45,
    )
    n = r.file(
        "art_analysis",
        "analysis.md",
        "# Competitive analysis (complete)\n\nAll 12 vendors reviewed. See the pricing and lock-in tables.\n",
    )
    r.t(0.2).artifact(
        "art_analysis",
        "analysis.md",
        "markdown",
        1,
        "/goals/g_cap01/artifacts/art_analysis",
        mime="text/markdown",
        size=n,
        step="step_write",
        export={"requires_approval": False},
    )
    r.t(0.3).step("step_write", steps[3][1], "done")
    r.t(0.3).cost(
        1.52,
        2.5,
        tokens={"in": 331000, "out": 27800, "cached": 61000},
        by_model=models([("deepseek/deepseek-v3.2", 331000, 27800, 61000, 1.52)]),
        context={"used": 112000, "window": 128000},
    )
    r.t(0.4).note("note_a4", "agent", "All 12 vendors are covered. The analysis is in `analysis.md`.", kind="final")
    r.t(0.2).run("done", summary="Complete. 12 vendors, $1.52 of $2.50 after raising the cap.")
    r.write(
        {"title": "Competitive analysis of 12 workflow vendors", "summary": "A goal that hits the spend cap and pauses"}
    )


if __name__ == "__main__":
    research()
    coding()
    cap()
    print("fixtures written to", OUT)
