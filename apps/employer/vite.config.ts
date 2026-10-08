import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// APP_BASE overrides the mount path inside the combined nginx container (infra/web.Dockerfile).
// Dev server mirrors nginx: /api/* is proxied to api-core with the prefix stripped (infra/nginx/nginx.conf).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: process.env.APP_BASE ?? "/employer/",
  server: {
    port: 5174,
    proxy: {
      "/api": {
        target: process.env.API_PROXY_TARGET ?? "http://localhost:8080",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
