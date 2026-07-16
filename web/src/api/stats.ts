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
  degraded: number;          // 用尽重答次数仍不过 → 保留最后一版、红徽章标未通过
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
/** 分层上下文（L1 窗口 / L2 摘要 / L3 检索）。仅 layered 策略产出，默认 full 故常态为 0。 */
export interface ContextStat {
  turns: number;                   // 有上下文记录的轮数（含 full）
  layered_turns: number;           // 其中走 layered 的轮数（下列指标的分母）
  evicted_total: number;           // 累计被挤出 L1 的历史条数
  // 挤出了历史、却没摘要成功的轮数 —— 这些轮模型是真丢了一段历史且不自知。
  // 不是 summary_errors：没挤出东西时摘要失败无害，拿那个当告警会天天误报。
  amnesia_turns: number;
  summary_errors: number;
  retrieval_errors: number;        // L3 挂了只是少了增益，与失忆不是一回事
  summary_ok: number;
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
    // 口径：本区（AI 运行统计）**整片全局、不按用户切** —— 轨迹库无 user_id 本就切不了，
    // gate/quality/context 若按用户切会和同页其它指标对不上（见 stats.py::_ops_section）。
    gate: GateStat;
    quality: QualityStat;
    context: ContextStat;
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
