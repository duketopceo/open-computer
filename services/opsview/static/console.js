// open-computer console behaviour. No dependencies, no build step.
// Palette, `?` overlay and nav all read the action registry the server embeds
// (#oc-actions, generated from services/opsview/registry.py).
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var el = function (tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  var ACTIONS = JSON.parse($("#oc-actions").textContent);
  var PAGE = document.documentElement.dataset.page;
  var LOG_DOM_CAP = 5000;

  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  function typing(t) { return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable); }

  // ---- toasts ----------------------------------------------------------
  function toast(msg, error) {
    var host = $(error ? "#alerts" : "#toasts");
    var t = el("div", "toast" + (error ? " error" : ""), msg);
    host.appendChild(t);
    if (!error) setTimeout(function () { t.remove(); }, 6000); // errors stay until the next action
    else setTimeout(function () { t.remove(); }, 20000);
  }

  // ---- layers (palette, help): one at a time, Esc restores focus -------
  var layer = null;
  function openLayer(build, label) {
    closeLayer();
    var opener = document.activeElement;
    var wrap = el("div", "layer");
    var dlg = el("div", "dialog");
    dlg.setAttribute("role", "dialog");
    dlg.setAttribute("aria-modal", "true");
    dlg.setAttribute("aria-label", label);
    wrap.appendChild(dlg);
    wrap.addEventListener("mousedown", function (e) { if (e.target === wrap) closeLayer(); });
    $("#layers").appendChild(wrap);
    layer = { wrap: wrap, opener: opener };
    build(dlg);
    return dlg;
  }
  function closeLayer() {
    if (!layer) return false;
    layer.wrap.remove();
    var o = layer.opener; layer = null;
    if (o && o.focus && document.contains(o)) o.focus();
    return true;
  }
  function trapTab(e) { if (e.key === "Tab" && layer) { e.preventDefault(); var i = $("input", layer.wrap); if (i) i.focus(); } }

  function keyHint(a) { return a.keys ? a.keys : ""; }

  // ---- palette ---------------------------------------------------------
  function openPalette() {
    if (layer && layer.wrap.querySelector("[role=combobox]")) { closeLayer(); return; }
    openLayer(function (dlg) {
      var input = el("input"); input.type = "text"; input.placeholder = "Type a command or section";
      input.setAttribute("role", "combobox"); input.setAttribute("aria-label", "Command palette");
      input.setAttribute("aria-expanded", "true"); input.setAttribute("aria-controls", "pal-list"); input.setAttribute("autocomplete", "off");
      var list = el("ul"); list.id = "pal-list"; list.setAttribute("role", "listbox"); list.setAttribute("aria-label", "Commands"); list.tabIndex = -1;
      dlg.appendChild(input); dlg.appendChild(list);
      var sel = 0, shown = [];
      function render() {
        var q = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
        shown = ACTIONS.filter(function (a) {
          if (a.id === "palette") return false;
          var hay = (a.label + " " + a.group + " " + a.keys + " " + a.note).toLowerCase();
          return q.every(function (w) { return hay.indexOf(w) >= 0; });
        });
        list.textContent = "";
        if (!shown.length) { var m = el("li", "empty-msg muted", "No command matches. Try a section name."); m.setAttribute("role", "presentation"); list.appendChild(m); }
        shown.forEach(function (a, i) {
          var li = el("li"); li.id = "pal-" + i; li.setAttribute("role", "option"); li.setAttribute("aria-selected", i === sel ? "true" : "false");
          li.appendChild(el("span", "grp", a.group)); li.appendChild(el("span", "lbl", a.label));
          if (a.note) li.appendChild(el("span", "nt", a.note));
          if (a.keys) li.appendChild(el("kbd", "", keyHint(a)));
          li.addEventListener("mousedown", function (ev) { ev.preventDefault(); run(a); });
          list.appendChild(li);
        });
        input.setAttribute("aria-activedescendant", shown.length ? "pal-" + sel : "");
        var cur = $("#pal-" + sel); if (cur) cur.scrollIntoView({ block: "nearest" });
      }
      function run(a) { var opener = layer && layer.opener; closeLayer(); perform(a.id, a, opener); }
      input.addEventListener("input", function () { sel = 0; render(); });
      input.addEventListener("keydown", function (e) {
        if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, shown.length - 1); render(); }
        else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); render(); }
        else if (e.key === "Enter") { e.preventDefault(); if (shown[sel]) run(shown[sel]); }
        else trapTab(e);
      });
      render(); input.focus();
    }, "Command palette");
  }

  // ---- ? overlay, generated from the same registry ----------------------
  function openHelp() {
    if (layer && layer.wrap.querySelector("[data-help]")) { closeLayer(); return; }
    openLayer(function (dlg) {
      dlg.setAttribute("data-help", "1");
      var input = el("input"); input.type = "text"; input.placeholder = "Filter shortcuts"; input.setAttribute("aria-label", "Filter keyboard shortcuts");
      var body = el("div", "help-body"); body.tabIndex = 0; body.setAttribute("aria-label", "Shortcuts");
      dlg.appendChild(input); dlg.appendChild(body);
      var order = []; ACTIONS.forEach(function (a) { if (order.indexOf(a.group) < 0) order.push(a.group); });
      function render() {
        var q = input.value.trim().toLowerCase();
        body.textContent = "";
        var any = false;
        order.forEach(function (g) {
          var rows = ACTIONS.filter(function (a) { return a.group === g && a.keys && (a.label + " " + a.keys).toLowerCase().indexOf(q) >= 0; });
          if (!rows.length) return;
          any = true;
          body.appendChild(el("h3", "", g));
          var ul = el("ul");
          rows.forEach(function (a) { var li = el("li", "static"); li.appendChild(el("span", "lbl", a.label)); li.appendChild(el("kbd", "", keyHint(a))); ul.appendChild(li); });
          body.appendChild(ul);
        });
        if (!any) body.appendChild(el("p", "empty-msg muted", "No shortcut matches."));
      }
      input.addEventListener("input", render);
      input.addEventListener("keydown", trapTab);
      render(); input.focus();
    }, "Keyboard shortcuts");
  }

  // ---- theme -----------------------------------------------------------
  function cycleTheme() {
    var cur = document.documentElement.getAttribute("data-theme") || "system";
    var next = { system: "dark", dark: "light", light: "system" }[cur];
    if (next === "system") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", next);
    store("oc-theme", next);
    toast("Theme: " + next);
  }

  // ---- row cursor (j/k/Enter) -------------------------------------------
  var cursor = -1;
  function rows() { return $$("[data-row]").filter(function (r) { return !r.hidden && r.offsetParent !== null; }); }
  function rowKey(r) { return r.dataset.approval || r.dataset.href || ""; }
  function setCursor(i, scroll) {
    var rs = rows();
    $$("[data-cur]").forEach(function (r) { r.removeAttribute("data-cur"); });
    if (!rs.length) { cursor = -1; return; }
    cursor = Math.max(0, Math.min(i, rs.length - 1));
    rs[cursor].setAttribute("data-cur", "");
    if (scroll !== false) rs[cursor].scrollIntoView({ block: "nearest" });
    store("oc-cur:" + location.pathname, rowKey(rs[cursor]));
  }
  function curRow() { var r = rows()[cursor]; return r || null; }
  function restoreCursor() {
    var saved = store("oc-cur:" + location.pathname); if (!saved) return;
    var rs = rows(); for (var i = 0; i < rs.length; i++) if (rowKey(rs[i]) === saved) { setCursor(i, false); return; }
  }
  function openRow() {
    var r = curRow(); if (!r) return;
    if (r.dataset.approval) { toggleDetail(r); return; }
    if (r.dataset.href) location.href = r.dataset.href;
  }
  function toggleDetail(item) {
    var d = $(".item-detail", item); if (!d) return;
    d.hidden = !d.hidden;
  }

  // ---- approvals ---------------------------------------------------------
  function csrf() { return $("meta[name=csrf-token]").content; }
  function post(url, data) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRF-Token": csrf() }, body: JSON.stringify(data) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); });
  }
  function afterDecision(item, msg) {
    var idx = rows().indexOf(item);
    item.remove();
    toast(msg);
    var left = $$("tbody.item").length;
    var badge = $("[data-action='nav.approvals'] .badge");
    if (badge) { if (left) badge.textContent = left; else badge.remove(); }
    if (!left) { setTimeout(function () { location.reload(); }, 600); return; }
    setCursor(idx, true);
  }
  function approve(item) {
    if (!item || item.dataset.busy) return;
    item.dataset.busy = "1";
    var dest = $(".dest", item).textContent.trim(), who = $(".who span", item).textContent;
    post("/approvals/" + item.dataset.approval + "/approve", { hash: item.dataset.hash }).then(function (r) {
      delete item.dataset.busy;
      if (r.ok) afterDecision(item, "Approved " + who + "'s request. The payload went to " + dest + ".");
      else toast(r.body.error || "Could not approve.", true);
    }, function () { delete item.dataset.busy; toast("Could not reach the console. Nothing was approved.", true); });
  }
  function openNote(item) {
    if (!item) return;
    var n = $(".item-note", item); n.hidden = false;
    var d = $(".item-detail", item); if (d) d.hidden = false;
    $("textarea", n).focus();
  }
  function closeNote(item) {
    var n = $(".item-note", item); n.hidden = true; $(".err", n).textContent = "";
    var b = $("[data-act=sendback]", item); if (b) b.focus();
  }
  function submitNote(item) {
    var n = $(".item-note", item), ta = $("textarea", n), err = $(".err", n);
    var note = ta.value.trim();
    if (!note) { err.textContent = "Add a note so the agent knows what to change."; ta.setAttribute("aria-invalid", "true"); ta.focus(); return; }
    ta.removeAttribute("aria-invalid"); err.textContent = "";
    var who = $(".who span", item).textContent;
    post("/approvals/" + item.dataset.approval + "/send-back", { hash: item.dataset.hash, note: note }).then(function (r) {
      if (r.ok) afterDecision(item, "Sent back to " + who + ". Nothing left the box.");
      else { err.textContent = r.body.error || "Could not send back."; ta.focus(); }
    }, function () { err.textContent = "Could not reach the console. Nothing was sent."; });
  }
  function needRow() {
    var r = curRow();
    if (!r || !r.dataset.approval) { setCursor(0); r = curRow(); }
    return r && r.dataset.approval ? r : null;
  }

  // ---- actions (single dispatch point for keys and the palette) ----------
  var byId = {}; ACTIONS.forEach(function (a) { byId[a.id] = a; });
  function perform(id, a, opener) {
    switch (id) {
      case "palette": return openPalette();
      case "help": return openHelp();
      case "theme": return cycleTheme();
      case "close": return escape();
      case "row.down": return setCursor(cursor + 1);
      case "row.up": return setCursor(cursor < 0 ? 0 : cursor - 1);
      case "row.open": return openRow();
      case "approval.approve": return approve(needRow());
      case "approval.sendback": return openNote(needRow());
      case "router.focus": case "audit.focus": var i = $("#q") || $("#aq"); if (i) i.focus(); return;
      default: if (a && a.href) location.href = a.href;
    }
  }
  function escape() {
    if (closeLayer()) return true;
    var open = $$(".item-note").filter(function (n) { return !n.hidden; })[0];
    if (open) { closeNote(open.parentNode); return true; }
    if (document.body.classList.contains("drawer")) { toggleDrawer(false); return true; }
    var det = $$(".item-detail").filter(function (n) { return !n.hidden; });
    if (det.length) { det.forEach(function (d) { d.hidden = true; }); return true; }
    if (cursor >= 0) { $$("[data-cur]").forEach(function (r) { r.removeAttribute("data-cur"); }); cursor = -1; return true; }
    return false;
  }

  // ---- keyboard ----------------------------------------------------------
  var gArmed = 0;
  var gMap = {};
  ACTIONS.forEach(function (a) { var m = /^g (\S)$/.exec(a.keys); if (m) gMap[m[1]] = a; });
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
    if (e.key === "Escape") {
      var noteTa = e.target && e.target.classList && e.target.classList.contains("note-input");
      if (escape() || noteTa) e.preventDefault();
      return;
    }
    if (layer) return; // dialogs handle their own keys
    if (e.target.classList && e.target.classList.contains("note-input") && e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submitNote(e.target.closest("tbody.item")); return; }
    if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (gArmed && Date.now() - gArmed < 900) {
      gArmed = 0;
      var g = gMap[e.key]; if (g) { e.preventDefault(); location.href = g.href; }
      return;
    }
    var onControl = e.target.closest && e.target.closest("a, button, summary, [role=button]");
    switch (e.key) {
      case "g": gArmed = Date.now(); return;
      case "?": e.preventDefault(); openHelp(); return;
      case "j": case "ArrowDown": if (e.key === "ArrowDown" && e.target.closest && e.target.closest(".log")) return; e.preventDefault(); setCursor(cursor + 1); return;
      case "k": case "ArrowUp": if (e.key === "ArrowUp" && e.target.closest && e.target.closest(".log")) return; e.preventDefault(); setCursor(cursor < 0 ? 0 : cursor - 1); return;
      case "Enter": if (onControl || cursor < 0) return; e.preventDefault(); openRow(); return;
      case "c": e.preventDefault(); location.href = "/runs/new"; return;
      case "/": if ($("#q") || $("#aq")) { e.preventDefault(); perform("router.focus"); } return;
      case "a": if (PAGE === "approvals") { e.preventDefault(); perform("approval.approve"); } return;
      case "r": if (PAGE === "approvals") { e.preventDefault(); perform("approval.sendback"); } return;
    }
  });

  // ---- click wiring -------------------------------------------------------
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-action]");
    if (t && !t.matches("a[href]")) { e.preventDefault(); perform(t.dataset.action, byId[t.dataset.action]); return; }
    var b = e.target.closest("[data-act]");
    if (b) {
      var item = b.closest("tbody.item");
      var act = b.dataset.act;
      if (act === "approve") approve(item);
      else if (act === "sendback") openNote(item);
      else if (act === "sendback-submit") submitNote(item);
      else if (act === "sendback-cancel") closeNote(item);
      return;
    }
    var row = e.target.closest("[data-row]");
    if (row && !e.target.closest("a, button, textarea, input")) { var rs = rows(); setCursor(rs.indexOf(row), false); if (row.dataset.href) location.href = row.dataset.href; else if (row.dataset.approval) toggleDetail(row); }
  });

  // ---- drawer (< 1024px) --------------------------------------------------
  function toggleDrawer(on) {
    document.body.classList.toggle("drawer", on);
    var m = $("#menu"); m.setAttribute("aria-expanded", on ? "true" : "false");
    if (on) { var f = $("#sidebar a"); if (f) f.focus(); } else m.focus();
  }
  $("#menu").addEventListener("click", function () { toggleDrawer(!document.body.classList.contains("drawer")); });

  // ---- skeleton fragments -------------------------------------------------
  $$("[data-frag]").forEach(function (host) {
    fetch(host.dataset.frag, { credentials: "same-origin", headers: { "Accept": "text/html" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (h) { host.innerHTML = h; host.removeAttribute("aria-busy"); restoreCursor(); })
      .catch(function () { host.textContent = "Could not load this section. Reload to retry."; host.removeAttribute("aria-busy"); });
  });

  // ---- busy form (long run): swap in skeleton, never a spinner ----------------
  $$("form[data-busy-form]").forEach(function (f) {
    f.addEventListener("submit", function () {
      var b = $("[data-busy]"); if (b) b.hidden = false;
      $$("button, a.btn", f).forEach(function (x) { x.setAttribute("disabled", "true"); x.setAttribute("aria-disabled", "true"); });
      f.hidden = true;
    });
  });

  // ---- virtualized log viewer: JetBrains Mono 12px, 18px lines, windowed ----------
  function initLog() {
    var host = $("#log"), data = $("#log-data"); if (!host || !data) return;
    var lines = JSON.parse(data.textContent), H = 18, OVER = 40;
    var spacer = el("div", "log-spacer"), win = el("div", "log-win");
    spacer.appendChild(win); host.appendChild(spacer);
    spacer.style.height = lines.length * H + "px";
    function draw() {
      var first = Math.max(0, Math.floor(host.scrollTop / H) - OVER);
      var count = Math.min(Math.ceil(host.clientHeight / H) + OVER * 2, LOG_DOM_CAP, lines.length - first);
      win.style.top = first * H + "px";
      var frag = document.createDocumentFragment();
      for (var i = first; i < first + count; i++) {
        var row = el("div", "ln"); row.appendChild(el("span", "no", String(i + 1))); row.appendChild(el("span", "tx", lines[i])); frag.appendChild(row);
      }
      win.textContent = ""; win.appendChild(frag);
      host.dataset.rendered = String(count);
    }
    var raf = 0;
    host.addEventListener("scroll", function () { if (!raf) raf = requestAnimationFrame(function () { raf = 0; draw(); }); });
    host.addEventListener("keydown", function (e) {
      if (e.key === "End") { e.preventDefault(); host.scrollTop = host.scrollHeight; }
      else if (e.key === "Home") { e.preventDefault(); host.scrollTop = 0; }
      else if (e.key === "j" || e.key === "k") { e.preventDefault(); e.stopPropagation(); host.scrollTop += (e.key === "j" ? H : -H) * 3; }
    });
    host.addEventListener("keydown", function (e) { if (e.key === "ArrowDown") { e.preventDefault(); host.scrollTop += H; } else if (e.key === "ArrowUp") { e.preventDefault(); host.scrollTop -= H; } });
    window.addEventListener("resize", draw);
    draw();
  }

  initLog();
  restoreCursor();
})();
