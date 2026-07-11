// 参考来源清单：渲染在 AI 回复正文下方，与正文内联 [n] 角标同一套编号。
// 按来源类型分图标/配色；web 外链新标签打开，知识库/题库跳对应页，其余类型就地展开原始片段。
import { useState } from "react";
import {
  Box, Typography, Stack, Chip, IconButton, Collapse, Link as MuiLink,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useNavigate } from "react-router-dom";
import MenuBookIcon from "@mui/icons-material/MenuBook";
import PublicIcon from "@mui/icons-material/Public";
import QuizIcon from "@mui/icons-material/Quiz";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import HistoryIcon from "@mui/icons-material/History";
import TerminalIcon from "@mui/icons-material/Terminal";
import ExtensionIcon from "@mui/icons-material/Extension";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import type { ChipProps } from "@mui/material";
import type { SourceItem, SourceType } from "../types";
import { citeId } from "./citations";

const META: Record<SourceType, { name: string; color: ChipProps["color"];
  Icon: typeof MenuBookIcon }> = {
  knowledge: { name: "知识库", color: "primary", Icon: MenuBookIcon },
  web: { name: "网络", color: "info", Icon: PublicIcon },
  question: { name: "题库", color: "secondary", Icon: QuizIcon },
  attachment: { name: "附件", color: "warning", Icon: AttachFileIcon },
  memory: { name: "对话记忆", color: "success", Icon: HistoryIcon },
  code: { name: "沙箱执行", color: "default", Icon: TerminalIcon },
  mcp: { name: "MCP", color: "secondary", Icon: ExtensionIcon },
};

export function SourceList({ sources, msgKey, flashId }: {
  sources: SourceItem[]; msgKey: string; flashId?: string | null;
}) {
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  if (!sources.length) return null;

  const toggle = (i: number) =>
    setExpanded((s) => {
      const next = new Set(s);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });

  return (
    <Box sx={{ mt: 1.5 }}>
      <Typography variant="caption" sx={{ fontWeight: 700, color: "text.secondary" }}>
        📚 参考来源（{sources.length}）
      </Typography>
      <Stack sx={{ mt: 0.5 }}>
        {sources.map((s) => {
          const meta = META[s.type] ?? META.mcp;
          const { Icon } = meta;
          const id = citeId(msgKey, s.index);
          const flash = flashId === id;
          const hasDetail = !!s.detail && !s.url
            && s.type !== "knowledge" && s.type !== "question";

          const onClick = () => {
            if (s.type === "knowledge") navigate("/knowledge");
            else if (s.type === "question") navigate("/questions");
            else if (hasDetail) toggle(s.index);
          };
          const clickable = s.type === "knowledge" || s.type === "question" || hasDetail;

          return (
            <Box key={s.index} id={id}
              sx={{
                borderRadius: 1, px: 0.75, py: 0.5,
                transition: "background-color .2s",
                bgcolor: flash ? (t) => alpha(t.palette.primary.main, 0.16) : "transparent",
              }}>
              <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", minWidth: 0 }}>
                <Typography variant="caption" sx={{
                  fontWeight: 700, color: "text.secondary", flexShrink: 0, minWidth: 20 }}>
                  [{s.index}]
                </Typography>
                <Icon sx={{ fontSize: 15, flexShrink: 0 }} color={meta.color as any} />
                <Chip label={meta.name} size="small" color={meta.color} variant="outlined"
                  sx={{ height: 18, flexShrink: 0, "& .MuiChip-label": { px: 0.75, fontSize: 11 } }} />
                {s.url ? (
                  <MuiLink href={s.url} target="_blank" rel="noopener noreferrer"
                    variant="caption"
                    sx={{ display: "inline-flex", alignItems: "center", gap: 0.25,
                      minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {s.label}<OpenInNewIcon sx={{ fontSize: 12, flexShrink: 0 }} />
                  </MuiLink>
                ) : (
                  <Typography variant="caption"
                    onClick={clickable ? onClick : undefined}
                    sx={{
                      minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      cursor: clickable ? "pointer" : "default",
                      color: clickable ? "primary.main" : "text.primary",
                      "&:hover": clickable ? { textDecoration: "underline" } : undefined,
                    }}>
                    {s.label}
                  </Typography>
                )}
                {hasDetail && (
                  <IconButton size="small" aria-label="展开来源" onClick={() => toggle(s.index)}
                    sx={{ p: 0.25, ml: "auto", flexShrink: 0 }}>
                    <ExpandMoreIcon sx={{ fontSize: 16,
                      transform: expanded.has(s.index) ? "rotate(180deg)" : "none",
                      transition: "transform .15s" }} />
                  </IconButton>
                )}
              </Stack>
              {hasDetail && (
                <Collapse in={expanded.has(s.index)} unmountOnExit>
                  <Typography variant="caption" component="pre" sx={{
                    m: 0, mt: 0.5, ml: 3.5, p: 1, borderRadius: 0.5,
                    fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all",
                    bgcolor: (t) => alpha(t.palette.text.primary, 0.06), color: "text.secondary",
                  }}>
                    {s.detail}
                  </Typography>
                </Collapse>
              )}
            </Box>
          );
        })}
      </Stack>
    </Box>
  );
}
