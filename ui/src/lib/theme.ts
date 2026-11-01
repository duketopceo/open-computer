// Theme choice: "light", "dark" or "system" (the default). The choice sets data-theme on the
// root element; "system" removes it so silver-lining.css follows prefers-color-scheme.
// The browser copy (localStorage "oc-theme", shared with the server-rendered pages) is the
// only store until the settings endpoint lands.

export type Theme = "light" | "dark" | "system";

const KEY = "oc-theme";

export function getTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    if (t === "light" || t === "dark") return t;
  } catch {
    /* storage blocked: follow system */
  }
  return "system";
}

export function applyTheme(theme: Theme, root: HTMLElement = document.documentElement): void {
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

export function setTheme(theme: Theme): void {
  applyTheme(theme);
  try {
    if (theme === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    /* storage blocked: applies for this page only */
  }
}
