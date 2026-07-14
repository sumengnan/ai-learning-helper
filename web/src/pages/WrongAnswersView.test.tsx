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
  snapshot: {
    type: "single", stem: "光合作用在哪?",
    options: ["线粒体", "叶绿体"], answer: 1, explanation: "叶绿体是光合场所",
  },
};

describe("WrongAnswersView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.wrong.list as any).mockResolvedValue([W]);
    (api.wrong.removeMany as any).mockResolvedValue(undefined);
  });

  it("卡片区分我的答案与正确答案", async () => {
    render(<MemoryRouter><WrongAnswersView /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/光合作用在哪/)).toBeTruthy());
    expect(screen.getByText(/我的答案/)).toBeTruthy();
    expect(screen.getByText(/正确答案/)).toBeTruthy();
    expect(screen.getByText(/线粒体/)).toBeTruthy();   // 我的（错误）作答按选项文本
    expect(screen.getByText(/叶绿体/)).toBeTruthy();   // 正确答案按选项文本
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
});
