import { useState } from "react";
import type { ChatMessage } from "../types";
import { streamChat } from "../api/client";
import { AgentProgress } from "./AgentProgress";

export function ChatView({ conversationId, initial }: { conversationId: string; initial: ChatMessage[] }) {
  const [messages, setMessages] = useState<ChatMessage[]>(initial);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function send() {
    if (!input.trim() || busy) return;
    const userMsg: ChatMessage = { role: "user", content: input };
    const assistant: ChatMessage = { role: "assistant", content: "", steps: [] };
    setMessages((m) => [...m, userMsg, assistant]);
    const msg = input; setInput(""); setBusy(true);
    const upd = (fn: (a: ChatMessage) => void) =>
      setMessages((m) => { const copy = [...m]; fn(copy[copy.length - 1]); return copy; });
    try {
      await streamChat(conversationId, msg, (e) => {
        if (e.type === "TextDelta") upd((a) => { a.content += e.data.text; });
        else if (e.type === "ToolStarted") upd((a) => a.steps!.push({ tool: e.data.tool_call.name, args: e.data.tool_call.arguments }));
        else if (e.type === "ToolFinished") upd((a) => {
          const s = a.steps![a.steps!.length - 1];
          if (s) { s.result = e.data.result.content; s.isError = e.data.result.is_error; }
        });
        else if (e.type === "ModelUsage") upd((a) => { a.usage = { tokens: e.data.usage.total, cost: e.data.cost_usd }; });
        else if (e.type === "RunError") upd((a) => { a.content += `\n[出错] ${e.data.error}`; });
      });
    } finally { setBusy(false); }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-right" : "text-left"}>
            <div className={`inline-block max-w-[80%] rounded-lg px-3 py-2 whitespace-pre-wrap ${
              m.role === "user" ? "bg-blue-500 text-white" : "bg-gray-200"}`}>
              {m.content || (m.role === "assistant" ? "…" : "")}
              {m.role === "assistant" && m.steps && <AgentProgress steps={m.steps} />}
              {m.usage && <div className="mt-1 text-xs text-gray-500">tokens {m.usage.tokens}{m.usage.cost != null ? ` · $${m.usage.cost.toFixed(4)}` : ""}</div>}
            </div>
          </div>
        ))}
      </div>
      <div className="p-3 border-t flex gap-2">
        <input className="flex-1 border rounded px-3 py-2" value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()} placeholder="问点什么…" />
        <button className="bg-blue-500 text-white rounded px-4 disabled:opacity-50"
          onClick={send} disabled={busy}>发送</button>
      </div>
    </div>
  );
}
