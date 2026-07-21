import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ForgotPasswordPage } from "./ForgotPasswordPage";
import * as client from "../api/client";

function renderPage() {
  return render(<MemoryRouter><ForgotPasswordPage /></MemoryRouter>);
}

const field = (label: string) => screen.getByLabelText(label, { selector: "input" });
const fill = (label: string, value: string) =>
  fireEvent.change(field(label), { target: { value } });
const submit = () => fireEvent.click(screen.getByRole("button", { name: "重置密码" }));

// 填满全部字段，个别项可覆盖——用于「只有这一项不对」的用例
function fillAll(over: Partial<Record<string, string>> = {}) {
  const v = {
    账号: "amy", 姓名: "艾米", 新密码: "new1234",
    确认新密码: "new1234", 验证码: "abcd", ...over,
  };
  for (const [label, value] of Object.entries(v)) fill(label, value);
}

describe("ForgotPasswordPage", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(client.auth, "captcha").mockResolvedValue({
      token: "tok", image: "data:image/svg+xml;base64,AAAA",
    });
  });
  afterEach(() => cleanup());

  it("四个字段加验证码齐备才提交", async () => {
    const spy = vi.spyOn(client.auth, "resetPassword").mockResolvedValue(undefined);
    renderPage();
    fillAll();
    submit();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0].slice(0, 3)).toEqual(["amy", "艾米", "new1234"]);
  });

  for (const [missing, message] of [
    ["账号", "请输入账号"],
    ["姓名", "请输入姓名"],
  ] as const) {
    it(`缺${missing}时报错且不发请求`, async () => {
      const spy = vi.spyOn(client.auth, "resetPassword");
      renderPage();
      fillAll({ [missing]: "" });
      submit();
      await waitFor(() => expect(screen.getByText(message)).toBeTruthy());
      expect(spy).not.toHaveBeenCalled();
    });
  }

  it("两次新密码不一致时报错且不发请求", async () => {
    const spy = vi.spyOn(client.auth, "resetPassword");
    renderPage();
    fillAll({ 确认新密码: "别的密码" });
    submit();
    await waitFor(() => expect(screen.getByText("两次输入的密码不一致")).toBeTruthy());
    expect(spy).not.toHaveBeenCalled();
  });

  it("新密码不足 6 位时报错且不发请求", async () => {
    const spy = vi.spyOn(client.auth, "resetPassword");
    renderPage();
    fillAll({ 新密码: "123", 确认新密码: "123" });
    submit();
    await waitFor(() => expect(screen.getByText("新密码至少 6 位")).toBeTruthy());
    expect(spy).not.toHaveBeenCalled();
  });

  it("缺验证码时报错且不发请求——它是挡住在线猜姓名的第一道闸", async () => {
    const spy = vi.spyOn(client.auth, "resetPassword");
    renderPage();
    fillAll({ 验证码: "" });
    submit();
    await waitFor(() => expect(screen.getByText("请输入验证码")).toBeTruthy());
    expect(spy).not.toHaveBeenCalled();
  });

  it("成功后不自动登录，只引导去登录页", async () => {
    // 猜对姓名不该直接变成一次静默的账号接管；后端也刻意不返回 token
    vi.spyOn(client.auth, "resetPassword").mockResolvedValue(undefined);
    renderPage();
    fillAll();
    submit();
    await waitFor(() => expect(screen.getByText(/密码已更新/)).toBeTruthy());
    expect(screen.getByRole("button", { name: "去登录" })).toBeTruthy();
  });

  it("后端拒绝时原样展示服务端文案（如账号或姓名不正确 / 次数过多）", async () => {
    vi.spyOn(client.auth, "resetPassword")
      .mockRejectedValue(new Error("账号或姓名不正确"));
    renderPage();
    fillAll();
    submit();
    await waitFor(() => expect(screen.getByText("账号或姓名不正确")).toBeTruthy());
    // 仍停在表单页，不该显示成功态
    expect(screen.queryByText(/密码已更新/)).toBeNull();
  });
});
