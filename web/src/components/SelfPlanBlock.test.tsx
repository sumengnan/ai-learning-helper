import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { SelfPlanBlock } from "./SelfPlanBlock";
import { PlanBlock } from "./PlanBlock";
import { isOrchestratorPlan, lastPlanText } from "./planSteps";

afterEach(() => cleanup());

const react = JSON.stringify([
  { title: "查资料", status: "done" },
  { title: "写总结", status: "running" },
]);
const orch = JSON.stringify([
  { id: "s1", title: "查资料", status: "done" },
  { id: "s2", title: "写总结", status: "running", depends_on: ["s1"] },
]);

describe("清单来源判定", () => {
  it("带 id 的是编排器计划，只有 title/status 的是模型自述清单", () => {
    expect(isOrchestratorPlan(orch)).toBe(true);
    expect(isOrchestratorPlan(react)).toBe(false);
  });

  it("空/坏 JSON 不算编排器计划，也不该抛", () => {
    expect(isOrchestratorPlan("")).toBe(false);
    expect(isOrchestratorPlan("{不是数组")).toBe(false);
    expect(isOrchestratorPlan(undefined)).toBe(false);
  });

  it("lastPlanText 取最后一条快照——plan 每更新一次追加一条，认最后那份", () => {
    expect(lastPlanText([
      { scope: "plan", text: "旧" }, { scope: "skill", text: "x" }, { scope: "plan", text: "新" },
    ])).toBe("新");
    expect(lastPlanText([{ scope: "skill", text: "x" }])).toBe(undefined);
  });
});

describe("SelfPlanBlock 渲染", () => {
  it("标题不沿用编排器的「任务步骤」——两者不是一回事，不该长得一样", () => {
    render(<SelfPlanBlock text={react} />);
    expect(screen.getByText("AI 自述清单")).toBeTruthy();
    expect(screen.queryByText("任务步骤")).toBeNull();
  });

  it("完成项画删除线（编排器计划刻意不画，靠这条区分两种块）", () => {
    render(<SelfPlanBlock text={react} />);
    expect(getComputedStyle(screen.getByText("查资料")).textDecoration).toContain("line-through");
    // 「写总结」同时出现在标题栏的当前步预览里，两处都不该有删除线（它还没完成）
    for (const el of screen.getAllByText("写总结")) {
      expect(getComputedStyle(el).textDecoration).not.toContain("line-through");
    }
  });

  it("不带序号/并行徽章/依赖标注——那些是编排器计划才有的结构", () => {
    render(<SelfPlanBlock text={react} />);
    expect(screen.queryByText("1.")).toBeNull();
    expect(screen.queryByText("并行")).toBeNull();
    expect(screen.queryByText(/依赖/)).toBeNull();
  });

  it("模型半途不更新清单时如实说「状态未知」，不替它断言做没做", () => {
    // 运行已成功结束（status=done），清单却还留着 running/pending 的步
    render(<SelfPlanBlock text={react} status="done" />);
    expect(screen.getByText(/状态未知/)).toBeTruthy();
    expect(screen.getByText(/清单未更新完/)).toBeTruthy();
  });

  it("空清单不渲染空壳块", () => {
    const { container } = render(<SelfPlanBlock text="[]" />);
    expect(container.textContent).toBe("");
  });
});

describe("与 PlanBlock 的对照", () => {
  it("编排器计划仍是「任务步骤」、带序号、完成项不画删除线", () => {
    render(<PlanBlock text={orch} />);
    expect(screen.getByText("任务步骤")).toBeTruthy();
    expect(screen.getByText("1.")).toBeTruthy();
    // 标题经 EllipsisText 渲染，取含该文本的节点判样式
    const done = screen.getAllByText("查资料").find((e) => e.textContent === "查资料")!;
    expect(getComputedStyle(done).textDecoration).not.toContain("line-through");
  });
});
