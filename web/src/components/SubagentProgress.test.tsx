import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { SubagentProgress } from "./SubagentProgress";

afterEach(() => cleanup());

const execItems = [
  { scope: "subagent:executor:s1", text: "调研快排", status: "running" as const, key: "__hdr__:s1" },
  {
    scope: "subagent:executor:s1", text: "调用工具 calc", status: "ok" as const, key: "c1",
    detail: { tool: "calc", args: { x: 1 }, result: "结果42", is_error: false },
  },
];

describe("SubagentProgress", () => {
  it("组标题用步骤描述、工具行展开出参数/结果", () => {
    render(<SubagentProgress items={execItems} live={false} stopped={false} status="ok" />);
    // 标题是步骤描述而非裸 id
    expect(screen.getByText("调研快排")).toBeTruthy();
    // 工具名出现在可展开行
    expect(screen.getByText("calc")).toBeTruthy();
    // 明细（MUI Accordion 折叠时子节点仍挂载）：参数 + 结果都在 DOM
    expect(screen.getByText("参数")).toBeTruthy();
    expect(screen.getByText("结果 · 成功")).toBeTruthy();
    expect(screen.getByText("结果42")).toBeTruthy();
  });

  it("dispatch 任务头行作组标题", () => {
    render(<SubagentProgress items={[
      { scope: "subagent:研究员", text: "开始任务：查资料", status: "running" as const },
      { scope: "subagent:研究员", text: "调用工具 web", status: "ok" as const, key: "c2",
        detail: { tool: "web", args: { q: "a" }, result: "r", is_error: false } },
    ]} live={false} stopped={false} status="ok" />);
    expect(screen.getByText("开始任务：查资料")).toBeTruthy();
    expect(screen.getByText("web")).toBeTruthy();
  });

  it("失败工具行显示失败结果", () => {
    render(<SubagentProgress items={[
      { scope: "subagent:executor:s2", text: "调用工具 bad", status: "error" as const, key: "c3",
        detail: { tool: "bad", args: {}, result: "炸了", is_error: true } },
    ]} live={false} stopped={false} status="error" />);
    expect(screen.getByText("结果 · 失败")).toBeTruthy();
  });

  it("MCP 工具显示 MCP 徽章 + server·tool，不露原始 mcp__ 前缀名", () => {
    render(<SubagentProgress items={[
      { scope: "subagent:executor:s1", text: "调用工具 mcp__websearch__bailian_web_search",
        status: "ok" as const, key: "c9",
        detail: { tool: "mcp__websearch__bailian_web_search", args: {}, result: "r", is_error: false } },
    ]} live={false} stopped={false} status="ok" />);
    expect(screen.getByText("MCP")).toBeTruthy();
    expect(screen.getByText("websearch · bailian_web_search")).toBeTruthy();
    // 不出现拼接的原始名
    expect(screen.queryByText(/mcp__websearch__bailian_web_search/)).toBeNull();
  });

  it("无 items 渲染 null", () => {
    const { container } = render(
      <SubagentProgress items={[]} live={false} stopped={false} status="ok" />);
    expect(container.firstChild).toBeNull();
  });
});
