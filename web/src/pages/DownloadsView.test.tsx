import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import DownloadsView from "./DownloadsView";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { downloads: { list: vi.fn(), remove: vi.fn() } },
}));

describe("DownloadsView", () => {
  beforeEach(() => vi.clearAllMocks());

  it("渲染下载列表", async () => {
    (api.downloads.list as any).mockResolvedValue([
      { id: "1", filename: "笔记.md", size: 12, content_type: "text/markdown", created_at: "2026-07-08" },
    ]);
    render(<DownloadsView />);
    await waitFor(() => expect(screen.getByText(/笔记\.md/)).toBeTruthy());
  });
});
