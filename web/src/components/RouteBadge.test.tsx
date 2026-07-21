import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { RouteBadge, routeModeOf } from "./RouteBadge";
import type { ChatMessage } from "../types";

afterEach(() => cleanup());

const prog = (detail: Record<string, unknown> | null, text = "x"): ChatMessage["progress"] =>
  [{ scope: "route", text, detail: detail as never }];

describe("routeModeOf 解析", () => {
  it("按 detail.mode 判定，两种路径各自可辨", () => {
    expect(routeModeOf(prog({ mode: "simple" }))).toBe("simple");
    expect(routeModeOf(prog({ mode: "plan" }))).toBe("plan");
  });

  it("没有 route 事件 → null（旧对话回放时不该凭空长出一个徽章）", () => {
    expect(routeModeOf([{ scope: "plan", text: "[]" }])).toBe(null);
    expect(routeModeOf(undefined)).toBe(null);
  });

  it("认不出的 mode 一律 null，不猜", () => {
    // 服务端将来若加了第三条路径，旧前端该沉默，而不是把它误标成已知的某一种
    expect(routeModeOf(prog({ mode: "react" }))).toBe(null);
    expect(routeModeOf(prog(null))).toBe(null);
  });

  it("只认 detail，不从文案反解——文案是给人看的，改文案不该改坏渲染", () => {
    expect(routeModeOf(prog({ mode: "plan" }, "简单直答"))).toBe("plan");
  });
});

describe("RouteBadge 渲染", () => {
  it("简单直答", () => {
    render(<RouteBadge mode="simple" />);
    expect(screen.getByText("简单直答")).toBeTruthy();
  });

  it("多步规划", () => {
    render(<RouteBadge mode="plan" />);
    expect(screen.getByText("多步规划")).toBeTruthy();
  });
});
