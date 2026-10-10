import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Welcome app is mounted at the site root; APP_BASE kept for symmetry with other apps.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: process.env.APP_BASE ?? "/",
  server: { port: 5176, proxy: { "/api": { target: process.env.COMIT_API_URL ?? "http://127.0.0.1:8080", changeOrigin: false, rewrite: path => path.replace(/^\/api/, "") } } },
});
