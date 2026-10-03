import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // Relative asset URLs plus hash routing let the same build be served from a
  // domain root, a sub-path (GitHub Pages project sites) or opened locally.
  base: "./",
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173 },
  preview: { host: "127.0.0.1", port: 4173 },
});
