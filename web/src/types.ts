export type AgentEvent = { type: string; data: any };
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  steps?: { tool: string; args: any; result?: string; isError?: boolean }[];
  progress?: { scope: string; text: string; status?: "running" | "ok" | "error" | null; key?: string | null }[];
  usage?: { tokens: number; cost: number | null };
};
export type Conversation = { id: string; title: string; created_at: string };
export type User = { id: string; username: string };
