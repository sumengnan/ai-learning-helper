import { Box, Typography } from "@mui/material";
import type { ReactNode } from "react";

// 统一的「无数据」空态：居中的图标 + 主文案 +（可选）辅助提示。
// fill=true 时撑满父容器高度并垂直居中（用于侧栏等定高容器）；
// 否则用较大的上下留白，随内容流排布（用于滚动内容页）。
export function EmptyState({ icon, title, hint, fill = false }: {
  icon: ReactNode; title: string; hint?: ReactNode; fill?: boolean;
}) {
  return (
    <Box sx={{
      display: "flex", flexDirection: "column", alignItems: "center",
      justifyContent: "center", gap: 1.5, px: 3, textAlign: "center",
      color: "text.secondary",
      ...(fill ? { height: "100%" } : { py: 8 }),
    }}>
      <Box sx={{
        width: 56, height: 56, borderRadius: "50%", display: "grid",
        placeItems: "center", bgcolor: "action.hover",
        "& svg": { fontSize: 30, opacity: 0.55 },
      }}>
        {icon}
      </Box>
      <Typography variant="body2" sx={{ fontWeight: 500 }}>{title}</Typography>
      {hint && <Typography variant="caption" sx={{ opacity: 0.75 }}>{hint}</Typography>}
    </Box>
  );
}
