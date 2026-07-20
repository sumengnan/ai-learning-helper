import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { ProgressBlock } from "./ProgressBlock";

// 技能详情走 api.skills.get；mock 掉网络，只验渲染与交互。
// vi.mock 会被提升到文件顶部，故 mockGet 必须用 vi.hoisted 一并提升，否则工厂里引用到的是 TDZ。
const { mockGet } = vi.hoisted(() => ({ mockGet: vi.fn() }));
vi.mock("../api/client", () => ({ api: { skills: { get: mockGet } } }));

afterEach(() => cleanup());

describe("ProgressBlock", () => {
  it("沙箱步骤带 agent 归属 → 用子代理标签区分", () => {
    render(<ProgressBlock title="沙箱执行" kind="sandbox" status="ok" items={[
      { scope: "sandbox", text: "执行 python", status: "ok", agent: "研究员" },
      { scope: "sandbox", text: "执行 ls", status: "ok" },
    ]} />);
    // 子代理名以独立标签出现（区别于主 agent 的沙箱步骤）
    expect(screen.getByText("研究员")).toBeTruthy();
    // 无 agent 的步骤正常显示（末步同时作为标题预览，故出现在预览+正文两处）
    expect(screen.getAllByText("执行 ls").length).toBeGreaterThanOrEqual(1);
  });

  it("最后一步预览超长 → 按硬字数上限截断为「…」", () => {
    const long = "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十";  // 30 字
    render(<ProgressBlock title="沙箱执行" kind="sandbox" status="ok" items={[
      { scope: "sandbox", text: long, status: "ok" },
    ]} />);
    const clamped = long.slice(0, 24) + "…";
    expect(screen.getByText(clamped)).toBeTruthy();   // 标题右侧预览被截断
    expect(screen.getByText(long)).toBeTruthy();       // 展开正文仍是完整文本
  });

  it("沙箱步骤的 executor:sN 归属 → 显示为可读的「步骤 sN」", () => {
    render(<ProgressBlock title="沙箱执行" kind="sandbox" status="ok" items={[
      { scope: "sandbox", text: "执行 python", status: "ok", agent: "executor:s2" },
    ]} />);
    // 机器味的 "executor:s2" 转成用户能读懂的「步骤 s2」（标题预览 + 正文均出现）
    expect(screen.getAllByText("步骤 s2").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("executor:s2")).toBeNull();
  });

  it("子代理块：每步前缀子 agent 名", () => {
    render(<ProgressBlock title="子代理执行" kind="subagent" status="ok" items={[
      { scope: "subagent:出题官", text: "调用工具 sample_questions", status: "ok" },
    ]} />);
    expect(screen.getByText("出题官")).toBeTruthy();
  });
});

describe("ProgressBlock · 技能详情展开", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockGet.mockResolvedValue({
      name: "wrong-answer-remediation", description: "错题精讲",
      body: "# 错题精讲\n\n## 步骤\n\n1. **取错题**：调用 sample_wrong_answers",
    });
  });

  // 外层 CollapsibleBlock 默认折叠，MUI 会给折叠内容加 aria-hidden，getByRole 一律跳过。
  // 不先展开就查不到技能行——「不可点开」类断言也会因此假通过。
  function expandBlock() {
    fireEvent.click(screen.getAllByRole("button")[0]);
  }

  const skillItem = {
    scope: "skill", text: "已启用技能「wrong-answer-remediation」：错题精讲与举一反三",
    status: "ok" as const, detail: { skill: "wrong-answer-remediation" },
  };

  it("技能行可点开，正文按 markdown 渲染（标题成为真正的 heading）", async () => {
    render(<ProgressBlock title="技能" kind="skill" status="ok" items={[skillItem]} />);
    expandBlock();
    // 未点技能行时不发请求，也看不到正文
    expect(mockGet).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /查看技能/ }));
    expect(mockGet).toHaveBeenCalledWith("wrong-answer-remediation");
    // md 渲染：# 变成 h1，** ** 变成 strong——不是原样吐出 markdown 源码
    expect(await screen.findByRole("heading", { name: "错题精讲" })).toBeTruthy();
    expect(screen.getByText("取错题").tagName.toLowerCase()).toBe("strong");
  });

  it("再点一次收起", async () => {
    render(<ProgressBlock title="技能" kind="skill" status="ok" items={[skillItem]} />);
    expandBlock();
    const row = screen.getByRole("button", { name: /查看技能/ });
    fireEvent.click(row);
    expect(await screen.findByRole("heading", { name: "错题精讲" })).toBeTruthy();
    fireEvent.click(row);
    expect(screen.queryByRole("heading", { name: "错题精讲" })).toBeNull();
  });

  it("技能已被删除/改名 → 显示错误而不是一直转圈", async () => {
    mockGet.mockRejectedValue(new Error("技能不存在或已被移除"));
    render(<ProgressBlock title="技能" kind="skill" status="ok" items={[skillItem]} />);
    expandBlock();
    fireEvent.click(screen.getByRole("button", { name: /查看技能/ }));
    expect(await screen.findByText("技能不存在或已被移除")).toBeTruthy();
  });

  it("没有 detail.skill 的技能行不可点开（如 load_skill 发的旧事件）", () => {
    render(<ProgressBlock title="技能" kind="skill" status="ok" items={[
      { scope: "skill", text: "已加载技能：错题精讲", status: "ok" },
    ]} />);
    expandBlock();
    expect(screen.queryByRole("button", { name: /查看技能/ })).toBeNull();
  });

  it("非技能块的行不受影响，不会因为带 detail 就变可点", () => {
    render(<ProgressBlock title="子代理执行" kind="subagent" status="ok" items={[
      { scope: "subagent:executor:s1", text: "调用工具 calc", status: "ok",
        detail: { skill: "wrong-answer-remediation" } },
    ]} />);
    expandBlock();
    expect(screen.queryByRole("button", { name: /查看技能/ })).toBeNull();
  });
});
