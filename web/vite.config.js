import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Build output goes into the Python package so the frontend service
// (gui_label_tool.frontend.app) can serve it as static files.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../gui_label_tool/frontend/static",
    emptyOutDir: true,
  },
  server: {
    // During `npm run dev`, proxy API and screenshots to the Python BFF.
    proxy: {
      "/api": "http://localhost:8810",
      "/screenshots": "http://localhost:8810",
    },
  },
});
