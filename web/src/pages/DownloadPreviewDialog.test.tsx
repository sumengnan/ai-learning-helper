import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { DownloadPreviewDialog } from "./DownloadPreviewDialog";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { downloads: { blob: vi.fn() } },
}));

function blobOf(text: string) {
  return { text: async () => text } as unknown as Blob;
}

beforeEach(() => {
  vi.resetAllMocks();
  (URL as any).createObjectURL = vi.fn(() => "blob:mock");
  (URL as any).revokeObjectURL = vi.fn();
});
afterEach(() => cleanup());

describe("DownloadPreviewDialog", () => {
  it("md 文件按 Markdown 渲染：# 标题变成 <h1>，而不是原样文字", async () => {
    (api.downloads.blob as any).mockResolvedValue(blobOf("# 大标题\n\n正文一段"));
    render(<DownloadPreviewDialog
      file={{ id: "1", filename: "笔记.md", content_type: "text/markdown" }}
      onClose={() => {}} />);
    await waitFor(() => {
      const h = screen.getByText("大标题");
      expect(h.tagName).toBe("H1");            // 真渲染了，不是 <pre> 里的纯文本
    });
    expect(screen.getByText("正文一段")).toBeTruthy();
  });

  it("content_type 丢了类型，也靠 .md 后缀走 Markdown 渲染", async () => {
    (api.downloads.blob as any).mockResolvedValue(blobOf("## 小标题"));
    render(<DownloadPreviewDialog
      file={{ id: "2", filename: "x.md", content_type: "application/octet-stream" }}
      onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("小标题").tagName).toBe("H2"));
  });

  it("非 md 文本仍走纯文本 <pre>：# 不被当成标题", async () => {
    (api.downloads.blob as any).mockResolvedValue(blobOf("# 这是普通文本里的井号"));
    render(<DownloadPreviewDialog
      file={{ id: "3", filename: "log.txt", content_type: "text/plain" }}
      onClose={() => {}} />);
    await waitFor(() => {
      const el = screen.getByText("# 这是普通文本里的井号");   // 井号原样保留
      expect(el.tagName).toBe("PRE");
    });
  });
});
