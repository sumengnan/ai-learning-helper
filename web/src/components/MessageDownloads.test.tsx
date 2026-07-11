import { render, screen, waitFor, fireEvent, cleanup, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { MessageDownloads } from "./MessageDownloads";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { downloads: { blob: vi.fn() } },
}));

const png = { id: "9", filename: "chart.png", content_type: "image/png", size: 2048 };
const bin = { id: "8", filename: "data.bin", content_type: "application/octet-stream", size: 10 };

beforeEach(() => {
  vi.resetAllMocks();
  (URL as any).createObjectURL = vi.fn(() => "blob:mock");
  (URL as any).revokeObjectURL = vi.fn();
});
afterEach(() => cleanup());

describe("MessageDownloads", () => {
  it("渲染生成文件：文件名 + 类型 + 大小 + 预览/下载按钮", () => {
    render(<MessageDownloads items={[png]} />);
    expect(screen.getByText("chart.png")).toBeTruthy();
    expect(screen.getByText(/图片 · 2\.0 KB/)).toBeTruthy();
    expect(screen.getByLabelText("预览 chart.png")).toBeTruthy();
    expect(screen.getByLabelText("下载 chart.png")).toBeTruthy();
  });

  it("不可预览类型不显示预览按钮，仍可下载", () => {
    render(<MessageDownloads items={[bin]} />);
    expect(screen.queryByLabelText("预览 data.bin")).toBeNull();
    expect(screen.getByLabelText("下载 data.bin")).toBeTruthy();
  });

  it("点击预览打开弹窗并加载图片", async () => {
    (api.downloads.blob as any).mockResolvedValue(new Blob(["x"], { type: "image/png" }));
    render(<MessageDownloads items={[png]} />);
    fireEvent.click(screen.getByLabelText("预览 chart.png"));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByRole("img", { name: "chart.png" })).toBeTruthy());
    expect(api.downloads.blob).toHaveBeenCalledWith("9");
  });
});
