import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import QuestionBankView from "./QuestionBankView";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    questions: {
      list: vi.fn(),
      generate: vi.fn(),
      remove: vi.fn(),
      removeMany: vi.fn(),
    },
  },
}));

describe("QuestionBankView", () => {
  beforeEach(() => vi.clearAllMocks());

  it("渲染已有题目列表", async () => {
    (api.questions.list as any).mockResolvedValue([
      { id: "1", type: "single", stem: "光合作用在哪?", source: "生物" },
    ]);
    render(<MemoryRouter><QuestionBankView /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/光合作用在哪/)).toBeTruthy());
  });
});
