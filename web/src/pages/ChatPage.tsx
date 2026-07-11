// web/src/pages/ChatPage.tsx
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box, Dialog, DialogTitle, DialogContent, DialogActions, TextField, Button,
} from "@mui/material";
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
  const [newOpen, setNewOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [searchParams, setSearchParams] = useSearchParams();

  const refresh = () => api.list().then(setConvs);

  // 进入页面时：优先打开 ?conv= 指定的会话（从下载页跳转而来），否则打开最近一个
  useEffect(() => {
    void (async () => {
      const list = await api.list();
      setConvs(list);
      const wanted = searchParams.get("conv");
      if (wanted && list.some((c) => c.id === wanted)) {
        void select(wanted);
        setSearchParams({}, { replace: true });   // 用后即清，避免刷新/后退重复定位
      } else if (list.length > 0) {
        void select(list[0].id);
      }
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
        ? m.steps.map((s) => ({ tool: s.tool, args: s.args, result: s.result,
                                isError: s.is_error, download: s.download }))
        : undefined,
      progress: m.progress ?? undefined,
      attachments: m.attachments ?? undefined,
      status: (m.status as "streaming" | "done" | "error" | "stopped" | "interrupted") ?? undefined,
      runId: m.run_id ?? undefined,   // 供刷新后接回在途生成
    })));
    setActiveId(id);
  }
  // 新建对话：弹窗输入名称
  function newConv() {
    setNewName("");
    setNewOpen(true);
  }
  async function createNamed() {
    const title = newName.trim() || "新对话";
    const { id } = await api.create(title);
    await refresh();
    setAutoSend(null); setInitial([]); setActiveId(id);
    setNewOpen(false);
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

      <Dialog open={newOpen} onClose={() => setNewOpen(false)}
        slotProps={{ paper: { sx: { width: 360 } } }}>
        <DialogTitle>新建对话</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus fullWidth variant="standard" value={newName}
            placeholder="新对话"
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void createNamed(); }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNewOpen(false)}>取消</Button>
          <Button variant="contained" disableElevation onClick={() => void createNamed()}>
            创建
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
