import { render, screen, waitFor, fireEvent, cleanup, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { MemoryRouter, Routes, Route, useSearchParams } from "react-router-dom";
import DownloadsView from "./DownloadsView";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { downloads: { list: vi.fn(), remove: vi.fn(), blob: vi.fn() } },
}));

const md = { id: "1", filename: "笔记.md", size: 12, content_type: "text/markdown", created_at: "2026-07-08", conv_id: null };
const png = { id: "2", filename: "图.png", size: 2048, content_type: "image/png", created_at: "2026-07-10", conv_id: "c9" };

function ConvMarker() {
  const [sp] = useSearchParams();
  return <div>conv:{sp.get("conv")}</div>;
}

function renderView() {
  return render(
    <MemoryRouter initialEntries={["/downloads"]}>
      <Routes>
        <Route path="/downloads" element={<DownloadsView />} />
        <Route path="/" element={<ConvMarker />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();  // 同时清空 mockResolvedValueOnce 队列，保证用例相互独立
  (URL as any).createObjectURL = vi.fn(() => "blob:mock");
  (URL as any).revokeObjectURL = vi.fn();
});
afterEach(() => cleanup());

describe("DownloadsView", () => {
  it("渲染卡片：副标题 + 类型 Chip + 人类可读大小 + 时间倒排", async () => {
    (api.downloads.list as any).mockResolvedValue([md, png]);
    renderView();
    await waitFor(() => expect(screen.getByText("笔记.md")).toBeTruthy());
    expect(screen.getByText(/共 2 个文件/)).toBeTruthy();
    expect(screen.getByText("文本")).toBeTruthy();
    expect(screen.getByText("图片")).toBeTruthy();
    expect(screen.getByText("2.0 KB")).toBeTruthy();  // 2048 → 2.0 KB
    // 时间倒排：图.png(07-10) 在 笔记.md(07-08) 之前
    const a = screen.getByText("图.png");
    const b = screen.getByText("笔记.md");
    expect(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("有来源会话的文件显示跳转按钮，点击跳到 /?conv=", async () => {
    (api.downloads.list as any).mockResolvedValue([md, png]);
    renderView();
    await waitFor(() => expect(screen.getByText("图.png")).toBeTruthy());
    const jumps = screen.getAllByLabelText("跳转到聊天");
    expect(jumps).toHaveLength(1);  // 仅 png 有 conv_id
    fireEvent.click(jumps[0]);
    await waitFor(() => expect(screen.getByText("conv:c9")).toBeTruthy());
  });

  it("单个删除弹确认框，确认后调用 remove", async () => {
    (api.downloads.list as any).mockResolvedValueOnce([md]).mockResolvedValueOnce([]);
    (api.downloads.remove as any).mockResolvedValue(undefined);
    renderView();
    await waitFor(() => expect(screen.getByText("笔记.md")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("删除文件"));
    expect(await screen.findByText("删除确认")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(api.downloads.remove).toHaveBeenCalledWith("1"));
  });

  it("批量删除：选中两项后删除", async () => {
    (api.downloads.list as any).mockResolvedValueOnce([md, png]).mockResolvedValueOnce([]);
    (api.downloads.remove as any).mockResolvedValue(undefined);
    renderView();
    await waitFor(() => expect(screen.getByText("笔记.md")).toBeTruthy());
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 笔记.md" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 图.png" }));
    fireEvent.click(await screen.findByRole("button", { name: /删除选中/ }));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(api.downloads.remove).toHaveBeenCalledTimes(2));
  });

  it("点击预览打开弹窗并加载图片", async () => {
    (api.downloads.list as any).mockResolvedValue([png]);
    (api.downloads.blob as any).mockResolvedValue(new Blob(["x"], { type: "image/png" }));
    renderView();
    await waitFor(() => expect(screen.getByText("图.png")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("预览文件"));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByRole("img", { name: "图.png" })).toBeTruthy());
    expect(api.downloads.blob).toHaveBeenCalledWith("2");
  });
});
