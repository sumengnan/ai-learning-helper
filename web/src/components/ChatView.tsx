import { useEffect, useRef, useState } from "react";
import {
  Box, Paper, TextField, Button, Typography, FormControlLabel, Switch,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
  IconButton, Tooltip, Snackbar, Alert, CircularProgress,
} from "@mui/material";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutlineOutlined";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import { motion } from "framer-motion";
import type { ChatMessage } from "../types";
import { streamChat, attachChat, stopRun, sendDecision, api } from "../api/client";
import { AgentProgress } from "./AgentProgress";
import { SourceList } from "./SourceList";
import { linkifyCitations, citeId } from "./citations";
import { EmptyHint } from "./EmptyHint";
import { ProgressBlock } from "./ProgressBlock";
import { PlanBlock } from "./PlanBlock";
import { Markdown } from "./Markdown";
import { RollingNumber } from "./RollingNumber";
import { AttachmentChips, type AttachmentItem } from "./Attachments";
import { bubbleVariants } from "./motion";

const MotionBox = motion(Box);

const SHOW_TOOLS_KEY = "chat_show_tools";
const SAVE_WRONG_KEY = "chat_save_wrong";
const MAX_ATTACHMENTS = 10;
const MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024;  // 100MB
const readBool = (k: string, dflt: boolean) => {
  const v = localStorage.getItem(k);
  return v === null ? dflt : v === "1";
};

// 等待 AI 回复时的“正在输入”三点动画（framer-motion 循环，风格与全站统一）
function TypingDots() {
  return (
    <Box sx={{ display: "flex", gap: 0.6, alignItems: "center", py: 0.75 }}>
      {[0, 1, 2].map((i) => (
        <MotionBox
          key={i}
          sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "text.secondary" }}
          animate={{ scale: [0.6, 1, 0.6], opacity: [0.3, 1, 0.3] }}
          transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut", delay: i * 0.15 }}
        />
      ))}
    </Box>
  );
}

// 耗时格式化为「几分几秒」；不足 1 分只显示秒
export function fmtDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total} 秒`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m} 分 ${s} 秒`;
}

// 生成中的实时耗时：每秒自增，从 startedAt 计到当下
function RunTimer({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return <span>耗时 {fmtDuration(now - startedAt)}</span>;
}

// 内容已开始但仍在生成时的底部活跃指示：即使数据暂停（如工具执行中）也表明「仍在处理」，
// 避免看起来卡住。
function StreamingHint() {
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mt: 0.5 }}>
      <CircularProgress size={12} thickness={5} />
      <Typography variant="caption" color="text.secondary">生成中…</Typography>
    </Box>
  );
}

// 助手回复的终态状态行：完成 / 失败 / 已停止 / 已中断
function ReplyStatus({ kind }: { kind: "done" | "error" | "stopped" | "interrupted" }) {
  const map = {
    done: { icon: <CheckCircleIcon sx={{ fontSize: 14 }} color="success" />, text: "已完成", color: "success.main" },
    error: { icon: <ErrorOutlineIcon sx={{ fontSize: 14 }} color="error" />, text: "回复失败", color: "error.main" },
    stopped: { icon: <StopCircleIcon sx={{ fontSize: 14 }} color="disabled" />, text: "已停止", color: "text.disabled" },
    interrupted: { icon: <ErrorOutlineIcon sx={{ fontSize: 14 }} color="warning" />, text: "已中断（服务重启）", color: "warning.main" },
  }[kind];
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mt: 0.5 }}>
      {map.icon}
      <Typography variant="caption" sx={{ color: map.color }}>{map.text}</Typography>
    </Box>
  );
}

export function ChatView({ conversationId, initial, autoSend }:
  { conversationId: string; initial: ChatMessage[]; autoSend?: string | null }) {
  const [messages, setMessages] = useState<ChatMessage[]>(initial);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [showTools, setShowTools] = useState(() => readBool(SHOW_TOOLS_KEY, true));
  const [saveWrong, setSaveWrong] = useState(() => readBool(SAVE_WRONG_KEY, false));
  const abortRef = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  // 危险命令人工确认：run_id 来自 RunStarted 事件，用于拼回传 URL
  const runIdRef = useRef<string | null>(null);
  // 本轮 turn 句柄（来自 X-Run-Id / 接回的 runId），用于 stop 与刷新后接回
  const turnRunIdRef = useRef<string | null>(null);
  const [approval, setApproval] = useState<
    { approvalId: string; command: string; reason: string } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const saveWrongRef = useRef(saveWrong);
  saveWrongRef.current = saveWrong;
  // 待发附件（发送前可增删）；含本地 File 供即时预览、上传状态。
  const [pending, setPending] = useState<AttachmentItem[]>([]);
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [flashId, setFlashId] = useState<string | null>(null);
  const flashTimer = useRef<number | undefined>(undefined);

  // 点击正文 [n] 角标：滚动到对应来源并短暂高亮
  const scrollToCite = (msgKey: string, n: number) => {
    const id = citeId(msgKey, n);
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "center" });
    setFlashId(id);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlashId(null), 1400);
  };

  const patchPending = (tmpId: string, patch: Partial<AttachmentItem> | null) =>
    setPending((ps) => patch === null
      ? ps.filter((p) => p.id !== tmpId)
      : ps.map((p) => (p.id === tmpId ? { ...p, ...patch } : p)));

  async function addFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0) return;
    for (const file of list) {
      if (pendingRef.current.length >= MAX_ATTACHMENTS) {
        setErr(`最多上传 ${MAX_ATTACHMENTS} 个附件`); break;
      }
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setErr(`「${file.name}」超过 100MB，未添加`); continue;
      }
      const tmpId = crypto.randomUUID();
      const item: AttachmentItem = {
        id: tmpId, filename: file.name, size: file.size,
        content_type: file.type || "application/octet-stream", file, status: "uploading",
      };
      setPending((ps) => [...ps, item]);
      try {
        const meta = await api.attachments.upload(conversationId, file);
        // 用服务端返回的真实 id 替换临时 id，保留本地 File 供预览
        patchPending(tmpId, { id: meta.id, size: meta.size,
          content_type: meta.content_type, status: undefined });
      } catch (e: any) {
        patchPending(tmpId, { status: "error", error: String(e?.message ?? e) });
        setErr(`「${file.name}」上传失败`);
      }
    }
  }

  async function removePending(item: AttachmentItem) {
    setPending((ps) => ps.filter((p) => p.id !== item.id));
    if (item.status !== "error" && item.status !== "uploading") {
      try { await api.attachments.remove(item.id); } catch { /* 已发送前删除，失败可忽略 */ }
    }
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragOver(false);
    if (e.dataTransfer.files?.length) void addFiles(e.dataTransfer.files);
  };
  const onPaste = (e: React.ClipboardEvent) => {
    const files = e.clipboardData?.files;
    if (files && files.length > 0) { e.preventDefault(); void addFiles(files); }
  };

  const toggleShowTools = (v: boolean) => {
    setShowTools(v); localStorage.setItem(SHOW_TOOLS_KEY, v ? "1" : "0");
  };
  const toggleSaveWrong = (v: boolean) => {
    setSaveWrong(v); localStorage.setItem(SAVE_WRONG_KEY, v ? "1" : "0");
  };

  // 卸载（含 App 用 key={activeId} 切换对话触发 remount）时取消在途流
  useEffect(() => () => abortRef.current?.abort(), []);

  // 跟随滚动：AI 回复流式更新时自动滚到底部；用户主动上滑离开底部则暂停跟随
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages]);
  const onScroll = () => {
    const el = scrollRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const upd = (fn: (a: ChatMessage) => void) =>
    setMessages((m) => {
      const copy = [...m];
      const last = { ...copy[copy.length - 1] };
      if (last.steps) last.steps = last.steps.map((s) => ({ ...s }));
      if (last.progress) last.progress = [...last.progress];
      fn(last);
      copy[copy.length - 1] = last;
      return copy;
    });

  // 把一个 SSE 事件应用到最后一条（助手）消息上；RunError 时调 markError。
  const applyEvent = (e: any, markError: () => void) => {
    if (e.type === "TextDelta") upd((a) => { a.content += e.data.text; });
    else if (e.type === "ToolStarted") upd((a) => a.steps!.push({ tool: e.data.tool_call.name, args: e.data.tool_call.arguments }));
    else if (e.type === "ToolFinished") upd((a) => {
      const s = a.steps![a.steps!.length - 1];
      if (s) { s.result = e.data.result.content; s.isError = e.data.result.is_error; }
    });
    else if (e.type === "ModelUsage") upd((a) => { a.usage = { tokens: e.data.usage.total, cost: e.data.cost_usd }; });
    // 来源借 Progress 通道传（scope=sources，text 为 JSON）：特判解析成 sources，不入 progress 列
    else if (e.type === "Progress" && e.data.scope === "sources") upd((a) => {
      try { a.sources = JSON.parse(e.data.text); } catch { /* 忽略坏 JSON */ }
    });
    else if (e.type === "Progress") upd((a) => { (a.progress ||= []).push({ scope: e.data.scope, text: e.data.text, status: e.data.status, key: e.data.key, agent: e.data.agent }); });
    else if (e.type === "RunStarted") runIdRef.current = e.data.run_id;
    else if (e.type === "ApprovalRequired") setApproval({ approvalId: e.data.approval_id, command: e.data.command, reason: e.data.reason });
    else if (e.type === "ApprovalResolved") setApproval(null);
    else if (e.type === "RunError") { upd((a) => { a.content += `\n[出错] ${e.data.error}`; }); markError(); }
  };

  async function send(text?: string) {
    const msg = (text ?? input).trim();
    // 仅取上传成功的附件（排除上传中/失败）
    const ready = pendingRef.current.filter((p) => !p.status);
    if ((!msg && ready.length === 0) || busyRef.current) return;
    if (pendingRef.current.some((p) => p.status === "uploading")) {
      setErr("附件仍在上传中，请稍候"); return;
    }
    const attachments = ready.map((p) => ({
      id: p.id, filename: p.filename, size: p.size, content_type: p.content_type }));
    const userMsg: ChatMessage = { role: "user", content: msg,
      attachments: attachments.length ? attachments : undefined };
    const assistant: ChatMessage = { role: "assistant", content: "", steps: [], status: "streaming",
      startedAt: Date.now() };
    setMessages((m) => [...m, userMsg, assistant]);
    let outcome: "done" | "error" | "stopped" = "done";
    setInput(""); setPending([]); setBusy(true); busyRef.current = true;
    const controller = new AbortController();
    abortRef.current = controller;
    const onEvent = (e: any) => applyEvent(e, () => { outcome = "error"; });
    try {
      await streamChat(conversationId, msg, onEvent, controller.signal, saveWrongRef.current,
        attachments.map((a) => a.id),
        (rid) => { turnRunIdRef.current = rid; upd((a) => { a.runId = rid; }); });
    } catch (err: any) {
      if (err?.name === "AbortError") outcome = "stopped";
      else { upd((a) => { a.content += `\n[连接失败] ${err}`; }); outcome = "error"; }
    } finally {
      upd((a) => {   // 收尾状态：完成/失败/已停止；冻结本轮耗时
        a.status = outcome;
        if (a.startedAt != null && a.elapsedMs == null) a.elapsedMs = Date.now() - a.startedAt;
      });
      setBusy(false); busyRef.current = false;
    }
  }

  // 停止本轮生成：显式取消后端后台任务（断开已不再取消它）+ 断开本地流。
  function stop() {
    const tid = turnRunIdRef.current;
    if (tid) void stopRun(tid);
    abortRef.current?.abort();
  }

  // 刷新/切换对话后接回一个在途 run：先清空该气泡（attach 会从头回放全部事件，避免与占位
  // 里 flush 的部分文本重复），再回放+实时续流直到结束。
  async function reattach(runId: string) {
    if (busyRef.current) return;
    turnRunIdRef.current = runId;
    setBusy(true); busyRef.current = true;
    upd((a) => { a.content = ""; a.steps = []; a.progress = undefined; a.sources = undefined;
      a.startedAt = a.startedAt ?? Date.now(); a.elapsedMs = undefined; });
    let outcome: "done" | "error" | "stopped" = "done";
    let reloaded = false;
    const controller = new AbortController();
    abortRef.current = controller;
    const onEvent = (e: any) => applyEvent(e, () => { outcome = "error"; });
    try {
      await attachChat(runId, onEvent, controller.signal);
    } catch (err: any) {
      if (err?.name === "RunEnded") {
        // run 已结束（宽限期外/服务重启）→ 重载该消息的最终态
        reloaded = true;
        try {
          const msgs = await api.messages(conversationId);
          const fin = [...msgs].reverse().find((m) => m.run_id === runId && m.role === "assistant");
          if (fin) upd((a) => {
            a.content = fin.content;
            a.status = (fin.status as ChatMessage["status"]) ?? "done";
            a.steps = fin.steps?.map((s) => ({ tool: s.tool, args: s.args, result: s.result, isError: s.is_error })) ?? a.steps;
            a.progress = fin.progress ?? a.progress;
            a.sources = fin.sources ?? a.sources;
          });
        } catch { /* 重载失败保持原样 */ }
      } else if (err?.name === "AbortError") outcome = "stopped";
      else outcome = "error";
    } finally {
      if (!reloaded) upd((a) => {
        a.status = outcome;
        if (a.startedAt != null && a.elapsedMs == null) a.elapsedMs = Date.now() - a.startedAt;
      });
      setBusy(false); busyRef.current = false;
    }
  }

  // 断点续传：挂载（刷新/切换对话，App 用 key=activeId remount）时，若最后一条助手消息仍在
  // 生成中（streaming）→ 自动接回那个在途 run，续上流式而非停留在"未完成"。
  useEffect(() => {
    const last = initial[initial.length - 1];
    if (last && last.role === "assistant" && last.status === "streaming" && last.runId) {
      void reattach(last.runId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 空态默认问题：挂载时若带 autoSend 则自动发问（key=对话 id，每对话仅触发一次）
  useEffect(() => {
    if (autoSend) void send(autoSend);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 危险命令弹窗：批准/拒绝 → 回传后端；无论成败都关闭弹窗（后端有超时兜底）
  async function decide(ok: boolean) {
    const cur = approval;
    setApproval(null);
    if (cur && runIdRef.current) {
      try { await sendDecision(runIdRef.current, cur.approvalId, ok); } catch { /* 后端超时兜底 */ }
    }
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Box ref={scrollRef} onScroll={onScroll}
        sx={{ flex: 1, overflowY: "auto", p: 2, display: "flex", flexDirection: "column", gap: 2 }}>
        {messages.length === 0 && <EmptyHint onAsk={(q) => send(q)} />}
        {messages.map((m, i) => (
          <MotionBox key={i} variants={bubbleVariants} initial="initial" animate="animate"
            sx={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
            <Paper
              elevation={0}
              sx={{
                maxWidth: "80%", px: 1.5, py: 1, borderRadius: 2,
                bgcolor: m.role === "user" ? "primary.main" : "action.hover",
                color: m.role === "user" ? "primary.contrastText" : "text.primary",
              }}
            >
              {m.role === "user" && m.attachments && m.attachments.length > 0 && (
                <Box sx={{ mb: m.content ? 1 : 0 }}>
                  <AttachmentChips items={m.attachments} />
                </Box>
              )}
              {m.role === "assistant" && m.progress && (() => {
                const planItems = m.progress.filter((p) => p.scope === "plan");
                const plan = planItems[planItems.length - 1];
                return plan ? <PlanBlock text={plan.text} /> : null;
              })()}
              {showTools && m.role === "assistant" && m.progress && m.progress.length > 0 && (() => {
                const sandbox = m.progress.filter((p) => p.scope === "sandbox");
                const sub = m.progress.filter((p) => p.scope.startsWith("subagent:"));
                const skill = m.progress.filter((p) => p.scope === "skill");
                const verify = m.progress.filter((p) => p.scope === "verify");
                const live = busy && i === messages.length - 1;
                const sbLast = sandbox[sandbox.length - 1];
                const sbLastText = sbLast?.text ?? "";
                // 命令行以显式 status 表达运行/成败；容器初始化等旧式行仍以 … 结尾判进行中
                const sbStatus: "running" | "ok" | "error" =
                  sandbox.some((p) => p.status === "error" || p.text.includes("失败")) ? "error"
                    : live && (sbLast?.status === "running" || sbLastText.endsWith("…")) ? "running"
                      : "ok";
                const subStatus: "running" | "ok" | "error" =
                  live && !sub.some((p) => /完成|未产出|失败/.test(p.text)) ? "running"
                    : sub.some((p) => /未产出|失败/.test(p.text)) ? "error" : "ok";
                // 校验门：最后一步 running 且在跑 → running；出现过通过 → ok；否则若有未通过 → error
                const vLast = verify[verify.length - 1];
                const vStatus: "running" | "ok" | "error" =
                  live && vLast?.status === "running" ? "running"
                    : verify.some((p) => p.status === "ok") ? "ok"
                      : verify.some((p) => p.status === "error") ? "error" : "ok";
                return (
                  <>
                    <ProgressBlock title="技能" kind="skill" items={skill} status="ok" />
                    <ProgressBlock title="沙箱执行" kind="sandbox" items={sandbox} status={sbStatus} />
                    <ProgressBlock title="子代理执行" kind="subagent" items={sub} status={subStatus} />
                    <ProgressBlock title="校验" kind="verify" items={verify} status={vStatus} />
                  </>
                );
              })()}
              {showTools && m.role === "assistant" && m.steps && m.steps.length > 0 && (
                <AgentProgress steps={m.steps} />
              )}
              {m.content ? (
                m.role === "assistant" ? (
                  <Markdown onCitationClick={(n) => scrollToCite(String(i), n)}>
                    {m.sources && m.sources.length
                      ? linkifyCitations(m.content, m.sources.length, String(i))
                      : m.content}
                  </Markdown>
                ) : (
                  <Typography component="div" sx={{ whiteSpace: "pre-wrap" }}>
                    {m.content}
                  </Typography>
                )
              ) : m.role === "assistant" && busy && i === messages.length - 1 ? (
                <TypingDots />
              ) : (
                <Typography component="div" sx={{ whiteSpace: "pre-wrap" }}>
                  {m.role === "assistant" ? "…" : ""}
                </Typography>
              )}
              {m.role === "assistant" && m.sources && m.sources.length > 0 && (
                <SourceList sources={m.sources} msgKey={String(i)} flashId={flashId} />
              )}
              {m.role === "assistant" && (() => {
                const live = busy && i === messages.length - 1 && m.status === "streaming";
                // 生成中且已有内容 → 底部 loading（覆盖工具执行等数据暂停时的「卡住」错觉）；
                // 内容为空时上面已显示 TypingDots，不重复。
                if (live) return m.content ? <StreamingHint /> : null;
                if (m.status === "done") return <ReplyStatus kind="done" />;
                if (m.status === "error") return <ReplyStatus kind="error" />;
                if (m.status === "stopped") return <ReplyStatus kind="stopped" />;
                if (m.status === "interrupted") return <ReplyStatus kind="interrupted" />;
                return null;
              })()}
              {showTools && m.role === "assistant" && (() => {
                const streaming = m.status === "streaming" && m.startedAt != null;
                const showElapsed = streaming || m.elapsedMs != null;
                if (!showElapsed && !m.usage) return null;
                return (
                  <Typography variant="caption" color="text.secondary" component="div"
                    sx={{ display: "flex", alignItems: "center", gap: 0.5, mt: 0.5, flexWrap: "wrap" }}>
                    {/* 耗时：生成中实时计数，完成后冻结 */}
                    {streaming ? <RunTimer startedAt={m.startedAt!} />
                      : m.elapsedMs != null ? <span>耗时 {fmtDuration(m.elapsedMs)}</span> : null}
                    {m.usage && (
                      <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}>
                        {showElapsed ? <span>·</span> : null}
                        <span>tokens</span> <RollingNumber value={m.usage.tokens} />
                        {m.usage.cost != null ? <span>· ${m.usage.cost.toFixed(4)}</span> : null}
                      </Box>
                    )}
                  </Typography>
                );
              })()}
            </Paper>
          </MotionBox>
        ))}
      </Box>
      <Box sx={{ px: 1.5, pt: 1, borderTop: 1, borderColor: "divider",
        display: "flex", flexWrap: "wrap", gap: 0.5 }}>
        <FormControlLabel
          control={<Switch size="small" checked={showTools}
            onChange={(e) => toggleShowTools(e.target.checked)} />}
          label={<Typography variant="caption">展示工具调用和 Token</Typography>}
        />
        <FormControlLabel
          control={<Switch size="small" checked={saveWrong}
            onChange={(e) => toggleSaveWrong(e.target.checked)} />}
          label={<Typography variant="caption">考试答错自动保存错题集</Typography>}
        />
      </Box>
      <Box
        onDragOver={(e) => { e.preventDefault(); if (!dragOver) setDragOver(true); }}
        onDragLeave={(e) => { e.preventDefault(); setDragOver(false); }}
        onDrop={onDrop}
        sx={{
          px: 1.5, pb: 1.5, pt: 0.5,
          ...(dragOver && { outline: 2, outlineStyle: "dashed", outlineColor: "primary.main",
            outlineOffset: -4, borderRadius: 1, bgcolor: "action.hover" }),
        }}
      >
        {pending.length > 0 && (
          <Box sx={{ pb: 1 }}>
            <AttachmentChips items={pending} onDelete={removePending} />
          </Box>
        )}
        <Box sx={{ display: "flex", gap: 1, alignItems: "flex-end" }}>
          <input ref={fileRef} hidden type="file" multiple
            onChange={(e) => { if (e.target.files) void addFiles(e.target.files); e.target.value = ""; }} />
          <Tooltip title="上传文件（也可拖拽/粘贴）">
            <span>
              <IconButton aria-label="上传文件" onClick={() => fileRef.current?.click()}
                disabled={pending.length >= MAX_ATTACHMENTS} sx={{ mb: 0.25 }}>
                <AttachFileIcon />
              </IconButton>
            </span>
          </Tooltip>
          <TextField
            fullWidth size="small" value={input}
            multiline minRows={1} maxRows={6}
            onChange={(e) => setInput(e.target.value)}
            onPaste={onPaste}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
            }}
            placeholder="问点什么…"
          />
          {busy ? (
            <Button variant="outlined" color="error" onClick={stop}
              sx={{ flexShrink: 0, mb: 0.25 }}>停止</Button>
          ) : (
            <Button variant="contained" onClick={() => send()}
              sx={{ flexShrink: 0, mb: 0.25 }}>发送</Button>
          )}
        </Box>
      </Box>

      <Snackbar open={err !== null} autoHideDuration={4000} onClose={() => setErr(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="warning" variant="filled" onClose={() => setErr(null)}>{err}</Alert>
      </Snackbar>

      {/* 危险命令人工确认：不设 onClose，backdrop/Esc 不关闭，须显式选择（走后端超时兜底） */}
      <Dialog open={approval !== null}
        slotProps={{ paper: { sx: { width: 420 } } }}>
        <DialogTitle>⚠️ 危险命令需要确认</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 1 }}>
            AI 想在沙箱内执行以下命令，可能造成破坏（{approval?.reason}）。是否允许？
          </DialogContentText>
          <Box component="pre" sx={{
            m: 0, p: 1, borderRadius: 1, bgcolor: "action.hover",
            fontFamily: "monospace", fontSize: 13, whiteSpace: "pre-wrap",
            wordBreak: "break-all", maxHeight: 200, overflow: "auto",
          }}>{approval?.command}</Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => decide(false)}>拒绝</Button>
          <Button variant="contained" color="error" disableElevation
            onClick={() => decide(true)}>批准执行</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
