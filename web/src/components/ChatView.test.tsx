import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ChatView, fmtDuration } from "./ChatView";
import { GATE_OPEN_KEY } from "./VerifyBadge";
import { streamChat, attachChat, stopRun, sendDecision, api } from "../api/client";

// mock streamChat：依次回调 TextDelta "你" / TextDelta "好" / RunFinished
vi.mock("../api/client", () => ({
  streamChat: vi.fn(async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
    onEvent({ type: "TextDelta", data: { text: "你" } });
    onEvent({ type: "TextDelta", data: { text: "好" } });
    onEvent({ type: "RunFinished", data: {} });
  }),
  attachChat: vi.fn(async () => undefined),
  stopRun: vi.fn(async () => undefined),
  sendDecision: vi.fn(async () => undefined),
  api: {
    messages: vi.fn(async () => []),
    autotitle: vi.fn(async () => ({ title: null })),
    downloads: { save: vi.fn(async () => undefined) },
    models: vi.fn(async () => ({ main: "m", fast: "m", judge: "m", embedding: null, rerank: null })),
    exam: { status: vi.fn(async () => ({ active: false })) },
  },
}));

describe("ChatView", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(streamChat).mockClear();
    vi.mocked(attachChat).mockClear();
    vi.mocked(stopRun).mockClear();
    vi.mocked(sendDecision).mockClear();
    vi.mocked(api.messages).mockClear();
    vi.mocked(api.messages).mockResolvedValue([] as any);
  });
  afterEach(() => cleanup());

  it("首页推荐问题被 StrictMode 假卸载掐断（还没拿到 X-Run-Id）→ 回后端捞句柄接回", async () => {
    // 用户实测场景：新建对话/点首页推荐问题 → autoSend 在挂载 effect 里发起，StrictMode 的
    // 假卸载立刻 abort 掉它。此时响应头还没到、onRunId 从未回调 → 本地没有 run 句柄。
    // 后端 start_turn 已把带 run_id 的 streaming 占位落了库，须据此接回；否则既不接回也不
    // 落终态，气泡永远停在空的「…」，而后台任务照跑到完并落库——只有刷新才看得见。
    vi.mocked(streamChat).mockImplementationOnce(
      (_cid: string, _msg: string, _onEvent: (e: any) => void, signal?: AbortSignal) =>
        new Promise((_resolve, reject) => {          // 注意：全程不调用 onRunId
          signal?.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
        }));
    vi.mocked(api.messages).mockResolvedValue([
      { role: "user", content: "推荐问题" },
      { role: "assistant", content: "", status: "streaming", run_id: "R7" },
    ] as any);
    vi.mocked(attachChat).mockImplementationOnce(async (_rid: string, onEvent: (e: any) => void) => {
      onEvent({ type: "TextDelta", data: { text: "后台跑完的答案" } });
      onEvent({ type: "RunFinished", data: {} });
    });
    render(
      <React.StrictMode>
        <ChatView conversationId="c1" initial={[]} autoSend="推荐问题" />
      </React.StrictMode>,
    );
    await waitFor(() => expect(vi.mocked(attachChat)).toHaveBeenCalledWith(
      "R7", expect.anything(), expect.anything()));
    await waitFor(() => expect(screen.getByText("后台跑完的答案")).toBeTruthy());
    expect(screen.queryByText("…")).toBeNull();       // 不再停在空的「…」
  });

  it("流被掐断且后端并无在途 run → 落可重试终态，不停在空的「…」", async () => {
    // 请求压根没到后端（无占位可捞）：此时必须收尾成失败态，而不是把气泡吊死在「…」。
    // 刻意全程不回调 onRunId（本地始终没有 run 句柄），复现"流被掐断且无在途 run"。
    vi.mocked(streamChat).mockImplementationOnce(
      (_cid: string, _msg: string, _onEvent: (e: any) => void, signal?: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
        }));
    vi.mocked(api.messages).mockResolvedValue([] as any);   // 后端没有在途 run
    render(
      <React.StrictMode>
        <ChatView conversationId="c1" initial={[]} autoSend="推荐问题" />
      </React.StrictMode>,
    );
    await waitFor(() => expect(screen.getByText("（连接中断，请重试）")).toBeTruthy());
    expect(vi.mocked(attachChat)).not.toHaveBeenCalled();
  });

  it("刷新后：最后一条助手消息 streaming → 自动接回续流至完成", async () => {
    vi.mocked(attachChat).mockImplementationOnce(
      async (_rid: string, onEvent: (e: any) => void) => {
        onEvent({ type: "TextDelta", data: { text: "续上" } });
        onEvent({ type: "TextDelta", data: { text: "了" } });
        onEvent({ type: "RunFinished", data: {} });
      });
    render(<ChatView conversationId="c1" initial={[
      { role: "user", content: "问题" },
      { role: "assistant", content: "断点前部分", status: "streaming", runId: "R1" },
    ]} />);
    // 自动用该 run_id 接回
    await waitFor(() => expect(vi.mocked(attachChat)).toHaveBeenCalledWith(
      "R1", expect.anything(), expect.anything()));
    // 回放重建后气泡为续上后的完整内容（清空占位再由回放重建，不与"断点前部分"重复）
    await waitFor(() => expect(screen.getByText("续上了")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("已完成")).toBeTruthy());
  });

  it("StrictMode 假卸载打断首次接回 → 自动重连续流，不误判为「已停止」", async () => {
    // 第一次 attach 被 StrictMode 卸载清理 abort；第二次应自动重连并续流至完成
    vi.mocked(attachChat)
      .mockImplementationOnce((_rid: string, _onEvent: (e: any) => void, signal?: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
        }))
      .mockImplementationOnce(async (_rid: string, onEvent: (e: any) => void) => {
        onEvent({ type: "TextDelta", data: { text: "续上了" } });
        onEvent({ type: "RunFinished", data: {} });
      });
    render(
      <React.StrictMode>
        <ChatView conversationId="c1" initial={[
          { role: "user", content: "问题" },
          { role: "assistant", content: "断点前", status: "streaming", runId: "R1" },
        ]} />
      </React.StrictMode>,
    );
    // 重连后续流到完成，展示续上内容与「已完成」，且从不停留在「已停止」
    await waitFor(() => expect(screen.getByText("续上了")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("已完成")).toBeTruthy());
    expect(screen.queryByText("已停止")).toBeNull();
    expect(vi.mocked(attachChat).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("非 streaming 的历史消息不触发接回", async () => {
    render(<ChatView conversationId="c1" initial={[
      { role: "user", content: "q" },
      { role: "assistant", content: "已完成的答案", status: "done", runId: "R0" },
    ]} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(vi.mocked(attachChat)).not.toHaveBeenCalled();
  });

  it("StrictMode 下 TextDelta 累加不重复（不可变更新）", async () => {
    render(
      <React.StrictMode>
        <ChatView conversationId="c1" initial={[]} />
      </React.StrictMode>,
    );
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    // 助手气泡最终应为 "你好"，而不是 StrictMode 重复应用导致的 "你你好好"
    await waitFor(() => expect(screen.getByText("你好")).toBeTruthy());
    expect(screen.queryByText("你你好好")).toBeNull();
  });

  it("AI 回复中封住输入/开关/附件，只留停止按钮", async () => {
    // streamChat 挂住（回一个 TextDelta 后不结束）→ busy 保持 true
    vi.mocked(streamChat).mockImplementationOnce(
      (_c: string, _m: string, onEvent: (e: any) => void) =>
        new Promise<void>(() => { onEvent({ type: "TextDelta", data: { text: "答" } }); }));
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() => expect(screen.getByText("停止")).toBeTruthy());
    // 输入框禁用（占位变为提示文案）
    expect((screen.getByPlaceholderText(/AI 正在回复/) as HTMLTextAreaElement).disabled).toBe(true);
    // 开关禁用
    expect((screen.getByLabelText("思考模式") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("结果校验") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("展示工具调用和 Token") as HTMLInputElement).disabled).toBe(true);
    // 附件按钮禁用
    expect((screen.getByLabelText("上传文件") as HTMLButtonElement).disabled).toBe(true);
  });

  it("拿到 run 句柄前「停止」禁用；句柄到达后启用并调 stopRun", async () => {
    // 未拿到 X-Run-Id 前停止只能断本地流、杀不掉后端任务，故按钮先禁用；句柄到达后再启用。
    let fireRunId: ((rid: string) => void) | null = null;
    vi.mocked(streamChat).mockImplementationOnce(
      (_c: string, _m: string, onEvent: (e: any) => void,
       _sig?: AbortSignal, _att?: string[], onRunId?: (rid: string) => void) =>
        new Promise<void>(() => {                 // 全程挂住，保持 busy
          onEvent({ type: "TextDelta", data: { text: "答" } });
          fireRunId = onRunId ?? null;            // 句柄暂不回调，稍后手动触发
        }));
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    const btn = (await screen.findByText("停止")).closest("button") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);              // 句柄未到 → 禁用
    fireEvent.click(btn);                          // 禁用态点击无效
    expect(vi.mocked(stopRun)).not.toHaveBeenCalled();
    act(() => fireRunId?.("R1"));                 // 句柄到达
    await waitFor(() => expect(btn.disabled).toBe(false));   // → 启用
    fireEvent.click(btn);
    expect(vi.mocked(stopRun)).toHaveBeenCalledWith("R1");
  });

  it("路由徽章：本轮走的是简单直答还是多步规划，用户看得见", async () => {
    // 走编排器会出「任务步骤」块，走单循环则一个块都没有——此前只能靠「有没有块」反推，
    // triage 误判（该拆步却判了 simple）便无从察觉。
    vi.mocked(streamChat).mockImplementationOnce(
      async (_c: string, _m: string, onEvent: (e: any) => void) => {
        onEvent({ type: "Progress", data: { scope: "route", text: "简单直答",
                                            key: "route", detail: { mode: "simple" } } });
        onEvent({ type: "TextDelta", data: { text: "好" } });
        onEvent({ type: "RunFinished", data: {} });
      });
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "你好" } });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() => expect(screen.getByText("简单直答")).toBeTruthy());

    // 关掉「展示工具调用和 Token」→ 徽章跟着隐藏，与技能块/沙箱块同一档可见性
    fireEvent.click(screen.getByLabelText("展示工具调用和 Token"));
    await waitFor(() => expect(screen.queryByText("简单直答")).toBeNull());
  });

  it("开关默认值：展示工具/Token 开", () => {
    render(<ChatView conversationId="c1" initial={[]} />);
    expect((screen.getByLabelText("展示工具调用和 Token") as HTMLInputElement).checked).toBe(true);
  });

  it("空产出（仅 RunFinished、无 TextDelta）→ 标失败并提示，不显示「已完成」", async () => {
    // 模型空回复：流正常结束但没有任何正文。前端须视为失败，避免"…"+「已完成」的误导
    vi.mocked(streamChat).mockImplementationOnce(
      async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
        onEvent({ type: "RunFinished", data: {} });
      });
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() => expect(screen.getByText("（本轮未产出内容，请重试）")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("回复失败")).toBeTruthy());
    expect(screen.queryByText("已完成")).toBeNull();
  });

  it("危险命令 ApprovalRequired → 弹窗渲染，批准后回传 sendDecision", async () => {
    vi.mocked(streamChat).mockImplementationOnce(
      async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
        onEvent({ type: "RunStarted", data: { run_id: "run-1" } });
        onEvent({ type: "ApprovalRequired", data: {
          approval_id: "a1", tool: "run_shell", command: "rm -rf /workspace", reason: "递归删除",
        } });
      });
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    // 弹窗出现，展示命令与原因
    await waitFor(() => expect(screen.getByText("rm -rf /workspace")).toBeTruthy());
    expect(screen.getByText(/递归删除/)).toBeTruthy();
    fireEvent.click(screen.getByText("批准执行"));
    await waitFor(() => expect(vi.mocked(sendDecision)).toHaveBeenCalledWith("run-1", "a1", true));
  });

  it("『思考模式』默认关并透传 think=false；开启后持久化为 1", async () => {
    render(<ChatView conversationId="c1" initial={[]} />);
    expect((screen.getByLabelText("思考模式") as HTMLInputElement).checked).toBe(false);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() => expect(vi.mocked(streamChat)).toHaveBeenCalled());
    const calls = vi.mocked(streamChat).mock.calls;
    expect(calls[calls.length - 1][6]).toBe(false);   // think 为第 7 个参数，默认关
    fireEvent.click(screen.getByLabelText("思考模式"));   // 开启
    expect(localStorage.getItem("chat_think")).toBe("1");
  });

  it("不再有『考试答错自动保存错题集』开关（答错必存，无从关闭）", async () => {
    render(<ChatView conversationId="c1" initial={[]} />);
    expect(screen.queryByLabelText("考试答错自动保存错题集")).toBeNull();
  });

  it("Progress scope=plan → 渲染任务步骤清单", async () => {
    vi.mocked(streamChat).mockImplementationOnce(
      async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
        onEvent({ type: "RunStarted", data: { run_id: "r1" } });
        onEvent({ type: "Progress", data: {
          scope: "plan", key: "plan",
          text: JSON.stringify([{ title: "第一步查资料", status: "running" }]),
        } });
        onEvent({ type: "TextDelta", data: { text: "好" } });
        onEvent({ type: "RunFinished", data: {} });
      });
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    // 现同时出现在标题预览与步骤清单两处（PlanBlock 新增「当前步骤」预览）
    await waitFor(() => expect(screen.getAllByText("第一步查资料").length).toBeGreaterThanOrEqual(1));
  });

  it("fmtDuration 格式化为「几分几秒」", () => {
    expect(fmtDuration(0)).toBe("0 秒");
    expect(fmtDuration(42_000)).toBe("42 秒");
    expect(fmtDuration(65_000)).toBe("1 分 5 秒");
    expect(fmtDuration(3_600_000)).toBe("60 分 0 秒");
  });

  it("完成后显示耗时与 tokens", async () => {
    vi.mocked(streamChat).mockImplementationOnce(
      async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
        onEvent({ type: "RunStarted", data: { run_id: "r1" } });
        onEvent({ type: "TextDelta", data: { text: "好" } });
        onEvent({ type: "ModelUsage", data: { usage: { total: 123 }, cost_usd: null } });
        onEvent({ type: "RunFinished", data: {} });
      });
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    // 耗时药丸只显示时长（时钟图标表达「耗时」语义）；tokens 药丸带 tokens 字样
    await waitFor(() => expect(screen.getByText(/\d+\s*秒/)).toBeTruthy());
    expect(screen.getByText("tokens")).toBeTruthy();
  });

  it("刷新还原：已完成的历史消息带持久化的耗时与 tokens 时照常显示", () => {
    render(<ChatView conversationId="c1" initial={[
      { role: "user", content: "问题" },
      { role: "assistant", content: "答案", status: "done",
        usage: { tokens: 1234, cost: 0.02 }, elapsedMs: 65_000 },
    ]} />);
    expect(screen.getByText("已完成")).toBeTruthy();
    expect(screen.getByText("1 分 5 秒")).toBeTruthy();   // 耗时来自持久化
    expect(screen.getByText("tokens")).toBeTruthy();
  });

  it("生成中显示「停止」按钮，点击后中断并恢复「发送」", async () => {
    vi.mocked(streamChat).mockImplementationOnce(
      (_cid: string, _msg: string, _onEvent: (e: any) => void, signal?: AbortSignal,
       _att?: string[], onRunId?: (rid: string) => void) =>
        new Promise((_resolve, reject) => {
          onRunId?.("R-stop");   // 提供 run 句柄，使「停止」按钮可用（否则新逻辑下禁用）
          signal?.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
        }));
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    const stopBtn = await screen.findByText("停止");   // 生成中：发送→停止
    fireEvent.click(stopBtn);
    await waitFor(() => expect(screen.getByText("发送")).toBeTruthy());  // 中断后恢复
  });

  it("回复完成后显示「已完成」状态", async () => {
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() => expect(screen.getByText("你好")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("已完成")).toBeTruthy());
  });

  it("回复出错（RunError）后显示「回复失败」状态", async () => {
    vi.mocked(streamChat).mockImplementationOnce(
      async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
        onEvent({ type: "TextDelta", data: { text: "部分" } });
        onEvent({ type: "RunError", data: { error: "boom" } });
      });
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() => expect(screen.getByText("回复失败")).toBeTruthy());
  });

  it("用户停止后显示「已停止」状态", async () => {
    vi.mocked(streamChat).mockImplementationOnce(
      (_cid: string, _msg: string, _onEvent: (e: any) => void, signal?: AbortSignal,
       _att?: string[], onRunId?: (rid: string) => void) =>
        new Promise((_resolve, reject) => {
          onRunId?.("R-stop");   // 提供 run 句柄，使「停止」按钮可用（否则新逻辑下禁用）
          signal?.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
        }));
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    fireEvent.click(await screen.findByText("停止"));
    await waitFor(() => expect(screen.getByText("已停止")).toBeTruthy());
  });

  it("交付门：先收到校验进度、后收到终稿 TextDelta → 只渲染终稿、进度可见", async () => {
    // 模拟服务端交付门顺序：校验中 → 校验通过 → 终稿分片 → 合成 RunFinished
    vi.mocked(streamChat).mockImplementationOnce(
      async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
        onEvent({ type: "Progress", data: { scope: "verify", text: "结果校验中…", status: "running", key: "k1" } });
        onEvent({ type: "Progress", data: { scope: "verify", text: "校验通过", status: "ok", key: "k1" } });
        onEvent({ type: "TextDelta", data: { text: "已核对" } });
        onEvent({ type: "TextDelta", data: { text: "的答案" } });
        onEvent({ type: "RunFinished", data: {} });
      });
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    // 只渲染补发的终稿（进度里的"结果校验中…"不会混进气泡正文）
    await waitFor(() => expect(screen.getByText("已核对的答案")).toBeTruthy());
    // 校验块可见（展示工具默认开）
    expect(screen.getByText("校验")).toBeTruthy();
  });

  it("交付门重答：初版流式 → reset 清屏 → 修正版流式，最终只见修正版", async () => {
    vi.mocked(streamChat).mockImplementationOnce(
      async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
        // 初版逐字流给用户
        onEvent({ type: "TextDelta", data: { text: "初版" } });
        onEvent({ type: "TextDelta", data: { text: "答案" } });
        // 未过校验 → 重答：清屏
        onEvent({ type: "Progress", data: { scope: "verify", text: "重答中…", status: "running" } });
        onEvent({ type: "Progress", data: { scope: "reset", text: "" } });
        // 修正版从头流式
        onEvent({ type: "TextDelta", data: { text: "修正" } });
        onEvent({ type: "TextDelta", data: { text: "版" } });
        onEvent({ type: "Progress", data: { scope: "verify", text: "校验通过", status: "ok", key: "k" } });
        onEvent({ type: "RunFinished", data: {} });
      });
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    // 收尾后气泡只剩修正版：初版被 reset 清掉，不残留
    await waitFor(() => expect(screen.getByText("修正版")).toBeTruthy());
    expect(screen.queryByText(/初版答案/)).toBeNull();
    expect(screen.queryByText(/初版答案修正版/)).toBeNull();   // 没拼接残留
  });

  it("『展示数据来源和引用』开关：默认展示来源，关闭后隐藏（issue 4）", async () => {
    render(
      <MemoryRouter>
        <ChatView conversationId="c1" initial={[
          { role: "user", content: "光合作用?" },
          { role: "assistant", content: "见知识库[1]。", status: "done",
            sources: [{ index: 1, type: "knowledge", label: "bio.pdf" }] },
        ]} />
      </MemoryRouter>);
    // 默认开：来源清单可见
    expect(screen.getByText(/参考来源/)).toBeTruthy();
    // 关闭开关 → 隐藏
    fireEvent.click(screen.getByLabelText("展示数据来源和引用"));
    await waitFor(() => expect(screen.queryByText(/参考来源/)).toBeNull());
  });

  it("思考模式：ReasoningDelta → 展示思考过程与「思考中」提示", async () => {
    vi.mocked(streamChat).mockImplementationOnce(
      async (_cid, _msg, onEvent: (e: any) => void) => {
        onEvent({ type: "ReasoningDelta", data: { text: "让我先分析一下" } });
        onEvent({ type: "TextDelta", data: { text: "答案" } });
        onEvent({ type: "RunFinished", data: {} });
      });
    render(<MemoryRouter><ChatView conversationId="c1" initial={[]} /></MemoryRouter>);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() => expect(screen.getByText(/让我先分析一下/)).toBeTruthy());
    // 完成后头部为「思考过程」（生成中为「思考中…」）——不与「思考模式」开关标签冲突
    expect(screen.getByText(/思考过程/)).toBeTruthy();
  });

  it("生成文件：save_download step → 渲染下载按钮，点击调用 downloads.save", async () => {
    const { api } = await import("../api/client");
    render(<MemoryRouter><ChatView conversationId="c1" initial={[
      { role: "user", content: "导出报告" },
      { role: "assistant", content: "已导出。", status: "done", steps: [
        { tool: "save_download", args: { filename: "报告.md" },
          result: "已保存到下载区：报告.md（10 字节）。〔下载ID:abc123〕" },
      ] },
    ]} /></MemoryRouter>);
    const btn = screen.getByRole("button", { name: /报告\.md/ });
    expect(btn).toBeTruthy();
    fireEvent.click(btn);
    await waitFor(() => expect((api as any).downloads.save)
      .toHaveBeenCalledWith("abc123", "报告.md"));
  });

  // 开了校验门的轮次，交付前不显示文件：校验不过会重答，届时服务端会清掉本轮产物，
  // 提前显示等于给用户一个马上失效的下载按钮。
  const _dlStep = {
    tool: "save_download", args: { filename: "报告.md" },
    result: "已保存到下载区：报告.md（10 字节）。〔下载ID:abc123〕",
  };

  it("校验门轮次交付前不显示生成的文件", async () => {
    render(<MemoryRouter><ChatView conversationId="c1" initial={[
      { role: "user", content: "导出报告" },
      { role: "assistant", content: "", status: "streaming", steps: [_dlStep],
        progress: [{ scope: "verify", text: "结果校验中…", status: "running", key: "v0" }] },
    ]} /></MemoryRouter>);
    expect(screen.queryByRole("button", { name: /报告\.md/ })).toBeNull();
    expect(screen.queryByText("生成的文件：")).toBeNull();
  });

  it("校验门轮次：工具已跑完但还没发校验事件时也不显示（否则会闪一下）", async () => {
    // 服务端在轮次开头就发门已开信号，故此刻 progress 里已有它、文件仍被盖住
    render(<MemoryRouter><ChatView conversationId="c1" initial={[
      { role: "user", content: "导出报告" },
      { role: "assistant", content: "", status: "streaming", steps: [_dlStep],
        progress: [{ scope: "verify", text: "生成中…", status: "running", key: GATE_OPEN_KEY }] },
    ]} /></MemoryRouter>);
    expect(screen.queryByRole("button", { name: /报告\.md/ })).toBeNull();
  });

  it("门已开信号盖住文件，但不把校验徽章一起带出来（两者共用 verify 通道，别再耦合）", async () => {
    render(<MemoryRouter><ChatView conversationId="c1" initial={[
      { role: "user", content: "导出报告" },
      { role: "assistant", content: "写着…", status: "streaming", steps: [_dlStep],
        progress: [{ scope: "verify", text: "生成中…", status: "running", key: GATE_OPEN_KEY }] },
    ]} /></MemoryRouter>);
    expect(screen.queryByRole("button", { name: /报告\.md/ })).toBeNull();   // 文件仍盖住
    expect(screen.queryByText("校验")).toBeNull();                            // 徽章尚未出现
  });

  it("校验通过交付后显示生成的文件", async () => {
    render(<MemoryRouter><ChatView conversationId="c1" initial={[
      { role: "user", content: "导出报告" },
      { role: "assistant", content: "已导出。", status: "done", steps: [_dlStep],
        progress: [{ scope: "verify", text: "校验通过", status: "ok", key: "v0" }] },
    ]} /></MemoryRouter>);
    expect(screen.getByRole("button", { name: /报告\.md/ })).toBeTruthy();
  });

  it("没开校验门时，生成中就显示文件（不改原有行为）", async () => {
    render(<MemoryRouter><ChatView conversationId="c1" initial={[
      { role: "user", content: "导出报告" },
      { role: "assistant", content: "写着…", status: "streaming", steps: [_dlStep] },
    ]} /></MemoryRouter>);
    expect(screen.getByRole("button", { name: /报告\.md/ })).toBeTruthy();
  });

  it("被清理的产物不再渲染按钮（重答后旧文件已被服务端删掉）", async () => {
    // 交付后 steps 里有两版：旧版标记已被 scope=purged 事件剔掉，只剩新版
    render(<MemoryRouter><ChatView conversationId="c1" initial={[
      { role: "user", content: "导出报告" },
      { role: "assistant", content: "已导出。", status: "done", steps: [
        { tool: "save_download", args: { filename: "旧版.md" },
          result: "已保存到下载区：旧版.md（10 字节）。\n（该版本未通过校验，此产物已作废删除）" },
        { tool: "save_download", args: { filename: "报告.md" },
          result: "已保存到下载区：报告.md（10 字节）。〔下载ID:abc123〕" },
      ] },
    ]} /></MemoryRouter>);
    expect(screen.getByRole("button", { name: /报告\.md/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /旧版\.md/ })).toBeNull();
  });
});

describe("计划来源区分（ReAct 清单 vs 编排器计划）", () => {
  // 本文件没配自动 cleanup：不清会残留上一个用例的 DOM，导致「隐藏」断言恒失败
  beforeEach(() => cleanup());
  const toolStep = { tool: "search_knowledge", args: { query: "x" }, result: "[1] 命中" };

  it("ReAct 清单（步骤无 id）到达时，扁平工具块必须保留", async () => {
    // 回归：两种计划同为 scope=plan。若只看 scope 就隐藏工具块，模型在简单直答里调
    // update_plan 发的清单会把工具块吞掉，而该清单又挂不了明细（无 id）——
    // 用户看到工具调用闪现后消失，点开步骤空空如也。
    render(<ChatView conversationId="c1" initial={[{
      key: "m1", role: "assistant", content: "答", status: "done",
      steps: [toolStep],
      progress: [{ scope: "plan", text: JSON.stringify([{ title: "查资料", status: "done" }]) }],
    } as any]} />);
    expect((await screen.findAllByText(/search_knowledge/)).length).toBeGreaterThan(0);
  });

  it("编排器计划（步骤带 id）到达时，扁平工具块隐藏（明细已在计划步下）", async () => {
    render(<ChatView conversationId="c2" initial={[{
      key: "m2", role: "assistant", content: "答", status: "done",
      steps: [toolStep],
      progress: [{ scope: "plan",
                   text: JSON.stringify([{ id: "s1", title: "查资料", status: "done" }]) }],
    } as any]} />);
    expect((await screen.findAllByText(/查资料/)).length).toBeGreaterThan(0);
    expect(screen.queryAllByText(/search_knowledge/)).toHaveLength(0);
  });
});
