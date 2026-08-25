import vue from "@vitejs/plugin-vue";

export function createViteConfig(environment = process.env) {
  const target = environment.II_FRONTEND_API_URL || "http://127.0.0.1:8000";
  const token = environment.II_FRONTEND_API_TOKEN;
  return {
    build: {
      outDir: "dist/client",
    },
    optimizeDeps: {
      include: ["vue"],
    },
    server: {
      host: "0.0.0.0",
      allowedHosts: ["terminal.local"],
      proxy: {
        "/api": {
          target,
          changeOrigin: true,
          configure(proxyServer) {
            if (!token) return;
            proxyServer.on("proxyReq", (proxyRequest) => {
              proxyRequest.setHeader("Authorization", `Bearer ${token}`);
            });
          },
        },
      },
      warmup: {
        clientFiles: ["./src/main.js", "./src/App.vue"],
      },
    },
    plugins: [vue()],
    test: {
      environment: "jsdom",
      include: ["src/**/*.test.js"],
    },
  };
}

export default createViteConfig();
