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
    (api.questions.remove as any).mockResolvedValue({ deleted: true, related_wrong: 0 });
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

  it("无对应错题时直接删除，不弹确认框", async () => {
    (api.questions.remove as any).mockResolvedValue({ deleted: true, related_wrong: 0 });
    render(<MemoryRouter><QuestionBankView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    fireEvent.click(screen.getByLabelText("删除题目"));
    await waitFor(() => expect(api.questions.remove).toHaveBeenCalledWith("1"));
    expect(screen.queryByText("删除题目并清理错题？")).toBeNull();
  });

  it("有对应错题时弹窗确认，确认后带 force 连带删除", async () => {
    (api.questions.remove as any).mockResolvedValueOnce({ deleted: false, related_wrong: 3 });
    render(<MemoryRouter><QuestionBankView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    fireEvent.click(screen.getByLabelText("删除题目"));
    // 弹出确认框，提示对应错题数
    await waitFor(() => expect(screen.getByText("删除题目并清理错题？")).toBeTruthy());
    expect(screen.getByText(/3/)).toBeTruthy();
    // 确认 → 以 force 再次调用
    (api.questions.remove as any).mockResolvedValueOnce({ deleted: true, related_wrong: 3 });
    fireEvent.click(screen.getByText("删除题目和错题"));
    await waitFor(() => expect(api.questions.remove).toHaveBeenCalledWith("1", true));
  });
});
