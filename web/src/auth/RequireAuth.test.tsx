import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "./AuthProvider";
import { RequireAuth } from "./RequireAuth";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<div>登录页</div>} />
          <Route path="/secret" element={<RequireAuth><div>机密内容</div></RequireAuth>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("RequireAuth", () => {
  beforeEach(() => localStorage.clear());

  it("未登录访问受保护页 → 重定向到登录页", () => {
    renderAt("/secret");
    expect(screen.getByText("登录页")).toBeTruthy();
    expect(screen.queryByText("机密内容")).toBeNull();
  });

  it("已登录（本地有 token+user）→ 放行", () => {
    localStorage.setItem("auth_token", "tok");
    localStorage.setItem("auth_user", JSON.stringify({ id: "u1", username: "alice" }));
    renderAt("/secret");
    expect(screen.getByText("机密内容")).toBeTruthy();
  });
});
