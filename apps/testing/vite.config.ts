import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// APP_BASE overrides the mount path inside the combined nginx container (infra/web.Dockerfile).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: process.env.APP_BASE ?? "/testing/",
  server: { port: 5175, proxy: { "/api": {
    target: process.env.COMIT_API_URL ?? "http://127.0.0.1:8080",
    changeOrigin: false, rewrite: path => path.replace(/^\/api/, ""),
  } } },
});
