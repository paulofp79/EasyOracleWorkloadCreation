/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the API to the backend (python -m app on 127.0.0.1:8080).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8080", changeOrigin: false } },
    fs: { allow: [".."] },
  },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "node" },
});
