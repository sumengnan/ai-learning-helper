import type { ChatMessage } from "../types";
import {
  Accordion, AccordionSummary, AccordionDetails, Typography, Box, CircularProgress,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import BuildIcon from "@mui/icons-material/Build";

export function AgentProgress({ steps }: { steps: NonNullable<ChatMessage["steps"]> }) {
  if (!steps.length) return null;
  return (
    <Box
      sx={{
        mb: 1, p: 1, borderRadius: 1.5,
        border: 1, borderColor: "divider", bgcolor: "background.default",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.5 }}>
        <BuildIcon sx={{ fontSize: 14 }} color="action" />
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
          工具调用
        </Typography>
      </Box>
      {steps.map((s, i) => {
        const pending = s.result === undefined;
        const ok = !pending && !s.isError;
        return (
          <Accordion
            key={i} disableGutters elevation={0}
            sx={{ bgcolor: "transparent", "&:before": { display: "none" } }}
          >
            <AccordionSummary
              expandIcon={<ExpandMoreIcon fontSize="small" />}
              sx={{
                minHeight: 0, px: 0,
                "& .MuiAccordionSummary-content": { my: 0.5, alignItems: "center", gap: 0.75 },
              }}
            >
              {pending ? (
                <CircularProgress size={14} />
              ) : ok ? (
                <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />
              ) : (
                <CancelIcon sx={{ fontSize: 16 }} color="error" />
              )}
              <Typography variant="caption" color={s.isError ? "error" : "text.primary"}>
                {s.tool}
              </Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ px: 0, pt: 0 }}>
              <Typography
                variant="caption" color="text.secondary"
                sx={{ display: "block", wordBreak: "break-all" }}
              >
                参数：{JSON.stringify(s.args)}
              </Typography>
              {s.result !== undefined && (
                <Typography
                  variant="caption" color={s.isError ? "error" : "text.secondary"}
                  sx={{ display: "block", wordBreak: "break-all" }}
                >
                  结果：{s.result}
                </Typography>
              )}
            </AccordionDetails>
          </Accordion>
        );
      })}
    </Box>
  );
}
