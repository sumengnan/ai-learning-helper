import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ChatView, fmtDuration } from "./ChatView";
import { GATE_OPEN_KEY } from "./VerifyBadge";
import { streamChat, attachChat, stopRun, sendDecision } from "../api/client";

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
  },
}));

describe("ChatView", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(streamChat).mockClear();
    vi.mocked(attachChat).mockClear();
    vi.mocked(stopRun).mockClear();
    vi.mocked(sendDecision).mockClear();
  });
  afterEach(() => cleanup());

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

  it("『思考模式』默认开并透传 think=true；关闭后持久化为 0", async () => {
    render(<ChatView conversationId="c1" initial={[]} />);
    expect((screen.getByLabelText("思考模式") as HTMLInputElement).checked).toBe(true);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    await waitFor(() => expect(vi.mocked(streamChat)).toHaveBeenCalled());
    const calls = vi.mocked(streamChat).mock.calls;
    expect(calls[calls.length - 1][6]).toBe(true);    // think 为第 7 个参数，默认开
    fireEvent.click(screen.getByLabelText("思考模式"));   // 关闭
    expect(localStorage.getItem("chat_think")).toBe("0");
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
      (_cid: string, _msg: string, _onEvent: (e: any) => void, signal?: AbortSignal) =>
        new Promise((_resolve, reject) => {
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
      (_cid: string, _msg: string, _onEvent: (e: any) => void, signal?: AbortSignal) =>
        new Promise((_resolve, reject) => {
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
        onEvent({ type: "Progress", data: { scope: "verify", text: "校验中…", status: "running", key: "k1" } });
        onEvent({ type: "Progress", data: { scope: "verify", text: "校验通过", status: "ok", key: "k1" } });
        onEvent({ type: "TextDelta", data: { text: "已核对" } });
        onEvent({ type: "TextDelta", data: { text: "的答案" } });
        onEvent({ type: "RunFinished", data: {} });
      });
    render(<ChatView conversationId="c1" initial={[]} />);
    fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
    fireEvent.click(screen.getByText("发送"));
    // 只渲染补发的终稿（进度里的"校验中…"不会混进气泡正文）
    await waitFor(() => expect(screen.getByText("已核对的答案")).toBeTruthy());
    // 校验块可见（展示工具默认开）
    expect(screen.getByText("校验")).toBeTruthy();
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
        progress: [{ scope: "verify", text: "校验中…", status: "running", key: "v0" }] },
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
