// 「AI 运行统计」页签（原系统监控）：通过 /monitor 路径渲染 HomeView，落在 ops 页签。
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import HomeView from "./HomeView";
import { statsApi, type StatsOverview } from "../api/stats";

vi.mock("../api/stats", () => ({ statsApi: { overview: vi.fn(), memory: vi.fn() } }));
vi.mock("../api/profile", () => ({
  profileApi: { get: vi.fn().mockResolvedValue({ identity: "", goal: "", explain_prefs: [], tone: "", notes: "" }) },
  isProfileSet: () => false,
}));

const OV: StatsOverview = {
  range_days: 14,
  learn: {
    assets: { documents: 4, document_chunks: 4, memory: 52, questions: 0, wrong_answers: 0 },
    conversations: 7, messages: 44, recent_downloads: [], last_conversation: null,
    abilities: [], effort: { runs: 94, total_tokens: 1140386, avg_steps: 3, max_steps: 12, success_rate: 0.93 },
    activity: Array.from({ length: 14 }, (_, i) => ({ date: `2026-07-${i + 1}`, runs: i, tokens: i * 100 })),
  },
  ops: {
    totals: {
      runs: 94, runs_finished: 87, runs_error: 7, success_rate: 0.93, model_calls: 235,
      total_tokens: 1140386, total_prompt: 900000, total_completion: 240386,
      avg_latency_ms: 7265, p95_latency_ms: 14200, retries: 0, cost_usd: 12.34, cost_currency: "¥",
      conversations: 7, messages: 44,
    },
    daily: Array.from({ length: 14 }, (_, i) => ({ date: `2026-07-${i + 1}`, runs: i, tokens: i * 100 })),
    by_model: [],
    tools: [
      { name: "http_request", count: 71, errors: 0, success_rate: 1.0 },
      { name: "run_shell", count: 43, errors: 2, success_rate: 0.953 },
    ],
    steps_histogram: [
      { bucket: "1", count: 9 }, { bucket: "2", count: 22 }, { bucket: "3", count: 26 },
      { bucket: "4", count: 14 }, { bucket: "5-6", count: 9 }, { bucket: "7+", count: 7 },
    ],
    gate: {
      turns: 20, retries: 4, avg_retries: 0.2, degraded: 1, degraded_rate: 0.05,
      first_pass_rate: 0.85, gate_errors: 0,
      notice_turns: 2,
      notices: [{ kind: "code", zh: "代码可运行", count: 3 }],
      layer_failures: [
        { layer: "judge", zh: "质量评分", count: 2 },
        { layer: "grounding", zh: "知识库依据", count: 1 },
      ],
    },
    quality: {
      scored_turns: 12, avg_final: 78.4, avg_plan: 81, avg_steps: 74.2,
      distribution: [
        { bucket: "0-59", count: 2 }, { bucket: "60-79", count: 4 },
        { bucket: "80-89", count: 4 }, { bucket: "90-100", count: 2 },
      ],
    },
    context: {
      turns: 20, layered_turns: 15, evicted_total: 42, amnesia_turns: 0,
      summary_errors: 0, retrieval_errors: 0, summary_ok: 15,
    },
  },
};

const renderOps = () =>
  render(<MemoryRouter initialEntries={["/monitor"]}><HomeView /></MemoryRouter>);

beforeEach(() => { vi.resetAllMocks(); localStorage.clear(); });
afterEach(() => cleanup());

describe("HomeView · AI 运行统计页签", () => {
  it("/monitor 落在运维指标：成功率 / P95 / 工具名", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderOps();
    await waitFor(() => expect(screen.getByText("P95 延迟")).toBeTruthy());
    expect(screen.getAllByText("成功率").length).toBeGreaterThan(0);
    expect(screen.getByText("14.2s")).toBeTruthy();
    expect(screen.getByText("http_request")).toBeTruthy();
  });

  it("估算成本按货币符号显示（¥）", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderOps();
    await waitFor(() => expect(screen.getByText("¥12.34")).toBeTruthy());
  });

  it("成本未知时显示占位（货币符号 + —）", async () => {
    (statsApi.overview as any).mockResolvedValue({
      ...OV, ops: { ...OV.ops, totals: { ...OV.ops.totals, cost_usd: null } },
    });
    renderOps();
    await waitFor(() => expect(screen.getByText("¥ —")).toBeTruthy());
  });

  it("默认按近 3 天拉取，切换时间范围会重新拉取（页签共用选择器）", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderOps();
    await waitFor(() => expect(screen.getByText("P95 延迟")).toBeTruthy());
    expect(statsApi.overview).toHaveBeenCalledWith(3);
    fireEvent.mouseDown(screen.getByLabelText("时间范围"));
    fireEvent.click(await screen.findByRole("option", { name: "近 30 天" }));
    await waitFor(() => expect(statsApi.overview).toHaveBeenCalledWith(30));
  });
});

describe("HomeView · 回答质量", () => {
  it("有评分时展示质量分、拦截率与失败层", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderOps();
    await waitFor(() => expect(screen.getByText("平均质量分")).toBeTruthy());
    expect(screen.getByText("78.4")).toBeTruthy();
    expect(screen.getByText("一次过率")).toBeTruthy();
    expect(screen.getByText("85%")).toBeTruthy();                    // first_pass_rate 0.85
    expect(screen.getByText("20 轮经过结果校验 · 共重答 4 次")).toBeTruthy();
    expect(screen.getByText("降级交付")).toBeTruthy();
    // 失败层用中文标签展示（后端翻好再传，前端不另抄一份映射）
    expect(screen.getByText("质量评分")).toBeTruthy();
    expect(screen.getByText("知识库依据")).toBeTruthy();
  });

  it("没有数据时照常出表格、值显示「—」，不换成说明文案", async () => {
    (statsApi.overview as any).mockResolvedValue({
      ...OV,
      ops: {
        ...OV.ops,
        gate: { ...OV.ops.gate, turns: 0, retries: 0, degraded: 0, first_pass_rate: 0,
                layer_failures: [], notice_turns: 0, notices: [] },
        quality: {
          scored_turns: 0, avg_final: null, avg_plan: null, avg_steps: null,
          distribution: [
            { bucket: "0-59", count: 0 }, { bucket: "60-79", count: 0 },
            { bucket: "80-89", count: 0 }, { bucket: "90-100", count: 0 },
          ],
        },
      },
    });
    renderOps();
    // 表格照出：标题与格子都在，只是值为 —/0
    await waitFor(() => expect(screen.getByText("平均质量分")).toBeTruthy());
    expect(screen.getByText("质量分分布")).toBeTruthy();
    expect(screen.getByText("哪一层拦下的")).toBeTruthy();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getByText("0 轮已评")).toBeTruthy();
    // 不再用一段说明文案顶替表格
    expect(screen.queryByText(/还没有质量评分记录/)).toBeNull();
    expect(screen.queryByText(/没有被拦下的回答/)).toBeNull();
  });

  it("回答质量是全局口径，标注跟随右上角时间范围", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderOps();
    await waitFor(() => expect(screen.getByText("回答质量")).toBeTruthy());
    // 不再标「仅本账号」——整页都是全局口径
    expect(screen.queryByText("仅本账号")).toBeNull();
    expect(screen.getAllByText("近 3 天").length).toBeGreaterThan(0);   // 默认范围
  });

  it("校验器自身故障（fail-open）的轮数要显形——那些「通过」并非真校验过", async () => {
    (statsApi.overview as any).mockResolvedValue({
      ...OV,
      ops: { ...OV.ops, gate: { ...OV.ops.gate, gate_errors: 3 } },
    });
    renderOps();
    await waitFor(() =>
      expect(screen.getByText("另有 3 轮因校验器故障未真校验")).toBeTruthy());
  });

  // 上下文健康度：L2 摘要失败此前完全静默，这块就是让它显形的地方
  it("上下文失忆轮数要显形并标红——那些回答是丢了历史且模型不自知时给出的", async () => {
    (statsApi.overview as any).mockResolvedValue({
      ...OV,
      ops: { ...OV.ops, context: { ...OV.ops.context, amnesia_turns: 2,
                                   summary_errors: 3, summary_ok: 12 } },
    });
    renderOps();
    await waitFor(() => expect(screen.getByText("上下文健康度")).toBeTruthy());
    expect(screen.getByText("L2摘要失败")).toBeTruthy();
    expect(screen.getByText("这些轮丢了更早历史、AI 却不自知，需排查")).toBeTruthy();
    // L2 失败要能看出「成功/总数」，而非只报一个成功数
    expect(screen.getByText("12 / 15")).toBeTruthy();
    // 未接住的即「L2摘要失败」，hint 点名二者是同一批，免得看着像重复计数
    expect(screen.getByText("压缩接住的轮数：3 轮没接住（即「L2摘要失败」）")).toBeTruthy();
  });

  it("没失忆时不误报：还没移出历史 与 移出但都接住了 要分得开", async () => {
    // evicted=0：对话都没超窗，一条历史都没移出 —— 不是「失忆已覆盖」，而是根本没发生
    (statsApi.overview as any).mockResolvedValue({
      ...OV,
      ops: { ...OV.ops, context: { turns: 20, layered_turns: 20, evicted_total: 0,
                                   amnesia_turns: 0, summary_errors: 0,
                                   retrieval_errors: 0, summary_ok: 0 } },
    });
    renderOps();
    await waitFor(() =>
      expect(screen.getByText("挤出的历史压缩成摘要时失败的轮数")).toBeTruthy());
    expect(screen.queryByText("这些轮丢了更早历史、AI 却不自知，需排查")).toBeNull();
  });

  it("L1窗口挤出与 L3检索失败拆成两格——前者只是少了增益，不该冲淡失忆", async () => {
    (statsApi.overview as any).mockResolvedValue({
      ...OV,
      ops: { ...OV.ops, context: { ...OV.ops.context, retrieval_errors: 7,
                                   evicted_total: 42, amnesia_turns: 0 } },
    });
    renderOps();
    // 两格各自独立：L1窗口挤出(42) 与 L3检索失败(7) 不再拼在一格里
    await waitFor(() => expect(screen.getByText("L1窗口挤出")).toBeTruthy());
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getByText("L3检索失败")).toBeTruthy();
    expect(screen.getByText(
      "从更早历史捞相关片段失败的轮数（只是少层参考，非失忆）")).toBeTruthy();
    // L3 挂了 7 次，但没失忆 → 失忆格仍是 0，hint 说清是「都接住了」而非「没挤出」
    expect(screen.getByText("挤出的历史都被摘要接住了，未发生丢失")).toBeTruthy();
  });
});

describe("HomeView · 交付提醒", () => {
  it("展示提醒轮数与各项次数——它只提示，不该被读成失败", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderOps();
    await waitFor(() => expect(screen.getByText("交付提醒")).toBeTruthy());
    // 轮数与各项次数只出现在这块的 hint 里，比裸数字「2」更能唯一定位到这张卡
    expect(screen.getByText("2 轮触发交付后检查 · 代码可运行 3")).toBeTruthy();
  });

  it("没有提醒时副标题说清查了什么、只提示不判失败，免得空值被当成没跑", async () => {
    (statsApi.overview as any).mockResolvedValue({
      ...OV,
      ops: { ...OV.ops, gate: { ...OV.ops.gate, notice_turns: 0, notices: [] } },
    });
    renderOps();
    await waitFor(() =>
      expect(screen.getByText("交付后查完整性·检索依据·代码可运行·引用链接 · 只提示不判失败")).toBeTruthy());
  });
});
