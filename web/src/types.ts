export type AgentEvent = { type: string; data: any };
export type Attachment = {
  id: string;
  filename: string;
  size: number;
  content_type: string;
};
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  steps?: { tool: string; args: any; result?: string; isError?: boolean }[];
  progress?: { scope: string; text: string; status?: "running" | "ok" | "error" | null; key?: string | null }[];
  usage?: { tokens: number; cost: number | null };
  attachments?: Attachment[];
  // 助手回复状态：streaming=生成中；done=完成；error=失败；stopped=用户停止
  status?: "streaming" | "done" | "error" | "stopped";
};
export type Conversation = { id: string; title: string; created_at: string };
export type User = { id: string; username: string };
