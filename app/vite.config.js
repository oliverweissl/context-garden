import { readFileSync } from "node:fs";
import { defineConfig } from "vite";

// The version shown in the app comes from package.json, which a release bumps.
const { version } = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8"));

// Tauri expects a fixed dev port and no browser auto-open.
export default defineConfig({
  clearScreen: false,
  define: { __APP_VERSION__: JSON.stringify(version) },
  server: { port: 1420, strictPort: true },
  build: { target: "es2022", outDir: "dist", emptyOutDir: true },
});
