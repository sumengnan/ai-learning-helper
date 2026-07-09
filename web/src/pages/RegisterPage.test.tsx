import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { RegisterPage } from "./RegisterPage";
import * as client from "../api/client";

function renderPage() {
  return render(<MemoryRouter><RegisterPage /></MemoryRouter>);
}

describe("RegisterPage", () => {
  beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
  afterEach(() => cleanup());

  it("两次密码不一致时报错且不发注册请求", async () => {
    const spy = vi.spyOn(client.auth, "register");
    renderPage();
    fireEvent.change(screen.getByLabelText("账号", { selector: "input" }), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("密码", { selector: "input" }), { target: { value: "pw1234" } });
    fireEvent.change(screen.getByLabelText("确认密码", { selector: "input" }), { target: { value: "different" } });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    await waitFor(() => expect(screen.getByText("两次输入的密码不一致")).toBeTruthy());
    expect(spy).not.toHaveBeenCalled();
  });

  it("密码不足 6 位时报错", async () => {
    const spy = vi.spyOn(client.auth, "register");
    renderPage();
    fireEvent.change(screen.getByLabelText("账号", { selector: "input" }), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText("密码", { selector: "input" }), { target: { value: "123" } });
    fireEvent.change(screen.getByLabelText("确认密码", { selector: "input" }), { target: { value: "123" } });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    await waitFor(() => expect(screen.getByText("密码至少 6 位")).toBeTruthy());
    expect(spy).not.toHaveBeenCalled();
  });
});
