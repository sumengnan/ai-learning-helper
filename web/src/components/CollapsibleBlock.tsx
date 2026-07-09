import type { ReactNode } from "react";
import {
  Accordion, AccordionSummary, AccordionDetails, Typography, CircularProgress,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";

// 可折叠的过程块（工具调用 / 沙箱执行 / 子代理执行 统一外观）
export function CollapsibleBlock({ icon, title, status, defaultExpanded = true, children }: {
  icon: ReactNode;
  title: string;
  status: "running" | "ok" | "error";
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
        <Typography variant="caption" sx={{ fontWeight: 700, flexGrow: 1 }}>
          {title}
        </Typography>
        {status === "running" ? (
          <CircularProgress size={14} />
        ) : status === "error" ? (
          <CancelIcon sx={{ fontSize: 16 }} color="error" />
        ) : (
          <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />
        )}
      </AccordionSummary>
      <AccordionDetails sx={{ px: 1, pt: 0, pb: 1 }}>
        {children}
      </AccordionDetails>
    </Accordion>
  );
}
