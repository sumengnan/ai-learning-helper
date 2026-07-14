import { Box, Typography, CircularProgress } from "@mui/material";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import { CollapsibleBlock } from "./CollapsibleBlock";
import { EllipsisText } from "./EllipsisText";
import type { ChatMessage } from "../types";

// 质量分展示：null/undefined → 「—」
const fmtScore = (n?: number | null) => (n === null || n === undefined ? "—" : String(n));

// 结果校验常驻徽章：脱离「展示工具调用」开关，恒在 assistant 气泡底部展示本轮校验/质量状态。
// - 进行中（本轮 streaming 或收到 verify running）→ spinner「验证中…」
// - 通过（verify ok，或流结束且无 verify error）→「校验通过」+ 若有 quality.final 显示「质量 N」
// - 未通过（verify error）→ 红色「未通过」+ 末条 verify 文案（summary）
// 展开明细：每步校验标记 checks[] + 轨迹 judge 三层质量分 quality。
// 仅当本轮有 verify/check/quality 任一信号时渲染，否则返回 null。
export function VerifyBadge({ message, live = false }: { message: ChatMessage; live?: boolean }) {
  const verify = (message.progress || []).filter((p) => p.scope === "verify");
  // 检索命中是正常情形，不作为校验状态展示（仅保留失败/未命中等有意义的每步校验）
  const checks = (message.checks || []).filter(
    (c) => !(c.tool === "search_memory" && c.status === "ok"));
  const quality = message.quality || null;

  // 无任何校验信号 → 不渲染徽章
  if (verify.length === 0 && checks.length === 0 && !quality) return null;

  const vLast = verify[verify.length - 1];
  // 校验历史：每一轮的终态（通过/未通过），失败轮保留原因；重答后新增新记录、通过后亦不清除
  const rounds = verify.filter((p) => p.status === "ok" || p.status === "error");
  // 仅当有多轮或出现过失败时展示历史（单轮直接通过无「过程」可留，主行已足够）
  const showHistory = rounds.length > 1 || rounds.some((p) => p.status === "error");
  const checkErr = checks.some((c) => c.status === "error");

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

  // 进行中：原地显示当前过程文案（校验中…/重答中…），出结果后替换为终态文案（而非堆日志）
  const label = state === "running" ? (vLast?.text || "验证中…")
    : state === "error" ? "未通过" : "校验通过";
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
      <CollapsibleBlock icon={<FactCheckIcon sx={{ fontSize: 15 }} color="action" />}
        title="校验" status={state} summary={summary}>
        {/* 每步校验（检索命中/代码执行）——发生时间靠前，列在最上 */}
        {checks.length > 0 && (
          <Box sx={{ mb: (showHistory || quality) ? 1 : 0 }}>
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
        {/* 最终交付门校验历史（较晚发生）：失败轮保留「哪层没过 + 原因」，通过后亦不清除 */}
        {showHistory && (
          <Box sx={{ mb: quality ? 1 : 0 }}>
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
