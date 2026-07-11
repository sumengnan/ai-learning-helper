export type AgentEvent = { type: string; data: any };
export type Attachment = {
  id: string;
  filename: string;
  size: number;
  content_type: string;
};
// 一条 AI 回复的参考来源（由后端从工具调用提炼）。index 与正文内联 [n] 角标对应。
export type SourceType =
  | "knowledge" | "web" | "question" | "attachment" | "memory" | "code" | "mcp";
export type SourceItem = {
  index: number;
  type: SourceType;
  label: string;
  url?: string;      // web 类：可点击外链
  detail?: string;   // memory/code/mcp 类：就地展开的原始片段
};
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  steps?: { tool: string; args: any; result?: string; isError?: boolean }[];
  progress?: { scope: string; text: string; status?: "running" | "ok" | "error" | null; key?: string | null; agent?: string | null }[];
  sources?: SourceItem[];
  usage?: { tokens: number; cost: number | null };
  attachments?: Attachment[];
  // 助手回复状态：streaming=生成中；done=完成；error=失败；stopped=用户停止；interrupted=服务重启中断
  status?: "streaming" | "done" | "error" | "stopped" | "interrupted";
  runId?: string;   // 本轮 run 句柄（刷新后接回 / 停止用）
  startedAt?: number;   // 本轮开始的客户端时间戳（毫秒），用于实时耗时计数
  elapsedMs?: number;   // 本轮总耗时（毫秒），完成时冻结
};
export type Conversation = { id: string; title: string; created_at: string };
export type User = { id: string; username: string };
