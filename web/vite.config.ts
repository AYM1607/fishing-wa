import { defineConfig } from "vite";

// Static SPA; data lives in public/data and is copied verbatim into dist/.
export default defineConfig({
  base: "./",
  server: { open: true },
});
