import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { VerifyBadge } from "./VerifyBadge";
import type { ChatMessage } from "../types";

afterEach(() => cleanup());

// 便捷构造一条 assistant 消息
const msg = (over: Partial<ChatMessage> = {}): ChatMessage =>
  ({ role: "assistant", content: "答案", ...over });

describe("VerifyBadge 状态机", () => {
  it("本轮进行中（live）→ 显示「验证中…」", () => {
    render(<VerifyBadge live message={msg({
      status: "streaming",
      progress: [{ scope: "verify", text: "校验中", status: "running" }],
    })} />);
    expect(screen.getByText("验证中…")).toBeTruthy();
  });

  it("verify running 也进入「验证中…」（即使非 live）", () => {
    render(<VerifyBadge message={msg({
      progress: [{ scope: "verify", text: "校验中", status: "running" }],
    })} />);
    expect(screen.getByText("验证中…")).toBeTruthy();
  });

  it("verify ok → 「校验通过」，有 quality.final 则并显示「质量 N」", () => {
    render(<VerifyBadge message={msg({
      progress: [{ scope: "verify", text: "通过", status: "ok" }],
      quality: { plan: 80, steps: 70, final: 90, feedback: "不错" },
    })} />);
    expect(screen.getByText("校验通过")).toBeTruthy();
    expect(screen.getByText("· 质量 90")).toBeTruthy();
  });

  it("流结束且无 verify error（非 live）→ 视为通过", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      checks: [{ tool: "search_memory", status: "ok", text: "检索命中" }],
    })} />);
    expect(screen.getByText("校验通过")).toBeTruthy();
  });

  it("verify error → 红色「未通过」+ 末条 verify 文案", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      progress: [{ scope: "verify", text: "grounding 未通过", status: "error" }],
    })} />);
    expect(screen.getByText("未通过")).toBeTruthy();
    // summary 预览 + 展开明细行两处均含该文案
    expect(screen.getAllByText("grounding 未通过").length).toBeGreaterThanOrEqual(1);
  });

  it("无 verify/check/quality 任一信号 → 不渲染（返回 null）", () => {
    const { container } = render(<VerifyBadge message={msg({ status: "done" })} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("VerifyBadge 常驻性与展开明细", () => {
  it("即使不传 live（模拟 showTools=false 场景）仍常驻渲染", () => {
    // 组件本身不读 showTools；只要有信号就渲染，验证其独立于工具开关
    render(<VerifyBadge message={msg({
      status: "done",
      quality: { plan: 1, steps: 2, final: 3, feedback: "" },
    })} />);
    expect(screen.getByText("校验通过")).toBeTruthy();
  });

  it("展开面板显示 checks 每步一行 + quality 三层分与 feedback", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      progress: [{ scope: "verify", text: "通过", status: "ok" }],
      checks: [
        { tool: "search_memory", status: "ok", text: "检索命中" },
        { tool: "run_python", status: "error", text: "run_python 执行未通过" },
      ],
      quality: { plan: 80, steps: 65, final: 90, feedback: "步骤可再精简" },
    })} />);
    // 点击展开 Accordion
    fireEvent.click(screen.getByText("校验通过"));
    expect(screen.getByText("检索命中")).toBeTruthy();
    expect(screen.getByText("run_python 执行未通过")).toBeTruthy();
    expect(screen.getByText("拆分 80")).toBeTruthy();
    expect(screen.getByText("关键步 65")).toBeTruthy();
    expect(screen.getByText("最终 90")).toBeTruthy();
    expect(screen.getByText("步骤可再精简")).toBeTruthy();
  });

  it("quality 分数为 null → 展开显示「—」", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      quality: { plan: null, steps: null, final: null, feedback: "" },
    })} />);
    fireEvent.click(screen.getByText("校验通过"));
    expect(screen.getByText("拆分 —")).toBeTruthy();
    expect(screen.getByText("关键步 —")).toBeTruthy();
    expect(screen.getByText("最终 —")).toBeTruthy();
  });
});

// applyEvent 对 quality 无效 JSON 的容错在 ChatView 内；此处验证组件对
// quality=null（解析失败后维持 null）时的健壮性：仅有 checks 信号照常渲染、不崩。
describe("VerifyBadge 容错", () => {
  it("quality 为 null（解析失败）时不崩，仅按其它信号渲染", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      quality: null,
      checks: [{ tool: "search_memory", status: "ok", text: "检索命中" }],
    })} />);
    expect(screen.getByText("校验通过")).toBeTruthy();
    fireEvent.click(screen.getByText("校验通过"));
    expect(screen.getByText("检索命中")).toBeTruthy();
    // 无 quality 段：不应出现三层分标签
    expect(screen.queryByText(/拆分/)).toBeNull();
  });
});
