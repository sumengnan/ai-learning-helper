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
