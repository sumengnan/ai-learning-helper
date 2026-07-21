import { Box, Typography } from "@mui/material";
import BoltIcon from "@mui/icons-material/Bolt";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import type { ChatMessage } from "../types";

// 编排器在分流处下发的路由结论（app/orchestration/orchestrator.py，scope=route）。
// 走编排器时用户能看到「任务步骤」块，走简单直答时一个块都没有——此前只能靠「有没有块」
// 反推，triage 误判（该拆步却判了 simple）便无从察觉。这条徽章把结论显式说出来。
export type RouteMode = "simple" | "plan";

const LABEL: Record<RouteMode, string> = {
  simple: "简单直答",
  plan: "多步规划",
};

// detail.mode 是权威字段；text 只是给人看的文案，不参与判定（改文案不该改坏渲染）。
export function routeModeOf(progress: ChatMessage["progress"]): RouteMode | null {
  const items = (progress || []).filter((p) => p.scope === "route");
  const last = items[items.length - 1];
  const mode = last?.detail?.mode;
  return mode === "simple" || mode === "plan" ? mode : null;
}

export function RouteBadge({ mode }: { mode: RouteMode }) {
  const Icon = mode === "simple" ? BoltIcon : AccountTreeOutlinedIcon;
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.75 }}>
      <Icon sx={{ fontSize: 14, color: "text.disabled" }} />
      <Typography variant="caption" sx={{ color: "text.disabled", lineHeight: 1 }}>
        {LABEL[mode]}
      </Typography>
    </Box>
  );
}
