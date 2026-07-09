import type { ChatMessage } from "../types";
import { Accordion, AccordionSummary, AccordionDetails, Typography, Box } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";

export function AgentProgress({ steps }: { steps: NonNullable<ChatMessage["steps"]> }) {
  if (!steps.length) return null;
  return (
    <Box sx={{ mt: 1 }}>
      {steps.map((s, i) => (
        <Accordion
          key={i} disableGutters elevation={0}
          sx={{ bgcolor: "transparent", "&:before": { display: "none" } }}
        >
          <AccordionSummary expandIcon={<ExpandMoreIcon fontSize="small" />} sx={{ minHeight: 0, px: 0 }}>
            <Typography variant="caption" color={s.isError ? "error" : "text.secondary"}>
              {s.result === undefined ? "调用工具" : "工具完成"}：{s.tool}
            </Typography>
          </AccordionSummary>
          <AccordionDetails sx={{ px: 0, pt: 0 }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", wordBreak: "break-all" }}>
              参数：{JSON.stringify(s.args)}
            </Typography>
            {s.result !== undefined && (
              <Typography variant="caption" sx={{ display: "block", wordBreak: "break-all" }}>
                结果：{s.result}
              </Typography>
            )}
          </AccordionDetails>
        </Accordion>
      ))}
    </Box>
  );
}
