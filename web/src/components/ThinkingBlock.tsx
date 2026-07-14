import { useEffect, useState } from "react";
import { Box, Typography, Collapse, IconButton } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";

// 思考模式（reasoning_content）的展示：生成中默认展开、实时填充，让用户看到模型正在思考——
// 这也直接说明了「首字慢是因为开了思考模式」。完成后自动折叠（用户可手动展开/收起）。
export function ThinkingBlock({ reasoning, live }: { reasoning: string; live: boolean }) {
  const [open, setOpen] = useState(live);
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (!touched) setOpen(live);   // 未手动操作时跟随 live：思考中展开、结束折叠
  }, [live, touched]);

  return (
    <Box sx={{
      mb: 1, border: "1px dashed", borderColor: "divider", borderRadius: 1.5,
      px: 1.25, py: 0.5, bgcolor: "action.hover",
    }}>
      <Box sx={{ display: "flex", alignItems: "center", cursor: "pointer" }}
        onClick={() => { setTouched(true); setOpen((o) => !o); }}>
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
