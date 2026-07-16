import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
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
