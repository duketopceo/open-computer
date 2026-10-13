// open-computer workbench (/work): three-pane workplace surface.
// Server-rendered panes stream from /_frag/work; selection rides in
// location.hash so refresh restores it. No dependencies, no build step.
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var el = function (tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  var rail = $("#w-rail"), main = $("#w-main"), insp = $("#w-inspect");
  if (!rail || !main || !insp) return; // not on the work page

  var sel = { run: (location.hash || "").replace(/^#/, ""), sub: "" };
  var timer = 0;

  function csrf() { return $("meta[name=csrf-token]").content; }
  function post(url, data) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRF-Token": csrf() }, body: JSON.stringify(data) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); });
  }
  function toast(msg, error) {
    var host = $(error ? "#alerts" : "#toasts"); if (!host) return;
    var t = el("div", "toast" + (error ? " error" : ""), msg);
    host.appendChild(t);
    setTimeout(function () { t.remove(); }, error ? 20000 : 6000);
  }

  function schedule(ms) { clearTimeout(timer); timer = setTimeout(poll, ms); }
  function paint(d) {
    rail.innerHTML = d.rail; main.innerHTML = d.main; insp.innerHTML = d.inspect;
    schedule(d.active ? 2500 : 8000);
  }
  function poll() {
    if (document.hidden) { schedule(8000); return; }
    var u = "/_frag/work?run=" + encodeURIComponent(sel.run || "");
    if (sel.sub !== "") u += "&sub=" + encodeURIComponent(sel.sub);
    fetch(u, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); }).then(paint)
      .catch(function () { schedule(8000); });
  }

  function showConfirm(goal, cap) {
    var card = el("div", "w-card");
    card.appendChild(el("p", "w-card-t", "Run this goal?"));
    var echo = el("p", "w-goal-echo"); echo.textContent = goal; card.appendChild(echo);
    card.appendChild(el("p", "muted caption",
      "A planner call decomposes the goal; subtasks placed cloud-* go to openrouter.ai. " +
      "Spend is metered against the $" + cap + " daily cap. Anything flagged private stays local or waits for approval."));
    var row = el("div", "actions");
    var go = el("button", "btn primary", "Run it"); go.type = "button";
    var cancel = el("button", "btn ghost", "Cancel"); cancel.type = "button";
    go.addEventListener("click", function () {
      go.disabled = true;
      post("/goals", { goal: goal, confirm: "1" }).then(function (r) {
        if (r.ok && r.body.run) {
          sel.run = r.body.run; sel.sub = ""; location.hash = sel.run; poll();
          toast("Goal submitted — decomposing and dispatching.");
        } else { go.disabled = false; toast((r.body && r.body.error) || "Could not submit the goal.", true); }
      }, function () { go.disabled = false; toast("Could not reach the console. Nothing was submitted.", true); });
    });
    cancel.addEventListener("click", poll);
    row.appendChild(go); row.appendChild(cancel); card.appendChild(row);
    insp.innerHTML = "";
    insp.appendChild(el("h2", "", "Confirm"));
    insp.appendChild(card);
  }

  document.addEventListener("click", function (e) {
    var rb = e.target.closest("[data-run]");
    if (rb) { sel.run = rb.getAttribute("data-run"); sel.sub = ""; location.hash = sel.run; poll(); return; }
    var tb = e.target.closest("[data-sub]");
    if (tb) { sel.sub = tb.getAttribute("data-sub"); poll(); return; }
  });

  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f.matches || !f.matches("form.composer")) return;
    e.preventDefault();
    var ta = f.querySelector("textarea[name=goal]");
    var goal = ta ? ta.value.trim() : "";
    if (!goal) return;
    post("/goals", { goal: goal }).then(function (r) {
      if (r.ok && r.body.needs_confirm) { showConfirm(goal, r.body.cap); return; }
      if (r.ok && r.body.run) { f.reset(); sel.run = r.body.run; sel.sub = ""; location.hash = sel.run; poll(); return; }
      toast((r.body && r.body.error) || "Could not submit the goal.", true);
    }, function () { toast("Could not reach the console. Nothing was submitted.", true); });
  });

  // j/k moves the run selection here (console.js's cursor leaves our rows alone)
  document.addEventListener("keydown", function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
    if (e.key !== "j" && e.key !== "k") return;
    var runs = rail.querySelectorAll("[data-run]");
    if (!runs.length) return;
    var idx = -1;
    runs.forEach(function (b, i) { if (b.getAttribute("data-run") === sel.run) idx = i; });
    idx = e.key === "j" ? Math.min(idx + 1, runs.length - 1) : Math.max(idx - 1, 0);
    var b = runs[idx];
    if (b) { sel.run = b.getAttribute("data-run"); sel.sub = ""; location.hash = sel.run; poll(); }
  });

  poll();
})();
