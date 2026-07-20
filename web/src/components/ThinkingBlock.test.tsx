import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, cleanup, act } from "@testing-library/react";
import { ThinkingBlock } from "./ThinkingBlock";

afterEach(cleanup);

describe("ThinkingBlock 顶部计时", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it("思考进行中 → 从 startedAt 实时读秒「思考中」", async () => {
    vi.setSystemTime(new Date("2024-01-01T00:00:03Z"));   // 已思考 3 秒
    const startedAt = new Date("2024-01-01T00:00:00Z").getTime();
    render(<ThinkingBlock reasoning="分析中" thinking startedAt={startedAt} />);
    expect(screen.getByText(/思考中…/)).toBeTruthy();
    expect(screen.getByText("· 3 秒")).toBeTruthy();

    await act(async () => { vi.advanceTimersByTime(2000); });
    expect(screen.getByText("· 5 秒")).toBeTruthy();        // 数字在走
  });

  it("思考结束 → 定格 elapsedMs，标题变「思考过程」，不再读秒", async () => {
    render(<ThinkingBlock reasoning="想好了" thinking={false} elapsedMs={14000} />);
    expect(screen.getByText("🧠 思考过程")).toBeTruthy();
    expect(screen.getByText("· 14 秒")).toBeTruthy();
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(screen.getByText("· 14 秒")).toBeTruthy();       // 定格不变
  });

  it("不足 1 秒显示「<1 秒」，不显示误导的「0 秒」", () => {
    render(<ThinkingBlock reasoning="秒回" thinking={false} elapsedMs={0} />);
    expect(screen.getByText("· <1 秒")).toBeTruthy();
    expect(screen.queryByText(/0 秒/)).toBeNull();
  });

  it("超过 1 分显示「N 分 N 秒」", () => {
    render(<ThinkingBlock reasoning="想了很久" thinking={false} elapsedMs={92000} />);
    expect(screen.getByText("· 1 分 32 秒")).toBeTruthy();
  });

  it("进行中但缺 startedAt（接回等）→ 不读秒，但仍显示「思考中」而不崩", () => {
    render(<ThinkingBlock reasoning="x" thinking startedAt={undefined} />);
    expect(screen.getByText(/思考中…/)).toBeTruthy();
    expect(screen.queryByText(/秒/)).toBeNull();
  });

  it("思考结束但无 elapsedMs（旧消息/非思考模式无耗时）→ 只显示「思考过程」，无时间", () => {
    render(<ThinkingBlock reasoning="旧数据" thinking={false} />);
    expect(screen.getByText("🧠 思考过程")).toBeTruthy();
    expect(screen.queryByText(/秒/)).toBeNull();
  });

  it("给 title 时用自定义标题（如任务计划思考/结果思考）", () => {
    render(<ThinkingBlock reasoning="怎么拆" thinking={false} title="任务计划思考" />);
    expect(screen.getByText("🧠 任务计划思考")).toBeTruthy();
    expect(screen.queryByText("🧠 思考过程")).toBeNull();
  });
});
