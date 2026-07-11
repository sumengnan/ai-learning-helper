import type { ReactNode } from "react";
import {
  Accordion, AccordionSummary, AccordionDetails, Typography, CircularProgress, Box,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import StopCircleIcon from "@mui/icons-material/StopCircle";

// 可折叠的过程块（工具调用 / 沙箱执行 / 子代理执行 / 任务步骤 统一外观）
// summary：标题右侧显示的“最后一步进度”预览；给了就用它替代整体状态图标
// stopped：用户主动停止时的终态（灰色停止图标，区别于成功/失败）
export function CollapsibleBlock({ icon, title, status, summary, defaultExpanded = false, children }: {
  icon: ReactNode;
  title: string;
  status: "running" | "ok" | "error" | "stopped";
  summary?: ReactNode;
  defaultExpanded?: boolean;
  children: ReactNode;
}) {
  return (
    <Accordion
      defaultExpanded={defaultExpanded} disableGutters elevation={0}
      sx={{
        mb: 1, border: 1, borderColor: "divider", borderRadius: 1.5,
        bgcolor: "background.default", overflow: "hidden",
        "&:before": { display: "none" },
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreIcon fontSize="small" />}
        sx={{
          minHeight: 0, px: 1,
          "& .MuiAccordionSummary-content": { my: 0.75, alignItems: "center", gap: 0.75 },
        }}
      >
        {icon}
        <Typography variant="caption" sx={{ fontWeight: 700, flexShrink: 0, mr: 1 }}>
          {title}
        </Typography>
        {summary ? (
          <Box sx={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center" }}>
            {summary}
          </Box>
        ) : status === "running" ? (
          <CircularProgress size={14} sx={{ ml: "auto" }} />
        ) : status === "error" ? (
          <CancelIcon sx={{ fontSize: 16, ml: "auto" }} color="error" />
        ) : status === "stopped" ? (
          <StopCircleIcon sx={{ fontSize: 16, ml: "auto" }} color="disabled" />
        ) : (
          <CheckCircleIcon sx={{ fontSize: 16, ml: "auto" }} color="success" />
        )}
      </AccordionSummary>
      <AccordionDetails sx={{ px: 1, pt: 0, pb: 1 }}>
        {children}
      </AccordionDetails>
    </Accordion>
  );
}
