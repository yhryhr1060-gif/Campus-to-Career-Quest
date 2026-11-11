import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server forwards /api to the FastAPI backend on port 8000.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
