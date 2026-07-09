// web/src/pages/ChatPage.tsx
import { useEffect, useState } from "react";
import { Box } from "@mui/material";
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
    setAutoSend(null);
    setInitial(msgs.map((m) => ({
      role: m.role as "user" | "assistant",
      content: m.content,
      steps: m.steps
        ? m.steps.map((s) => ({ tool: s.tool, args: s.args, result: s.result, isError: s.is_error }))
        : undefined,
    })));
    setActiveId(id);
  }
  async function newConv() {
    // 最近一个对话若还没有任何问答，直接复用它，避免堆积空对话
    const recent = convs[0];
    if (recent) {
      const msgs = await api.messages(recent.id);
      if (msgs.length === 0) {
        setAutoSend(null); setInitial([]); setActiveId(recent.id);
        return;
      }
    }
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
      <Box sx={{ flex: 1, minWidth: 0, height: "100%", bgcolor: "background.paper" }}>
        {activeId
          ? <ChatView key={activeId} conversationId={activeId} initial={initial} autoSend={autoSend} />
          : <EmptyHint onAsk={ask} />}
      </Box>
    </Box>
  );
}
