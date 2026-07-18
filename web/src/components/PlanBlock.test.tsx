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

  it("每步前带序号 1. 2. 3.，正在执行的步仍显示转圈", () => {
    const snap = JSON.stringify([
      { title: "查资料", status: "done" },
      { title: "计算中", status: "running" },
      { title: "汇总", status: "pending" },
    ]);
    const { container } = render(<PlanBlock text={snap} live />);
    expect(screen.getByText("1.")).toBeTruthy();
    expect(screen.getByText("2.")).toBeTruthy();
    expect(screen.getByText("3.")).toBeTruthy();
    // 正在执行的步仍有转圈标识
    expect(container.querySelector('[role="progressbar"]')).toBeTruthy();
  });

  it("生成中：只有正在执行的步转圈，待办步不转圈", () => {
    const snap = JSON.stringify([
      { title: "已完成", status: "done" },
      { title: "执行中", status: "running" },
      { title: "待办1", status: "pending" },
      { title: "待办2", status: "pending" },
    ]);
    const { container } = render(<PlanBlock text={snap} live />);
    // 恰好一个进度圈（正在执行的那步），而不是所有非终态步都转圈
    expect(container.querySelectorAll('[role="progressbar"]').length).toBe(1);
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

// 模型约 1/6 的多步任务会中途停止更新清单（实测轨迹：只调了 2 次 update_plan，
// 清单停在 [done,done,running,pending]，而第 4 步的 save_download 明明执行了）。
// 前端无从得知那些步骤做没做 —— 所以只能如实说未知，不能替模型下断言。
describe("PlanBlock · 运行结束但模型没把清单更新完", () => {
  const STALE = JSON.stringify([
    { title: "检索技术演进", status: "done" },
    { title: "检索落地模式", status: "done" },
    { title: "整合信息", status: "running" },
    { title: "生成笔记", status: "pending" },
  ]);

  it("正常跑完(done)：未收尾的步骤标『状态未知』，不谎称未完成、也不装作没开始", () => {
    render(<PlanBlock text={STALE} live={false} status="done" />);
    expect(screen.getByText(/整合信息（状态未知）/)).toBeTruthy();
    expect(screen.getByText(/生成笔记（状态未知）/)).toBeTruthy();
    // 这两步实际可能已完成（文件都生成了），断言「未完成」就是撒谎
    expect(screen.queryByText(/整合信息（未完成）/)).toBeNull();
  });

  it("标题点明清单没更新完 —— 否则「2/4 完成」会被读成任务只干了一半", () => {
    render(<PlanBlock text={STALE} live={false} status="done" />);
    expect(screen.getByText("2/4 完成")).toBeTruthy();
    expect(screen.getByText("· 清单未更新完")).toBeTruthy();
  });

  it("未知步骤用问号图标，与 pending 的空心圈区分开", () => {
    const { container } = render(<PlanBlock text={STALE} live={false} status="done" />);
    // 空心圈的语义是「还没开始」，这里我们没资格这么断言
    expect(container.querySelector('[data-testid="HelpOutlinedIcon"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="RadioButtonUncheckedIcon"]')).toBeNull();
  });

  it("运行失败(error)：未收尾的步骤是『未完成』而非『未知』——运行都没跑完", () => {
    render(<PlanBlock text={STALE} live={false} status="error" />);
    expect(screen.getByText(/整合信息（未完成）/)).toBeTruthy();
    expect(screen.queryByText(/状态未知/)).toBeNull();
    expect(screen.queryByText("· 清单未更新完")).toBeNull();
  });

  it("清单收尾干净时不加任何噪音（84% 的正常情况）", () => {
    const clean = JSON.stringify([
      { title: "查资料", status: "done" },
      { title: "汇总", status: "done" },
    ]);
    render(<PlanBlock text={clean} live={false} status="done" />);
    expect(screen.queryByText("· 清单未更新完")).toBeNull();
    expect(screen.queryByText(/状态未知/)).toBeNull();
    expect(screen.getByText("2/2 完成")).toBeTruthy();
  });

  it("生成中不预判：还在跑就该转圈，不能说未知", () => {
    const { container } = render(<PlanBlock text={STALE} live status="streaming" />);
    expect(container.querySelector('[role="progressbar"]')).toBeTruthy();
    expect(screen.queryByText(/状态未知/)).toBeNull();
  });

  it("用户停止仍走『已取消』，不被未知逻辑抢走", () => {
    render(<PlanBlock text={STALE} live={false} status="stopped" />);
    expect(screen.getByText(/整合信息（已取消）/)).toBeTruthy();
    expect(screen.queryByText(/状态未知/)).toBeNull();
  });
});

describe("PlanBlock · 并行/依赖标注", () => {
  const plan = JSON.stringify([
    { id: "s1", title: "调研技术", status: "done", depends_on: [] },
    { id: "s2", title: "调研案例", status: "running", depends_on: [] },
    { id: "s3", title: "调研风险", status: "done", depends_on: [] },
    { id: "s4", title: "整合", status: "pending", depends_on: ["s1", "s2", "s3"] },
    { id: "s5", title: "生成报告", status: "pending", depends_on: ["s4"] },
  ]);

  it("同层多步标『并行』，依赖步标『依赖 序号』", () => {
    render(<PlanBlock text={plan} live status="streaming" />);
    // s1/s2/s3 同为第 0 层、3 步 → 并行徽章（出现 3 次）
    expect(screen.getAllByText("并行").length).toBe(3);
    // s4 依赖 s1·s2·s3 → 依赖 1·2·3
    expect(screen.getByText("依赖 1·2·3")).toBeTruthy();
    // s5 依赖 s4 → 依赖 4
    expect(screen.getByText("依赖 4")).toBeTruthy();
  });

  it("s5 独占第 2 层 → 不标并行", () => {
    render(<PlanBlock text={plan} live status="streaming" />);
    // s4 独占第 1 层、s5 独占第 2 层：都不并行；只有 s1/s2/s3 三个并行徽章
    expect(screen.getAllByText("并行").length).toBe(3);
  });

  it("ReAct 清单（无 id/依赖）不出现并行/依赖标注", () => {
    const react = JSON.stringify([
      { title: "查资料", status: "done" }, { title: "汇总", status: "done" },
    ]);
    render(<PlanBlock text={react} />);
    expect(screen.queryByText("并行")).toBeNull();
    expect(screen.queryByText(/依赖/)).toBeNull();
  });
});

describe("PlanBlock · 编排器计划步嵌套执行明细", () => {
  const plan = JSON.stringify([
    { id: "s1", title: "调研快排", status: "done" },
    { id: "s2", title: "汇总", status: "done" },
  ]);
  const subItems = [
    { scope: "subagent:executor:s1", text: "调研快排", status: "running" as const, key: "__hdr__:s1" },
    { scope: "subagent:executor:s1", text: "调用工具 web", status: "ok" as const, key: "c1",
      detail: { tool: "web", args: { q: "快排" }, result: "结果X", is_error: false } },
  ];

  it("带 id 且有匹配 executor 明细的步 → 可展开，露出 agent + 工具调用 + 入参/返回", () => {
    render(<PlanBlock text={plan} live={false} status="done" subItems={subItems} />);
    // 顶部仍是计划步（总任务步骤）
    expect(screen.getAllByText("调研快排").length).toBeGreaterThanOrEqual(1);
    // 展开后：执行 agent、工具名、参数/结果都在 DOM（MUI Accordion 折叠时子节点仍挂载）
    expect(screen.getByText("executor:s1")).toBeTruthy();
    expect(screen.getByText("web")).toBeTruthy();
    expect(screen.getByText("参数")).toBeTruthy();
    expect(screen.getByText("结果X")).toBeTruthy();
  });

  it("无匹配明细的步（如 s2）不产生工具明细，仍是纯步骤行", () => {
    render(<PlanBlock text={plan} live={false} status="done" subItems={subItems} />);
    // s2 没有 executor:s2 的明细 → 不出现「执行明细」小标
    // s1 出现一次执行明细，s2 不出现（故总计恰好 1 处）
    expect(screen.getAllByText("执行明细").length).toBe(1);
  });

  it("无 subItems（ReAct 清单）→ 完全按纯行渲染，无展开", () => {
    render(<PlanBlock text={plan} live={false} status="done" />);
    expect(screen.queryByText("执行明细")).toBeNull();
    expect(screen.queryByText("executor:s1")).toBeNull();
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
