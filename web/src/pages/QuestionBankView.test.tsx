import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import QuestionBankView from "./QuestionBankView";
import { api } from "../api/client";

afterEach(() => cleanup());

vi.mock("../api/client", () => ({
  api: {
    questions: {
      list: vi.fn(),
      sources: vi.fn(),
      import: vi.fn(),
      remove: vi.fn(),
      removeMany: vi.fn(),
    },
  },
}));

const Q = {
  id: "1", type: "single", stem: "光合作用在哪?",
  options: ["线粒体", "叶绿体"], answer: 1, explanation: "叶绿体", source: "生物",
  created_at: "2026-07-13T00:00:00Z",
};

describe("QuestionBankView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.questions.list as any).mockResolvedValue({ items: [Q], total: 1 });
    (api.questions.sources as any).mockResolvedValue(["生物"]);
  });

  it("渲染题目并显示人性化答案", async () => {
    render(<MemoryRouter><QuestionBankView /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/光合作用在哪/)).toBeTruthy());
    expect(screen.getByText(/叶绿体/)).toBeTruthy();   // 答案按选项文本显示
  });

  it("点题目卡片打开详情弹框", async () => {
    render(<MemoryRouter><QuestionBankView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    fireEvent.click(screen.getByText(/光合作用在哪/));
    await waitFor(() => expect(screen.getByText("题目详情")).toBeTruthy());
  });
});
