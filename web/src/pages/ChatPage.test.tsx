import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/client", () => ({
  api: {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue({ id: "new1" }),
    messages: vi.fn().mockResolvedValue([]),
    rename: vi.fn().mockResolvedValue(undefined),
    autotitle: vi.fn().mockResolvedValue({ title: null }),
    remove: vi.fn().mockResolvedValue(undefined),
    models: vi.fn().mockResolvedValue({ main: "m", fast: "m", judge: "m", embedding: null, rerank: null }),
    exam: { status: vi.fn().mockResolvedValue({ active: false }) },
  },
  streamChat: vi.fn().mockResolvedValue(undefined),
}));

import { ChatPage } from "./ChatPage";
import { api, streamChat } from "../api/client";
import { SUGGESTIONS } from "../components/EmptyHint";

// 断言取自 SUGGESTIONS 本身，不写死文案：这些卡片的措辞是产品文案、会改，
// 写死会让「改文案」平白变成红灯（55643b4 即如此）。测试要盯的是「卡片渲染出来、
// 点了能带着那句话发问」这个行为，不是某句具体的话。
const FIRST = SUGGESTIONS[0].text;

describe("ChatPage 空态引导", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("无对话时展示标题与默认问题卡片", async () => {
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    expect(await screen.findByText("基于 Harness 架构的 AI 学习助手")).toBeTruthy();
    for (const s of SUGGESTIONS) expect(screen.getByText(s.text)).toBeTruthy();
  });

  it("已有未聊天的新对话时再点新对话：不重复创建并提示", async () => {
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    const newBtn = await screen.findByRole("button", { name: "新对话" });
    fireEvent.click(newBtn);
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1));
    fireEvent.click(newBtn);   // 空草稿仍在，再次点击不应再建
    expect(await screen.findByText("已经添加了新对话，可以开始聊天了")).toBeTruthy();
    expect(api.create).toHaveBeenCalledTimes(1);
  });

  it("点击默认问题卡片：新建对话并自动发问", async () => {
    render(<MemoryRouter><ChatPage /></MemoryRouter>);
    fireEvent.click(await screen.findByText(FIRST));
    await waitFor(() => expect(api.create).toHaveBeenCalled());
    await waitFor(() => expect(streamChat).toHaveBeenCalled());
    const calls = vi.mocked(streamChat).mock.calls;
    const call = calls[calls.length - 1];
    expect(call[0]).toBe("new1");
    expect(call[1]).toBe(FIRST);
  });
});
