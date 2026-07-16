import { useState } from "react";
import { Box, Typography, Collapse, IconButton } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { fmtDuration } from "./duration";
import { LiveDuration } from "./LiveDuration";

// 思考耗时多为秒级，fmtDuration 对不足 1 秒显示「0 秒」，这里改用「<1 秒」避免误读（与 PlanBlock 一致）
const fmtThink = (ms: number) => (ms < 1000 ? "<1 秒" : fmtDuration(ms));

// 思考模式（reasoning_content）的展示：默认折叠，头部提示「思考中…」说明首字为何慢；点击展开。
// 顶部计时/耗时：
// - thinking（仍在思考、正文未开始）+ startedAt → 实时读秒「思考中 · N秒」
// - 思考已结束 + elapsedMs → 定格「思考 N秒」（刷新后由后端 reasoning_ms 还原）
// 读秒严格以 thinking 为闸：正文一开始就停，不会在回答阶段还涨着一个误导的秒数。
export function ThinkingBlock({ reasoning, thinking, startedAt, elapsedMs }: {
  reasoning: string; thinking: boolean;
  startedAt?: number; elapsedMs?: number;
}) {
  const [open, setOpen] = useState(false);   // 默认折叠，点击展开

  // 「· 」并进同一文本节点（不拆成兄弟节点），便于整体读取与测试精确匹配
  const durSx = { ml: 0.75, flexShrink: 0, color: "text.secondary", opacity: 0.8,
                  fontVariantNumeric: "tabular-nums" } as const;
  const duration = thinking && startedAt != null ? (
    <Typography component="span" variant="caption" sx={durSx}>
      <LiveDuration startedAt={startedAt} format={(ms) => `· ${fmtThink(ms)}`} />
    </Typography>
  ) : !thinking && elapsedMs != null ? (
    <Typography component="span" variant="caption" sx={durSx}>{`· ${fmtThink(elapsedMs)}`}</Typography>
  ) : null;

  return (
    <Box sx={{
      mb: 1, border: "1px dashed", borderColor: "divider", borderRadius: 1.5,
      px: 1.25, py: 0.5, bgcolor: "action.hover",
    }}>
      <Box sx={{ display: "flex", alignItems: "center", cursor: "pointer" }}
        onClick={() => setOpen((o) => !o)}>
        <Typography variant="caption" sx={{ fontWeight: 600, color: "text.secondary", flexShrink: 0 }}>
          {thinking ? "🧠 思考中…（已开启思考模式，回复较慢）" : "🧠 思考过程"}
        </Typography>
        {duration}
        <Box sx={{ flex: 1 }} />
        <IconButton size="small" aria-label="展开/收起思考过程">
          <ExpandMoreIcon fontSize="small"
            sx={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .2s" }} />
        </IconButton>
      </Box>
      <Collapse in={open}>
        <Typography variant="body2" sx={{
          whiteSpace: "pre-wrap", mt: 0.5, color: "text.secondary",
          maxHeight: 300, overflowY: "auto", lineHeight: 1.7,
        }}>
          {reasoning}
        </Typography>
      </Collapse>
    </Box>
  );
}
