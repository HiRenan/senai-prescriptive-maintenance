/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Relative references keep the bundle servable from any prefix, matching the
  // published staging contract ("./assets/…").
  base: "./",
  build: {
    target: "es2023",
    outDir: "dist",
    assetsDir: "assets",
    // The published CSP has no data: allowances; nothing may be inlined.
    assetsInlineLimit: 0,
    // The delivery grammar forbids source maps in the published bundle.
    sourcemap: false,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Mirrors the server.mjs prefix strip; the closed allowlist and limits
      // remain a production concern covered by server tests and Playwright.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    exclude: ["tests/browser/**"],
  },
});
