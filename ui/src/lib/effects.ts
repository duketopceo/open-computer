// "Reduce effects": one switch for every decorative animation (background sheen, text reveal,
// edge glow, timeline motion). It sets data-effects="off" on the root element; the CSS also
// honours prefers-reduced-motion on its own. With nothing stored the OS preference decides.
// The browser copy (localStorage "oc-effects": "on" | "off") is the only store.

export type Effects = "on" | "off";

const KEY = "oc-effects";

const osReduces = (): boolean => {
  try {
    return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
};

export function getEffects(): Effects {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "on" || v === "off") return v;
  } catch {
    /* storage blocked: follow the OS */
  }
  return osReduces() ? "off" : "on";
}

export function applyEffects(effects: Effects, root: HTMLElement = document.documentElement): void {
  if (effects === "off") root.setAttribute("data-effects", "off");
  else root.removeAttribute("data-effects");
}

export function setEffects(effects: Effects): void {
  applyEffects(effects);
  try {
    localStorage.setItem(KEY, effects);
  } catch {
    /* storage blocked: applies for this page only */
  }
}
