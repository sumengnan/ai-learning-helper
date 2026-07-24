import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "../types";
import {
  Accordion, AccordionSummary, AccordionDetails, Typography, Box, CircularProgress, Chip,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import BuildIcon from "@mui/icons-material/Build";
import { CollapsibleBlock } from "./CollapsibleBlock";
import { ToolCallDetail } from "./ToolCallDetail";
import { ToolLabel } from "./ToolLabel";

// 标题右侧「最后一步」预览的硬字数上限，超出显示 …
const SUMMARY_MAX = 24;

// 未完成步骤的图标：仅生成中(live)转圈；用户停止→灰色停止；其余终态→红叉（被打断，未完成）
function pendingIcon(live: boolean, stopped: boolean, size: number) {
  if (live) return <CircularProgress size={size} />;
  if (stopped) return <StopCircleIcon sx={{ fontSize: size + 3 }} color="disabled" />;
  return <CancelIcon sx={{ fontSize: size + 3 }} color="error" />;
}

// live=本条消息仍在生成；stopped=用户已停止本轮。停止后未完成的工具调用不再转圈，标「已取消」。
export function AgentProgress({ steps, live = false, stopped = false }: {
  steps: NonNullable<ChatMessage["steps"]>; live?: boolean; stopped?: boolean;
}) {
  const last = steps[steps.length - 1];
  const lastPending = steps.length > 0 && last.result === undefined;

  // 进行中步骤实时耗时：让用户看到「正在执行、已多久」而非只有干转圈（hooks 须在 early-return 前）
  const [now, setNow] = useState(() => Date.now());
  const startRef = useRef(Date.now());
  const runKey = `${steps.length}:${last?.tool ?? ""}`;
  useEffect(() => { startRef.current = Date.now(); setNow(Date.now()); }, [runKey]);
  useEffect(() => {
    if (!(live && lastPending)) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live, lastPending]);
  const elapsed = Math.floor((now - startRef.current) / 1000);

  if (!steps.length) return null;
  const pending = steps.some((s) => s.result === undefined);
  const anyError = steps.some((s) => s.isError);
  const status: "running" | "ok" | "error" | "stopped" =
    live && pending ? "running"
      : anyError ? "error"
        : pending ? (stopped ? "stopped" : "error")
          : "ok";
  const cutLabel = stopped ? "（已取消）" : "（未完成）";
  const summary = (
    <>
      {lastPending ? (
        pendingIcon(live, stopped, 11)
      ) : last.isError ? (
        <CancelIcon sx={{ fontSize: 14 }} color="error" />
      ) : (
        <CheckCircleIcon sx={{ fontSize: 14 }} color="success" />
      )}
      <ToolLabel name={last.tool} sx={{ ml: 0.5 }} maxChars={SUMMARY_MAX} />
      {lastPending && live && (
        <Typography variant="caption" color="text.secondary" sx={{ ml: 0.5, flexShrink: 0 }}>
          执行中{elapsed > 0 ? ` · 已 ${elapsed}s` : "…"}
        </Typography>
      )}
      {lastPending && !live && (
        <Typography variant="caption" color="text.secondary" sx={{ ml: 0.5, flexShrink: 0 }}>
          {cutLabel}
        </Typography>
      )}
    </>
  );
  return (
    <CollapsibleBlock
      icon={<BuildIcon sx={{ fontSize: 15 }} color="action" />}
      title="工具调用"
      status={status}
      summary={summary}
    >
      {steps.map((s, i) => {
        const sp = s.result === undefined;
        const ok = !sp && !s.isError;
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
              {sp ? (
                pendingIcon(live, stopped, 14)
              ) : ok ? (
                <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />
              ) : (
                <CancelIcon sx={{ fontSize: 16 }} color="error" />
              )}
              <Typography variant="caption" component="div"
                color={s.isError ? "error" : "text.primary"}>
                <ToolLabel name={s.tool} />
                {sp && !live ? cutLabel : ""}
              </Typography>
              {s.image && (
                <Box component="span" title={`执行容器镜像：${s.image}`}
                  sx={{ ml: 0.5, px: 0.6, py: 0.1, borderRadius: 0.75, fontSize: 10.5,
                        fontFamily: "monospace", color: "text.secondary",
                        bgcolor: "action.hover", whiteSpace: "nowrap" }}>
                  {s.image}
                </Box>
              )}
            </AccordionSummary>
            <AccordionDetails sx={{ px: 0, pt: 0 }}>
              <ToolCallDetail args={s.args} result={s.result} isError={s.isError} />
            </AccordionDetails>
          </Accordion>
        );
      })}
    </CollapsibleBlock>
  );
}
