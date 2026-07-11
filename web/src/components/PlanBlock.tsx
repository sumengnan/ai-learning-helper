import { Box, Typography, CircularProgress } from "@mui/material";
import { EllipsisText } from "./EllipsisText";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import PlaylistAddCheckIcon from "@mui/icons-material/PlaylistAddCheck";

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

function stepIcon(status: PlanStatus) {
  if (status === "done") return <CheckCircleIcon sx={{ fontSize: 15 }} color="success" />;
  if (status === "failed") return <CancelIcon sx={{ fontSize: 15 }} color="error" />;
  if (status === "running") return <CircularProgress size={12} />;
  return <RadioButtonUncheckedIcon sx={{ fontSize: 15 }} color="disabled" />;
}

export function PlanBlock({ text }: { text?: string | null }) {
  const steps = parseSteps(text);
  if (!steps.length) return null;
  const done = steps.filter((s) => s.status === "done").length;
  // 标题后紧跟「当前步骤」预览：优先进行中，其次最后一个已完成，否则第一个
  const current = steps.find((s) => s.status === "running")
    ?? [...steps].reverse().find((s) => s.status === "done")
    ?? steps[0];
  return (
    <Box sx={{ mb: 1, p: 1, borderRadius: 1, bgcolor: "background.paper",
               border: 1, borderColor: "divider" }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mb: 0.5 }}>
        <PlaylistAddCheckIcon sx={{ fontSize: 16 }} color="action" />
        <Typography variant="caption" sx={{ fontWeight: 600, flexShrink: 0 }}>任务步骤</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
          {done}/{steps.length} 完成
        </Typography>
        <Box sx={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center" }}>
          <EllipsisText text={current.title} maxChars={24} sx={{ opacity: 0.85 }} />
        </Box>
      </Box>
      {steps.map((s, i) => (
        <Box key={i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.2 }}>
          {stepIcon(s.status)}
          <Typography
            variant="caption"
            sx={{
              color: s.status === "failed" ? "error.main" : "text.secondary",
              textDecoration: s.status === "done" ? "line-through" : "none",
            }}
          >
            {s.title}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
