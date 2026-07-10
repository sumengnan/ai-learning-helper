import { Box, Typography, CircularProgress } from "@mui/material";
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
  return (
    <Box sx={{ mb: 1, p: 1, borderRadius: 1, bgcolor: "background.paper",
               border: 1, borderColor: "divider" }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mb: 0.5 }}>
        <PlaylistAddCheckIcon sx={{ fontSize: 16 }} color="action" />
        <Typography variant="caption" sx={{ fontWeight: 600 }}>任务步骤</Typography>
        <Typography variant="caption" color="text.secondary">{done}/{steps.length} 完成</Typography>
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
