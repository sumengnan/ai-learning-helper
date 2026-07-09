import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { authFetch, setToken, getToken, setUnauthorizedHandler } from "./client";

function mockResponse(status: number, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({}), { status, headers });
}

describe("authFetch", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => { vi.restoreAllMocks(); setUnauthorizedHandler(null); });

  it("有 token 时注入 Authorization 头", async () => {
    setToken("tok123");
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(mockResponse(200));
    await authFetch("/api/x");
    const init = spy.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer tok123");
  });

  it("响应含 X-Refresh-Token 时更新本地 token", async () => {
    setToken("old");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockResponse(200, { "X-Refresh-Token": "fresh" }));
    await authFetch("/api/x");
    expect(getToken()).toBe("fresh");
  });

  it("401 时清除 token 并触发登出回调", async () => {
    setToken("bad");
    const onUnauth = vi.fn();
    setUnauthorizedHandler(onUnauth);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(mockResponse(401));
    await authFetch("/api/x");
    expect(getToken()).toBeNull();
    expect(onUnauth).toHaveBeenCalledOnce();
  });
});
