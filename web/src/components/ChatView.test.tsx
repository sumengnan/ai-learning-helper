import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { ChatView } from "./ChatView";
import { streamChat, sendDecision } from "../api/client";

// mock streamChat：依次回调 TextDelta "你" / TextDelta "好" / RunFinished
vi.mock("../api/client", () => ({
  streamChat: vi.fn(async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
    onEvent({ type: "TextDelta", data: { text: "你" } });
    onEvent({ type: "TextDelta", data: { text: "好" } });
    onEvent({ type: "RunFinished", data: {} });
  }),
  sendDecision: vi.fn(async () => undefined),
}));

describe("ChatView", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(streamChat).mockClear();
    vi.mocked(sendDecision).mockClear();
  });
  afterEach(() => cleanup());

  it("StrictMode 下 TextDelta 累加不重复（不可变更新）", async () => {
    render(
      <React.StrictMode>
        <ChatView conversationId="c1" initial={[]} />
      </React.StrictMode>,
    );
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    // 助手气泡最终应为 "你好"，而不是 StrictMode 重复应用导致的 "你你好好"
    await waitFor(() => expect(screen.getByText("你好")).toBeTruthy());
    expect(screen.queryByText("你你好好")).toBeNull();
  });

  it("开关默认值：展示工具/Token 开、答错保存关", () => {
    render(<ChatView conversationId="c1" initial={[]} />);
    expect((screen.getByLabelText("展示工具调用和 Token") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("考试答错自动保存错题集") as HTMLInputElement).checked).toBe(false);
  });

  it("危险命令 ApprovalRequired → 弹窗渲染，批准后回传 sendDecision", async () => {
    vi.mocked(streamChat).mockImplementationOnce(
      async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
        onEvent({ type: "RunStarted", data: { run_id: "run-1" } });
        onEvent({ type: "ApprovalRequired", data: {
          approval_id: "a1", tool: "run_shell", command: "rm -rf /workspace", reason: "递归删除",
        } });
      });
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    // 弹窗出现，展示命令与原因
    await waitFor(() => expect(screen.getByText("rm -rf /workspace")).toBeTruthy());
    expect(screen.getByText(/递归删除/)).toBeTruthy();
    fireEvent.click(screen.getByText("批准执行"));
    await waitFor(() => expect(vi.mocked(sendDecision)).toHaveBeenCalledWith("run-1", "a1", true));
  });

  it("开启『答错自动保存』后持久化并透传给 streamChat", async () => {
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.click(screen.getByLabelText("考试答错自动保存错题集"));
    expect(localStorage.getItem("chat_save_wrong")).toBe("1");
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() => expect(vi.mocked(streamChat)).toHaveBeenCalled());
    const calls = vi.mocked(streamChat).mock.calls;
    const call = calls[calls.length - 1];
    expect(call[4]).toBe(true);        // saveWrong 为第 5 个参数
  });
});
