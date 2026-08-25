// @vitest-environment node

import { describe, expect, it, vi } from "vitest";

import { createViteConfig } from "../../vite.config.mjs";

describe("Vite 本地事故接口代理", () => {
  it("只在代理请求中注入服务端 Token", () => {
    const config = createViteConfig({
      II_FRONTEND_API_URL: "http://127.0.0.1:8000",
      II_FRONTEND_API_TOKEN: "runtime-secret",
    });
    const proxy = config.server.proxy["/api"];
    const handler = vi.fn();
    const proxyServer = { on: vi.fn((event, callback) => event === "proxyReq" && callback({ setHeader: handler })) };

    proxy.configure(proxyServer);

    expect(proxy.target).toBe("http://127.0.0.1:8000");
    expect(handler).toHaveBeenCalledWith("Authorization", "Bearer runtime-secret");
    expect(config.define).toBeUndefined();
  });
});
