// 「AI 运行统计」页签（原系统监控）：通过 /monitor 路径渲染 HomeView，落在 ops 页签。
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import HomeView from "./HomeView";
import { statsApi, type StatsOverview } from "../api/stats";

vi.mock("../api/stats", () => ({ statsApi: { overview: vi.fn(), memory: vi.fn() } }));

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
