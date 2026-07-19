import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { MemoryDrawer } from "./MemoryDrawer";
import { statsApi } from "../api/stats";

vi.mock("../api/stats", () => ({
  statsApi: {
    memory: vi.fn(), deleteMemory: vi.fn(),
    deleteMemories: vi.fn(), consolidateMemory: vi.fn(),
  },
}));

const ITEMS = [
  { id: "m1", text: "偏好甲", collection: "conversation:c1", mem_type: "semantic", created_at: "2026-07-18T00:00:00+00:00" },
  { id: "m2", text: "偏好乙", collection: "conversation:c1", mem_type: "episodic", created_at: "2026-07-18T00:00:00+00:00" },
];

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("MemoryDrawer", () => {
  it("按 total 请求全部，并给每条展示类型标签（语义/情景）", async () => {
    (statsApi.memory as ReturnType<typeof vi.fn>).mockResolvedValue(ITEMS);
    render(<MemoryDrawer open total={157} onClose={() => {}} />);
    await waitFor(() => expect(statsApi.memory).toHaveBeenCalledWith(157));
    expect(await screen.findByText("偏好甲")).toBeTruthy();
    expect(screen.getAllByText("语义").length).toBeGreaterThanOrEqual(1);   // 卡片类型标签
    expect(screen.getAllByText("情景").length).toBeGreaterThanOrEqual(1);
  });

  it("未提供 total 时回退到较大默认值（不再写死 50）", async () => {
    (statsApi.memory as ReturnType<typeof vi.fn>).mockResolvedValue(ITEMS);
    render(<MemoryDrawer open onClose={() => {}} />);
    await waitFor(() => expect(statsApi.memory).toHaveBeenCalledWith(200));
  });

  it("类型筛选：点「语义」只保留 semantic 条目", async () => {
    (statsApi.memory as ReturnType<typeof vi.fn>).mockResolvedValue(ITEMS);
    render(<MemoryDrawer open total={2} onClose={() => {}} />);
    await screen.findByText("偏好甲");
    fireEvent.click(screen.getByText(/^语义\s+1$/));       // 筛选按钮「语义 1」
    expect(screen.getByText("偏好甲")).toBeTruthy();
    expect(screen.queryByText("偏好乙")).toBeNull();        // episodic 被筛掉
  });

  it("单条删除回调 onCountDelta(-1) 并移除该条", async () => {
    (statsApi.memory as ReturnType<typeof vi.fn>).mockResolvedValue(ITEMS);
    (statsApi.deleteMemory as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    const onCountDelta = vi.fn();
    render(<MemoryDrawer open total={2} onClose={() => {}} onCountDelta={onCountDelta} />);
    await screen.findByText("偏好甲");
    fireEvent.click(screen.getAllByLabelText("删除这条记忆")[0]);
    await waitFor(() => expect(statsApi.deleteMemory).toHaveBeenCalledWith("m1"));
    await waitFor(() => expect(onCountDelta).toHaveBeenCalledWith(-1));
    expect(screen.queryByText("偏好甲")).toBeNull();
  });

  it("多选批量删除回调 onCountDelta(-删除数)", async () => {
    (statsApi.memory as ReturnType<typeof vi.fn>).mockResolvedValue(ITEMS);
    (statsApi.deleteMemories as ReturnType<typeof vi.fn>).mockResolvedValue({ deleted: ["m1", "m2"] });
    const onCountDelta = vi.fn();
    render(<MemoryDrawer open total={2} onClose={() => {}} onCountDelta={onCountDelta} />);
    await screen.findByText("偏好甲");
    fireEvent.click(screen.getByRole("button", { name: "多选" }));
    const boxes = screen.getAllByLabelText("选择这条记忆");
    fireEvent.click(boxes[0]); fireEvent.click(boxes[1]);
    fireEvent.click(screen.getByRole("button", { name: /删除选中/ }));
    await waitFor(() => expect(statsApi.deleteMemories).toHaveBeenCalledWith(["m1", "m2"]));
    await waitFor(() => expect(onCountDelta).toHaveBeenCalledWith(-2));
  });

  it("整理相似偏好：调 consolidateMemory 并回传净减少量", async () => {
    (statsApi.memory as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(ITEMS)          // 首次加载
      .mockResolvedValueOnce([ITEMS[0]]);    // 整理后重新加载
    (statsApi.consolidateMemory as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ clusters: 1, merged: 2, created: 1 });
    const onCountDelta = vi.fn();
    render(<MemoryDrawer open total={2} onClose={() => {}} onCountDelta={onCountDelta} />);
    await screen.findByText("偏好甲");
    fireEvent.click(screen.getByRole("button", { name: /整理相似偏好/ }));
    await waitFor(() => expect(statsApi.consolidateMemory).toHaveBeenCalled());
    await waitFor(() => expect(onCountDelta).toHaveBeenCalledWith(-1));   // merged2 - created1 = 净减 1
  });

  it("删除失败不回调 onCountDelta，条目保留", async () => {
    (statsApi.memory as ReturnType<typeof vi.fn>).mockResolvedValue(ITEMS);
    (statsApi.deleteMemory as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("删除失败：500"));
    const onCountDelta = vi.fn();
    render(<MemoryDrawer open total={2} onClose={() => {}} onCountDelta={onCountDelta} />);
    await screen.findByText("偏好甲");
    fireEvent.click(screen.getAllByLabelText("删除这条记忆")[0]);
    await waitFor(() => expect(statsApi.deleteMemory).toHaveBeenCalledWith("m1"));
    await screen.findByText("删除失败：500");
    expect(onCountDelta).not.toHaveBeenCalled();
    expect(screen.getByText("偏好甲")).toBeTruthy();
  });
});
