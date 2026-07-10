import type { AgentEvent, Conversation, User } from "../types";

const TOKEN_KEY = "auth_token";
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

/** 统一 fetch：注入 Authorization 头、吸收 X-Refresh-Token 续期、401 触发登出。 */
export async function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const resp = await fetch(input, { ...init, headers });
  const refreshed = resp.headers.get("X-Refresh-Token");
  if (refreshed) setToken(refreshed);
  if (resp.status === 401) {
    setToken(null);
    onUnauthorized?.();
  }
  return resp;
}

async function detail(r: Response, fallback: string): Promise<string> {
  return (await r.json().catch(() => ({})))?.detail || fallback;
}

export const auth = {
  register: async (username: string, password: string): Promise<{ token: string; user: User }> => {
    const r = await fetch("/api/auth/register", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!r.ok) throw new Error(await detail(r, "注册失败"));
    return r.json();
  },
  login: async (username: string, password: string): Promise<{ token: string; user: User }> => {
    const r = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!r.ok) throw new Error(await detail(r, "登录失败"));
    return r.json();
  },
};

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
  signal?: AbortSignal, saveWrong = false,
): Promise<void> {
  const resp = await authFetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, message, save_wrong: saveWrong }),
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
  list: (): Promise<Conversation[]> => authFetch("/api/conversations").then((r) => r.json()),
  create: (title?: string): Promise<{ id: string }> =>
    authFetch("/api/conversations", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }) }).then((r) => r.json()),
  rename: (id: string, title: string): Promise<void> =>
    authFetch(`/api/conversations/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }) }).then(() => undefined),
  messages: (id: string): Promise<{
    role: string; content: string;
    steps?: { tool: string; args: unknown; result?: string; is_error?: boolean }[] | null;
    progress?: { scope: string; text: string; status?: "running" | "ok" | "error" | null; key?: string | null }[] | null;
  }[]> =>
    authFetch(`/api/conversations/${id}/messages`).then((r) => r.json()),
  remove: (id: string): Promise<void> =>
    authFetch(`/api/conversations/${id}`, { method: "DELETE" }).then(() => undefined),
  documents: {
    list: (): Promise<{ id: string; filename: string; num_chunks: number; uploaded_at: string }[]> =>
      authFetch("/api/documents").then((r) => r.json()),
    upload: (file: File) => {
      const fd = new FormData(); fd.append("file", file);
      return authFetch("/api/documents", { method: "POST", body: fd }).then(async (r) => {
        if (!r.ok) throw new Error(await detail(r, `上传失败：${r.status}`));
        return r.json();
      });
    },
    remove: (id: string): Promise<void> =>
      authFetch(`/api/documents/${id}`, { method: "DELETE" }).then(() => undefined),
  },
  questions: {
    generate: (topic: string, count: number, types: string[]) =>
      authFetch("/api/questions/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic, count, types }),
      }).then(async (r) => {
        if (!r.ok) throw new Error(await detail(r, "出题失败"));
        return r.json();
      }),
    list: () => authFetch("/api/questions").then((r) => r.json()),
    remove: (id: string) =>
      authFetch(`/api/questions/${id}`, { method: "DELETE" }).then(() => undefined),
    removeMany: (ids: string[]) =>
      authFetch("/api/questions/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      }).then(() => undefined),
  },
  wrong: {
    list: () => authFetch("/api/wrong-answers").then((r) => r.json()),
    removeMany: (ids: string[]) =>
      authFetch("/api/wrong-answers/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      }).then(() => undefined),
  },
  downloads: {
    list: () => authFetch("/api/downloads").then((r) => r.json()),
    remove: (id: string) =>
      authFetch(`/api/downloads/${id}`, { method: "DELETE" }).then(() => undefined),
  },
};
