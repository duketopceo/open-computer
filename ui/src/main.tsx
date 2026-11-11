import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { RouterProvider, matchRoute, useRouter } from "./lib/router";
import Approvals from "./pages/Approvals";
import Audit from "./pages/Audit";
import Devices from "./pages/Devices";
import NotFound from "./pages/NotFound";
import Automations from "./pages/Automations";
import Overview from "./pages/Overview";
import Settings from "./pages/Settings";
import Skills from "./pages/Skills";
import Router from "./pages/Router";
import Runs from "./pages/Runs";
import SandboxDetail from "./pages/SandboxDetail";
import Sandboxes from "./pages/Sandboxes";
import { applyEffects, getEffects } from "./lib/effects";
import { applyTheme, getTheme } from "./lib/theme";
import "./index.css";

applyTheme(getTheme());
applyEffects(getEffects());

// Route table. `/` is the run-focused App, unchanged; every other page renders
// inside PageShell. Ported pages are added here (and flipped to `spa: true` in
// lib/nav.ts); anything unmatched is the in-app not-found page.
type Row = { path: string; page: (p: Record<string, string>) => JSX.Element };
const ROUTES: Row[] = [
  { path: "/", page: () => <App /> },
  { path: "/devices", page: () => <Devices /> },
  { path: "/sandboxes", page: () => <Sandboxes /> },
  { path: "/sandboxes/:id", page: (p) => <SandboxDetail id={p.id} /> },
  { path: "/router", page: () => <Router /> },
  { path: "/audit", page: () => <Audit /> },
  { path: "/approvals", page: () => <Approvals /> },
  { path: "/runs", page: () => <Runs /> },
  { path: "/overview", page: () => <Overview /> },
  { path: "/skills", page: () => <Skills /> },
  { path: "/automations", page: () => <Automations /> },
  { path: "/goals", page: () => <Runs /> },
  { path: "/settings", page: () => <Settings /> },
];

function Routes() {
  const { path } = useRouter();
  const m = matchRoute(path, ROUTES);
  return m ? m.route.page(m.params) : <NotFound />;
}

async function boot() {
  // Demo build only: install the fixtures transport (and the banner) before the first render. `__OC_DEMO__` is a build-time
  // constant, so this whole branch and the demo chunk are absent from the normal bundle.
  let Banner: (() => JSX.Element) | null = null;
  if (__OC_DEMO__) {
    const demo = await import("./demo/install");
    demo.installDemo();
    Banner = demo.DemoBanner;
  }
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <RouterProvider>
        {Banner ? (
          <div className="flex h-full flex-col">
            <Banner />
            <div className="min-h-0 flex-1">
              <Routes />
            </div>
          </div>
        ) : (
          <Routes />
        )}
      </RouterProvider>
    </StrictMode>,
  );
}

void boot();
