import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// APP_BASE overrides the mount path inside the combined nginx container (infra/web.Dockerfile).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: process.env.APP_BASE ?? "/candidate/",
  server: { port: 5173 },
});
