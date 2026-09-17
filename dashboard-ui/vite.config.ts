import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built bundle is served by FastAPI from src/loompa/dashboard/static/
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/loompa/dashboard/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1600,
    rollupOptions: { output: { manualChunks: { phaser: ["phaser"], react: ["react", "react-dom"] } } },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/ws": { target: "ws://127.0.0.1:8765", ws: true },
    },
  },
});
