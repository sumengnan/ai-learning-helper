import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import HomeView from "./HomeView";
import { statsApi, type StatsOverview } from "../api/stats";
import { profileApi } from "../api/profile";

vi.mock("../api/stats", () => ({ statsApi: {
  overview: vi.fn(), memory: vi.fn(), deleteMemory: vi.fn(),
  deleteMemories: vi.fn(), consolidateMemory: vi.fn(),
} }));
vi.mock("../api/profile", () => ({
  profileApi: { get: vi.fn().mockResolvedValue({ identity: "", goal: "", explain_prefs: [], tone: "", notes: "" }) },
  isProfileSet: () => false,
}));

const OV: StatsOverview = {
  range_days: 14,
  learn: {
    assets: { documents: 4, memory: 52, questions: 0, wrong_answers: 0 },
    conversations: 7, messages: 44,
    recent_downloads: [{ id: "dl1", filename: "复习提纲.md", content_type: "text/markdown", size: 128, created_at: "2026-07-11T07:00:00+00:00" }],
    last_conversation: { id: "c1", title: "二叉树遍历", updated_at: "2026-07-11T10:00:00+00:00", message_count: 12 },
    abilities: [{ icon: "🌐", label: "联网查资料", count: 82 }, { icon: "💻", label: "运行代码", count: 53 }],
    effort: { runs: 94, total_tokens: 1140386, avg_steps: 3, max_steps: 12, success_rate: 0.93 },
    activity: Array.from({ length: 14 }, (_, i) => ({ date: `2026-07-${i + 1}`, runs: i, tokens: i * 100 })),
  },
  ops: {
    totals: {
      runs: 94, runs_finished: 87, runs_error: 7, success_rate: 0.93, model_calls: 235,
      total_tokens: 1140386, total_prompt: 900000, total_completion: 240386,
      avg_latency_ms: 7265, p95_latency_ms: 14200, retries: 0, cost_usd: null, cost_currency: "¥",
      conversations: 7, messages: 44,
    },
    daily: Array.from({ length: 14 }, (_, i) => ({ date: `2026-07-${i + 1}`, runs: i, tokens: i * 100 })),
    tools: [
      { name: "http_request", count: 71, errors: 0, success_rate: 1.0 },
      { name: "run_shell", count: 43, errors: 2, success_rate: 0.953 },
    ],
    steps_histogram: [
      { bucket: "1", count: 9 }, { bucket: "2", count: 22 }, { bucket: "3", count: 26 },
      { bucket: "4", count: 14 }, { bucket: "5-6", count: 9 }, { bucket: "7+", count: 7 },
    ],
    // 全空：两个门默认关闭，这才是默认配置下的常态
    gate: {
      turns: 0, retries: 0, avg_retries: 0, degraded: 0, degraded_rate: 0,
      first_pass_rate: 0, gate_errors: 0, layer_failures: [],
    },
    quality: {
      scored_turns: 0, avg_final: null, avg_plan: null, avg_steps: null,
      distribution: [
        { bucket: "0-59", count: 0 }, { bucket: "60-79", count: 0 },
        { bucket: "80-89", count: 0 }, { bucket: "90-100", count: 0 },
      ],
    },
    context: {
      turns: 0, layered_turns: 0, evicted_total: 0, amnesia_turns: 0,
      summary_errors: 0, retrieval_errors: 0, summary_ok: 0,
    },
  },
};

const renderHome = () => render(<MemoryRouter><HomeView /></MemoryRouter>);

beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear();
  // resetAllMocks 会清掉工厂里设的实现，重新给个性化接口一个默认返回
  (profileApi.get as any).mockResolvedValue(
    { identity: "", goal: "", explain_prefs: [], tone: "", notes: "" });
});
afterEach(() => cleanup());

describe("HomeView", () => {
  it("默认渲染学习主场：继续上次 + 资产计数 + 能力条", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderHome();
    await waitFor(() => expect(screen.getByText("二叉树遍历")).toBeTruthy());
    expect(screen.getByText("52")).toBeTruthy();          // AI 记的偏好
    expect(screen.getByText("联网查资料")).toBeTruthy();   // 能力（产品话术）
    expect(screen.getByText("114.0 万")).toBeTruthy();     // token 格式化
    // 工程黑话不出现在学习主场（默认落在「概览」视图，运维指标在「AI 运行统计」切换项下）
    expect(screen.queryByText("P95 延迟")).toBeNull();
    expect(screen.queryByRole("button", { name: "工程台" })).toBeNull();  // 切换页签已移除
  });

  it("打开偏好抽屉按总数请求，删除一条后卡片数字同步递减", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    (statsApi.memory as any).mockResolvedValue([
      { id: "m1", text: "偏好甲", collection: "conversation:c1", mem_type: "semantic", created_at: "2026-07-18T00:00:00+00:00" },
      { id: "m2", text: "偏好乙", collection: "conversation:c1", mem_type: "semantic", created_at: "2026-07-18T00:00:00+00:00" },
    ]);
    (statsApi.deleteMemory as any).mockResolvedValue(undefined);
    renderHome();
    await waitFor(() => expect(screen.getByText("52")).toBeTruthy());   // 初始总数（overview 口径）

    fireEvent.click(screen.getByText("AI 记的偏好"));                    // 点卡片开抽屉
    await screen.findByText("偏好甲");
    expect(statsApi.memory).toHaveBeenCalledWith(52);                   // 按总数请求 → 展示全部

    fireEvent.click(screen.getAllByLabelText("删除这条记忆")[0]);        // 删一条
    await waitFor(() => expect(statsApi.deleteMemory).toHaveBeenCalledWith("m1"));
    await waitFor(() => expect(screen.getByText("51")).toBeTruthy());   // 卡片 52 → 51
    expect(screen.queryByText("52")).toBeNull();
  });

  it("提供「概览」「AI 运行统计」切换按钮，点后者切到运维指标", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderHome();
    await waitFor(() => expect(screen.getByText("二叉树遍历")).toBeTruthy());
    // 默认概览视图不出现运维黑话
    expect(screen.queryByText("P95 延迟")).toBeNull();
    expect(screen.getAllByText("概览").length).toBe(2);           // 标题 + 切换按钮
    fireEvent.click(screen.getByRole("button", { name: "AI 运行统计" }));
    await waitFor(() => expect(screen.getByText("P95 延迟")).toBeTruthy());
    expect(screen.getByText("AI 运行统计(全局)")).toBeTruthy();     // 切换后标题带「(全局)」
  });

  it("无对话时显示开始对话引导", async () => {
    (statsApi.overview as any).mockResolvedValue({
      ...OV, learn: { ...OV.learn, last_conversation: null },
    });
    renderHome();
    await waitFor(() => expect(screen.getByText("还没有对话")).toBeTruthy());
  });

  it("加载失败显示错误提示", async () => {
    (statsApi.overview as any).mockRejectedValue(new Error("boom"));
    renderHome();
    await waitFor(() => expect(screen.getByText(/概览加载失败/)).toBeTruthy());
  });

  it("默认按近 3 天拉取，切换时间范围会重新拉取", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderHome();
    await waitFor(() => expect(screen.getByText("二叉树遍历")).toBeTruthy());
    expect(statsApi.overview).toHaveBeenCalledWith(3);       // 默认近 3 天
    fireEvent.mouseDown(screen.getByLabelText("时间范围"));    // 打开 Select
    fireEvent.click(await screen.findByRole("option", { name: "近 30 天" }));
    await waitFor(() => expect(statsApi.overview).toHaveBeenCalledWith(30));
  });

  it("点击「AI 记的偏好」打开抽屉并加载记忆", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    (statsApi.memory as any).mockResolvedValue([
      { id: "mem1", text: "用户偏好用中文", collection: "semantic", created_at: "2026-07-11T01:00:00+00:00" },
    ]);
    renderHome();
    await waitFor(() => expect(screen.getByText("二叉树遍历")).toBeTruthy());
    fireEvent.click(screen.getByText("AI 记的偏好"));
    await waitFor(() => expect(screen.getByText("AI 记住的偏好")).toBeTruthy());  // 抽屉标题
    expect(await screen.findByText("用户偏好用中文")).toBeTruthy();
    expect(statsApi.memory).toHaveBeenCalled();
  });

  it("记忆抽屉可删除单条", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    (statsApi.memory as any).mockResolvedValue([
      { id: "mem1", text: "用户偏好用中文", collection: "semantic", created_at: "2026-07-11T01:00:00+00:00" },
    ]);
    (statsApi.deleteMemory as any).mockResolvedValue(undefined);
    renderHome();
    await waitFor(() => expect(screen.getByText("二叉树遍历")).toBeTruthy());
    fireEvent.click(screen.getByText("AI 记的偏好"));
    expect(await screen.findByText("用户偏好用中文")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("删除这条记忆"));
    await waitFor(() => expect(statsApi.deleteMemory).toHaveBeenCalledWith("mem1"));
    await waitFor(() => expect(screen.queryByText("用户偏好用中文")).toBeNull());  // 已从列表移除
  });

  it("最近产物提供预览与下载入口", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderHome();
    await waitFor(() => expect(screen.getByText("复习提纲.md")).toBeTruthy());
    expect(screen.getByLabelText("预览 复习提纲.md")).toBeTruthy();  // md 可预览
    expect(screen.getByLabelText("下载 复习提纲.md")).toBeTruthy();
  });

  it("没有产物时显示空提示", async () => {
    (statsApi.overview as any).mockResolvedValue({
      ...OV, learn: { ...OV.learn, recent_downloads: [] },
    });
    renderHome();
    // 文案随时间范围变（如「今日/近 3 天…还没有产物生成」），断言范围无关的子串
    await waitFor(() => expect(screen.getByText(/还没有产物生成/)).toBeTruthy());
  });
});
