import { authFetch } from "./client";

export interface Ability { icon: string; label: string; count: number }
export interface DailyPoint { date: string; runs: number; tokens: number }
export interface ToolStat { name: string; count: number; errors: number; success_rate: number }
export interface StepBucket { bucket: string; count: number }
export interface RecentDownload { id: string; filename: string; content_type: string; size: number; created_at: string }
export interface MemoryItem { text: string; collection: string; created_at: string }

export interface StatsOverview {
  range_days: number;
  learn: {
    assets: { documents: number; memory: number; questions: number; wrong_answers: number };
    conversations: number;
    messages: number;
    recent_downloads: RecentDownload[];
    last_conversation: { id: string; title: string; updated_at: string; message_count: number } | null;
    abilities: Ability[];
    effort: { runs: number; total_tokens: number; avg_steps: number; max_steps: number; success_rate: number };
    activity: DailyPoint[];
  };
  ops: {
    totals: {
      runs: number; runs_finished: number; runs_error: number; success_rate: number;
      model_calls: number; total_tokens: number; total_prompt: number; total_completion: number;
      avg_latency_ms: number; p95_latency_ms: number; retries: number; cost_usd: number | null;
      conversations: number; messages: number;
    };
    daily: DailyPoint[];
    tools: ToolStat[];
    steps_histogram: StepBucket[];
  };
}

export const statsApi = {
  overview: (days = 14): Promise<StatsOverview> =>
    authFetch(`/api/stats/overview?days=${days}`).then((r) => {
      if (!r.ok) throw new Error(`加载失败：${r.status}`);
      return r.json();
    }),
  memory: (limit = 50): Promise<MemoryItem[]> =>
    authFetch(`/api/stats/memory?limit=${limit}`).then((r) => {
      if (!r.ok) throw new Error(`加载失败：${r.status}`);
      return r.json();
    }),
};
