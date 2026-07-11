import { Box, Typography, CircularProgress } from "@mui/material";
import { EllipsisText } from "./EllipsisText";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import PlaylistAddCheckIcon from "@mui/icons-material/PlaylistAddCheck";
import { CollapsibleBlock } from "./CollapsibleBlock";

type PlanStatus = "pending" | "running" | "done" | "failed";
type PlanStepData = { title: string; status: PlanStatus };

function parseSteps(text?: string | null): PlanStepData[] {
  if (!text) return [];
  try {
    const arr = JSON.parse(text);
    if (!Array.isArray(arr)) return [];
    return arr.filter((s) => s && typeof s.title === "string");
  } catch {
    return [];
  }
}

// live=生成中才转圈；停止/结束后 running 步骤按「已取消/未完成」呈现，不再永远转圈
function stepIcon(status: PlanStatus, live: boolean, stopped: boolean) {
  if (status === "done") return <CheckCircleIcon sx={{ fontSize: 15 }} color="success" />;
  if (status === "failed") return <CancelIcon sx={{ fontSize: 15 }} color="error" />;
  if (status === "running") {
    if (live) return <CircularProgress size={12} />;
    if (stopped) return <StopCircleIcon sx={{ fontSize: 15 }} color="disabled" />;
  }
  return <RadioButtonUncheckedIcon sx={{ fontSize: 15 }} color="disabled" />;
}

// 任务步骤：可折叠（issue 3）；用户停止后运行中的步骤标「已取消」（issue 2）
export function PlanBlock({ text, live = false, stopped = false }: {
  text?: string | null; live?: boolean; stopped?: boolean;
}) {
  const steps = parseSteps(text);
  if (!steps.length) return null;
  const done = steps.filter((s) => s.status === "done").length;
  const anyFailed = steps.some((s) => s.status === "failed");
  const anyRunning = steps.some((s) => s.status === "running");
  const blockStatus: "running" | "ok" | "error" | "stopped" =
    anyFailed ? "error"
      : live && anyRunning ? "running"
        : stopped && anyRunning ? "stopped"
          : "ok";
  // 标题后紧跟「当前步骤」预览：优先进行中，其次最后一个已完成，否则第一个；超长截断为 …
  const current = steps.find((s) => s.status === "running")
    ?? [...steps].reverse().find((s) => s.status === "done")
    ?? steps[0];
  const summary = (
    <>
      <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
        {done}/{steps.length} 完成
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", ml: 0.5 }}>
        <EllipsisText text={current.title} maxChars={24} sx={{ opacity: 0.85 }} />
      </Box>
    </>
  );
  return (
    <CollapsibleBlock
      icon={<PlaylistAddCheckIcon sx={{ fontSize: 16 }} color="action" />}
      title="任务步骤" status={blockStatus} summary={summary} defaultExpanded
    >
      {steps.map((s, i) => {
        const cancelled = !live && stopped && s.status === "running";
        return (
          <Box key={i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.2 }}>
            {stepIcon(s.status, live, stopped)}
            <Typography
              variant="caption"
              sx={{
                color: s.status === "failed" ? "error.main" : "text.secondary",
                textDecoration: s.status === "done" ? "line-through" : "none",
              }}
            >
              {s.title}{cancelled ? "（已取消）" : ""}
            </Typography>
          </Box>
        );
      })}
    </CollapsibleBlock>
  );
}
