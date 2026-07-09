// web/src/pages/ChatPage.tsx
import { useEffect, useState } from "react";
import { Box, Paper, Typography } from "@mui/material";
import type { Conversation, ChatMessage } from "../types";
import { api } from "../api/client";
import { ConversationList } from "../components/ConversationList";
import { ChatView } from "../components/ChatView";

const SUGGESTIONS = [
  "查询最新的 AI 资讯，保存到知识库",
  '执行代码：echo "Hello " + "AI"',
  "从题库抽 10 道题，开始模拟考试",
  "对我的错题集做个总结",
];

function EmptyState({ onAsk }: { onAsk: (q: string) => void }) {
  return (
    <Box sx={{
      height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", gap: 3, p: 3,
    }}>
      <Typography variant="h5" sx={{ fontWeight: 700, textAlign: "center" }}>
        基于 Harness 架构的 AI 学习助手
      </Typography>
      <Box sx={{
        display: "flex", flexWrap: "wrap", gap: 1.5,
        justifyContent: "center", maxWidth: 640,
      }}>
        {SUGGESTIONS.map((s) => (
          <Paper
            key={s} variant="outlined"
            onClick={() => onAsk(s)}
            sx={{
              px: 2, py: 1.5, borderRadius: 2, cursor: "pointer", maxWidth: 300,
              "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" },
            }}
          >
            <Typography variant="body2">{s}</Typography>
          </Paper>
        ))}
      </Box>
    </Box>
  );
}

export function ChatPage() {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [initial, setInitial] = useState<ChatMessage[]>([]);
  const [autoSend, setAutoSend] = useState<string | null>(null);

  const refresh = () => api.list().then(setConvs);
  useEffect(() => { refresh(); }, []);

  async function select(id: string) {
    setAutoSend(null);
    setActiveId(id);
    const msgs = await api.messages(id);
    setInitial(msgs.map((m) => ({ role: m.role as "user" | "assistant", content: m.content })));
  }
  async function newConv() {
    const { id } = await api.create();
    await refresh();
    setAutoSend(null); setInitial([]); setActiveId(id);
  }
  async function ask(question: string) {
    const { id } = await api.create();
    await refresh();
    setInitial([]); setAutoSend(question); setActiveId(id);
  }
  async function del(id: string) {
    await api.remove(id); await refresh();
    if (id === activeId) { setActiveId(null); setInitial([]); setAutoSend(null); }
  }
  async function rename(id: string, title: string) {
    await api.rename(id, title); await refresh();
  }

  return (
    <Box sx={{ display: "flex", height: "100%" }}>
      <ConversationList items={convs} activeId={activeId} onSelect={select}
        onNew={newConv} onDelete={del} onRename={rename} />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {activeId
          ? <ChatView key={activeId} conversationId={activeId} initial={initial} autoSend={autoSend} />
          : <EmptyState onAsk={ask} />}
      </Box>
    </Box>
  );
}
