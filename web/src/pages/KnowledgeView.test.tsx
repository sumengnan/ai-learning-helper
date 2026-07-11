// web/src/pages/KnowledgeView.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { KnowledgeView } from "./KnowledgeView";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { documents: { list: vi.fn(), search: vi.fn(), upload: vi.fn(), remove: vi.fn() } },
}));

const mkDoc = (i: number) => ({
  id: String(i), filename: `doc${i}.txt`, uploaded_at: "2026-07-11T00:00:00Z",
  category: "文本", excerpt: `摘要内容 ${i}`, num_chunks: 3, size: 100,
});

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

describe("KnowledgeView", () => {
  it("渲染卡片式文档列表与副标题", async () => {
    (api.documents.list as any).mockResolvedValue({
      items: [mkDoc(1)], total: 1, total_chunks: 42,
    });
    render(<KnowledgeView />);
    await waitFor(() => expect(screen.getByText(/doc1\.txt/)).toBeTruthy());
    expect(screen.getByText(/共 42 篇文档片段/)).toBeTruthy();
    expect(screen.getByText(/摘要内容 1/)).toBeTruthy();
    expect(screen.getByText("文本")).toBeTruthy();
    // 默认列表态不显示相关度
    expect(screen.queryByText(/相关度/)).toBeNull();
  });

  it("分页：total 超过一页时显示分页控件", async () => {
    (api.documents.list as any).mockResolvedValue({
      items: Array.from({ length: 8 }, (_, i) => mkDoc(i + 1)),
      total: 20, total_chunks: 60,
    });
    render(<KnowledgeView />);
    await waitFor(() => expect(screen.getByText(/doc1\.txt/)).toBeTruthy());
    // 20/8 = 3 页，出现第 2、3 页按钮
    expect(screen.getByRole("button", { name: /Go to page 3/i })).toBeTruthy();
  });

  it("搜索态显示相关度标签", async () => {
    (api.documents.list as any).mockResolvedValue({ items: [], total: 0, total_chunks: 0 });
    (api.documents.search as any).mockResolvedValue([
      { id: "9", filename: "hit.txt", uploaded_at: "2026-07-11T00:00:00Z",
        category: "文本", excerpt: "命中片段", relevance: 87 },
    ]);
    render(<KnowledgeView />);
    const box = await screen.findByPlaceholderText("搜索文档…");
    fireEvent.change(box, { target: { value: "光合作用" } });
    await waitFor(() => expect(screen.getByText(/相关度 87%/)).toBeTruthy());
    expect(api.documents.search).toHaveBeenCalledWith("光合作用");
    expect(screen.getByText(/hit\.txt/)).toBeTruthy();
  });
});
