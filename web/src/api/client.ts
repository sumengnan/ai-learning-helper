import type { AgentEvent, Conversation } from "../types";

export function drainSSE(buffer: string): { events: AgentEvent[]; rest: string } {
  const events: AgentEvent[] = [];
  let idx: number;
  while ((idx = buffer.indexOf("\n\n")) >= 0) {
    const chunk = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    const line = chunk.split("\n").find((l) => l.startsWith("data: "));
    if (line) events.push(JSON.parse(line.slice(6)) as AgentEvent);
  }
  return { events, rest: buffer };
}

export async function streamChat(
  conversationId: string, message: string, onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, message }),
    signal,
  });
  if (!resp.ok || !resp.body) throw new Error(`chat 失败：${resp.status}`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = drainSSE(buffer);
    buffer = rest;
    events.forEach(onEvent);
  }
}

export const api = {
  list: (): Promise<Conversation[]> => fetch("/api/conversations").then((r) => r.json()),
  create: (title?: string): Promise<{ id: string }> =>
    fetch("/api/conversations", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }) }).then((r) => r.json()),
  messages: (id: string): Promise<{ role: string; content: string }[]> =>
    fetch(`/api/conversations/${id}/messages`).then((r) => r.json()),
  remove: (id: string): Promise<void> =>
    fetch(`/api/conversations/${id}`, { method: "DELETE" }).then(() => undefined),
  documents: {
    list: (): Promise<{ id: string; filename: string; num_chunks: number; uploaded_at: string }[]> =>
      fetch("/api/documents").then((r) => r.json()),
    upload: (file: File) => {
      const fd = new FormData(); fd.append("file", file);
      return fetch("/api/documents", { method: "POST", body: fd }).then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `上传失败：${r.status}`);
        return r.json();
      });
    },
    remove: (id: string): Promise<void> =>
      fetch(`/api/documents/${id}`, { method: "DELETE" }).then(() => undefined),
  },
  questions: {
    generate: (topic: string, count: number, types: string[]) =>
      fetch("/api/questions/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic, count, types }),
      }).then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "出题失败");
        return r.json();
      }),
    list: () => fetch("/api/questions").then((r) => r.json()),
    remove: (id: string) =>
      fetch(`/api/questions/${id}`, { method: "DELETE" }).then(() => undefined),
    removeMany: (ids: string[]) =>
      fetch("/api/questions/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      }).then(() => undefined),
  },
  exams: {
    compose: (count: number, types: string[] | null) =>
      fetch("/api/exams", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count, types }),
      }).then((r) => { if (!r.ok) throw new Error("组卷失败"); return r.json(); }),
    submit: (answers: { question_id: string; user_answer: unknown }[]) =>
      fetch("/api/exams/submit", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers }),
      }).then((r) => { if (!r.ok) throw new Error("交卷失败"); return r.json(); }),
    history: () => fetch("/api/exams").then((r) => r.json()),
  },
  wrong: {
    list: () => fetch("/api/wrong-answers").then((r) => r.json()),
    removeMany: (ids: string[]) =>
      fetch("/api/wrong-answers/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      }).then(() => undefined),
  },
  downloads: {
    list: () => fetch("/api/downloads").then((r) => r.json()),
    remove: (id: string) =>
      fetch(`/api/downloads/${id}`, { method: "DELETE" }).then(() => undefined),
  },
};
