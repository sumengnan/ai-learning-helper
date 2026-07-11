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
      avg_latency_ms: 7265, p95_latency_ms: 14200, retries: 0, cost_usd: null,
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

const renderHome = () => render(<MemoryRouter><HomeView /></MemoryRouter>);

beforeEach(() => vi.resetAllMocks());
afterEach(() => cleanup());

describe("HomeView", () => {
  it("默认渲染学习主场：继续上次 + 资产计数 + 能力条", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderHome();
    await waitFor(() => expect(screen.getByText("二叉树遍历")).toBeTruthy());
    expect(screen.getByText("52")).toBeTruthy();          // AI 记的偏好
    expect(screen.getByText("联网查资料")).toBeTruthy();   // 能力（产品话术）
    expect(screen.getByText("114.0 万")).toBeTruthy();     // token 格式化
    // 工程黑话默认不出现在学习主场
    expect(screen.queryByText("P95 延迟")).toBeNull();
  });

  it("切到工程台：出现运维指标（成功率 / P95 / 工具成功率）", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderHome();
    await waitFor(() => expect(screen.getByText("二叉树遍历")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "工程台" }));
    await waitFor(() => expect(screen.getByText("P95 延迟")).toBeTruthy());
    expect(screen.getByText("成功率")).toBeTruthy();
    expect(screen.getByText("14.2s")).toBeTruthy();        // p95 格式化
    expect(screen.getByText("http_request")).toBeTruthy(); // 原始工具名（工程口径）
    expect(screen.getByText("$ —")).toBeTruthy();          // 成本占位
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
      { text: "用户偏好用中文", collection: "semantic", created_at: "2026-07-11T01:00:00+00:00" },
    ]);
    renderHome();
    await waitFor(() => expect(screen.getByText("二叉树遍历")).toBeTruthy());
    fireEvent.click(screen.getByText("🧠 AI 记的偏好"));
    await waitFor(() => expect(screen.getByText("AI 记住的偏好")).toBeTruthy());  // 抽屉标题
    expect(await screen.findByText("用户偏好用中文")).toBeTruthy();
    expect(statsApi.memory).toHaveBeenCalled();
  });

  it("最近产物提供预览与下载入口", async () => {
    (statsApi.overview as any).mockResolvedValue(OV);
    renderHome();
    await waitFor(() => expect(screen.getByText("复习提纲.md")).toBeTruthy());
    expect(screen.getByLabelText("预览 复习提纲.md")).toBeTruthy();  // md 可预览
    expect(screen.getByLabelText("下载 复习提纲.md")).toBeTruthy();
  });
});
