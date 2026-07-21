// web/src/components/AccountDialogs.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { ChangeNameDialog, ChangePasswordDialog } from "./AccountDialogs";

const updateName = vi.fn();
const changePassword = vi.fn();
const refreshUser = vi.fn();

vi.mock("../api/client", () => ({
  auth: {
    updateName: (...a: unknown[]) => updateName(...a),
    changePassword: (...a: unknown[]) => changePassword(...a),
  },
}));
vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: "1", username: "amy", full_name: "艾米" }, refreshUser }),
}));

beforeEach(() => {
  updateName.mockReset().mockResolvedValue({ id: "1", username: "amy", full_name: "艾米丽" });
  changePassword.mockReset().mockResolvedValue(undefined);
  refreshUser.mockReset();
});
afterEach(() => cleanup());

describe("ChangeNameDialog", () => {
  it("预填当前姓名，改完提交并刷新本地 user", async () => {
    const onClose = vi.fn();
    render(<ChangeNameDialog open onClose={onClose} />);
    const input = screen.getByLabelText("姓名", { selector: "input" }) as HTMLInputElement;
    expect(input.value).toBe("艾米");            // 预填，省得用户重打一遍

    fireEvent.change(input, { target: { value: "艾米丽" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(updateName).toHaveBeenCalledWith("艾米丽"));
    // 顶栏显示的是姓名，不刷新本地 user 就会一直显示旧名字直到重新登录
    await waitFor(() => expect(refreshUser).toHaveBeenCalledWith(
      { id: "1", username: "amy", full_name: "艾米丽" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("姓名为空时不发请求", async () => {
    render(<ChangeNameDialog open onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("姓名", { selector: "input" }), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("请填写姓名")).toBeTruthy();
    expect(updateName).not.toHaveBeenCalled();
  });

  it("后端报错时展示原因且不关闭", async () => {
    updateName.mockRejectedValue(new Error("姓名不超过 64 字"));
    const onClose = vi.fn();
    render(<ChangeNameDialog open onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("姓名不超过 64 字")).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("ChangePasswordDialog", () => {
  const fill = async (cur: string, next: string, confirm: string) => {
    fireEvent.change(screen.getByLabelText("当前密码", { selector: "input" }), { target: { value: cur } });
    fireEvent.change(screen.getByLabelText("新密码", { selector: "input" }), { target: { value: next } });
    fireEvent.change(screen.getByLabelText("确认新密码", { selector: "input" }), { target: { value: confirm } });
  };

  it("填对则提交当前密码与新密码", async () => {
    const onClose = vi.fn();
    render(<ChangePasswordDialog open onClose={onClose} />);
    await fill("old1234", "new5678", "new5678");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(changePassword).toHaveBeenCalledWith("old1234", "new5678"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("两次新密码不一致时不发请求", async () => {
    render(<ChangePasswordDialog open onClose={vi.fn()} />);
    await fill("old1234", "new5678", "new5679");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("两次输入的新密码不一致")).toBeTruthy();
    expect(changePassword).not.toHaveBeenCalled();
  });

  it("新密码太短时本地就拦下，不白跑一趟后端", async () => {
    render(<ChangePasswordDialog open onClose={vi.fn()} />);
    await fill("old1234", "abc", "abc");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("密码至少 6 位")).toBeTruthy();
    expect(changePassword).not.toHaveBeenCalled();
  });

  it("当前密码错误由后端判定，原样展示且不关闭", async () => {
    changePassword.mockRejectedValue(new Error("当前密码不正确"));
    const onClose = vi.fn();
    render(<ChangePasswordDialog open onClose={onClose} />);
    await fill("猜的", "new5678", "new5678");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("当前密码不正确")).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });
});
