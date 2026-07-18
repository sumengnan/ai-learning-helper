// web/src/pages/ChatPage.tsx
import { useEffect, useState } from "react";
import { Box, Snackbar, Alert } from "@mui/material";
import type { Conversation, ChatMessage } from "../types";
import { api } from "../api/client";
import { ConversationList } from "../components/ConversationList";
import { ChatView } from "../components/ChatView";
import { EmptyHint } from "../components/EmptyHint";

export function ChatPage() {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [initial, setInitial] = useState<ChatMessage[]>([]);
  const [autoSend, setAutoSend] = useState<string | null>(null);
  // 已新建但还没开始聊天的空对话 id；用于避免重复新建、并给出提示
  const [draftId, setDraftId] = useState<string | null>(null);
  const [snack, setSnack] = useState<string | null>(null);

  const refresh = () => api.list().then(setConvs);

  // 进入页面时默认打开最近一个对话，而不是空白页
  useEffect(() => {
    void (async () => {
      const list = await api.list();
      setConvs(list);
      if (list.length > 0) void select(list[0].id);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function select(id: string) {
    const msgs = await api.messages(id);
    // 选中的若仍是空的「新对话」，继续视作草稿；否则清掉草稿标记
    const conv = convs.find((c) => c.id === id);
    setDraftId(msgs.length === 0 && conv?.title === "新对话" ? id : null);
    setAutoSend(null);
    setInitial(msgs.map((m) => ({
      role: m.role as "user" | "assistant",
      content: m.content,
      steps: m.steps
        ? m.steps.map((s) => ({ tool: s.tool, args: s.args, result: s.result, isError: s.is_error }))
        : undefined,
      progress: m.progress ?? undefined,
      sources: m.sources ?? undefined,
      attachments: m.attachments ?? undefined,
      status: (m.status as "streaming" | "done" | "error" | "stopped" | "interrupted") ?? undefined,
      runId: m.run_id ?? undefined,   // 供刷新后接回在途生成
      // 刷新后仍还原用量与耗时
      usage: m.tokens != null ? { tokens: m.tokens, cost: m.cost ?? null } : undefined,
      elapsedMs: m.elapsed_ms ?? undefined,
      reasoning: m.reasoning ?? undefined,   // 刷新后还原思考过程（含编排器"结果思考"）
      reasoningMs: m.reasoning_ms ?? undefined,   // 刷新后还原思考耗时
      // 刷新后还原"任务计划思考"（文本 + 耗时）：从落库的 plan_reasoning 进度行拼回
      planReasoning: (m.progress || [])
        .filter((p) => p.scope === "plan_reasoning")
        .map((p) => p.text).join("") || undefined,
      planReasoningMs: (m.progress || [])
        .find((p) => p.scope === "plan_reasoning" && p.detail?.elapsed_ms != null)
        ?.detail?.elapsed_ms ?? undefined,
    })));
    setActiveId(id);
  }
  // 新建对话：直接创建空对话，标题由发出的第一句话自动生成
  async function newConv() {
    // 已存在一个新建但未聊天的空对话 → 不重复创建，提示去开始聊天
    if (draftId && draftId === activeId) {
      setSnack("已经添加了新对话，可以开始聊天了");
      return;
    }
    const { id } = await api.create();
    await refresh();
    setAutoSend(null); setInitial([]); setActiveId(id); setDraftId(id);
  }
  async function ask(question: string) {
    const { id } = await api.create();
    await refresh();
    setInitial([]); setAutoSend(question); setActiveId(id); setDraftId(null);
  }
  async function del(id: string) {
    await api.remove(id); await refresh();
    if (id === draftId) setDraftId(null);
    if (id === activeId) { setActiveId(null); setInitial([]); setAutoSend(null); }
  }

  return (
    <Box sx={{ display: "flex", height: "100%" }}>
      <ConversationList items={convs} activeId={activeId} onSelect={select}
        onNew={newConv} onDelete={del} />
      <Box sx={{ flex: 1, minWidth: 0, height: "100%", bgcolor: "background.paper" }}>
        {activeId
          ? <ChatView key={activeId} conversationId={activeId} initial={initial}
              autoSend={autoSend} onTitled={refresh}
              onStart={() => setDraftId(null)} />
          : <EmptyHint onAsk={ask} />}
      </Box>
      <Snackbar
        open={Boolean(snack)} autoHideDuration={3000}
        onClose={() => setSnack(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert severity="info" variant="filled" onClose={() => setSnack(null)}>
          {snack}
        </Alert>
      </Snackbar>
    </Box>
  );
}
