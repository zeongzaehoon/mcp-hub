import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// "/src" is resolved by Vite relative to the project root, which avoids a dependency on
// @types/node (no `path`/`__dirname` needed in the config).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": "/src",
    },
  },
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    // dev server is also reached by the in-network e2e runner (docker-compose service name)
    allowedHosts: ["localhost", "frontend"],
  },
});
