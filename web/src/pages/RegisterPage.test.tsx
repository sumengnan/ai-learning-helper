import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { RegisterPage } from "./RegisterPage";
import * as client from "../api/client";
import { AuthProvider } from "../auth/AuthProvider";

// 必须套 AuthProvider：页面调的是 useAuth().register，而 context 默认值是个空函数——
// 不套的话「校验不通过就不该发请求」这类断言会因为压根没有请求路径而永远成立（空转）。
function renderPage() {
  return render(
    <MemoryRouter><AuthProvider><RegisterPage /></AuthProvider></MemoryRouter>);
}

describe("RegisterPage", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(client.auth, "captcha").mockResolvedValue({
      token: "tok", image: "data:image/svg+xml;base64,AAAA",
    });
  });
  afterEach(() => cleanup());

  it("两次密码不一致时报错且不发注册请求", async () => {
    const spy = vi.spyOn(client.auth, "register");
    renderPage();
    fireEvent.change(screen.getByLabelText("账号", { selector: "input" }), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("姓名", { selector: "input" }), { target: { value: "爱丽丝" } });
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
    fireEvent.change(screen.getByLabelText("姓名", { selector: "input" }), { target: { value: "鲍勃" } });
    fireEvent.change(screen.getByLabelText("密码", { selector: "input" }), { target: { value: "123" } });
    fireEvent.change(screen.getByLabelText("确认密码", { selector: "input" }), { target: { value: "123" } });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    await waitFor(() => expect(screen.getByText("密码至少 6 位")).toBeTruthy());
    expect(spy).not.toHaveBeenCalled();
  });

  it("姓名必填——它是本站唯一的找回密码凭据，留空的账号日后无从自助重置", async () => {
    const spy = vi.spyOn(client.auth, "register");
    renderPage();
    fireEvent.change(screen.getByLabelText("账号", { selector: "input" }), { target: { value: "carol" } });
    fireEvent.change(screen.getByLabelText("密码", { selector: "input" }), { target: { value: "pw1234" } });
    fireEvent.change(screen.getByLabelText("确认密码", { selector: "input" }), { target: { value: "pw1234" } });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    await waitFor(() => expect(screen.getByText("请输入姓名")).toBeTruthy());
    expect(spy).not.toHaveBeenCalled();
  });

  it("姓名旁写明用途——填错或忘了就无法自助重置，这事得说在前面", () => {
    renderPage();
    expect(screen.getByText(/忘记密码时需凭此姓名重置/)).toBeTruthy();
  });

  it("姓名随注册请求一并提交（去首尾空格）", async () => {
    const spy = vi.spyOn(client.auth, "register").mockResolvedValue({ token: "t", user: { id: "u", username: "dave" } });
    renderPage();
    fireEvent.change(screen.getByLabelText("账号", { selector: "input" }), { target: { value: "dave" } });
    fireEvent.change(screen.getByLabelText("姓名", { selector: "input" }), { target: { value: "  戴夫  " } });
    fireEvent.change(screen.getByLabelText("密码", { selector: "input" }), { target: { value: "pw1234" } });
    fireEvent.change(screen.getByLabelText("确认密码", { selector: "input" }), { target: { value: "pw1234" } });
    fireEvent.change(screen.getByLabelText("验证码", { selector: "input" }), { target: { value: "abcd" } });
    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][2]).toBe("戴夫");
  });
});
