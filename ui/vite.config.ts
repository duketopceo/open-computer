import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, URL } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const here = fileURLToPath(new URL(".", import.meta.url));
const STATIC = path.resolve(here, "../services/opsview/static");

// The demo's pre-paint theme script lives in opsview's static dir (index.html loads /static/theme-init.js). opsview
// serves it in the real console; the static demo host has no such route, so the demo build ships its own copy.
function themeInit(): Plugin {
  const file = path.join(STATIC, "theme-init.js");
  return {
    name: "oc-demo-theme-init",
    configureServer(server) {
      server.middlewares.use("/static/theme-init.js", (_req, res) => {
        res.setHeader("Content-Type", "text/javascript");
        res.end(fs.readFileSync(file));
      });
    },
    closeBundle() {
      const out = path.resolve(here, "dist-demo/static");
      fs.mkdirSync(out, { recursive: true });
      fs.copyFileSync(file, path.join(out, "theme-init.js"));
    },
  };
}

// Served by the opsview console (services/opsview). In dev, proxy API calls
// to the local console so the SPA is a thin shell against the real backend.
// `vite build --mode demo` builds the public fixtures demo (docs/runbooks/demo-deploy.md): `__OC_DEMO__` is a
// compile-time constant, true only for that mode, so the normal bundle never contains the demo code. The normal build
// refuses to run with VITE_DEMO in the environment, so a stray variable cannot leak fixtures into it.
export default defineConfig(({ mode }) => {
  const demo = mode === "demo";
  if (!demo && process.env.VITE_DEMO !== undefined) {
    throw new Error("VITE_DEMO is set: the demo is built only with `--mode demo` (pnpm build:demo). Unset it for a normal build.");
  }
  return {
    plugins: [react(), ...(demo ? [themeInit()] : [])],
    base: "/",
    define: { __OC_DEMO__: JSON.stringify(demo) },
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
        // One token file feeds the SPA and the server-rendered pages (KTD11).
        "@tokens": fileURLToPath(new URL("../services/opsview/static", import.meta.url)),
      },
    },
    build: {
      outDir: demo ? "dist-demo" : "dist",
      emptyOutDir: true,
      rollupOptions: {
        output: {
          // All demo code in one chunk named `demo`, so budget.json can key on it.
          manualChunks: (id: string) => (id.includes("/src/demo/") ? "demo" : undefined),
        },
      },
    },
    server: {
      fs: { allow: [".."] },
      port: 5173,
      proxy: demo
        ? undefined
        : {
            "/api": "http://localhost:8791",
            "/goals": "http://localhost:8791",
            "/_frag": "http://localhost:8791",
          },
    },
  };
});
