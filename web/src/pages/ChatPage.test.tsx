import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/client", () => ({
  api: {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue({ id: "new1" }),
    messages: vi.fn().mockResolvedValue([]),
    rename: vi.fn().mockResolvedValue(undefined),
    remove: vi.fn().mockResolvedValue(undefined),
  },
  streamChat: vi.fn().mockResolvedValue(undefined),
}));

import { ChatPage } from "./ChatPage";
import { api, streamChat } from "../api/client";

describe("ChatPage 空态引导", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("无对话时展示标题与默认问题卡片", async () => {
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    expect(await screen.findByText("基于 Harness 架构的 AI 学习助手")).toBeTruthy();
    expect(screen.getByText("考考我 AI 知识")).toBeTruthy();
  });

  it("点击默认问题卡片：新建对话并自动发问", async () => {
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    fireEvent.click(await screen.findByText("考考我 AI 知识"));
    await waitFor(() => expect(api.create).toHaveBeenCalled());
    await waitFor(() => expect(streamChat).toHaveBeenCalled());
    const calls = vi.mocked(streamChat).mock.calls;
    const call = calls[calls.length - 1];
    expect(call[0]).toBe("new1");
    expect(call[1]).toBe("考考我 AI 知识");
  });
});
