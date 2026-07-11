// web/src/pages/KnowledgeDetailView.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { KnowledgeDetailView } from "./KnowledgeDetailView";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { documents: { get: vi.fn() } },
}));

function renderDetail(id: string) {
  return render(
    <MemoryRouter initialEntries={[`/knowledge/${id}`]}>
      <Routes>
        <Route path="/knowledge/:id" element={<KnowledgeDetailView />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => cleanup());

describe("KnowledgeDetailView", () => {
  it("展示片段完整正文 + 来源/分类/日期", async () => {
    (api.documents.get as any).mockResolvedValue({
      id: "1", filename: "bio.txt", category: "文本",
      uploaded_at: "2026-07-11T00:00:00Z", doc_id: "d1",
      text: "光合作用是植物利用光能的完整过程……全文很长。",
    });
    renderDetail("1");
    await waitFor(() => expect(screen.getByText(/光合作用是植物利用光能的完整过程/)).toBeTruthy());
    expect(api.documents.get).toHaveBeenCalledWith("1");
    expect(screen.getByText("bio.txt")).toBeTruthy();
    expect(screen.getByText("文本")).toBeTruthy();
    expect(screen.getByText("片段详情")).toBeTruthy();
  });

  it("加载失败显示错误", async () => {
    (api.documents.get as any).mockRejectedValue(new Error("片段不存在"));
    renderDetail("nope");
    await waitFor(() => expect(screen.getByText(/片段不存在/)).toBeTruthy());
  });
});
