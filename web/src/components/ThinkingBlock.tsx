import { useState } from "react";
import { Box, Typography, Collapse, IconButton } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";

// 思考模式（reasoning_content）的展示：默认折叠，仅头部提示「思考中…（已开启思考模式，回复较慢）」，
// 说明首字为何慢；用户点击可展开查看实时思考过程。
export function ThinkingBlock({ reasoning, live }: { reasoning: string; live: boolean }) {
  const [open, setOpen] = useState(false);   // 默认折叠，点击展开

  return (
    <Box sx={{
      mb: 1, border: "1px dashed", borderColor: "divider", borderRadius: 1.5,
      px: 1.25, py: 0.5, bgcolor: "action.hover",
    }}>
      <Box sx={{ display: "flex", alignItems: "center", cursor: "pointer" }}
        onClick={() => setOpen((o) => !o)}>
        <Typography variant="caption" sx={{ flex: 1, fontWeight: 600, color: "text.secondary" }}>
          {live ? "🧠 思考中…（已开启思考模式，回复较慢）" : "🧠 思考过程"}
        </Typography>
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
