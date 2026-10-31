import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { RouterProvider, matchRoute, useRouter } from "./lib/router";
import Approvals from "./pages/Approvals";
import Audit from "./pages/Audit";
import Devices from "./pages/Devices";
import NotFound from "./pages/NotFound";
import Overview from "./pages/Overview";
import Router from "./pages/Router";
import Runs from "./pages/Runs";
import SandboxDetail from "./pages/SandboxDetail";
import Sandboxes from "./pages/Sandboxes";
import "./index.css";

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
  { path: "/goals", page: () => <Runs /> },
];

function Routes() {
  const { path } = useRouter();
  const m = matchRoute(path, ROUTES);
  return m ? m.route.page(m.params) : <NotFound />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider>
      <Routes />
    </RouterProvider>
  </StrictMode>,
);
