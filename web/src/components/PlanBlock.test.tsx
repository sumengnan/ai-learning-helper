import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, cleanup, act } from "@testing-library/react";
import { PlanBlock } from "./PlanBlock";

afterEach(cleanup);

describe("PlanBlock", () => {
  it("空或非法快照渲染 null", () => {
    expect(render(<PlanBlock text={undefined} />).container.firstChild).toBeNull();
    cleanup();
    expect(render(<PlanBlock text="not json" />).container.firstChild).toBeNull();
    cleanup();
    expect(render(<PlanBlock text="[]" />).container.firstChild).toBeNull();
  });

  it("有序渲染步骤、计数正确、重试步在失败步之后", () => {
    const snap = JSON.stringify([
      { title: "查资料", status: "done" },
      { title: "计算", status: "failed" },
      { title: "重试：换沙箱", status: "running" },
      { title: "汇总", status: "pending" },
    ]);
    render(<PlanBlock text={snap} />);
    expect(screen.getByText("1/4 完成")).toBeTruthy();
    const all = screen
      .getAllByText(/查资料|计算|重试：换沙箱|汇总/)
      .map((e) => e.textContent);
    // 首项为标题右侧「当前步骤」预览（进行中的那步）；其后为有序步骤清单
    expect(all[0]).toBe("重试：换沙箱");
    expect(all.slice(1)).toEqual(["查资料", "计算", "重试：换沙箱", "汇总"]);
  });

  it("生成中：运行中的步骤显示进度圈", () => {
    const snap = JSON.stringify([{ title: "计算中", status: "running" }]);
    const { container } = render(<PlanBlock text={snap} live />);
    expect(container.querySelector('[role="progressbar"]')).toBeTruthy();
  });

  it("用户停止后：运行中的步骤标『已取消』且不再转圈（issue 2）", () => {
    const snap = JSON.stringify([
      { title: "查资料", status: "done" },
      { title: "计算中", status: "running" },
    ]);
    const { container } = render(<PlanBlock text={snap} live={false} stopped />);
    expect(screen.getByText(/计算中（已取消）/)).toBeTruthy();
    expect(container.querySelector('[role="progressbar"]')).toBeNull();
  });
});

describe("PlanBlock 每步耗时", () => {
  it("有 elapsed_ms 的步骤显示耗时，没有的不显示", () => {
    const snap = JSON.stringify([
      { title: "查资料", status: "done", elapsed_ms: 3500 },
      { title: "计算", status: "done", elapsed_ms: 92000 },
      { title: "汇总", status: "running" },              // 还在跑：无耗时
      { title: "收尾", status: "pending" },              // 没开始：无耗时
    ]);
    render(<PlanBlock text={snap} live />);
    expect(screen.getByText("3 秒")).toBeTruthy();
    expect(screen.getByText("1 分 32 秒")).toBeTruthy();
    // 只有两步有耗时，不给 running/pending 编时间
    expect(screen.queryAllByText(/秒$/).length).toBe(2);
  });

  it("不足 1 秒显示「<1 秒」，不显示误导性的「0 秒」", () => {
    render(<PlanBlock text={JSON.stringify([{ title: "快步", status: "done", elapsed_ms: 0 }])} />);
    expect(screen.getByText("<1 秒")).toBeTruthy();
    expect(screen.queryByText("0 秒")).toBeNull();
  });

  it("刷新后（live=false）耗时照常渲染——数据来自落库的 plan 快照", () => {
    const snap = JSON.stringify([{ title: "查资料", status: "done", elapsed_ms: 4000 }]);
    render(<PlanBlock text={snap} live={false} />);
    expect(screen.getByText("4 秒")).toBeTruthy();
  });
});

describe("PlanBlock 进行中读秒", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  const running = (startedAtMs: number) =>
    JSON.stringify([{ title: "查资料", status: "running", started_at_ms: startedAtMs }]);

  it("live 时按 started_at_ms 每秒读秒", async () => {
    vi.setSystemTime(new Date("2024-01-01T00:00:04Z"));   // 已跑了 4 秒
    const startedAt = new Date("2024-01-01T00:00:00Z").getTime();
    render(<PlanBlock text={running(startedAt)} live />);
    expect(screen.getByText("4 秒")).toBeTruthy();

    await act(async () => { vi.advanceTimersByTime(3000); });
    expect(screen.getByText("7 秒")).toBeTruthy();         // 数字确实在走
  });

  it("刷新后接着读秒——起点来自落库快照，不是从本次挂载起算", async () => {
    // 页面刷新时该步已跑了 90 秒：必须显示 1 分 30 秒，而不是从 0 重新数
    vi.setSystemTime(new Date("2024-01-01T00:01:30Z"));
    const startedAt = new Date("2024-01-01T00:00:00Z").getTime();
    render(<PlanBlock text={running(startedAt)} live />);
    expect(screen.getByText("1 分 30 秒")).toBeTruthy();
  });

  it("非 live（已停止/已中断）不读秒——那步已按「已取消」呈现，跳动的秒数只会误导", async () => {
    vi.setSystemTime(new Date("2024-01-01T00:00:04Z"));
    const startedAt = new Date("2024-01-01T00:00:00Z").getTime();
    render(<PlanBlock text={running(startedAt)} live={false} stopped />);
    expect(screen.queryByText("4 秒")).toBeNull();
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(screen.queryByText(/秒/)).toBeNull();           // 始终不涨
  });
});
