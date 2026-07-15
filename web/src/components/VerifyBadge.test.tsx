import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { VerifyBadge, GATE_OPEN_KEY } from "./VerifyBadge";
import type { ChatMessage } from "../types";

afterEach(() => cleanup());

// 便捷构造一条 assistant 消息
const msg = (over: Partial<ChatMessage> = {}): ChatMessage =>
  ({ role: "assistant", content: "答案", ...over });

describe("VerifyBadge 状态机", () => {
  it("本轮进行中（live）→ 原地显示当前过程文案（校验中…/重答中…）", () => {
    render(<VerifyBadge live message={msg({
      status: "streaming",
      progress: [{ scope: "verify", text: "校验中…", status: "running" }],
    })} />);
    expect(screen.getByText("校验中…")).toBeTruthy();
  });

  it("verify running（非 live）→ 显示过程文案（如重答中…）", () => {
    render(<VerifyBadge message={msg({
      progress: [{ scope: "verify", text: "重答中…", status: "running" }],
    })} />);
    expect(screen.getByText("重答中…")).toBeTruthy();
  });

  it("live 但只有每步校验、无最终校验 → 不谎称「验证中」（按每步定通过）", () => {
    render(<VerifyBadge live message={msg({
      status: "streaming",
      checks: [{ tool: "run_python", status: "ok", text: "run_python 执行通过" }],
    })} />);
    expect(screen.queryByText("验证中…")).toBeNull();
    expect(screen.getByText("步骤校验通过")).toBeTruthy();
  });

  it("verify ok → 「结果校验通过」，有 quality.final 则并显示「质量 N」", () => {
    render(<VerifyBadge message={msg({
      progress: [{ scope: "verify", text: "通过", status: "ok" }],
      quality: { plan: 80, steps: 70, final: 90, feedback: "不错" },
    })} />);
    expect(screen.getByText("结果校验通过")).toBeTruthy();
    expect(screen.getByText("· 质量 90")).toBeTruthy();
  });

  it("流结束且无 verify error（非 live）→ 视为通过", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      checks: [{ tool: "run_python", status: "ok", text: "run_python 执行通过" }],
    })} />);
    expect(screen.getByText("步骤校验通过")).toBeTruthy();
  });

  it("检索命中（成功）不作为校验状态展示；无其它信号则不渲染", () => {
    const { container } = render(<VerifyBadge message={msg({
      status: "done",
      checks: [{ tool: "search_memory", status: "ok", text: "检索命中" }],
    })} />);
    expect(container.firstChild).toBeNull();   // 唯一信号是检索命中 ok → 被过滤 → 不渲染
  });

  it("verify error → 红色「结果校验未通过」+ 末条 verify 文案", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      progress: [{ scope: "verify", text: "grounding 未通过", status: "error" }],
    })} />);
    expect(screen.getByText("结果校验未通过")).toBeTruthy();
    // summary 预览 + 展开明细行两处均含该文案
    expect(screen.getAllByText(/grounding 未通过/).length).toBeGreaterThanOrEqual(1);
  });

  it("多轮：失败轮记录与原因保留，最终通过后仍可展开查看历史", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      progress: [
        { scope: "verify", text: "校验中…", status: "running", key: "k1" },
        { scope: "verify", text: "judge 分数过低", status: "error", key: "k1" },
        { scope: "verify", text: "重答中…", status: "running" },
        { scope: "verify", text: "校验中…", status: "running", key: "k2" },
        { scope: "verify", text: "校验通过", status: "ok", key: "k2" },
      ],
    })} />);
    expect(screen.getByText("结果校验通过")).toBeTruthy();        // 主行：最终通过
    expect(screen.getByText(/judge 分数过低/)).toBeTruthy();      // 历史：失败轮原因仍在
  });

  it("无 verify/check/quality 任一信号 → 不渲染（返回 null）", () => {
    const { container } = render(<VerifyBadge message={msg({ status: "done" })} />);
    expect(container.firstChild).toBeNull();
  });

  it("只有门已开信号（回答刚起头）→ 不渲染：此刻没有任何东西被校验过", () => {
    const { container } = render(<VerifyBadge live message={msg({
      status: "streaming", content: "",
      progress: [{ scope: "verify", text: "生成中…", status: "running", key: GATE_OPEN_KEY }],
    })} />);
    expect(container.firstChild).toBeNull();
  });

  it("门已开信号后真的开始校验 → 徽章此时才出现，且显示「校验中…」而非「生成中…」", () => {
    render(<VerifyBadge live message={msg({
      status: "streaming",
      progress: [
        { scope: "verify", text: "生成中…", status: "running", key: GATE_OPEN_KEY },
        { scope: "verify", text: "校验中…", status: "running", key: "k1" },
      ],
    })} />);
    expect(screen.getByText("校验中…")).toBeTruthy();
    expect(screen.queryByText("生成中…")).toBeNull();
  });
});

describe("VerifyBadge 常驻性与展开明细", () => {
  it("即使不传 live（模拟 showTools=false 场景）仍常驻渲染", () => {
    // 组件本身不读 showTools；只要有信号就渲染，验证其独立于工具开关
    render(<VerifyBadge message={msg({
      status: "done",
      quality: { plan: 1, steps: 2, final: 3, feedback: "" },
    })} />);
    expect(screen.getByText("结果校验通过")).toBeTruthy();
  });

  it("展开面板显示 checks 每步一行 + quality 三层分与 feedback", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      progress: [{ scope: "verify", text: "通过", status: "ok" }],
      checks: [
        { tool: "run_python", status: "error", text: "run_python 执行未通过" },
      ],
      quality: { plan: 80, steps: 65, final: 90, feedback: "步骤可再精简" },
    })} />);
    // 点击展开 Accordion（有 verify ok → 主行「结果校验通过」）
    fireEvent.click(screen.getByText("结果校验通过"));
    expect(screen.getByText("run_python 执行未通过")).toBeTruthy();
    expect(screen.getByText("拆分 80")).toBeTruthy();
    expect(screen.getByText("关键步 65")).toBeTruthy();
    expect(screen.getByText("最终 90")).toBeTruthy();
    expect(screen.getByText("步骤可再精简")).toBeTruthy();
  });

  it("quality 分数全为 null → 不摆一排「—」（judge 只对真发生过的环节打分）", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      quality: { plan: null, steps: null, final: null, feedback: "" },
    })} />);
    fireEvent.click(screen.getByText("结果校验通过"));
    expect(screen.queryByText(/拆分/)).toBeNull();
    expect(screen.queryByText(/关键步/)).toBeNull();
    expect(screen.queryByText(/最终/)).toBeNull();
  });

  it("模型没调 plan 工具 → 只显示实际打了分的项，不显示「拆分 —」", () => {
    // plan 为 null 是设计（无拆分可评），但摆个横线会让人以为是 0 分或出错
    render(<VerifyBadge message={msg({
      status: "done",
      quality: { plan: null, steps: 100, final: 100, feedback: "完成得不错" },
    })} />);
    fireEvent.click(screen.getByText("结果校验通过"));
    expect(screen.queryByText(/拆分/)).toBeNull();
    expect(screen.getByText("关键步 100")).toBeTruthy();
    expect(screen.getByText("最终 100")).toBeTruthy();
    expect(screen.getByText("完成得不错")).toBeTruthy();
  });

  it("0 分是真分数，不能被当成空值隐藏", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      quality: { plan: 0, steps: 0, final: 0, feedback: "很差" },
    })} />);
    // 徽章状态由交付门定，与 judge 分数无关；这里只关心 0 分有没有被显示出来
    fireEvent.click(screen.getByText("结果校验通过"));
    expect(screen.getByText("拆分 0")).toBeTruthy();
    expect(screen.getByText("最终 0")).toBeTruthy();
  });
});

// 两套校验机制共用徽章，主行必须说清是哪一层——否则关掉「结果校验」开关的用户看到
// 「校验通过」会以为结果被校验过（实际只是 run_shell 执行成功的每步标记）。
describe("VerifyBadge 区分步骤校验与结果校验", () => {
  it("关闭结果校验（无 verify/quality）+ run_shell 执行通过 → 主行说「步骤校验通过」，不谎称结果已校验", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      checks: [{ tool: "run_shell", status: "ok", text: "run_shell 执行通过" }],
    })} />);
    expect(screen.getByText("步骤校验通过")).toBeTruthy();
    expect(screen.queryByText("结果校验通过")).toBeNull();
  });

  it("关闭结果校验 + run_shell 执行失败 → 「步骤校验未通过」（失败仍须告知，不静音）", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      checks: [{ tool: "run_shell", status: "error", text: "run_shell 执行未通过" }],
    })} />);
    expect(screen.getByText("步骤校验未通过")).toBeTruthy();
  });

  it("仅有 quality（轨迹质量分属结果层）→ 算结果校验", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      checks: [{ tool: "run_shell", status: "ok", text: "run_shell 执行通过" }],
      quality: { plan: 80, steps: 70, final: 90, feedback: "" },
    })} />);
    expect(screen.getByText("结果校验通过")).toBeTruthy();
  });

  it("两层信号并存 → 展开明细按来源分组标注，各行不会被误读", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      progress: [
        { scope: "verify", text: "judge 分数过低", status: "error", key: "k1" },
        { scope: "verify", text: "校验通过", status: "ok", key: "k2" },
      ],
      checks: [{ tool: "run_shell", status: "ok", text: "run_shell 执行通过" }],
    })} />);
    expect(screen.getByText("结果校验通过")).toBeTruthy();    // 主行：以结果层为准
    fireEvent.click(screen.getByText("结果校验通过"));
    expect(screen.getByText("步骤校验")).toBeTruthy();        // 分组标题：步骤层明细
    expect(screen.getByText("结果校验")).toBeTruthy();        // 分组标题：交付门历史
    expect(screen.getByText("run_shell 执行通过")).toBeTruthy();
    expect(screen.getByText(/judge 分数过低/)).toBeTruthy();
  });
});

// applyEvent 对 quality 无效 JSON 的容错在 ChatView 内；此处验证组件对
// quality=null（解析失败后维持 null）时的健壮性：仅有 checks 信号照常渲染、不崩。
describe("VerifyBadge 容错", () => {
  it("quality 为 null（解析失败）时不崩，仅按其它信号渲染", () => {
    render(<VerifyBadge message={msg({
      status: "done",
      quality: null,
      checks: [{ tool: "run_python", status: "ok", text: "run_python 执行通过" }],
    })} />);
    // quality 解析失败 → 结果层无信号，降级为步骤校验（不因 quality 字段在场就算结果已校验）
    expect(screen.getByText("步骤校验通过")).toBeTruthy();
    fireEvent.click(screen.getByText("步骤校验通过"));
    expect(screen.getByText("run_python 执行通过")).toBeTruthy();
    // 无 quality 段：不应出现三层分标签
    expect(screen.queryByText(/拆分/)).toBeNull();
  });
});

describe("刷新后从 progress 重建质量分", () => {
  // quality 只在实时 SSE 时由 ChatView 赋值；走 ui_messages 加载历史时它是 undefined，
  // 但 _emit_quality 同时把同一份 JSON 写进了 progress 列 —— 徽章须能从那里还原
  const qJson = JSON.stringify({ plan: 80, steps: 70, final: 90, feedback: "拆分清楚" });

  it("无 message.quality 但 progress 里有 → 仍显示质量分（此前刷新即消失）", () => {
    render(<VerifyBadge message={msg({
      progress: [
        { scope: "verify", text: "校验通过", status: "ok" },
        { scope: "quality", text: qJson, status: "ok" },
      ],
    })} />);
    expect(screen.getByText("· 质量 90")).toBeTruthy();
  });

  it("展开后三层分与简评都还原", () => {
    render(<VerifyBadge message={msg({
      progress: [
        { scope: "verify", text: "校验通过", status: "ok" },
        { scope: "quality", text: qJson, status: "ok" },
      ],
    })} />);
    fireEvent.click(screen.getByText("结果校验通过"));
    expect(screen.getByText("拆分 80")).toBeTruthy();
    expect(screen.getByText("关键步 70")).toBeTruthy();
    expect(screen.getByText("最终 90")).toBeTruthy();
    expect(screen.getByText("拆分清楚")).toBeTruthy();
  });

  it("只有 quality、无 verify → 徽章仍渲染（不因缺 verify 而整个消失）", () => {
    render(<VerifyBadge message={msg({
      progress: [{ scope: "quality", text: qJson, status: "ok" }],
    })} />);
    expect(screen.getByText("· 质量 90")).toBeTruthy();
  });

  it("message.quality 优先于 progress（实时路径不被回退值覆盖）", () => {
    render(<VerifyBadge message={msg({
      quality: { plan: 10, steps: 10, final: 11, feedback: "实时的" },
      progress: [
        { scope: "verify", text: "校验通过", status: "ok" },
        { scope: "quality", text: qJson, status: "ok" },
      ],
    })} />);
    expect(screen.getByText("· 质量 11")).toBeTruthy();
  });

  it("progress 里的 quality 是脏 JSON → 忽略，不崩", () => {
    render(<VerifyBadge message={msg({
      progress: [
        { scope: "verify", text: "校验通过", status: "ok" },
        { scope: "quality", text: "{不是合法JSON", status: "ok" },
      ],
    })} />);
    expect(screen.getByText("结果校验通过")).toBeTruthy();
    expect(screen.queryByText(/质量/)).toBeNull();
  });
});

describe("刷新后从 progress 重建每步校验", () => {
  // checks 不是数据库列，是从 progress 派生的；ChatView 只在实时 SSE 时赋值，
  // 刷新走 ui_messages 加载则为空 —— 但 scope="check" 的条目一直在 progress 列里
  const ck = (text: string, status: "ok" | "error", tool: string) =>
    ({ scope: "check", text, status, key: `check:${tool}` });

  it("无 message.checks 但 progress 里有 → 步骤校验仍显示（此前刷新即消失）", () => {
    render(<VerifyBadge message={msg({
      progress: [ck("run_python 执行通过", "ok", "run_python"),
                 { scope: "verify", text: "校验通过", status: "ok" }],
    })} />);
    fireEvent.click(screen.getByText("结果校验通过"));
    expect(screen.getByText("run_python 执行通过")).toBeTruthy();
  });

  it("交付门关时刷新 → 主行按步骤校验降级，不谎称结果校验", () => {
    render(<VerifyBadge message={msg({
      progress: [ck("run_python 执行通过", "ok", "run_python")],
    })} />);
    expect(screen.getByText("步骤校验通过")).toBeTruthy();
  });

  it("刷新后步骤校验失败仍亮红", () => {
    render(<VerifyBadge message={msg({
      progress: [ck("run_python 执行未通过", "error", "run_python")],
    })} />);
    expect(screen.getByText("步骤校验未通过")).toBeTruthy();
  });

  it("同工具多条 → 合并为一行取最后一条（与实时逻辑一致）", () => {
    render(<VerifyBadge message={msg({
      progress: [ck("run_python 执行未通过", "error", "run_python"),
                 ck("run_python 执行通过", "ok", "run_python")],
    })} />);
    expect(screen.getByText("步骤校验通过")).toBeTruthy();       // 后来者覆盖
    fireEvent.click(screen.getByText("步骤校验通过"));
    expect(screen.queryByText("run_python 执行未通过")).toBeNull();
  });

  it("message.checks 优先于 progress（实时路径不被回退值覆盖）", () => {
    render(<VerifyBadge message={msg({
      checks: [{ tool: "run_python", status: "error", text: "实时的失败" }],
      progress: [ck("run_python 执行通过", "ok", "run_python")],
    })} />);
    expect(screen.getByText("步骤校验未通过")).toBeTruthy();
  });

  it("search_memory 命中仍被过滤（重建不绕过既有过滤）", () => {
    render(<VerifyBadge message={msg({
      progress: [ck("search_memory 命中 3 条", "ok", "search_memory")],
    })} />);
    expect(screen.queryByText("search_memory 命中 3 条")).toBeNull();
  });
});
