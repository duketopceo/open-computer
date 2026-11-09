import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "@tokens": fileURLToPath(new URL("../services/opsview/static", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // effectsCss.test.ts and palette.test.ts read their CSS as raw text; other CSS stays stubbed out.
    css: { include: [/index\.css/, /oc-palette\.css/] },
  },
});
