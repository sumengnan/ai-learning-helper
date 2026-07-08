// web/src/pages/KnowledgeView.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { KnowledgeView } from "./KnowledgeView";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { documents: { list: vi.fn(), upload: vi.fn(), remove: vi.fn() } },
}));

describe("KnowledgeView", () => {
  it("渲染文档列表", async () => {
    (api.documents.list as any).mockResolvedValue([
      { id: "1", filename: "bio.txt", num_chunks: 3, uploaded_at: "" },
    ]);
    render(<KnowledgeView />);
    await waitFor(() => expect(screen.getByText(/bio\.txt/)).toBeTruthy());
    expect(screen.getByText(/3 块/)).toBeTruthy();
  });
});
