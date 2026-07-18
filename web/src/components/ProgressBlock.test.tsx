import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { ProgressBlock } from "./ProgressBlock";

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
