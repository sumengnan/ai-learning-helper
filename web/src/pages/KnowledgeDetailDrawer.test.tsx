// web/src/pages/KnowledgeDetailDrawer.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { KnowledgeDetailDrawer } from "./KnowledgeDetailDrawer";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { documents: { get: vi.fn() } },
}));

beforeEach(() => vi.clearAllMocks());
afterEach(() => cleanup());

describe("KnowledgeDetailDrawer", () => {
  it("展示片段完整正文 + 来源/分类/日期", async () => {
    (api.documents.get as any).mockResolvedValue({
      id: "1", filename: "bio.txt", category: "文本",
      uploaded_at: "2026-07-11T00:00:00Z", doc_id: "d1",
      text: "光合作用是植物利用光能的完整过程……全文很长。",
    });
    render(<KnowledgeDetailDrawer id="1" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/光合作用是植物利用光能的完整过程/)).toBeTruthy());
    expect(api.documents.get).toHaveBeenCalledWith("1");
    expect(screen.getByText("bio.txt")).toBeTruthy();
    expect(screen.getByText("文本")).toBeTruthy();
    expect(screen.getByText("片段详情")).toBeTruthy();
  });

  it("Markdown 片段按 md 渲染，不显示原码", async () => {
    (api.documents.get as any).mockResolvedValue({
      id: "2", filename: "note.md", category: "Markdown",
      uploaded_at: "2026-07-11T00:00:00Z", doc_id: "d2",
      text: "# 光合作用\n\n**要点**：叶绿体吸收光能。",
    });
    render(<KnowledgeDetailDrawer id="2" onClose={() => {}} />);
    // 标题渲染成 <h1>，加粗渲染成 <strong> —— 而不是原样显示 # 和 **
    await waitFor(() => expect(screen.getByRole("heading", { name: "光合作用" })).toBeTruthy());
    expect(screen.getByText("要点").tagName).toBe("STRONG");
    expect(screen.queryByText(/# 光合作用/)).toBeNull();
    expect(screen.queryByText(/\*\*要点\*\*/)).toBeNull();
  });

  it("加载失败显示错误", async () => {
    (api.documents.get as any).mockRejectedValue(new Error("片段不存在"));
    render(<KnowledgeDetailDrawer id="nope" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/片段不存在/)).toBeTruthy());
  });

  it("id 为 null 时不请求详情", () => {
    render(<KnowledgeDetailDrawer id={null} onClose={() => {}} />);
    expect(api.documents.get).not.toHaveBeenCalled();
  });
});
