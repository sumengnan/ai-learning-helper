import type { AgentEvent, Attachment, Conversation, User } from "../types";

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

/** 消费一个 SSE 响应体：逐块解析事件回调，直到流结束或断开。 */
async function consumeSSE(resp: Response, onEvent: (e: AgentEvent) => void): Promise<void> {
  const reader = resp.body!.getReader();
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

export async function streamChat(
  conversationId: string, message: string, onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal, saveWrong = false, attachmentIds: string[] = [],
  onRunId?: (runId: string) => void,
): Promise<void> {
  const resp = await authFetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: conversationId, message, save_wrong: saveWrong,
      attachment_ids: attachmentIds,
    }),
    signal,
  });
  if (resp.status === 409) throw new Error("上一轮还在进行中，请稍候");
  if (!resp.ok || !resp.body) throw new Error(`chat 失败：${resp.status}`);
  // X-Run-Id：本轮句柄，供 stop / 刷新后接回
  const rid = resp.headers.get("X-Run-Id");
  if (rid) onRunId?.(rid);
  await consumeSSE(resp, onEvent);
}

/** 刷新后接回一个在途 run：回放已生成部分 + 实时续流。run 已结束返回 409（调用方改重载消息）。 */
export async function attachChat(
  runId: string, onEvent: (e: AgentEvent) => void, signal?: AbortSignal,
): Promise<void> {
  const resp = await authFetch(`/api/chat/attach/${runId}`, { signal });
  if (resp.status === 409) { const e = new Error("run 已结束"); e.name = "RunEnded"; throw e; }
  if (!resp.ok || !resp.body) throw new Error(`attach 失败：${resp.status}`);
  await consumeSSE(resp, onEvent);
}

/** 停止一个在途 run（取消后端后台任务，落已生成部分 + status=stopped）。 */
export async function stopRun(runId: string): Promise<void> {
  await authFetch(`/api/chat/stop/${runId}`, { method: "POST" }).catch(() => undefined);
}

/** 危险命令人工确认：把批准/拒绝决策回传给挂起的后端协程。 */
export async function sendDecision(
  runId: string, approvalId: string, approved: boolean,
): Promise<void> {
  const r = await authFetch(`/api/chat/${runId}/decision`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approval_id: approvalId, approved }),
  });
  if (!r.ok) throw new Error(`decision 失败：${r.status}`);
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
    attachments?: Attachment[] | null;
    run_id?: string | null;
    status?: string | null;
  }[]> =>
    authFetch(`/api/conversations/${id}/messages`).then((r) => r.json()),
  remove: (id: string): Promise<void> =>
    authFetch(`/api/conversations/${id}`, { method: "DELETE" }).then(() => undefined),
  documents: {
    list: (page = 1, size = 8): Promise<{
      items: { id: string; filename: string; size: number; num_chunks: number;
               uploaded_at: string; excerpt: string; category: string }[];
      total: number; total_chunks: number;
    }> =>
      authFetch(`/api/documents?page=${page}&size=${size}`).then((r) => r.json()),
    search: (q: string): Promise<{
      id: string; filename: string; uploaded_at: string;
      category: string; excerpt: string; relevance: number;
    }[]> =>
      authFetch(`/api/documents/search?q=${encodeURIComponent(q)}`).then((r) => r.json()),
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
  attachments: {
    upload: (convId: string, file: File): Promise<Attachment> => {
      const fd = new FormData(); fd.append("file", file);
      return authFetch(`/api/conversations/${convId}/attachments`, { method: "POST", body: fd })
        .then(async (r) => {
          if (!r.ok) throw new Error(await detail(r, `上传失败：${r.status}`));
          return r.json();
        });
    },
    remove: (id: string): Promise<void> =>
      authFetch(`/api/attachments/${id}`, { method: "DELETE" }).then(() => undefined),
    // 预览需带 Bearer 头，<img src> 无法携带，故取回鉴权后的 blob 供组件建 object URL
    blob: (id: string): Promise<Blob> =>
      authFetch(`/api/attachments/${id}`).then(async (r) => {
        if (!r.ok) throw new Error(`加载失败：${r.status}`);
        return r.blob();
      }),
  },
};
