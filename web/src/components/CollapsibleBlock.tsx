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
export function CollapsibleBlock({ icon, title, status, summary, defaultExpanded = false, large = false, children }: {
  icon: ReactNode;
  title: string;
  status: "running" | "ok" | "error" | "stopped";
  summary?: ReactNode;
  defaultExpanded?: boolean;
  large?: boolean;   // 强调型块头（更大更粗的标题，与内部条目拉开层级），用于「任务步骤」总块
  children: ReactNode;
}) {
  return (
    <Accordion
      defaultExpanded={defaultExpanded} disableGutters elevation={0}
      sx={{
        mb: 1, border: 1, borderColor: large ? "primary.main" : "divider",
        borderRadius: 1.5, bgcolor: "background.default", overflow: "hidden",
        "&:before": { display: "none" },
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreIcon fontSize={large ? "medium" : "small"} />}
        sx={{
          minHeight: 0, px: large ? 1.25 : 1,
          "& .MuiAccordionSummary-content": {
            my: large ? 1 : 0.75, alignItems: "center", gap: large ? 1 : 0.75 },
        }}
      >
        {icon}
        <Typography
          variant={large ? "subtitle2" : "caption"}
          sx={{ fontWeight: large ? 800 : 700, fontSize: large ? "1rem" : undefined,
                flexShrink: 0, mr: 1 }}
        >
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
