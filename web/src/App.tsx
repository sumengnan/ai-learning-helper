import { useEffect, useState } from "react";
import type { Conversation, ChatMessage } from "./types";
import { api } from "./api/client";
import { ConversationList } from "./components/ConversationList";
import { ChatView } from "./components/ChatView";

export default function App() {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [initial, setInitial] = useState<ChatMessage[]>([]);

  const refresh = () => api.list().then(setConvs);
  useEffect(() => { refresh(); }, []);

  async function select(id: string) {
    setActiveId(id);
    const msgs = await api.messages(id);
    setInitial(msgs.map((m) => ({ role: m.role as "user" | "assistant", content: m.content })));
  }
  async function newConv() { const { id } = await api.create(); await refresh(); await select(id); }
  async function del(id: string) { await api.remove(id); await refresh(); if (id === activeId) { setActiveId(null); setInitial([]); } }

  return (
    <div className="flex h-full">
      <ConversationList items={convs} activeId={activeId} onSelect={select} onNew={newConv} onDelete={del} />
      <div className="flex-1">
        {activeId
          ? <ChatView key={activeId} conversationId={activeId} initial={initial} />
          : <div className="h-full flex items-center justify-center text-gray-400">新建或选择一个对话开始</div>}
      </div>
    </div>
  );
}
