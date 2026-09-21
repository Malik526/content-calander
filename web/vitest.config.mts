import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Milestone 3.5 (Phase 27 — test framework). Vitest + React Testing
// Library, not Jest: no extra transform config needed on top of Next's
// own tsconfig, and it shares the "@/*" alias below with tsconfig.json
// so test imports match app imports exactly.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
    exclude: ["node_modules", ".next", "out"],
  },
});
