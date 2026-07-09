import type { ChatMessage } from "../types";
import {
  Accordion, AccordionSummary, AccordionDetails, Typography, Box, CircularProgress,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
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
              {/* 参数：蓝色左边框；结果：绿色成功 / 红色失败——两块明显区分 */}
              <Box sx={{
                mb: 0.5, px: 1, py: 0.5, borderRadius: 0.5,
                borderLeft: 3, borderColor: "info.main",
                bgcolor: (t) => alpha(t.palette.info.main, 0.08),
              }}>
                <Typography variant="caption" sx={{ fontWeight: 700, color: "info.main" }}>
                  参数
                </Typography>
                <Typography
                  variant="caption" component="pre"
                  sx={{ m: 0, fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all" }}
                >
                  {JSON.stringify(s.args, null, 2)}
                </Typography>
              </Box>
              {s.result !== undefined && (
                <Box sx={{
                  px: 1, py: 0.5, borderRadius: 0.5,
                  borderLeft: 3, borderColor: s.isError ? "error.main" : "success.main",
                  bgcolor: (t) => alpha(
                    (s.isError ? t.palette.error : t.palette.success).main, 0.1),
                }}>
                  <Typography
                    variant="caption"
                    sx={{ fontWeight: 700, color: s.isError ? "error.main" : "success.main" }}
                  >
                    {s.isError ? "结果 · 失败" : "结果 · 成功"}
                  </Typography>
                  <Typography
                    variant="caption" component="pre"
                    sx={{ m: 0, fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all",
                          color: "text.primary" }}
                  >
                    {s.result}
                  </Typography>
                </Box>
              )}
            </AccordionDetails>
          </Accordion>
        );
      })}
    </Box>
  );
}
