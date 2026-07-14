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
  const checks = message.checks || [];
  const quality = message.quality || null;

  // 无任何校验信号 → 不渲染徽章
  if (verify.length === 0 && checks.length === 0 && !quality) return null;

  const vOk = verify.some((p) => p.status === "ok");
  const vErr = verify.some((p) => p.status === "error");
  const vLast = verify[verify.length - 1];
  const vRunning = vLast?.status === "running";

  // 状态机：未通过优先；其次通过；再次进行中；否则（流结束且无 error）视为通过
  const state: "running" | "ok" | "error" =
    vErr ? "error" : vOk ? "ok" : (live || vRunning) ? "running" : "ok";

  const stateIcon =
    state === "running" ? <CircularProgress size={14} />
      : state === "error" ? <CancelIcon sx={{ fontSize: 16 }} color="error" />
        : <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />;

  const label = state === "running" ? "验证中…" : state === "error" ? "未通过" : "校验通过";
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
    <Box sx={{ mt: 1 }}>
      <CollapsibleBlock icon={<FactCheckIcon sx={{ fontSize: 15 }} color="action" />}
        title="校验" status={state} summary={summary}>
        {checks.length > 0 && (
          <Box sx={{ mb: quality ? 1 : 0 }}>
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
        {/* checks / quality 都空但有 verify 信号时，展开面板给出 verify 明细行 */}
        {checks.length === 0 && !quality && verify.map((p, i) => (
          <Box key={p.key ?? i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.15 }}>
            {p.status === "error"
              ? <CancelIcon sx={{ fontSize: 14 }} color="error" />
              : p.status === "ok"
                ? <CheckCircleIcon sx={{ fontSize: 14 }} color="success" />
                : <CircularProgress size={11} />}
            <Typography variant="caption" color="text.secondary">{p.text}</Typography>
          </Box>
        ))}
      </CollapsibleBlock>
    </Box>
  );
}
