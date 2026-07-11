// web/src/pages/KnowledgeView.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { KnowledgeView } from "./KnowledgeView";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { documents: { list: vi.fn(), search: vi.fn(), upload: vi.fn(), remove: vi.fn() } },
}));

// 一个片段（chunk）：filename 是来源文件名，excerpt 是片段正文
const mkFrag = (i: number) => ({
  id: String(i), filename: `doc${i}.txt`, uploaded_at: "2026-07-11T00:00:00Z",
  category: "文本", excerpt: `片段正文 ${i}`,
});

function renderView() {
  return render(
    <MemoryRouter initialEntries={["/knowledge"]}>
      <Routes>
        <Route path="/knowledge" element={<KnowledgeView />} />
        <Route path="/knowledge/:id" element={<div>片段详情页</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

describe("KnowledgeView", () => {
  it("渲染片段卡片：来源文件名 + 正文 + 副标题用片段总数", async () => {
    (api.documents.list as any).mockResolvedValue({ items: [mkFrag(1)], total: 42 });
    renderView();
    await waitFor(() => expect(screen.getByText(/doc1\.txt/)).toBeTruthy());
    expect(screen.getByText(/共 42 篇文档片段/)).toBeTruthy();  // total = 片段数
    expect(screen.getByText(/片段正文 1/)).toBeTruthy();
    expect(screen.getByText("文本")).toBeTruthy();
    // 默认列表态不显示相关度
    expect(screen.queryByText(/相关度/)).toBeNull();
  });

  it("点击卡片跳转到片段详情页", async () => {
    (api.documents.list as any).mockResolvedValue({ items: [mkFrag(1)], total: 1 });
    renderView();
    await waitFor(() => expect(screen.getByText(/片段正文 1/)).toBeTruthy());
    fireEvent.click(screen.getByText(/片段正文 1/));  // 点卡片内容 → 冒泡到 Card onClick
    await waitFor(() => expect(screen.getByText("片段详情页")).toBeTruthy());
  });

  it("分页：片段总数超过一页时显示分页控件", async () => {
    (api.documents.list as any).mockResolvedValue({
      items: Array.from({ length: 8 }, (_, i) => mkFrag(i + 1)), total: 20,
    });
    renderView();
    await waitFor(() => expect(screen.getByText(/doc1\.txt/)).toBeTruthy());
    // 20/8 = 3 页，出现第 3 页按钮
    expect(screen.getByRole("button", { name: /Go to page 3/i })).toBeTruthy();
  });

  it("搜索态显示相关度标签", async () => {
    (api.documents.list as any).mockResolvedValue({ items: [], total: 0 });
    (api.documents.search as any).mockResolvedValue([
      { id: "9", filename: "hit.txt", uploaded_at: "2026-07-11T00:00:00Z",
        category: "文本", excerpt: "命中片段", relevance: 87 },
    ]);
    renderView();
    const box = await screen.findByPlaceholderText("搜索文档…");
    fireEvent.change(box, { target: { value: "光合作用" } });
    await waitFor(() => expect(screen.getByText(/相关度 87%/)).toBeTruthy());
    expect(api.documents.search).toHaveBeenCalledWith("光合作用");
    expect(screen.getByText(/hit\.txt/)).toBeTruthy();
  });
});
