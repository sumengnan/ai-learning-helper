import { Box, Typography, CircularProgress } from "@mui/material";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import { CollapsibleBlock } from "./CollapsibleBlock";
import { EllipsisText } from "./EllipsisText";
import type { ChatMessage } from "../types";

// 质量分展示：null/undefined → 「—」
const fmtScore = (n?: number | null) => (n === null || n === undefined ? "—" : String(n));

// 从 progress 里重建轨迹质量分（scope="quality"，text 为 JSON）。
// message.quality 只在实时 SSE 时由 ChatView 赋值，刷新后走 ui_messages 加载则没有；
// 但 _emit_quality 同时把这条写进了 progress 列，故可从那里还原。解析失败当作没有。
function qualityFromProgress(progress: ChatMessage["progress"]): ChatMessage["quality"] {
  const items = (progress || []).filter((p) => p.scope === "quality");
  const last = items[items.length - 1];
  if (!last?.text) return null;
  try {
    const q = JSON.parse(last.text);
    return q && typeof q === "object" ? q : null;
  } catch {
    return null;      // 脏数据不该让整个气泡崩掉
  }
}

// 展开明细里的来源分组标题（步骤校验 / 结果校验）
const SectionLabel = ({ text }: { text: string }) => (
  <Typography variant="caption" color="text.disabled"
    sx={{ display: "block", fontWeight: 600, letterSpacing: 0.3, py: 0.15 }}>
    {text}
  </Typography>
);

// 校验常驻徽章：脱离「展示工具调用」开关，恒在 assistant 气泡底部展示本轮校验/质量状态。
//
// 两套彼此独立的校验机制共用本徽章，主行文案必须区分二者——否则用户关掉聊天页「结果校验」开关后，
// 仅由每步校验触发的徽章仍写「校验通过」，等于谎称结果被校验过：
// - 结果校验：交付门（scope=verify，受聊天页开关 + 服务端 enable_answer_gate 双重控制）与轨迹
//   质量分（quality，由 trajectory judge 产出，同属结果层）→「结果校验通过/未通过」
// - 步骤校验：工具执行的实时标记（scope=check，由服务端 enable_step_check 控制、默认开，
//   聊天页无开关）→「步骤校验通过/未通过」
//
// - 进行中（本轮 streaming 或收到 verify running）→ spinner + 当前过程文案
// - 通过 → 「<层>校验通过」+ 若有 quality.final 显示「质量 N」
// - 未通过 → 红色「<层>校验未通过」+ 末条 verify 文案（summary）
// 展开明细按来源分组标注（步骤校验 / 结果校验 / 三层质量分），单看一行也知道它属于哪层。
// 仅当本轮有 verify/check/quality 任一信号时渲染，否则返回 null。
export function VerifyBadge({ message, live = false }: { message: ChatMessage; live?: boolean }) {
  const verify = (message.progress || []).filter((p) => p.scope === "verify");
  // 检索命中是正常情形，不作为校验状态展示（仅保留失败/未命中等有意义的每步校验）
  const checks = (message.checks || []).filter(
    (c) => !(c.tool === "search_memory" && c.status === "ok"));
  // quality 只在实时 SSE 时被 ChatView 赋值；刷新后从 progress 里的 scope="quality"
  // 条目重建（_emit_quality 把同一份 JSON 既推事件也写进 progress 列），否则质量分
  // 徽章刷新即消失，而数据其实一直在。
  const quality = message.quality || qualityFromProgress(message.progress);

  // 无任何校验信号 → 不渲染徽章
  if (verify.length === 0 && checks.length === 0 && !quality) return null;

  const vLast = verify[verify.length - 1];
  // 校验历史：每一轮的终态（通过/未通过），失败轮保留原因；重答后新增新记录、通过后亦不清除
  const rounds = verify.filter((p) => p.status === "ok" || p.status === "error");
  // 仅当有多轮或出现过失败时展示历史（单轮直接通过无「过程」可留，主行已足够）
  const showHistory = rounds.length > 1 || rounds.some((p) => p.status === "error");
  const checkErr = checks.some((c) => c.status === "error");

  // 本轮结果层是否真的跑过：交付门事件或轨迹质量分任一即算。二者皆无时只剩每步校验信号，
  // 主行须降级说「步骤校验」——这正是结果校验开关关闭时的情形。
  const kind = verify.length > 0 || quality ? "结果校验" : "步骤校验";

  // 状态以「最后一个 verify 事件」为准：running 显示当前过程（校验中…/重答中…，故未通过→重答中→
  // 通过是连续过渡），ok/error 为终态。无 verify 事件时不谎称「验证中」，按每步校验有无失败定 ok/error。
  const state: "running" | "ok" | "error" =
    vLast?.status === "running" ? "running"
      : vLast?.status === "error" ? "error"
        : vLast?.status === "ok" ? "ok"
          : checkErr ? "error" : "ok";
  void live;

  const stateIcon =
    state === "running" ? <CircularProgress size={14} />
      : state === "error" ? <CancelIcon sx={{ fontSize: 16 }} color="error" />
        : <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />;

  // 进行中：原地显示当前过程文案（校验中…/重答中…，均出自 verify 事件），出结果后替换为终态文案
  const label = state === "running" ? (vLast?.text || "验证中…")
    : state === "error" ? `${kind}未通过` : `${kind}通过`;
  const qFinal = quality ? quality.final : undefined;

  const summary = (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, minWidth: 0, flex: 1 }}>
      {stateIcon}
      <Typography variant="caption"
        color={state === "error" ? "error" : "text.primary"}
        sx={{ fontWeight: 600, flexShrink: 0 }}>
        {label}
      </Typography>
      {state === "ok" && qFinal !== undefined && qFinal !== null && (
        <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
          · 质量 {qFinal}
        </Typography>
      )}
      {state === "error" && vLast?.text && (
        <EllipsisText text={vLast.text} sx={{ ml: 0.5, color: "text.secondary" }} maxChars={28} />
      )}
    </Box>
  );

  return (
    <Box>
      {/* 块名保持通用的「校验」——本块可同时含两层信号，具体是哪层由主行文案与展开分组标题交代 */}
      <CollapsibleBlock icon={<FactCheckIcon sx={{ fontSize: 15 }} color="action" />}
        title="校验" status={state} summary={summary}>
        {/* 步骤校验（scope=check，工具执行时发生、时间靠前）——带来源小标题，
            免得在结果校验也在场时被误读成交付门的明细 */}
        {checks.length > 0 && (
          <Box sx={{ mb: (showHistory || quality) ? 1 : 0 }}>
            <SectionLabel text="步骤校验" />
            {checks.map((c, i) => (
              <Box key={c.tool + i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.15 }}>
                {c.status === "error"
                  ? <CancelIcon sx={{ fontSize: 14 }} color="error" />
                  : <CheckCircleIcon sx={{ fontSize: 14 }} color="success" />}
                <Typography variant="caption" color="text.secondary">{c.text}</Typography>
              </Box>
            ))}
          </Box>
        )}
        {/* 结果校验：交付门历史（较晚发生），失败轮保留「哪层没过 + 原因」，通过后亦不清除 */}
        {showHistory && (
          <Box sx={{ mb: quality ? 1 : 0 }}>
            <SectionLabel text="结果校验" />
            {rounds.map((p, idx) => (
              <Box key={idx} sx={{ display: "flex", alignItems: "flex-start", gap: 0.75, py: 0.15 }}>
                {p.status === "error"
                  ? <CancelIcon sx={{ fontSize: 14, mt: 0.15 }} color="error" />
                  : <CheckCircleIcon sx={{ fontSize: 14, mt: 0.15 }} color="success" />}
                <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "pre-wrap" }}>
                  {rounds.length > 1 ? `第 ${idx + 1} 次：` : ""}
                  {p.status === "error" ? `未通过 — ${p.text}` : "校验通过"}
                </Typography>
              </Box>
            ))}
          </Box>
        )}
        {quality && (
          <Box>
            <Box sx={{ display: "flex", gap: 1.5, flexWrap: "wrap", py: 0.15 }}>
              <Typography variant="caption" color="text.secondary">拆分 {fmtScore(quality.plan)}</Typography>
              <Typography variant="caption" color="text.secondary">关键步 {fmtScore(quality.steps)}</Typography>
              <Typography variant="caption" color="text.secondary">最终 {fmtScore(quality.final)}</Typography>
            </Box>
            {quality.feedback && (
              <Typography variant="caption" color="text.secondary"
                sx={{ display: "block", mt: 0.25, whiteSpace: "pre-wrap" }}>
                {quality.feedback}
              </Typography>
            )}
          </Box>
        )}
      </CollapsibleBlock>
    </Box>
  );
}
