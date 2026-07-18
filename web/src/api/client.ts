import type { AgentEvent, Attachment, Conversation, SourceItem, User } from "../types";

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

export interface VersionInfo {
  version: string;
  git_sha: string;
  built_at: string;
}

export interface Question {
  id: string;
  type: string;
  stem: string;
  options: string[] | null;
  answer: unknown;
  explanation: string;
  source: string;
  created_at: string;
}

/** 后端版本信息（公开端点，无需鉴权），用于页脚部署自检。 */
export async function fetchVersion(): Promise<VersionInfo> {
  const r = await fetch("/api/version");
  if (!r.ok) throw new Error("获取后端版本失败");
  return r.json();
}

export interface Captcha {
  token: string;
  image: string; // data URI（SVG）
}

export const auth = {
  // 公开端点：取一枚验证码（token + 图片），token 随登录/注册回传后端校验
  captcha: async (): Promise<Captcha> => {
    const r = await fetch("/api/auth/captcha");
    if (!r.ok) throw new Error("获取验证码失败");
    return r.json();
  },
  register: async (
    username: string, password: string,
    captchaToken = "", captchaText = "",
  ): Promise<{ token: string; user: User }> => {
    const r = await fetch("/api/auth/register", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username, password, captcha_token: captchaToken, captcha_text: captchaText,
      }),
    });
    if (!r.ok) throw new Error(await detail(r, "注册失败"));
    return r.json();
  },
  login: async (
    username: string, password: string,
    captchaToken = "", captchaText = "",
  ): Promise<{ token: string; user: User }> => {
    const r = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username, password, captcha_token: captchaToken, captcha_text: captchaText,
      }),
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
  signal?: AbortSignal, attachmentIds: string[] = [],
  onRunId?: (runId: string) => void, think = false, verify = true,
): Promise<void> {
  const resp = await authFetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: conversationId, message,
      attachment_ids: attachmentIds, think, verify,
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
  // 用第一句话自动命名对话（后端 LLM 提炼标题；失败兜底截断）
  autotitle: (id: string, message: string): Promise<{ title: string | null }> =>
    authFetch(`/api/conversations/${id}/autotitle`, { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }) }).then((r) => r.json()),
  messages: (id: string): Promise<{
    role: string; content: string;
    steps?: { tool: string; args: unknown; result?: string; is_error?: boolean }[] | null;
    progress?: { scope: string; text: string; status?: "running" | "ok" | "error" | null; key?: string | null; agent?: string | null; detail?: { tool?: string; args?: unknown; result?: string; is_error?: boolean; elapsed_ms?: number } | null }[] | null;
    sources?: SourceItem[] | null;
    attachments?: Attachment[] | null;
    run_id?: string | null;
    status?: string | null;
    tokens?: number | null;
    cost?: number | null;
    elapsed_ms?: number | null;
    reasoning?: string | null;
    reasoning_ms?: number | null;
  }[]> =>
    authFetch(`/api/conversations/${id}/messages`).then((r) => r.json()),
  remove: (id: string): Promise<void> =>
    authFetch(`/api/conversations/${id}`, { method: "DELETE" }).then(() => undefined),
  documents: {
    // 知识库以切分后的片段（chunk）为单元：每片一项，标注来源文件名
    list: (page = 1, size = 8, category = ""): Promise<{
      items: { id: string; filename: string; uploaded_at: string;
               category: string; excerpt: string }[];
      total: number;
    }> =>
      authFetch(`/api/documents?page=${page}&size=${size}`
        + (category ? `&category=${encodeURIComponent(category)}` : "")).then((r) => r.json()),
    search: (q: string): Promise<{
      id: string; filename: string; uploaded_at: string;
      category: string; excerpt: string; relevance: number;
    }[]> =>
      authFetch(`/api/documents/search?q=${encodeURIComponent(q)}`).then((r) => r.json()),
    get: (id: string): Promise<{
      id: string; filename: string; text: string;
      category: string; uploaded_at: string; doc_id: string;
    }> =>
      authFetch(`/api/documents/${id}`).then(async (r) => {
        if (!r.ok) throw new Error(await detail(r, `加载失败：${r.status}`));
        return r.json();
      }),
    // duplicate=true：内容与已有文档完全相同，后端未重复入库，id 指向原有那篇
    upload: (file: File): Promise<{ id: string; filename: string; num_chunks: number;
                                    duplicate: boolean }> => {
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
    list: (params: { page?: number; size?: number; type?: string; source?: string; q?: string } = {}):
      Promise<{ items: Question[]; total: number }> => {
      const sp = new URLSearchParams();
      sp.set("page", String(params.page ?? 1));
      sp.set("size", String(params.size ?? 20));
      if (params.type) sp.set("type", params.type);
      if (params.source) sp.set("source", params.source);
      if (params.q) sp.set("q", params.q);
      return authFetch(`/api/questions?${sp.toString()}`).then((r) => r.json());
    },
    sources: (): Promise<string[]> =>
      authFetch("/api/questions/sources").then((r) => r.json()),
    import: (file: File): Promise<{ imported: number; skipped_invalid: number; skipped_duplicate: number }> => {
      const fd = new FormData(); fd.append("file", file);
      return authFetch("/api/questions/import", { method: "POST", body: fd }).then(async (r) => {
        if (!r.ok) throw new Error(await detail(r, `导入失败：${r.status}`));
        return r.json();
      });
    },
    // 删题：force=false 时若有对应错题则不删，返回 {deleted:false, related_wrong:N} 供前端确认；
    // force=true 则连带删除对应错题。
    remove: (id: string, force = false): Promise<{ deleted: boolean; related_wrong: number }> =>
      authFetch(`/api/questions/${id}${force ? "?force=true" : ""}`, { method: "DELETE" })
        .then((r) => r.json()),
    removeMany: (ids: string[]): Promise<void> =>
      authFetch("/api/questions/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      }).then(() => undefined),
  },
  wrong: {
    list: (params: { page?: number; size?: number; type?: string; q?: string } = {}) => {
      const sp = new URLSearchParams();
      sp.set("page", String(params.page ?? 1));
      sp.set("size", String(params.size ?? 20));
      if (params.type) sp.set("type", params.type);
      if (params.q) sp.set("q", params.q);
      return authFetch(`/api/wrong-answers?${sp.toString()}`).then((r) => r.json());
    },
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
    // 预览/缩略图需带 Bearer 头，<img src> 无法携带，故取回鉴权 blob 供组件建 object URL
    blob: (id: string): Promise<Blob> =>
      authFetch(`/api/downloads/${id}`).then(async (r) => {
        if (!r.ok) throw new Error(await detail(r, `加载失败：${r.status}`));
        return r.blob();
      }),
    // 鉴权 blob → object URL → 触发浏览器保存（下载菜单与聊天页共用）
    save: async (id: string, filename: string): Promise<void> => {
      const r = await authFetch(`/api/downloads/${id}`);
      if (!r.ok) throw new Error(await detail(r, `下载失败：${r.status}`));
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    },
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
