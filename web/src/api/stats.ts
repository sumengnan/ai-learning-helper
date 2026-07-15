import { authFetch } from "./client";

export interface Ability { icon: string; label: string; count: number }
export interface DailyPoint { date: string; runs: number; tokens: number }
export interface ToolStat { name: string; count: number; errors: number; success_rate: number }
export interface StepBucket { bucket: string; count: number }
export interface RecentDownload { id: string; filename: string; content_type: string; size: number; created_at: string }
export interface MemoryItem { id: string; text: string; collection: string; created_at: string }
export interface GateLayer { layer: string; zh: string; count: number }
/** 交付门重答统计（读 conversation_messages.verify 列）。
 *  注意 retries 与 ops.totals.retries 是两回事：那个是 LLM 网络重试。 */
export interface GateStat {
  turns: number;
  retries: number;
  avg_retries: number;
  degraded: number;          // 用尽重答次数仍不过 → 带 ⚠️ 降级交付
  degraded_rate: number;
  first_pass_rate: number;   // 一次就过的比例
  gate_errors: number;       // 校验器自身故障而跳过校验（fail-open）的轮数：这些「通过」并非真通过
  layer_failures: GateLayer[];
}
/** 轨迹 judge 的分层质量分。judge 默认关闭，故常态可能全为空/0。 */
export interface QualityStat {
  scored_turns: number;
  avg_final: number | null;
  avg_plan: number | null;
  avg_steps: number | null;
  distribution: StepBucket[];      // 与步数直方图同形状，复用 StepsHistogram 渲染
}

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
      cost_currency: string;
      conversations: number; messages: number;
    };
    daily: DailyPoint[];
    tools: ToolStat[];
    steps_histogram: StepBucket[];
    // 注意口径：totals/daily/tools/steps_histogram 是全局的（轨迹库无 user_id），
    // 而 gate 与 quality 按当前用户隔离
    gate: GateStat;
    quality: QualityStat;
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
  deleteMemory: (id: string): Promise<void> =>
    authFetch(`/api/stats/memory/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => {
      if (!r.ok) throw new Error(`删除失败：${r.status}`);
    }),
};
