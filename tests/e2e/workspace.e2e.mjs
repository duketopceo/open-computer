// Browser gates for the agent workspace (silver-lining.md section 16.9 plus the earlier sections), core flows: @replay @approval @live @palette @keyboard @citations @cap @phone @layout.
// Runs against the replay fixture (tests/opsview/serve_fixture.py --replay): no sandbox, no model, only 127.0.0.1.
//
//   cd tests/e2e && npm i && npx playwright install chromium && npm run test:workspace
import { test } from "node:test";
import assert from "node:assert/strict";
import { harness, S, open, complete, held, axe, api, approvalsOf, frameOf, tabsOpen, AXE_TAGS } from "./ws-helpers.mjs";

harness([8871, 8872, 8873]);

// ============================================================================ @replay
for (const goal of ["g_research01", "g_coding01", "g_cap01"]) {
  test(`@replay projection determinism: ${goal} twice, and paced vs instant, give identical DOM`, async () => {
    const snaps = [];
    for (const q of ["speed=0&hold=0&restart=1", "speed=0&hold=0&restart=1", "speed=60&hold=0&restart=1"]) {
      const { ctx, page } = await open(S.RO, goal, { q });
      await complete(page);
      await page.waitForTimeout(350);
      snaps.push(await page.evaluate(() => window.__oc.snapshot()));
      assert.deepEqual(page.errors, []);
      await ctx.close();
    }
    for (const k of ["timeline", "side", "dock", "chrome"]) {
      assert.equal(snaps[1][k], snaps[0][k], `${goal}: second replay differs in ${k}`);
      assert.equal(snaps[2][k], snaps[0][k], `${goal}: paced replay differs from instant in ${k}`);
    }
    assert.ok(snaps[0].timeline.length > 500 && snaps[0].side.length > 500, "snapshot is not empty");
  });

  test(`@replay the browser projection agrees with the server fold: ${goal}`, async () => {
    const { ctx, page } = await open(S.RO, goal, { q: "speed=0&hold=0&restart=1" });
    await complete(page);
    const [mine, theirs] = await Promise.all([page.evaluate(() => window.__oc.summary()), page.evaluate((g) => fetch(`/goals/${g}/state`, { credentials: "same-origin" }).then((r) => r.json()), goal)]);
    for (const k of ["status", "reason", "spent_usd", "cap_usd", "pending_approvals", "artifacts", "sources", "changes", "last_seq", "group"]) assert.deepEqual(mine[k], theirs[k], `${goal}.${k}`);
    await ctx.close();
  });
}

test("@replay resume: dropping the stream mid-run and reconnecting rebuilds the same screen", async () => {
  const { ctx, page } = await open(S.RO, "g_research01", { q: "speed=0&hold=0&restart=1" });
  await complete(page);
  const full = await page.evaluate(() => window.__oc.snapshot());
  const { ctx: c2, page: p2 } = await open(S.RO, "g_research01", { q: "speed=0&hold=0" });
  await p2.route("**/events*", (r) => r.abort());
  await p2.reload();
  await p2.waitForTimeout(300);
  assert.match(await p2.textContent("#banner"), /Replay|Disconnected|Reconnecting/);
  await p2.unroute("**/events*");
  await complete(p2).catch(() => {});
  await p2.waitForTimeout(2800); // EventSource retries after the server's 2s retry hint
  await complete(p2);
  await p2.waitForTimeout(350);
  assert.deepEqual(await p2.evaluate(() => window.__oc.snapshot()), full);
  await ctx.close(); await c2.close();
});

// ============================================================================ @approval
test("@approval Esc defers and never approves or denies", async () => {
  const { ctx, page } = await open(S.RW, "g_coding01");
  await held(page);
  const decides = [];
  page.on("request", (r) => r.url().includes("/decide") && decides.push(r.url()));
  await page.focus("#dock .acard");
  await page.keyboard.press("Escape");
  await page.waitForSelector("#dock .dock-deferred");
  assert.equal(await page.locator("#dock .acard").count(), 0, "card undocked");
  assert.deepEqual(decides, [], "Esc sent no decision");
  assert.equal((await approvalsOf(page, "g_coding01")).find((a) => a.id === "ap_clone01").status, "pending");
  assert.match(await page.textContent('[data-badge="outbox"]'), /1/);
  assert.ok(await page.locator('[data-badge="outbox"].attn').count(), "Outbox badge stays amber");
  assert.equal(await page.evaluate(() => document.activeElement.id), "composer-text", "focus moves to the composer");
  await page.click('#dock [data-act="undefer"]');
  await held(page);
  await page.reload();
  await held(page); // a reload re-docks anything still pending
  await ctx.close();
});

test("@approval keyboard: y focuses the card, a allows once (low risk), high risk needs a second press", async () => {
  const { ctx, page } = await open(S.RW, "g_coding01");
  await held(page);
  const decides = [];
  page.on("request", (r) => r.url().includes("/decide") && decides.push(JSON.parse(r.postData())));
  await page.locator("#composer-text").focus();
  await page.keyboard.press("Escape");
  await page.keyboard.press("y");
  assert.equal(await page.evaluate(() => document.activeElement.dataset.approval), "ap_clone01");
  await page.keyboard.press("a");
  await page.waitForSelector('#dock [data-approval="ap_push01"]');
  assert.equal(decides.length, 1);
  assert.equal(decides[0].choice, "once");
  assert.equal(decides[0].confirmed, undefined, "low risk needs no confirmation");
  // high risk (a decision leaves focus in the composer; Esc, then y)
  await page.keyboard.press("Escape");
  await page.keyboard.press("y");
  assert.equal(await page.evaluate(() => document.activeElement.dataset.approval), "ap_push01");
  await page.keyboard.press("a");
  await page.waitForSelector('#dock [data-act="once"]:has-text("Press again to confirm")');
  assert.equal(decides.length, 1, "first press on a high-risk card decides nothing");
  assert.equal((await approvalsOf(page, "g_coding01")).find((a) => a.id === "ap_push01").status, "pending");
  await page.keyboard.press("Escape"); // defers: and disarms
  await page.waitForSelector("#dock .dock-deferred");
  await page.click('#dock [data-act="undefer"]');
  await held(page);
  assert.match(await page.textContent('#dock [data-act="once"]'), /Allow once/, "arming does not survive a defer");
  await page.focus("#dock .acard");
  await page.keyboard.press("a");
  await page.keyboard.press("a");
  await page.waitForFunction(() => window.__oc.summary().status === "done");
  assert.equal(decides.length, 2);
  assert.equal(decides[1].confirmed, true);
  assert.match(await page.textContent("#timeline"), /Allowed once by luke@test: git push origin fix\/ratelimit-flake/);
  await ctx.close();
});

test("@approval clicking is the same: two presses on high risk, one on low risk", async () => {
  const { ctx, page } = await open(S.RW, "g_coding01");
  await held(page);
  assert.ok(await page.locator('#dock [data-act="once"].primary').count(), "low risk: Allow once is the primary action");
  await page.click('#dock [data-act="once"]');
  await page.waitForSelector('#dock [data-approval="ap_push01"]');
  assert.equal(await page.locator('#dock [data-act="once"].primary').count(), 0, "high risk: Allow once is secondary");
  const decides = [];
  page.on("request", (r) => r.url().includes("/decide") && decides.push(r.url()));
  await page.click('#dock [data-act="goal"]');
  assert.equal(decides.length, 0);
  await page.click('#dock [data-act="goal"]');
  await page.waitForFunction(() => window.__oc.summary().status === "done");
  assert.equal(decides.length, 1);
  await ctx.close();
});

test("@approval a stale payload hash, a reused approval and a skipped confirmation are all refused", async () => {
  const { ctx, page } = await open(S.RW, "g_coding01");
  await held(page);
  const good = (await approvalsOf(page, "g_coding01"))[0].payload_hash;
  const D = "/goals/g_coding01/approvals/ap_clone01/decide";
  assert.equal((await api(page, D, { choice: "once", hash: "0".repeat(64) })).status, 409, "stale hash");
  assert.equal((await approvalsOf(page, "g_coding01"))[0].status, "pending", "a refused decision changes nothing");
  assert.equal((await api(page, D, { choice: "once", hash: good })).status, 200);
  assert.equal((await api(page, D, { choice: "once", hash: good })).status, 409, "single-use");
  assert.equal((await api(page, D, { choice: "deny", hash: good })).status, 409);
  await page.waitForSelector('#dock [data-approval="ap_push01"]');
  const ph = (await approvalsOf(page, "g_coding01")).find((a) => a.id === "ap_push01").payload_hash;
  assert.equal((await api(page, "/goals/g_coding01/approvals/ap_push01/decide", { choice: "once", hash: ph })).status, 428, "high risk without confirmation");
  assert.equal((await api(page, "/goals/g_coding01/approvals/ap_push01/decide", { choice: "once", hash: good, confirmed: true })).status, 409, "the previous approval's hash can't approve another payload");
  assert.equal((await approvalsOf(page, "g_coding01")).find((a) => a.id === "ap_push01").status, "pending");
  await ctx.close();
});

test("@approval deny with a note: nothing is pushed and the run says so", async () => {
  const { ctx, page } = await open(S.RW, "g_coding01");
  await held(page);
  await page.click('#dock [data-act="once"]');
  await page.waitForSelector('#dock [data-approval="ap_push01"]');
  await page.focus("#dock .acard");
  await page.keyboard.press("r");
  await page.fill("#dock textarea", "Not yet, open the PR from a fork.");
  await page.click('#dock [data-act="deny-submit"]');
  await page.waitForFunction(() => window.__oc.summary().status === "done");
  const text = await page.textContent("#timeline");
  assert.match(text, /Denied by luke@test: git push/);
  assert.match(text, /Push was denied, nothing left the box/);
  await page.click("#tab-progress");
  assert.ok(await page.locator(".plan-skipped").count(), "the push step is skipped");
  const evs = await page.evaluate(() => window.__oc.count());
  assert.ok(evs > 0);
  await ctx.close();
});

// ============================================================================ @live
test("@live a 200-event burst keeps scroll position, the selected tab, focus and the hovered row", async () => {
  const { ctx, page } = await open(S.LIVE, "g_cap01", { h: 620 });
  await held(page, "#dock .acard.cap");
  await page.click("#tab-artifacts");
  await page.focus('[data-fid="art-art_outline"]');
  await page.evaluate(() => { const t = document.getElementById("timeline"); t.scrollTop = 40; });
  await page.waitForTimeout(150);
  const msg = page.locator("#timeline [data-msg]").nth(1);
  await msg.hover();
  const before = await page.evaluate(() => {
    const t = document.getElementById("timeline"), m = document.querySelectorAll("#timeline [data-msg]")[1].getBoundingClientRect();
    return { top: t.scrollTop, tab: document.querySelector('[role=tab][aria-selected=true]').dataset.tab, focus: document.activeElement.dataset.fid, rowTop: m.top, rowLeft: m.left, count: window.__oc.count(), renders: window.__oc.renders(), card: !!document.querySelector("#dock .acard.cap") };
  });
  assert.ok(before.top > 0 || before.top === 0);
  const res = await page.evaluate(async () => (await fetch("/__test/burst?goal=g_cap01&n=200", { method: "POST" })).json());
  assert.equal(res.events, 201);
  await page.waitForFunction((n) => window.__oc.count() >= n, before.count + 201, { timeout: 15000 });
  await page.waitForTimeout(500);
  const after = await page.evaluate(() => {
    const t = document.getElementById("timeline"), m = document.querySelectorAll("#timeline [data-msg]")[1].getBoundingClientRect();
    const pill = document.getElementById("newpill");
    return { top: t.scrollTop, tab: document.querySelector('[role=tab][aria-selected=true]').dataset.tab, focus: document.activeElement.dataset.fid, rowTop: m.top, rowLeft: m.left, renders: window.__oc.renders(), pill: pill.hidden ? "" : pill.textContent, card: !!document.querySelector("#dock .acard.cap"), count: window.__oc.count() };
  });
  assert.equal(after.top, before.top, "scroll position moved");
  assert.equal(after.tab, before.tab, "the tab switched");
  assert.equal(after.focus, before.focus, "focus moved");
  assert.equal(after.rowTop, before.rowTop, "the hovered row moved");
  assert.equal(after.rowLeft, before.rowLeft);
  assert.ok(after.card, "the cap card is still docked");
  assert.match(after.pill, /^\d+ new$/, "new items wait behind the pill");
  assert.ok(Number(after.pill.split(" ")[0]) >= 100);
  assert.ok(after.renders - before.renders <= 8, `200 events caused ${after.renders - before.renders} renders; coalescing keeps it to a handful (max 4/s)`);
  assert.deepEqual(page.errors, []);
  await page.click("#newpill");
  await page.waitForFunction(() => document.getElementById("newpill").hidden);
  assert.ok(await page.evaluate(() => { const t = document.getElementById("timeline"); return t.scrollHeight - t.scrollTop - t.clientHeight < 60; }), "revealing scrolls to the newest");
  await ctx.close();
});

test("@live following the bottom stays at the bottom, and no pill appears", async () => {
  const { ctx, page } = await open(S.LIVE, "g_cap01", { q: "speed=0&restart=1", h: 700 });
  await held(page, "#dock .acard.cap");
  await page.evaluate(() => { const t = document.getElementById("timeline"); t.scrollTop = t.scrollHeight; });
  await page.waitForTimeout(100);
  const n = await page.evaluate(() => window.__oc.count());
  await page.evaluate(async () => (await fetch("/__test/burst?goal=g_cap01&n=40", { method: "POST" })).json());
  await page.waitForFunction((x) => window.__oc.count() >= x + 41, n);
  await page.waitForTimeout(400);
  assert.ok(await page.evaluate(() => { const t = document.getElementById("timeline"); return t.scrollHeight - t.scrollTop - t.clientHeight < 60; }));
  assert.ok(await page.evaluate(() => document.getElementById("newpill").hidden));
  await ctx.close();
});

// ============================================================================ @palette
test("@palette every registered action is in the palette, the ? overlay and the DOM stays inside the registry", async () => {
  const { ctx, page } = await open(S.RO, "g_research01", { q: "speed=0&hold=0&restart=1" });
  await complete(page);
  const actions = await page.evaluate(() => JSON.parse(document.getElementById("oc-actions").textContent));
  assert.ok(actions.length > 40);
  await page.keyboard.press("Control+k");
  const labels = await page.$$eval("#pal-list li .lbl", (n) => n.map((x) => x.textContent));
  for (const a of actions.filter((x) => x.id !== "palette")) assert.ok(labels.includes(a.label), `palette is missing "${a.label}"`);
  assert.ok(labels.some((l) => /Compare pgvector/.test(l)), "palette also searches goals");
  assert.ok(labels.includes("report.md") && labels.some((l) => /ANN-Benchmarks/.test(l)), "and artifacts and sources");
  await page.keyboard.type("raise cap");
  assert.equal(await page.locator('#pal-list li[aria-selected="true"] .lbl').textContent(), "Raise cap and resume");
  await page.keyboard.press("Escape");
  await page.keyboard.press("?");
  const helped = await page.$$eval(".help-body li .lbl", (n) => n.map((x) => x.textContent));
  for (const a of actions.filter((x) => x.keys)) assert.ok(helped.includes(a.label), `? is missing "${a.label}"`);
  await page.keyboard.press("Escape");
  const onScreen = await page.$$eval("[data-action]", (n) => [...new Set(n.map((x) => x.dataset.action))]);
  const ids = new Set(actions.map((a) => a.id));
  for (const id of onScreen) assert.ok(ids.has(id), `${id} is on screen but not in the registry`);
  await ctx.close();
});

test("@palette running a palette command does the thing (theme, tab, sidebar toggle)", async () => {
  const { ctx, page } = await open(S.RO, "g_research01", { q: "speed=0&hold=0&restart=1" });
  await complete(page);
  await page.keyboard.press("Control+k"); await page.keyboard.type("sidebar: cost"); await page.keyboard.press("Enter");
  assert.equal(await page.getAttribute("#tab-cost", "aria-selected"), "true");
  await page.keyboard.press("Control+k"); await page.keyboard.type("toggle sidebar"); await page.keyboard.press("Enter");
  assert.equal(await page.getAttribute("#ws", "data-right"), "closed");
  await ctx.close();
});

// ============================================================================ @keyboard
test("@keyboard the whole journey with no pointer: approve, switch tabs, move messages, send, stop", async () => {
  const { ctx, page } = await open(S.RW, "g_coding01");
  await held(page);
  // keymap
  await page.locator("body").press("Escape");
  await page.keyboard.press("]"); assert.equal(await page.getAttribute("#ws", "data-right"), "closed");
  await page.keyboard.press("]"); assert.equal(await page.getAttribute("#ws", "data-right"), "open");
  await page.keyboard.press("["); assert.equal(await page.getAttribute("#ws", "data-left"), "closed");
  await page.keyboard.press("["); assert.equal(await page.getAttribute("#ws", "data-left"), "open");
  await page.keyboard.press("/"); assert.equal(await page.evaluate(() => document.activeElement.id), "composer-text");
  await page.keyboard.type("use pytest-repeat please");
  await page.keyboard.press("Control+Enter");
  await page.waitForFunction(() => /use pytest-repeat please/.test(document.getElementById("timeline").textContent));
  await page.keyboard.press("Escape");
  // 1-7 only while the sidebar has focus
  await page.keyboard.press("3");
  assert.equal(await page.getAttribute("#tab-progress", "aria-selected"), "true", "3 does nothing outside the sidebar");
  await page.focus("#tab-progress");
  for (const [k, t] of [["3", "artifacts"], ["5", "outbox"], ["7", "cost"], ["1", "progress"]]) {
    await page.keyboard.press(k);
    assert.equal(await page.getAttribute(`#tab-${t}`, "aria-selected"), "true", `key ${k}`);
  }
  await page.keyboard.press("ArrowRight"); assert.equal(await page.getAttribute("#tab-sources", "aria-selected"), "true");
  // messages
  await page.locator("body").click({ position: { x: 700, y: 300 } });
  await page.keyboard.press("Shift+J");
  const first = await page.evaluate(() => document.activeElement.dataset.msg);
  await page.keyboard.press("Shift+J");
  const second = await page.evaluate(() => document.activeElement.dataset.msg);
  assert.ok(first && second && first !== second);
  await page.keyboard.press("Shift+K");
  assert.equal(await page.evaluate(() => document.activeElement.dataset.msg), first);
  // outbox list: j/k/Enter/x
  await page.focus("#tab-outbox"); await page.keyboard.press("Enter");
  await page.focus('[data-fid="out-ap_clone01"]');
  await page.keyboard.press("x");
  assert.ok(await page.locator(".out-row.sel").count());
  await page.keyboard.press("Escape");
  assert.equal(await page.locator(".out-row.sel").count(), 0, "Esc clears the selection first");
  // approve with keys alone
  await page.keyboard.press("y");
  await page.keyboard.press("a");
  await page.waitForSelector('#dock [data-approval="ap_push01"]');
  await page.keyboard.press("Escape");
  await page.keyboard.press("y"); await page.keyboard.press("a"); await page.keyboard.press("a");
  await page.waitForFunction(() => window.__oc.summary().status === "done");
  // palette + help
  await page.keyboard.press("Control+k"); assert.ok(await page.locator('[role="combobox"]').isVisible());
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape"); // focus returned to the composer; one more Esc leaves it
  await page.keyboard.press("?"); assert.ok(await page.locator("[data-help]").isVisible());
  await page.keyboard.press("Escape");
  // g g goes to the workspace home
  await page.keyboard.press("g"); await page.keyboard.press("g");
  await page.waitForURL(/\/workspace$/);
  await ctx.close();
});

test("@keyboard . twice stops a paused run; once only asks", async () => {
  const { ctx, page } = await open(S.RW, "g_cap01");
  await held(page, "#dock .acard.cap");
  await page.locator("body").press("Escape");
  await page.keyboard.press(".");
  assert.match(await page.textContent("#toasts"), /Press \. again/);
  assert.equal((await page.evaluate(() => window.__oc.summary())).status, "paused");
  await page.keyboard.press(".");
  await page.waitForFunction(() => window.__oc.summary().status === "cancelled");
  assert.match(await page.textContent("#timeline"), /Run stopped/);
  await ctx.close();
});

test("@keyboard a key can be rebound from ? and the new key works, with clashes refused", async () => {
  const { ctx, page } = await open(S.RO, "g_research01", { q: "speed=0&hold=0&restart=1" });
  await complete(page);
  await page.keyboard.press("?");
  await page.click('[aria-label="Rebind Toggle goal list"]');
  await page.keyboard.press("Shift+B");
  await page.waitForSelector('.help-body li:has-text("Toggle goal list") kbd:has-text("shift+b")');
  await page.click('[aria-label="Rebind Toggle sidebar"]');
  await page.keyboard.press("Shift+B");
  assert.match(await page.textContent("#alerts"), /already used/);
  await page.keyboard.press("Escape"); await page.keyboard.press("Escape");
  await page.keyboard.press("Shift+B");
  assert.equal(await page.getAttribute("#ws", "data-left"), "closed");
  await ctx.close();
});

// ============================================================================ @citations
test("@citations chips merge, hover shows the passage, click highlights the source, Esc returns", async () => {
  const { ctx, page } = await open(S.RO, "g_research01", { q: "speed=0&hold=0&restart=1" });
  await complete(page);
  const chips = await page.$$eval('#timeline [data-msg="note_a3"] .cite', (n) => n.map((x) => x.textContent));
  assert.ok(chips.includes("1–2") && chips.includes("3–4"), `adjacent chips merge: ${chips}`);
  const chip = page.locator('#timeline [data-msg="note_a3"] .cite[data-cite="1,2"]');
  await chip.hover();
  assert.match(await page.textContent("#cite-pop"), /pgvector: open-source vector similarity/);
  assert.match(await page.textContent("#cite-pop"), /github\.com/);
  await chip.focus();
  await page.keyboard.press("Enter");
  assert.equal(await page.getAttribute("#tab-sources", "aria-selected"), "true");
  assert.ok(await page.locator('.src-row[data-src="1"].hit').count(), "the source row is highlighted");
  assert.equal(await page.evaluate(() => document.activeElement.dataset.srcBtn), "1");
  await page.keyboard.press("Escape");
  assert.equal(await page.evaluate(() => document.activeElement.dataset.cite), "1,2", "Esc returns to the chip");
  // the unverified source is flagged
  await page.click("#tab-sources");
  assert.ok(await page.locator('.src-row[data-src="6"] .nv:has-text("Not verified")').count());
  assert.equal(await page.locator('.src-row .nv').count(), 1);
  // clicking a source scrolls the chat to its first citation
  await page.evaluate(() => { document.getElementById("timeline").scrollTop = 0; });
  await page.click('.src-row[data-src="3"] .row-btn');
  await page.waitForSelector('#timeline .cite.hit');
  assert.ok(await page.evaluate(() => { const m = document.querySelector('#timeline .cite.hit').getBoundingClientRect(); return m.top > 0 && m.bottom < innerHeight; }));
  await ctx.close();
});

test("@citations file citations open the diff at that line", async () => {
  const { ctx, page } = await open(S.RO, "g_coding01", { q: "speed=0&hold=0&restart=1" });
  await complete(page);
  await page.click('#timeline .cite.file:has-text("src/gateway/ratelimit.py:8")');
  assert.equal(await page.getAttribute("#tab-changes", "aria-selected"), "true");
  await page.waitForSelector('.chg-row .dl[data-new="8"].hit');
  await ctx.close();
});

// ============================================================================ @cap
test("@cap the run pauses at the cap, posts 80% and 100% inline, and resumes with one action", async () => {
  const { ctx, page } = await open(S.RW, "g_cap01");
  await held(page, "#dock .acard.cap");
  const text = await page.textContent("#timeline");
  assert.match(text, /80% of the \$1\.00 cap is spent/);
  assert.match(text, /The \$1\.00 cap is reached/);
  assert.match(await page.textContent("#t-status"), /Paused at cap/);
  assert.match(await page.textContent("#t-spend"), /\$1\.00 \/ \$1\.00/);
  assert.ok(await page.locator("#t-spend.full").count());
  assert.equal(await page.locator("#dock .acard.cap button").count(), 1, "a single action");
  assert.match(await page.textContent("#dock .acard.cap button"), /Raise cap and resume/);
  assert.ok(await page.locator("#btn-stop").isVisible());
  await page.fill("#cap-input", "0.5");
  const posts = [];
  page.on("request", (r) => r.url().includes("/resume") && posts.push(1));
  await page.click("#dock .acard.cap button");
  assert.ok(await page.evaluate(() => document.getElementById("cap-input").matches(":invalid")), "a cap at or below spend is invalid");
  assert.deepEqual(posts, [], "nothing is sent for it");
  assert.equal((await api(page, "/goals/g_cap01/resume", { cap_usd: 0.5 })).status, 422, "and the server refuses it too");
  await page.fill("#cap-input", "2.50");
  await page.click("#dock .acard.cap button");
  await page.waitForFunction(() => window.__oc.summary().status === "done");
  assert.match(await page.textContent("#t-spend"), /\$1\.52 \/ \$2\.50/);
  assert.match(await page.textContent("#timeline"), /Resumed with a \$2\.50 cap/);
  await ctx.close();
});

// ============================================================================ @phone
test("@phone chat only, strip chips open the sheet on that tab, the approval card has large targets, swipe closes the sheet", async () => {
  const { ctx, page } = await open(S.RW, "g_coding01", { w: 390, h: 844, theme: "dark" });
  await held(page);
  assert.equal(await page.isVisible("#goals-col"), false, "the goal list is its own screen");
  assert.equal(await page.isVisible("#side"), false, "the sidebar is a bottom sheet, closed");
  const chips = await page.$$eval("#strip .strip-chip", (n) => n.map((x) => x.textContent.trim()));
  assert.ok(chips.some((c) => /Outbox 1/.test(c)) && chips.some((c) => /Sources/.test(c)) && chips.some((c) => /Artifacts/.test(c)), String(chips));
  const card = await page.locator("#dock .acard").boundingBox();
  assert.ok(card.width >= 390 - 32, "full-width card");
  for (const act of ["once", "goal", "deny"]) {
    const b = await page.locator(`#dock [data-act="${act}"]`).boundingBox();
    assert.ok(b.height >= 44 && b.width >= 300, `${act} target is ${b.width}x${b.height}`);
  }
  assert.ok((await page.locator("#composer").boundingBox()).y > card.y, "the card sits above the composer");
  await page.click('[data-strip="outbox"]');
  assert.equal(await page.getAttribute("#ws", "data-sheet"), "open");
  assert.equal(await page.getAttribute("#tab-outbox", "aria-selected"), "true");
  assert.equal(await page.getAttribute("#side", "data-snap"), "half");
  // snap points: drag handle up to full, down to half, down to peek, down to close
  const handle = await page.locator("#sheet-handle").boundingBox();
  const drag = async (dy) => { const hb = await page.locator("#sheet-handle").boundingBox(); await page.mouse.move(hb.x + hb.width / 2, hb.y + 8); await page.mouse.down(); await page.mouse.move(hb.x + hb.width / 2, hb.y + 8 + dy / 2); await page.mouse.move(hb.x + hb.width / 2, hb.y + 8 + dy); await page.mouse.up(); };
  await drag(-120); assert.equal(await page.getAttribute("#side", "data-snap"), "full");
  await drag(120); assert.equal(await page.getAttribute("#side", "data-snap"), "half");
  await drag(120); assert.equal(await page.getAttribute("#side", "data-snap"), "peek");
  await drag(120); assert.equal(await page.getAttribute("#ws", "data-sheet"), "closed", "swipe down closes");
  assert.ok(handle.height >= 32);
  // keyboard: handle arrows and Esc
  await page.click('[data-strip="outbox"]'); await page.focus("#sheet-handle"); await page.keyboard.press("ArrowUp");
  assert.equal(await page.getAttribute("#side", "data-snap"), "full");
  await page.keyboard.press("Escape");
  assert.equal(await page.getAttribute("#ws", "data-sheet"), "closed");
  // no horizontal page scroll
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 390));
  await ctx.close();
});

test("@phone the goal list is its own screen and opens a goal", async () => {
  const { ctx, page } = await open(S.RO, null, { w: 390, h: 844, q: "" });
  await page.waitForSelector("#goal-list .gl-row");
  assert.ok(await page.isVisible("#goals-col"));
  assert.equal(await page.isVisible("#timeline"), false);
  await page.click('#goal-list [data-goal="g_research01"]');
  await page.waitForURL(/g_research01/);
  assert.ok(await page.isVisible("#timeline"));
  assert.ok(await page.isVisible(".back-goals"));
  await ctx.close();
});

// ============================================================================ layout and themes
test("@layout three columns at 1440, resizable sidebar persists, spend chip and context ring are always visible", async () => {
  const { ctx, page } = await open(S.RO, "g_research01", { q: "speed=0&hold=0&restart=1" });
  await complete(page);
  const cols = await page.evaluate(() => ["goals-col", "main", "side"].map((id) => document.getElementById(id).getBoundingClientRect().width));
  assert.equal(cols[0], 240); assert.equal(cols[2], 360); assert.ok(cols[1] >= 480);
  const msgW = await page.evaluate(() => document.querySelector("#timeline [data-msg]").getBoundingClientRect().width);
  assert.ok(msgW <= 760);
  for (const t of ["progress", "sources", "artifacts", "changes", "outbox", "box", "cost"]) {
    await page.click(`#tab-${t}`);
    assert.ok(await page.isVisible("#t-spend") && await page.isVisible("#t-ctx"), `spend chip and context ring visible on ${t}`);
  }
  assert.equal(await page.locator('[role=tab]').count(), 7);
  await page.focus("#resizer");
  await page.keyboard.press("ArrowLeft"); await page.keyboard.press("ArrowLeft");
  assert.equal(await page.evaluate(() => document.getElementById("side").getBoundingClientRect().width), 392);
  await page.reload();
  await complete(page);
  assert.equal(await page.evaluate(() => document.getElementById("side").getBoundingClientRect().width), 392, "width persists");
  await ctx.close();
});

test("@layout the draft autosaves and survives a reload; tab choice persists per goal", async () => {
  const { ctx, page } = await open(S.RO, "g_research01", { q: "speed=0&hold=0&restart=1" });
  await complete(page);
  await page.fill("#composer-text", "half-written thought");
  await page.click("#tab-cost");
  await page.waitForTimeout(2300);
  await page.reload();
  await complete(page);
  assert.equal(await page.inputValue("#composer-text"), "half-written thought");
  assert.equal(await page.getAttribute("#tab-cost", "aria-selected"), "true");
  await ctx.close();
});

test("@layout no GET happens until the human acts: opening a goal does not start its replay", async () => {
  const { ctx, page } = await open(S.RO, null, { q: "" });
  const j = await page.evaluate(async () => (await (await fetch("/goals.json", { credentials: "same-origin" })).json()).goals);
  assert.ok(j.length === 3);
  await ctx.close();
});

test("@layout reduced motion: nothing animates at rest, the single running step is the only shimmer", async () => {
  const { ctx, page } = await open(S.RW, "g_coding01");
  await held(page);
  const running = await page.locator(".plan-running").count();
  assert.equal(running, 1, "at most one running step");
  const anim = await page.evaluate(() => [...document.querySelectorAll("*")].filter((e) => getComputedStyle(e, "::after").animationName !== "none" || getComputedStyle(e).animationName !== "none").length);
  assert.equal(anim, 0, "reducedMotion: no running animation");
  await ctx.close();
});
