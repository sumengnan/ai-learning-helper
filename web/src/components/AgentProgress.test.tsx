import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { AgentProgress } from "./AgentProgress";

afterEach(() => cleanup());

describe("AgentProgress", () => {
  it("MCP 工具显示 MCP 标签与 server·tool，且与普通工具一样展示参数/结果", () => {
    render(<AgentProgress steps={[
      { tool: "mcp__example__calc", args: { expression: "(2+3)**4" }, result: "625", isError: false },
    ]} />);
    expect(screen.getAllByText("MCP").length).toBeGreaterThan(0);            // MCP 标签
    expect(screen.getAllByText("example · calc").length).toBeGreaterThan(0); // 友好名
    expect(screen.getByText("625")).toBeTruthy();                           // 结果照常展示
  });

  it("普通工具原样显示名字，不加 MCP 标签", () => {
    render(<AgentProgress steps={[
      { tool: "calculator", args: { expression: "1+1" }, result: "2", isError: false },
    ]} />);
    expect(screen.getAllByText("calculator").length).toBeGreaterThan(0);
    expect(screen.queryByText("MCP")).toBeNull();
  });
});
