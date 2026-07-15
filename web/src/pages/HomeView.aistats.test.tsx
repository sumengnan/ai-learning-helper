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
    assets: { documents: 4, memory: 52, questions: 0, wrong_answers: 0 },
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
    expect(screen.getByText("20 轮经过交付门 · 共重答 4 次")).toBeTruthy();
    expect(screen.getByText("降级交付")).toBeTruthy();
    // 失败层用中文标签展示（后端翻好再传，前端不另抄一份映射）
    expect(screen.getByText("质量评分")).toBeTruthy();
    expect(screen.getByText("知识库依据")).toBeTruthy();
  });

  it("两个门都没开时给出可操作的空态提示，而不是空白或 0 分", async () => {
    (statsApi.overview as any).mockResolvedValue({
      ...OV,
      ops: {
        ...OV.ops,
        gate: { ...OV.ops.gate, turns: 0, layer_failures: [] },
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
    await waitFor(() => expect(screen.getByText(/还没有质量评分记录/)).toBeTruthy());
    // 默认配置下这块本就是空的，必须说清怎么开，否则会被当成 bug
    expect(screen.getByText("HARNESS_ENABLE_TRAJECTORY_JUDGE")).toBeTruthy();
    expect(screen.queryByText("平均质量分")).toBeNull();
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
});
