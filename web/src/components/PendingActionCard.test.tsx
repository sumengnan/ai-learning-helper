import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { PendingActionCard } from "./PendingActionCard";
import { api } from "../api/client";

const base = {
  id: "pa1", kind: "delete_questions", count: 2,
  labels: ["什么是自注意力机制", "Transformer 摒弃了循环"],
  created_at: "", expires_at: "",
};

beforeEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("PendingActionCard", () => {
  it("待确认时列出题干与两个按钮，并说明尚未执行", async () => {
    vi.spyOn(api.pendingActions, "get").mockResolvedValue({ ...base, status: "pending" } as any);
    render(<PendingActionCard id="pa1" />);
    expect(await screen.findByText(/从题库删除题目/)).toBeTruthy();
    expect(screen.getByText("什么是自注意力机制")).toBeTruthy();
    expect(screen.getByText(/尚未执行/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "确认删除" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "取消" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("点确认后调 confirm 并转为「已删除」，按钮消失", async () => {
    vi.spyOn(api.pendingActions, "get").mockResolvedValue({ ...base, status: "pending" } as any);
    const confirm = vi.spyOn(api.pendingActions, "confirm")
      .mockResolvedValue({ ok: true, deleted: 2 });
    render(<PendingActionCard id="pa1" />);
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith("pa1"));
    expect(await screen.findByText("已删除")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认删除" })).toBeNull();
  });

  it("点取消调 reject，不调 confirm", async () => {
    vi.spyOn(api.pendingActions, "get").mockResolvedValue({ ...base, status: "pending" } as any);
    const reject = vi.spyOn(api.pendingActions, "reject").mockResolvedValue({ ok: true });
    const confirm = vi.spyOn(api.pendingActions, "confirm");
    render(<PendingActionCard id="pa1" />);
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));
    await waitFor(() => expect(reject).toHaveBeenCalledWith("pa1"));
    expect(confirm).not.toHaveBeenCalled();
    expect(await screen.findByText("已取消")).toBeTruthy();
  });

  it("已确认过的（刷新后重挂载）不得再显示可点按钮", async () => {
    // 关键：状态以服务端为准。否则刷新后用户会以为没点、再点一次——服务端虽有一次性
    // 保护会返回 409，但让用户看见一个假的可点按钮本身就是缺陷。
    vi.spyOn(api.pendingActions, "get").mockResolvedValue({ ...base, status: "confirmed" } as any);
    render(<PendingActionCard id="pa1" />);
    expect(await screen.findByText("已删除")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认删除" })).toBeNull();
  });

  it("已过期的显示「已过期」且不可点", async () => {
    vi.spyOn(api.pendingActions, "get").mockResolvedValue({ ...base, status: "expired" } as any);
    render(<PendingActionCard id="pa1" />);
    expect(await screen.findByText("已过期")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认删除" })).toBeNull();
  });

  it("记录已不存在时整卡不渲染，不留空壳", async () => {
    vi.spyOn(api.pendingActions, "get").mockRejectedValue(new Error("404"));
    const { container } = render(<PendingActionCard id="pa1" />);
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });

  it("确认失败时报错并按服务端真实状态刷新按钮", async () => {
    const get = vi.spyOn(api.pendingActions, "get")
      .mockResolvedValueOnce({ ...base, status: "pending" } as any)
      .mockResolvedValueOnce({ ...base, status: "confirmed" } as any);
    vi.spyOn(api.pendingActions, "confirm").mockRejectedValue(new Error("该操作已处理过"));
    render(<PendingActionCard id="pa1" />);
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));
    expect(await screen.findByText("该操作已处理过")).toBeTruthy();
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "确认删除" })).toBeNull());
  });
});
