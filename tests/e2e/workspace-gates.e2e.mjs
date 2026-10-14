// Browser gates for the agent workspace (silver-lining.md section 16.9 plus the earlier sections), artifact viewers and accessibility: @artifacts @a11y.
// Runs against the replay fixture (tests/opsview/serve_fixture.py --replay): no sandbox, no model, only 127.0.0.1.
//
//   cd tests/e2e && npm i && npx playwright install chromium && npm run test:gates
import { test } from "node:test";
import assert from "node:assert/strict";
import { harness, S, open, complete, held, axe, api, approvalsOf, frameOf, tabsOpen, AXE_TAGS } from "./ws-helpers.mjs";

harness([8874, 8875, 8876]);

// ============================================================================ @artifacts
const ART = [
  ["g_research01", "art_report", "markdown", async (p) => {
    assert.ok(await p.locator(".viewer .md h2").count());
    assert.ok((await p.locator(".viewer .cite").count()) >= 5, "citations render as chips");
    assert.ok(await p.locator(".viewer .md-table").count());
  }],
  ["g_research01", "art_csv", "csv", async (p) => {
    assert.equal((await p.textContent("[data-csv-count]")).trim(), "Showing 100 of 4,210 rows");
    assert.equal(await p.locator(".viewer tbody tr").count(), 100);
  }],
  ["g_research01", "art_chart", "svg", async (p) => {
    await p.waitForFunction(() => { const i = document.querySelector(".viewer img"); return i && i.complete && i.naturalWidth > 0; });
    assert.ok((await p.getAttribute(".viewer img", "alt")).length > 3);
  }],
  ["g_research01", "art_html", "html", async (p) => {
    assert.equal(await p.getAttribute(".viewer iframe", "sandbox"), "allow-scripts", "no allow-same-origin, no forms, no top navigation");
    const fr = await frameOf(p, "/artifacts/art_html");
    await fr.waitForFunction(() => /network: blocked 4\/4/.test(document.getElementById("net").textContent), null, { timeout: 8000 });
  }],
  ["g_research01", "art_pdf", "pdf", async (p) => {
    assert.ok(await p.locator(".viewer iframe").count());
    const r = await p.request.get(new URL(await p.getAttribute(".viewer iframe", "src"), p.url()).href);
    assert.equal(r.status(), 200);
    assert.match(r.headers()["content-type"], /pdf/);
    assert.ok(await p.locator('.viewer [data-act="export-request"]').count(), "export of this one needs approval");
  }],
  ["g_coding01", "art_patch", "diff", async (p) => {
    assert.ok((await p.locator(".viewer .dl-add").count()) > 5 && (await p.locator(".viewer .dl-del").count()) > 3);
    assert.ok(await p.locator(".viewer .dl-add .dl-s .sr").count(), "added/removed are not conveyed by color alone");
  }],
  ["g_coding01", "art_code", "code", async (p) => {
    assert.ok((await p.locator(".viewer .code-line").count()) > 10);
    await p.click('.viewer .seg-btn:has-text("Source")');
    assert.ok(await p.isVisible(".viewer .code-source"));
    assert.ok(!(await p.isVisible(".viewer .code-preview")));
    await p.click('.viewer .seg-btn:has-text("Preview")');
    assert.ok(await p.isVisible(".viewer .code-preview"));
  }],
  ["g_coding01", "art_mmd", "mermaid", async (p) => {
    assert.equal(await p.locator(".viewer .mmd-node").count(), 8);
    assert.ok((await p.locator(".viewer .mmd-edge").count()) >= 7);
    await p.click('.viewer .seg-btn:has-text("Source")');
    assert.match(await p.textContent(".viewer .code-source"), /flowchart LR/);
  }],
  ["g_coding01", "art_img", "image", async (p) => {
    await p.waitForFunction(() => { const i = document.querySelector(".viewer img"); return i && i.complete && i.naturalWidth === 360; });
  }],
  ["g_coding01", "art_zip", "archive", async (p) => {
    assert.equal(await p.locator(".viewer tbody tr").count(), 4);
    assert.match(await p.textContent(".viewer"), /4 entries/);
  }],
  ["g_coding01", "art_txt", "text", async (p) => {
    assert.equal(await p.locator(".viewer .code-line").count(), 2);
  }],
];

for (const theme of ["dark", "light"]) {
  test(`@artifacts every viewer renders its fixture, axe 0 violations, ${theme} 1440`, async () => {
    for (const goal of ["g_research01", "g_coding01"]) {
      const { ctx, page } = await open(S.RO, goal, { theme, q: "speed=0&hold=0&restart=1" });
      await complete(page);
      await page.click("#tab-artifacts");
      for (const [g, id, kind, check] of ART.filter((a) => a[0] === goal)) {
        await page.click(`[data-art="${id}"] .row-btn`);
        await page.waitForSelector(`.viewer[data-viewer="${kind}"]`);
        await page.evaluate(() => document.querySelector(".viewer").__ready);
        await check(page);
        assert.deepEqual(await axe(page), [], `${g}/${id} ${theme}`);
        await page.click('[data-act="art-back"]');
      }
      assert.deepEqual(page.errors.filter((e) => !/Refused to|net::ERR|Failed to load resource|violates the following Content Security Policy/.test(e)), [], goal);
      await ctx.close();
    }
  });
}

test("@artifacts expanded overlay and the phone full-screen viewer pass axe", async () => {
  for (const [theme, w, h] of [["dark", 1440, 900], ["light", 390, 844]]) {
    const { ctx, page } = await open(S.RO, "g_research01", { theme, w, h, q: "speed=0&hold=0&restart=1" });
    await complete(page);
    if (w < 768) { await page.click('#timeline [data-open-artifact="art_csv"]'); }
    else { await page.click("#tab-artifacts"); await page.click('[data-art="art_csv"] .row-btn'); await page.click('[data-act="art-expand"]'); }
    await page.waitForSelector("#overlay .viewer");
    await page.evaluate(() => document.querySelector("#overlay .viewer").__ready);
    const box = await page.locator("#overlay").boundingBox();
    if (w < 768) assert.deepEqual([box.x, box.width], [0, 390], "phones: full screen");
    else assert.ok(box.width > 500 && box.width < 1100, "desktop: over the center column only");
    assert.deepEqual(await axe(page), [], `${theme} ${w}`);
    await page.keyboard.press("Escape");
    assert.ok(await page.locator("#overlay").isHidden(), "Esc returns");
    await ctx.close();
  }
});

test("@artifacts the HTML viewer has no network", async () => {
  const { ctx, page } = await open(S.RO, "g_research01", { q: "speed=0&hold=0&restart=1" });
  const hits = [];
  await ctx.route(/example\.invalid/, (r) => { hits.push(r.request().url()); return r.abort(); });
  await complete(page);
  await page.click("#tab-artifacts");
  await page.click('[data-art="art_html"] .row-btn');
  const fr = await frameOf(page, "/artifacts/art_html");
  await fr.waitForFunction(() => /network: blocked 4\/4/.test(document.getElementById("net").textContent), null, { timeout: 8000 });
  assert.deepEqual(hits, [], "the sandboxed page reached the network");
  // the response itself forbids it
  const r = await page.request.get(S.RO + "/goals/g_research01/artifacts/art_html");
  const csp = r.headers()["content-security-policy"];
  assert.match(csp, /sandbox allow-scripts/); assert.match(csp, /default-src 'none'/);
  assert.doesNotMatch(csp, /allow-same-origin|connect-src|allow-forms|allow-top-navigation/);
  // it can't read the console either: opaque origin
  assert.equal(await fr.evaluate(() => { try { return typeof parent.document.cookie; } catch (e) { return "blocked"; } }), "blocked");
  // positive control: the same probe from a page with no CSP DOES reach the route, so the assertion above can fail
  const ctl = await ctx.newPage();
  await ctl.goto("about:blank");
  await ctl.evaluate(() => fetch("https://example.invalid/control", { mode: "no-cors" }).catch(() => {}));
  assert.equal(hits.length, 1, "control: the route sees requests from an unsandboxed page");
  await ctx.close();
});

test("@artifacts opening the HTML in a new tab is a separate, explicit action and stays sandboxed", async () => {
  const { ctx, page } = await open(S.RO, "g_research01", { q: "speed=0&hold=0&restart=1" });
  await complete(page);
  await page.click("#tab-artifacts");
  await page.click('[data-art="art_html"] .row-btn');
  const link = page.locator('.viewer a:has-text("Open in new tab")');
  assert.equal(await link.getAttribute("target"), "_blank");
  assert.match(await link.getAttribute("rel"), /noopener/);
  const hits = [];
  await ctx.route(/example\.invalid/, (r) => { hits.push(1); return r.abort(); });
  const popup = ctx.waitForEvent("page");
  await link.click();
  const tab = await popup;
  await tab.waitForFunction(() => /network: blocked 4\/4/.test(document.getElementById("net").textContent), null, { timeout: 8000 });
  assert.deepEqual(hits, []);
  await ctx.close();
});

test("@artifacts markdown never turns source text into active content", async () => {
  const { ctx, page } = await open(S.RO, "g_research01", { q: "speed=0&hold=0&restart=1" });
  await complete(page);
  const r = await page.evaluate(async () => {
    const { render } = await import("/static/ws/markdown.js");
    const host = document.createElement("div");
    host.appendChild(render('# <img src=x onerror=alert(1)>\n<script>alert(2)</script>\n[x](javascript:alert(3)) [y](https://ok.example/p) **<b>b</b>** `<i>`\n\n| a |\n|---|\n| <svg onload=alert(4)> |', { cites: true }));
    return { html: host.innerHTML, active: host.querySelectorAll("script,img,iframe,svg,object,[onerror],[onload]").length, hrefs: [...host.querySelectorAll("a")].map((a) => a.getAttribute("href")) };
  });
  assert.equal(r.active, 0);
  assert.deepEqual(r.hrefs, ["https://ok.example/p"], "javascript: links are not links");
  assert.match(r.html, /&lt;script&gt;/);
  await ctx.close();
});

test("@artifacts a gated export needs an approval first, and shows it in the dock", async () => {
  const { ctx, page } = await open(S.RW, "g_research01", { q: "speed=0&hold=0&restart=1" });
  await complete(page);
  await page.click("#tab-artifacts");
  await page.click('[data-art="art_pdf"] .row-btn');
  assert.equal((await page.request.get(S.RW + "/goals/g_research01/artifacts/art_pdf?download=1")).status(), 403);
  await page.click('[data-act="export-request"]');
  await held(page);
  assert.match(await page.textContent("#dock .acard-title"), /Export report\.pdf/);
  await page.focus("#dock .acard");
  await page.keyboard.press("a");
  await page.waitForFunction(() => window.__oc.summary().pending_approvals === 0);
  assert.equal((await page.request.get(S.RW + "/goals/g_research01/artifacts/art_pdf?download=1")).status(), 200);
  await ctx.close();
});

// ============================================================================ @a11y
for (const theme of ["dark", "light"]) {
  for (const w of [1440, 390]) {
    test(`@a11y axe: 0 violations on every tab and state, ${theme}, ${w}px`, async () => {
      const h = w === 390 ? 844 : 900;
      let { ctx, page } = await open(S.RO, "g_research01", { theme, w, h, q: "speed=0&hold=0&restart=1" });
      await complete(page);
      assert.deepEqual(await axe(page), [], `conversation ${theme} ${w}`);
      await tabsOpen(page, w);
      for (const t of ["progress", "sources", "artifacts", "changes", "outbox", "box", "cost"]) {
        await page.click(`#tab-${t}`);
        assert.deepEqual(await axe(page), [], `tab ${t} ${theme} ${w}`);
      }
      await page.keyboard.press("Escape");
      await page.keyboard.press("Control+k");
      assert.deepEqual(await axe(page, ["wcag2a", "wcag2aa", "wcag22aa"]), [], `palette ${theme} ${w}`);
      await page.keyboard.press("Escape");
      await page.keyboard.press("?");
      assert.deepEqual(await axe(page, ["wcag2a", "wcag2aa", "wcag22aa"]), [], `help ${theme} ${w}`);
      await ctx.close();

      ({ ctx, page } = await open(S.RW, "g_coding01", { theme, w, h }));
      await held(page);
      assert.deepEqual(await axe(page), [], `low-risk card ${theme} ${w}`);
      await page.click('#dock [data-act="once"]');
      await page.waitForSelector('#dock [data-approval="ap_push01"]');
      assert.deepEqual(await axe(page), [], `high-risk card ${theme} ${w}`);
      await page.click('#dock [data-act="goal"]');
      assert.deepEqual(await axe(page), [], `armed card ${theme} ${w}`);
      await page.click('#dock [data-act="full"]');
      assert.deepEqual(await axe(page, ["wcag2a", "wcag2aa", "wcag22aa"]), [], `payload overlay ${theme} ${w}`);
      await page.keyboard.press("Escape");
      await tabsOpen(page, w);
      await page.click("#tab-changes"); await page.click(".chg-row .row-btn");
      assert.deepEqual(await axe(page), [], `changes ${theme} ${w}`);
      await page.click("#tab-outbox");
      assert.deepEqual(await axe(page), [], `outbox ${theme} ${w}`);
      await ctx.close();

      ({ ctx, page } = await open(S.RW, "g_cap01", { theme, w, h }));
      await held(page, "#dock .acard.cap");
      assert.deepEqual(await axe(page), [], `cap pause ${theme} ${w}`);
      await ctx.close();

      ({ ctx, page } = await open(S.RO, null, { theme, w, h, q: "" }));
      await page.waitForTimeout(500);
      assert.deepEqual(await axe(page), [], `empty ${theme} ${w}`);
      await ctx.close();
    });
  }
}

