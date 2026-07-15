import { useEffect, useRef, useState } from "react";
import {
  Box, Paper, TextField, Button, Typography, FormControlLabel, Switch,
  Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
  IconButton, Tooltip, Snackbar, Alert, CircularProgress,
} from "@mui/material";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import DownloadIcon from "@mui/icons-material/Download";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutlineOutlined";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import { motion } from "framer-motion";
import type { ChatMessage } from "../types";
import { streamChat, attachChat, stopRun, sendDecision, api } from "../api/client";
import { AgentProgress } from "./AgentProgress";
import { ThinkingBlock } from "./ThinkingBlock";
import { SourceList } from "./SourceList";
import { MessageMeta } from "./MessageMeta";
import { linkifyCitations, citeId } from "./citations";
import { EmptyHint } from "./EmptyHint";
import { ProgressBlock } from "./ProgressBlock";
import { VerifyBadge, isGateOpen } from "./VerifyBadge";
import { PlanBlock } from "./PlanBlock";
import { Markdown } from "./Markdown";
import { RollingNumber } from "./RollingNumber";
import { AttachmentChips, type AttachmentItem } from "./Attachments";
import { bubbleVariants } from "./motion";

const MotionBox = motion(Box);

const SHOW_TOOLS_KEY = "chat_show_tools";
const SHOW_SOURCES_KEY = "chat_show_sources";
const THINK_KEY = "chat_think";
const VERIFY_KEY = "chat_verify";
const MAX_ATTACHMENTS = 10;
const MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024;  // 100MB
const readBool = (k: string, dflt: boolean) => {
  const v = localStorage.getItem(k);
  return v === null ? dflt : v === "1";
};

// 从助手消息的工具轨迹里提取 AI 生成的可下载文件（save_download 结果带机读标记〔下载ID:...〕）。
// 基于已持久化的 steps，故刷新后仍可用。
const DL_ID_RE = /〔下载ID:([0-9a-fA-F]+)〕/;
function generatedFiles(steps?: { tool: string; args?: any; result?: string }[]) {
  const out: { id: string; filename: string }[] = [];
  for (const s of steps || []) {
    if (s.tool !== "save_download" || !s.result) continue;
    const m = s.result.match(DL_ID_RE);
    if (m && !out.some((f) => f.id === m[1])) {
      out.push({ id: m[1], filename: (s.args && s.args.filename) || "下载文件" });
    }
  }
  return out;
}

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

// 耗时格式化保持从此处导出（历史引用/测试用），实现移入 duration.ts
export { fmtDuration } from "./duration";

export function ChatView({ conversationId, initial, autoSend, onTitled, onStart }:
  { conversationId: string; initial: ChatMessage[]; autoSend?: string | null;
    onTitled?: () => void; onStart?: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>(initial);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [showTools, setShowTools] = useState(() => readBool(SHOW_TOOLS_KEY, true));
  const [showSources, setShowSources] = useState(() => readBool(SHOW_SOURCES_KEY, true));
  const [think, setThink] = useState(() => readBool(THINK_KEY, true));
  const [verify, setVerify] = useState(() => readBool(VERIFY_KEY, true));
  const abortRef = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  // 区分「用户点停止」与「卸载/StrictMode 重挂载导致的 abort」：只有前者才落「已停止」终态，
  // 后者应保留 streaming 由重挂载后重新接回，续上而非中断。
  const userStoppedRef = useRef(false);
  // 组件真实挂载中标记（StrictMode 会 setup→cleanup→setup，cleanup 里置 false、二次 setup 置回 true），
  // 用于判断一次 abort 后是否应自动重连。
  const mountedRef = useRef(false);
  // 危险命令人工确认：run_id 来自 RunStarted 事件，用于拼回传 URL
  const runIdRef = useRef<string | null>(null);
  // 本轮 turn 句柄（来自 X-Run-Id / 接回的 runId），用于 stop 与刷新后接回
  const turnRunIdRef = useRef<string | null>(null);
  const [approval, setApproval] = useState<
    { approvalId: string; command: string; reason: string } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const thinkRef = useRef(think);
  thinkRef.current = think;
  const verifyRef = useRef(verify);
  verifyRef.current = verify;
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
  const toggleShowSources = (v: boolean) => {
    setShowSources(v); localStorage.setItem(SHOW_SOURCES_KEY, v ? "1" : "0");
  };
  const toggleThink = (v: boolean) => {
    setThink(v); localStorage.setItem(THINK_KEY, v ? "1" : "0");
  };
  const toggleVerify = (v: boolean) => {
    setVerify(v); localStorage.setItem(VERIFY_KEY, v ? "1" : "0");
  };

  // 卸载（含 App 用 key={activeId} 切换对话触发 remount）时取消在途流。
  // mountedRef 让 send/reattach 能区分「真卸载」与「StrictMode 假卸载」，后者需自动重连。
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; abortRef.current?.abort(); };
  }, []);

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
    else if (e.type === "ReasoningDelta") upd((a) => { a.reasoning = (a.reasoning || "") + e.data.text; });
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
    // 每步校验（scope=check）：从 key `check:<tool>` 或 text 提取工具名，同工具合并为一行。
    // 仍入 progress 列供徽章判定 verify/check 信号；另单列 checks 供徽章展开明细。
    else if (e.type === "Progress" && e.data.scope === "check") upd((a) => {
      (a.progress ||= []).push({ scope: e.data.scope, text: e.data.text, status: e.data.status, key: e.data.key, agent: e.data.agent });
      const key: string = e.data.key || "";
      const tool = key.startsWith("check:") ? key.slice("check:".length) : (e.data.text || "").split(/\s+/)[0] || "check";
      const status: "ok" | "error" = e.data.status === "error" ? "error" : "ok";
      const row = { tool, status, text: e.data.text || "" };
      a.checks = [...(a.checks || [])];
      const at = a.checks.findIndex((c) => c.tool === tool);
      if (at >= 0) a.checks[at] = row; else a.checks.push(row);
    });
    // 轨迹质量分（scope=quality）：text 为 JSON，解析失败忽略该事件、不抛。
    else if (e.type === "Progress" && e.data.scope === "quality") upd((a) => {
      try { a.quality = JSON.parse(e.data.text); } catch { /* 解析失败忽略，不显示质量分 */ }
    });
    // 已清理的下载产物（scope=purged，text 为 id 数组 JSON）：校验不过重答时，服务端会把
    // 被否那版生成的文件删掉。steps 是从 ToolFinished 攒的、仍带着〔下载ID:x〕，不剔掉就会
    // 留一个指向已删文件的死按钮。特判、不入 progress 列。
    else if (e.type === "Progress" && e.data.scope === "purged") upd((a) => {
      let ids: string[] = [];
      try { ids = JSON.parse(e.data.text); } catch { return; }
      if (!Array.isArray(ids) || !ids.length || !a.steps) return;
      a.steps = a.steps.map((s) => {
        const hit = ids.find((id) => (s.result || "").includes(`〔下载ID:${id}〕`));
        return hit
          ? { ...s, result: (s.result || "").replace(`〔下载ID:${hit}〕`, "")
              + "\n（该版本未通过校验，此产物已作废删除）" }
          : s;
      });
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
    // 新对话首条消息：用它自动命名（并行、不阻塞回复；失败不影响聊天）
    const isFirst = messages.length === 0;
    if (isFirst) onStart?.();   // 通知上层：这条新对话已开始聊天，不再是空草稿
    stickRef.current = true;   // 发送即恢复跟随：即使之前上滑看历史，也自动回到底部
    setMessages((m) => [...m, userMsg, assistant]);
    if (isFirst && msg) {
      void api.autotitle(conversationId, msg).then(() => onTitled?.()).catch(() => {});
    }
    let outcome: "done" | "error" | "stopped" | null = "done";
    userStoppedRef.current = false;
    setInput(""); setPending([]); setBusy(true); busyRef.current = true;
    const controller = new AbortController();
    abortRef.current = controller;
    const onEvent = (e: any) => applyEvent(e, () => { outcome = "error"; });
    try {
      await streamChat(conversationId, msg, onEvent, controller.signal,
        attachments.map((a) => a.id),
        (rid) => { turnRunIdRef.current = rid; upd((a) => { a.runId = rid; }); },
        thinkRef.current, verifyRef.current);
    } catch (err: any) {
      // 用户点停止 → 已停止；非用户 abort（卸载/重挂载）→ null：不落终态，保留 streaming 待重连
      if (err?.name === "AbortError") outcome = userStoppedRef.current ? "stopped" : null;
      else { upd((a) => { a.content += `\n[连接失败] ${err}`; }); outcome = "error"; }
    } finally {
      setBusy(false); busyRef.current = false;
      if (outcome === null) {
        // 非用户主动中断：后端后台任务仍在跑，若组件仍挂载且已拿到 run 句柄 → 自动接回续流
        const rid = turnRunIdRef.current;
        if (mountedRef.current && rid) void reattach(rid);
        return;
      }
      const status = outcome;   // 早返回后已排除 null
      upd((a) => {   // 收尾状态：完成/失败/已停止；冻结本轮耗时
        // 空产出兜底：流正常结束(done)却没有任何正文 → 视为失败并给出提示，
        // 避免把"…"+「已完成」这种静默失败伪装成成功（后端通常已补发 RunError，此为双保险）。
        if (status === "done" && !a.content.trim()) {
          a.status = "error";
          a.content = "（本轮未产出内容，请重试）";
        } else {
          a.status = status;
        }
        if (a.startedAt != null && a.elapsedMs == null) a.elapsedMs = Date.now() - a.startedAt;
      });
    }
  }

  // 停止本轮生成：显式取消后端后台任务（断开已不再取消它）+ 断开本地流。
  function stop() {
    userStoppedRef.current = true;   // 标记为用户主动停止：本次 abort 才落「已停止」终态
    const tid = turnRunIdRef.current;
    if (tid) void stopRun(tid);
    abortRef.current?.abort();
  }

  // 刷新/切换对话后接回一个在途 run：先清空该气泡（attach 会从头回放全部事件，避免与占位
  // 里 flush 的部分文本重复），再回放+实时续流直到结束。
  async function reattach(runId: string) {
    if (busyRef.current) return;
    userStoppedRef.current = false;
    turnRunIdRef.current = runId;
    setBusy(true); busyRef.current = true;
    upd((a) => { a.content = ""; a.steps = []; a.progress = undefined; a.sources = undefined;
      a.startedAt = a.startedAt ?? Date.now(); a.elapsedMs = undefined; });
    let outcome: "done" | "error" | "stopped" | null = "done";
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
      // 用户点停止 → 已停止；非用户 abort（卸载/StrictMode 假卸载）→ null：不落终态、稍后重连
      } else if (err?.name === "AbortError") outcome = userStoppedRef.current ? "stopped" : null;
      else outcome = "error";
    } finally {
      setBusy(false); busyRef.current = false;
      if (outcome === null) {
        // 被卸载/重挂载打断：组件仍挂载（StrictMode 假卸载后已重挂）→ 重新接回，续上而非停留
        if (mountedRef.current) void reattach(runId);
        return;
      }
      const status = outcome;   // 早返回后已排除 null
      if (!reloaded) upd((a) => {
        // 空产出兜底（同 send）：接回正常结束却无正文 → 视为失败，避免"…"+「已完成」误导
        if (status === "done" && !a.content.trim()) {
          a.status = "error";
          a.content = "（本轮未产出内容，请重试）";
        } else {
          a.status = status;
        }
        if (a.startedAt != null && a.elapsedMs == null) a.elapsedMs = Date.now() - a.startedAt;
      });
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
              {/* 思考过程：置于最顶（工具调用等过程块之上），先于正文展示推理内容 */}
              {m.role === "assistant" && m.reasoning && (
                <ThinkingBlock reasoning={m.reasoning}
                  live={busy && i === messages.length - 1 && m.status === "streaming"} />
              )}
              {m.role === "assistant" && m.progress && (() => {
                const planItems = m.progress.filter((p) => p.scope === "plan");
                const plan = planItems[planItems.length - 1];
                const live = busy && i === messages.length - 1 && m.status === "streaming";
                return plan ? (
                  <PlanBlock text={plan.text} live={live} stopped={m.status === "stopped"} />
                ) : null;
              })()}
              {showTools && m.role === "assistant" && m.progress && m.progress.length > 0 && (() => {
                const sandbox = m.progress.filter((p) => p.scope === "sandbox");
                const sub = m.progress.filter((p) => p.scope.startsWith("subagent:"));
                const skill = m.progress.filter((p) => p.scope === "skill");
                const live = busy && i === messages.length - 1 && m.status === "streaming";
                const stopped = m.status === "stopped";
                // 进行中的块：生成中转圈；用户停止→stopped（已取消）；否则收尾为 ok
                const endInflight = (inflight: boolean) =>
                  live && inflight ? "running" : stopped && inflight ? "stopped" : "ok";
                const sbLast = sandbox[sandbox.length - 1];
                const sbLastText = sbLast?.text ?? "";
                // 命令行以显式 status 表达运行/成败；容器初始化等旧式行仍以 … 结尾判进行中
                const sbStatus: "running" | "ok" | "error" | "stopped" =
                  sandbox.some((p) => p.status === "error" || p.text.includes("失败")) ? "error"
                    : endInflight(sbLast?.status === "running" || sbLastText.endsWith("…"));
                const subStatus: "running" | "ok" | "error" | "stopped" =
                  sub.some((p) => /未产出|失败/.test(p.text)) ? "error"
                    : endInflight(!sub.some((p) => /完成|未产出|失败/.test(p.text)));
                // 校验状态改由常驻 VerifyBadge 展示（脱离本 showTools 分支）
                return (
                  <>
                    <ProgressBlock title="技能" kind="skill" items={skill} status="ok" />
                    <ProgressBlock title="沙箱执行" kind="sandbox" items={sandbox} status={sbStatus} />
                    <ProgressBlock title="子代理执行" kind="subagent" items={sub} status={subStatus} />
                  </>
                );
              })()}
              {showTools && m.role === "assistant" && m.steps && m.steps.length > 0 && (
                <AgentProgress steps={m.steps}
                  live={busy && i === messages.length - 1 && m.status === "streaming"}
                  stopped={m.status === "stopped"} />
              )}
              {m.content ? (
                m.role === "assistant" ? (
                  <Markdown onCitationClick={(n) => scrollToCite(String(i), n)}>
                    {showSources && m.sources && m.sources.length
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
              {/* AI 生成的可下载文件：常驻一行，不受「展示工具调用」开关影响 */}
              {m.role === "assistant" && (() => {
                const files = generatedFiles(m.steps);
                if (!files.length) return null;
                // 开了校验门的轮次，交付前一律不显示：校验不过会带反馈重答，届时本轮产物会被
                // 服务端清理掉，提前显示等于给用户一个马上会失效的下载按钮。服务端在轮次开头
                // 就下发 scope=verify 信号，故整个生成/校验/重答期间都能盖住，不会闪一下。
                // 这里必须把门已开信号(isGateOpen)算在内 —— 它正是轮次开头唯一那条 verify 事件，
                // 是「不闪一下」的全部依据。别为了跟徽章的过滤保持一致而把它排掉。
                const gating = m.status === "streaming"
                  && (m.progress || []).some((p) => p.scope === "verify");
                if (gating) return null;
                return (
                  <Box sx={{ mt: 1, display: "flex", flexWrap: "wrap", gap: 1, alignItems: "center" }}>
                    <Typography variant="caption" color="text.secondary">生成的文件：</Typography>
                    {files.map((f) => (
                      <Button key={f.id} size="small" variant="outlined"
                        startIcon={<DownloadIcon fontSize="small" />}
                        onClick={() => api.downloads.save(f.id, f.filename)
                          .catch(() => setErr(`「${f.filename}」下载失败`))}>
                        {f.filename}
                      </Button>
                    ))}
                  </Box>
                );
              })()}
              {/* 元信息页脚：校验 / 状态 / 耗时 / tokens / 参考来源——均为系统级信息，
                  用虚线与正文分隔，同处虚线下方，各成一块提高辨识度。生成中即显示状态与实时耗时。 */}
              {m.role === "assistant" && (() => {
                const live = busy && i === messages.length - 1 && m.status === "streaming";
                const hasSources = !!(showSources && m.sources && m.sources.length > 0);
                // 校验是系统级信息，与状态/耗时/tokens 同处虚线下方（不受 showTools 开关影响）
                // quality 刷新后不在 m.quality 上（只实时赋值），但 progress 里有
                // scope="quality"，故一并认；否则「只有质量分、无 verify」的轮刷新后不渲染徽章
                // 门已开信号（isGateOpen）不算校验信号：它在轮次开头就到，用它起徽章等于
                // 回答刚起头就转圈谎称在校验。徽章要等真正的校验事件（「校验中…」）才出现。
                const hasVerify = !!((m.progress || []).some(
                  (p) => (p.scope === "verify" && !isGateOpen(p))
                    || p.scope === "check" || p.scope === "quality")
                  || m.quality);
                const hasStatus = live || m.status === "done" || m.status === "error"
                  || m.status === "stopped" || m.status === "interrupted";
                const hasElapsed = (live && m.startedAt != null) || (showTools && m.elapsedMs != null);
                const hasTokens = showTools && !!m.usage;
                const showMetaRow = hasStatus || hasElapsed || hasTokens;
                if (!showMetaRow && !hasSources && !hasVerify) return null;
                // 正文已有内容时用虚线与正文分隔；生成初期正文尚空则不画分隔线，避免悬空的线
                const separated = !!m.content;
                return (
                  <Box sx={{ mt: separated ? 1.25 : 0.75, pt: separated ? 1 : 0,
                    borderTop: separated ? "1px dashed" : 0, borderColor: "divider",
                    display: "flex", flexDirection: "column", gap: 0.75 }}>
                    {hasVerify && <VerifyBadge message={m} live={live} />}
                    {showMetaRow && (
                      <MessageMeta status={m.status} live={live} startedAt={m.startedAt}
                        elapsedMs={m.elapsedMs} usage={m.usage} showMeta={showTools} />
                    )}
                    {hasSources && (
                      <SourceList sources={m.sources!} msgKey={String(i)} flashId={flashId} />
                    )}
                  </Box>
                );
              })()}
            </Paper>
          </MotionBox>
        ))}
      </Box>
      <Box sx={{ px: 1.5, pt: 1, borderTop: 1, borderColor: "divider",
        display: "flex", flexWrap: "wrap", gap: 0.5 }}>
        <FormControlLabel
          control={<Switch size="small" checked={think}
            onChange={(e) => toggleThink(e.target.checked)} />}
          label={<Typography variant="caption">思考模式</Typography>}
        />
        <FormControlLabel
          control={<Switch size="small" checked={verify}
            onChange={(e) => toggleVerify(e.target.checked)} />}
          label={<Typography variant="caption">结果校验</Typography>}
        />
        <FormControlLabel
          control={<Switch size="small" checked={showTools}
            onChange={(e) => toggleShowTools(e.target.checked)} />}
          label={<Typography variant="caption">展示工具调用和 Token</Typography>}
        />
        <FormControlLabel
          control={<Switch size="small" checked={showSources}
            onChange={(e) => toggleShowSources(e.target.checked)} />}
          label={<Typography variant="caption">展示数据来源和引用</Typography>}
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
