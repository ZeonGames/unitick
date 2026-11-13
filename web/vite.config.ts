import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The report renderer and its styles live in the Python package, so the HTML
// reports and this app draw matches with exactly the same code.
const render = fileURLToPath(new URL("../src/unitick/render", import.meta.url));
const api = process.env.UNITICK_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@render": render } },
  server: {
    port: 5173,
    fs: { allow: [".", render] },
    proxy: { "/api": api, "/_media": api },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 1500 },
});
