import { useEffect, useRef, useState } from "react";
import { Box, Paper, TextField, Button, Typography } from "@mui/material";
import type { ChatMessage } from "../types";
import { streamChat } from "../api/client";
import { AgentProgress } from "./AgentProgress";

export function ChatView({ conversationId, initial, autoSend }:
  { conversationId: string; initial: ChatMessage[]; autoSend?: string | null }) {
  const [messages, setMessages] = useState<ChatMessage[]>(initial);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const busyRef = useRef(false);

  // 卸载（含 App 用 key={activeId} 切换对话触发 remount）时取消在途流
  useEffect(() => () => abortRef.current?.abort(), []);

  const upd = (fn: (a: ChatMessage) => void) =>
    setMessages((m) => {
      const copy = [...m];
      const last = { ...copy[copy.length - 1] };
      if (last.steps) last.steps = last.steps.map((s) => ({ ...s }));
      fn(last);
      copy[copy.length - 1] = last;
      return copy;
    });

  async function send(text?: string) {
    const msg = (text ?? input).trim();
    if (!msg || busyRef.current) return;
    const userMsg: ChatMessage = { role: "user", content: msg };
    const assistant: ChatMessage = { role: "assistant", content: "", steps: [] };
    setMessages((m) => [...m, userMsg, assistant]);
    setInput(""); setBusy(true); busyRef.current = true;
    const controller = new AbortController();
    abortRef.current = controller;
    const onEvent = (e: any) => {
      if (e.type === "TextDelta") upd((a) => { a.content += e.data.text; });
      else if (e.type === "ToolStarted") upd((a) => a.steps!.push({ tool: e.data.tool_call.name, args: e.data.tool_call.arguments }));
      else if (e.type === "ToolFinished") upd((a) => {
        const s = a.steps![a.steps!.length - 1];
        if (s) { s.result = e.data.result.content; s.isError = e.data.result.is_error; }
      });
      else if (e.type === "ModelUsage") upd((a) => { a.usage = { tokens: e.data.usage.total, cost: e.data.cost_usd }; });
      else if (e.type === "RunError") upd((a) => { a.content += `\n[出错] ${e.data.error}`; });
    };
    try {
      await streamChat(conversationId, msg, onEvent, controller.signal);
    } catch (err: any) {
      if (err?.name !== "AbortError") upd((a) => { a.content += `\n[连接失败] ${err}`; });
    } finally { setBusy(false); busyRef.current = false; }
  }

  // 空态默认问题：挂载时若带 autoSend 则自动发问（key=对话 id，每对话仅触发一次）
  useEffect(() => {
    if (autoSend) void send(autoSend);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Box sx={{ flex: 1, overflowY: "auto", p: 2, display: "flex", flexDirection: "column", gap: 2 }}>
        {messages.map((m, i) => (
          <Box key={i} sx={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
            <Paper
              elevation={0}
              sx={{
                maxWidth: "80%", px: 1.5, py: 1, borderRadius: 2,
                bgcolor: m.role === "user" ? "primary.main" : "action.hover",
                color: m.role === "user" ? "primary.contrastText" : "text.primary",
              }}
            >
              <Typography component="div" sx={{ whiteSpace: "pre-wrap" }}>
                {m.content || (m.role === "assistant" ? "…" : "")}
              </Typography>
              {m.role === "assistant" && m.steps && <AgentProgress steps={m.steps} />}
              {m.usage && (
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                  tokens {m.usage.tokens}{m.usage.cost != null ? ` · $${m.usage.cost.toFixed(4)}` : ""}
                </Typography>
              )}
            </Paper>
          </Box>
        ))}
      </Box>
      <Box sx={{ p: 1.5, borderTop: 1, borderColor: "divider", display: "flex", gap: 1 }}>
        <TextField
          fullWidth size="small" value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          placeholder="问点什么…"
        />
        <Button variant="contained" onClick={() => send()} disabled={busy}>发送</Button>
      </Box>
    </Box>
  );
}
