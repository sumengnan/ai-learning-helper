import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { ChatView } from "./ChatView";
import { streamChat, api } from "../api/client";

vi.mock("../api/client", () => ({
  streamChat: vi.fn(async () => undefined),
  sendDecision: vi.fn(async () => undefined),
  api: {
    attachments: {
      upload: vi.fn(async (_cid: string, file: File) => ({
        id: "srv-1", filename: file.name, size: file.size, content_type: file.type,
      })),
      remove: vi.fn(async () => undefined),
      blob: vi.fn(async () => new Blob(["x"])),
    },
  },
}));

function uploadFile(container: HTMLElement, file: File) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

describe("ChatView 附件", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(streamChat).mockClear();
    vi.mocked(api.attachments.upload).mockClear();
    vi.mocked(api.attachments.remove).mockClear();
  });
  afterEach(() => cleanup());

  it("点击上传 → 芯片出现，发送时带 attachment_ids 并渲染到用户气泡", async () => {
    const { container } = render(<ChatView conversationId="c1" initial={[]} />);
    uploadFile(container, new File(["hello"], "note.txt", { type: "text/plain" }));
    await waitFor(() => expect(vi.mocked(api.attachments.upload)).toHaveBeenCalled());
    // 芯片显示文件名
    await waitFor(() => expect(screen.getAllByText("note.txt").length).toBeGreaterThan(0));

    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "看看" } });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() => expect(vi.mocked(streamChat)).toHaveBeenCalled());
    const calls = vi.mocked(streamChat).mock.calls;
    const call = calls[calls.length - 1];
    expect(call[5]).toEqual(["srv-1"]);   // 第 6 个参数为 attachment ids
    // 发送后气泡仍渲染附件名
    await waitFor(() => expect(screen.getAllByText("note.txt").length).toBeGreaterThan(0));
  });

  it("删除待发附件 → 芯片消失且调用后端删除", async () => {
    const { container } = render(<ChatView conversationId="c1" initial={[]} />);
    uploadFile(container, new File(["hello"], "gone.txt", { type: "text/plain" }));
    await waitFor(() => expect(screen.getByText("gone.txt")).toBeTruthy());
    // MUI Chip 的删除按钮
    fireEvent.click(screen.getByTestId("CancelIcon"));
    await waitFor(() => expect(screen.queryByText("gone.txt")).toBeNull());
    expect(vi.mocked(api.attachments.remove)).toHaveBeenCalledWith("srv-1");
  });

  it("初始消息带 attachments → 用户气泡渲染附件", () => {
    render(<ChatView conversationId="c1" initial={[
      { role: "user", content: "带附件的历史消息",
        attachments: [{ id: "a1", filename: "past.pdf", size: 100, content_type: "application/pdf" }] },
    ]} />);
    expect(screen.getByText("past.pdf")).toBeTruthy();
  });
});
