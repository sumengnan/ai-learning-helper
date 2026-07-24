import { forwardRef, type ReactNode } from "react";
import { Box, CircularProgress, Tooltip, useTheme } from "@mui/material";
import { alpha } from "@mui/material/styles";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutlineOutlined";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import AccessTimeIcon from "@mui/icons-material/AccessTime";
import TollIcon from "@mui/icons-material/Toll";
import { RollingNumber } from "./RollingNumber";
import { fmtDuration } from "./duration";
import { LiveDuration } from "./LiveDuration";
import type { ChatMessage } from "../types";

// 一枚彩色药丸：图标 + 内容，底色取自语义色的浅色调，辨识度高且与正文明显不同。
// forwardRef + 透传 props：让外层 MUI Tooltip 能挂 ref 与 hover 事件（token 药丸用到）。
const Pill = forwardRef<HTMLSpanElement, {
  icon: ReactNode; color: string; title?: string; children: ReactNode;
}>(function Pill({ icon, color, title, children, ...rest }, ref) {
  return (
    <Box component="span" ref={ref} title={title} {...rest} sx={{
      display: "inline-flex", alignItems: "center", gap: 0.5,
      px: 0.9, py: 0.3, borderRadius: 999, whiteSpace: "nowrap",
      bgcolor: alpha(color, 0.13), color,
      fontSize: 11.5, fontWeight: 600, lineHeight: 1.5,
      fontVariantNumeric: "tabular-nums",
      "& svg": { fontSize: 14 },
    }}>
      {icon}{children}
    </Box>
  );
});

// 助手回复的元信息条：状态 / 耗时 / tokens 各为一枚独立药丸，一眼可辨。
export function MessageMeta({ status, live, startedAt, elapsedMs, usage, usageByModel, showMeta }: {
  status?: ChatMessage["status"];
  live: boolean;
  startedAt?: number;
  elapsedMs?: number;
  usage?: { tokens: number; cost: number | null };
  usageByModel?: Record<string, { tokens: number; cost: number | null }>;   // 分模型明细（hover 展示）
  showMeta: boolean;   // 「展示工具调用和 Token」开关：仅控制 tokens 是否显示（耗时始终显示）
}) {
  const t = useTheme();
  const S = t.palette;

  const statusPill = (() => {
    if (live) return (
      <Pill icon={<CircularProgress size={12} thickness={5} />} color={S.primary.main}>生成中</Pill>
    );
    switch (status) {
      case "done": return <Pill icon={<CheckCircleIcon />} color={S.success.main}>已完成</Pill>;
      case "error": return <Pill icon={<ErrorOutlineIcon />} color={S.error.main}>回复失败</Pill>;
      case "stopped": return <Pill icon={<StopCircleIcon />} color={S.text.disabled}>已停止</Pill>;
      case "interrupted": return (
        <Pill icon={<ErrorOutlineIcon />} color={S.warning.main} title="服务重启导致中断">已中断</Pill>
      );
      default: return null;
    }
  })();

  // 耗时始终显示（生成中实时增长、完成后固定值），不受「展示 Token」开关控制；只有 token 受开关控制
  const showElapsed = (live && startedAt != null) || elapsedMs != null;
  const showTokens = showMeta && !!usage;
  if (!statusPill && !showElapsed && !showTokens) return null;

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, flexWrap: "wrap" }}>
      {statusPill}
      {showElapsed && (
        <Pill icon={<AccessTimeIcon />} color={S.info.main} title="本轮耗时">
          {live && startedAt != null ? <LiveDuration startedAt={startedAt} /> : fmtDuration(elapsedMs!)}
        </Pill>
      )}
      {showTokens && usage && (() => {
        // 合计本轮所有模型；hover 展示分模型明细（模型名：tokens · ¥）
        const entries = Object.entries(usageByModel || {});
        const title = entries.length
          ? "本轮各模型用量：\n" + entries.map(([m, u]) =>
              `${m}：${u.tokens} tokens${u.cost != null ? ` · ¥${u.cost.toFixed(4)}` : ""}`).join("\n")
          : "本轮 token 用量（所有模型合计）";
        // 用 MUI Tooltip 而非原生 title：原生 title 弹出延迟由浏览器固定（~1s+）无法调，
        // 太慢。enterDelay 调小让明细几乎即时弹出。多行明细用 pre-line 保留换行。
        return (
          <Tooltip placement="top" enterDelay={150} enterNextDelay={150}
            title={<Box sx={{ whiteSpace: "pre-line" }}>{title}</Box>}>
            <Pill icon={<TollIcon />} color={S.secondary.main}>
              <RollingNumber value={usage.tokens} /><Box component="span" sx={{ ml: 0.4, opacity: 0.8 }}>tokens</Box>
              {usage.cost != null ? <Box component="span" sx={{ ml: 0.4, opacity: 0.8 }}>· ¥{usage.cost.toFixed(4)}</Box> : null}
              {entries.length > 1 ? <Box component="span" sx={{ ml: 0.4, opacity: 0.6 }}>· {entries.length} 模型</Box> : null}
            </Pill>
          </Tooltip>
        );
      })()}
    </Box>
  );
}
