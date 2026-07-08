import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChatView } from "./ChatView";

// mock streamChat：依次回调 TextDelta "你" / TextDelta "好" / RunFinished
vi.mock("../api/client", () => ({
  streamChat: vi.fn(async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
    onEvent({ type: "TextDelta", data: { text: "你" } });
    onEvent({ type: "TextDelta", data: { text: "好" } });
    onEvent({ type: "RunFinished", data: {} });
  }),
}));

describe("ChatView", () => {
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
});
