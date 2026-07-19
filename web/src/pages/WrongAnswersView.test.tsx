import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import WrongAnswersView from "./WrongAnswersView";
import { api } from "../api/client";

afterEach(() => cleanup());

vi.mock("../api/client", () => ({
  api: {
    wrong: {
      list: vi.fn(),
      removeMany: vi.fn(),
    },
  },
}));

const W = {
  id: "w1",
  user_answer: 0,
  created_at: new Date(Date.now() - 3 * 86400 * 1000).toISOString(),   // 3 天前
  snapshot: {
    type: "single", stem: "光合作用在哪?",
    options: ["线粒体", "叶绿体"], answer: 1, explanation: "叶绿体是光合场所",
  },
};

describe("WrongAnswersView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.wrong.list as any).mockResolvedValue({ items: [W], total: 1 });
    (api.wrong.removeMany as any).mockResolvedValue(undefined);
  });

  it("卡片区分我的答案与正确答案", async () => {
    render(<MemoryRouter><WrongAnswersView /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/光合作用在哪/)).toBeTruthy());
    expect(screen.getByText(/我的答案/)).toBeTruthy();
    expect(screen.getByText(/正确答案/)).toBeTruthy();
    // 「我的答案：」在 span、字母在文本节点，跨元素——用 textContent 精确匹配
    const exact = (t: string) => (_: string, el: Element | null) => el?.textContent === t;
    expect(screen.getAllByText(exact("我的答案：A")).length).toBeGreaterThan(0);   // 我的作答用字母 A
    expect(screen.getAllByText(exact("正确答案：B")).length).toBeGreaterThan(0);   // 正确答案用字母 B
  });

  it("卡片显示入集时间（相对时间）", async () => {
    render(<MemoryRouter><WrongAnswersView /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("3 天前")).toBeTruthy());
  });

  it("详情弹框显示答错时间", async () => {
    render(<MemoryRouter><WrongAnswersView /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/光合作用在哪/)).toBeTruthy());
    fireEvent.click(screen.getByText(/光合作用在哪/));
    await waitFor(() => expect(screen.getByText("错题详情")).toBeTruthy());
    // 时间已按新鲜度上色，与「答错」拆成两个节点；「3 天前」列表+详情各一处，故用 getAllByText
    expect(screen.getAllByText("3 天前").length).toBeGreaterThan(0);
    expect(screen.getByText("答错")).toBeTruthy();
  });

  it("没有批量删除按钮", async () => {
    render(<MemoryRouter><WrongAnswersView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    expect(screen.queryByText(/批量删除/)).toBeNull();
  });

  it("点卡片打开详情弹框", async () => {
    render(<MemoryRouter><WrongAnswersView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    fireEvent.click(screen.getByText(/光合作用在哪/));
    await waitFor(() => expect(screen.getByText("错题详情")).toBeTruthy());
    // 详情内含解析
    expect(screen.getByText(/叶绿体是光合场所/)).toBeTruthy();
  });

  it("点删除按钮移除该错题", async () => {
    render(<MemoryRouter><WrongAnswersView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    fireEvent.click(screen.getByLabelText("删除错题"));
    await waitFor(() => expect(api.wrong.removeMany).toHaveBeenCalledWith(["w1"]));
  });

  it("输入题名后按关键词查询（防抖）", async () => {
    render(<MemoryRouter><WrongAnswersView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    fireEvent.change(screen.getByPlaceholderText("搜索题名…"), { target: { value: "光合" } });
    await waitFor(() => expect(api.wrong.list).toHaveBeenCalledWith(
      expect.objectContaining({ q: "光合", page: 1 })));
  });

  it("选题型后按题型筛选", async () => {
    render(<MemoryRouter><WrongAnswersView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(screen.getByRole("option", { name: "判断" }));
    await waitFor(() => expect(api.wrong.list).toHaveBeenCalledWith(
      expect.objectContaining({ type: "truefalse", page: 1 })));
  });

  it("筛选无结果时提示调整条件，而非「暂无错题」", async () => {
    render(<MemoryRouter><WrongAnswersView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    (api.wrong.list as any).mockResolvedValue({ items: [], total: 0 });
    fireEvent.change(screen.getByPlaceholderText("搜索题名…"), { target: { value: "不存在的题" } });
    await waitFor(() => expect(screen.getByText("未找到符合条件的错题")).toBeTruthy());
    expect(screen.queryByText("暂无错题")).toBeNull();
  });

  it("错题数超过一页时显示分页控件", async () => {
    (api.wrong.list as any).mockResolvedValue({ items: [W], total: 25 });
    render(<MemoryRouter><WrongAnswersView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    // 25/10 = 3 页
    expect(screen.getByRole("button", { name: /Go to page 3/i })).toBeTruthy();
  });
});
