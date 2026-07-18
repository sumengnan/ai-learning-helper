import { Box, Chip } from "@mui/material";
import { EllipsisText } from "./EllipsisText";

// MCP 工具名为 mcp__<server>__<tool>；拆出来友好展示为「server · tool」并挂 MCP 标签。
export function mcpParts(name: string): { server: string; tool: string } | null {
  const m = /^mcp__(.+?)__(.+)$/.exec(name);
  return m ? { server: m[1], tool: m[2] } : null;
}

// 工具名标签：MCP 工具显示「MCP」小标签 + server·tool（不露原始 mcp__ 前缀名），普通工具原样显示。
// 主聊天工具块、子代理/计划步的执行明细共用。
export function ToolLabel({ name, sx, maxChars }: { name: string; sx?: object; maxChars?: number }) {
  const mcp = mcpParts(name);
  if (!mcp) return <EllipsisText text={name} sx={sx} maxChars={maxChars} />;
  return (
    <Box sx={{ display: "inline-flex", alignItems: "center", gap: 0.5, minWidth: 0, ...sx }}>
      <Chip label="MCP" size="small" color="secondary" variant="outlined"
        sx={{ height: 16, "& .MuiChip-label": { px: 0.5, fontSize: 10, fontWeight: 700 } }} />
      <EllipsisText text={`${mcp.server} · ${mcp.tool}`} maxChars={maxChars} />
    </Box>
  );
}
