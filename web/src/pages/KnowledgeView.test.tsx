// web/src/pages/KnowledgeView.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { KnowledgeView, SEARCH_DEBOUNCE_MS } from "./KnowledgeView";
import { api } from "../api/client";

// 搜索走 SEARCH_DEBOUNCE_MS(1s) 防抖，waitFor 默认超时 1s 会卡边界，统一给足余量
const SEARCH_WAIT = { timeout: SEARCH_DEBOUNCE_MS + 1500 };

vi.mock("../api/client", () => ({
  api: { documents: { list: vi.fn(), search: vi.fn(), upload: vi.fn(), remove: vi.fn(), get: vi.fn() } },
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

  it("点击卡片打开片段详情抽屉（不跳转页面）", async () => {
    (api.documents.list as any).mockResolvedValue({ items: [mkFrag(1)], total: 1 });
    (api.documents.get as any).mockResolvedValue({
      id: "1", filename: "doc1.txt", category: "文本",
      uploaded_at: "2026-07-11T00:00:00Z", doc_id: "d1",
      text: "片段完整正文内容。",
    });
    renderView();
    await waitFor(() => expect(screen.getByText(/片段正文 1/)).toBeTruthy());
    fireEvent.click(screen.getByText(/片段正文 1/));  // 点卡片内容 → 冒泡到 Card onClick
    await waitFor(() => expect(screen.getByText("片段详情")).toBeTruthy());
    expect(screen.getByText(/片段完整正文内容/)).toBeTruthy();
    expect(api.documents.get).toHaveBeenCalledWith("1");
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
    await waitFor(() => expect(screen.getByText(/相关度 87%/)).toBeTruthy(), SEARCH_WAIT);
    expect(api.documents.search).toHaveBeenCalledWith("光合作用");
    expect(screen.getByText(/hit\.txt/)).toBeTruthy();
  });

  it("无精排分（relevance_kind=rank）时标签改为『排名分』以区分绝对相关度", async () => {
    (api.documents.list as any).mockResolvedValue({ items: [], total: 0 });
    (api.documents.search as any).mockResolvedValue([
      { id: "9", filename: "hit.txt", uploaded_at: "2026-07-11T00:00:00Z",
        category: "文本", excerpt: "命中片段", relevance: 87, relevance_kind: "rank" },
    ]);
    renderView();
    const box = await screen.findByPlaceholderText("搜索文档…");
    fireEvent.change(box, { target: { value: "光合作用" } });
    await waitFor(() => expect(screen.getByText(/排名分 87%/)).toBeTruthy(), SEARCH_WAIT);
    expect(screen.queryByText(/相关度 87%/)).toBeNull();
  });

  it("防抖间隔就是 1 秒", () => {
    expect(SEARCH_DEBOUNCE_MS).toBe(1000);
  });

  it("输入后不立即搜索（有防抖，不是逐字搜）", async () => {
    (api.documents.list as any).mockResolvedValue({ items: [], total: 0 });
    (api.documents.search as any).mockResolvedValue([]);
    renderView();
    const box = await screen.findByPlaceholderText("搜索文档…");
    fireEvent.change(box, { target: { value: "光" } });
    // 输入的当下防抖窗口还没到，绝不能已经发出请求
    expect(api.documents.search).not.toHaveBeenCalled();
  });

  it("连续输入只在停下后搜一次，且用最后的值", async () => {
    (api.documents.list as any).mockResolvedValue({ items: [], total: 0 });
    (api.documents.search as any).mockResolvedValue([]);
    renderView();
    const box = await screen.findByPlaceholderText("搜索文档…");
    // 快速逐字输入：每个字都重置防抖，最终只该发一次、且是完整词
    fireEvent.change(box, { target: { value: "光" } });
    fireEvent.change(box, { target: { value: "光合" } });
    fireEvent.change(box, { target: { value: "光合作用" } });
    await waitFor(() => expect(api.documents.search).toHaveBeenCalledTimes(1), SEARCH_WAIT);
    expect(api.documents.search).toHaveBeenCalledWith("光合作用");
  });


  it("搜索转圈不影响导入按钮(F5:两者 busy 独立)", async () => {
    (api.documents.list as any).mockResolvedValue({ items: [], total: 0 });
    // search 永不 resolve → 停在搜索进行中
    (api.documents.search as any).mockReturnValue(new Promise(() => {}));
    renderView();
    const box = await screen.findByPlaceholderText("搜索文档…");
    fireEvent.change(box, { target: { value: "泛型" } });
    await waitFor(() => expect(api.documents.search).toHaveBeenCalled(), SEARCH_WAIT);
    // 搜索进行中:导入按钮不该被这个状态带着一起转圈/禁用
    expect(screen.getByText("导入文档")).toBeTruthy();
    expect(screen.queryByText("导入中…")).toBeNull();
  });

});
