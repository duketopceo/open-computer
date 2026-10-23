import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by the opsview console (services/opsview). In dev, proxy API calls
// to the local console so the SPA is a thin shell against the real backend.
export default defineConfig({
  plugins: [react()],
  base: "/",
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8791",
      "/goals": "http://localhost:8791",
      "/_frag": "http://localhost:8791",
    },
  },
});