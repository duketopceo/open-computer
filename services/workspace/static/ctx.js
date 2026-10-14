// Shared singleton: the projection, view state (never derived from events), and the few actions every module needs.
import { createState } from "./projection.js";
import { store } from "./util.js";

const boot = JSON.parse(document.getElementById("ws-boot").textContent);
export const ctx = {
  boot,
  goal: boot.goal,
  state: createState(boot.goal),
  ui: {
    tab: "progress",
    expandedSteps: new Set(),
    panelArtifact: null,       // artifact id open inside the sidebar panel
    overlayArtifact: null,     // artifact id expanded over the center column
    reviewed: new Set(),
    openFiles: new Set(),
    deferred: new Set(),       // approval ids the operator pressed Esc on: still pending, just not docked
    armed: new Map(),          // approval id -> "once" | "goal": high risk needs a second press
    selected: new Set(),
    highlightSource: null,
    noteTo: null,              // step id the "Send note" input is open for
    follow: true,              // timeline sticks to the bottom only while the user is at the bottom
    revealed: 0,               // how many timeline entries are shown; the rest wait behind "N new"
    unreviewedSeen: new Set(),
    stopArmedAt: -Infinity,
  },
  render: () => {},            // set by app.js
  toast: () => {},
  announce: () => {},
  post: () => Promise.reject(new Error("not ready")),
};
ctx.persist = (k, v) => store("oc-ws:" + (ctx.goal || "none") + ":" + k, v);
export const csrf = () => document.querySelector("meta[name=csrf-token]").content;
