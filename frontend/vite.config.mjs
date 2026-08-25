import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  build: {
    outDir: "dist/client",
  },
  optimizeDeps: {
    include: ["vue"],
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: ["terminal.local"],
    warmup: {
      clientFiles: ["./src/main.js", "./src/App.vue"],
    },
  },
  plugins: [vue()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.js"],
  },
});
