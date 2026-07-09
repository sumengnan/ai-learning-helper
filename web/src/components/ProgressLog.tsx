import { Box, Typography, CircularProgress } from "@mui/material";
import StorageIcon from "@mui/icons-material/Storage";
import AccountTreeIcon from "@mui/icons-material/AccountTree";

// 沙箱初始化 / 子代理派发等执行过程的进度旁路展示
export function ProgressLog({ items, busy }: {
  items: { scope: string; text: string }[]; busy: boolean;
}) {
  if (!items.length) return null;
  const label = (scope: string) =>
    scope === "sandbox" ? "沙箱"
      : scope.startsWith("subagent:") ? `子代理 ${scope.slice("subagent:".length)}`
      : scope;
  return (
    <Box sx={{
      mb: 1, p: 1, borderRadius: 1.5, border: 1, borderStyle: "dashed",
      borderColor: "divider", bgcolor: "background.default",
    }}>
      {items.map((p, i) => {
        const last = i === items.length - 1;
        return (
          <Box key={i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.25 }}>
            {busy && last ? (
              <CircularProgress size={12} />
            ) : p.scope === "sandbox" ? (
              <StorageIcon sx={{ fontSize: 14 }} color="action" />
            ) : (
              <AccountTreeIcon sx={{ fontSize: 14 }} color="action" />
            )}
            <Typography variant="caption" color="text.secondary">
              <b>{label(p.scope)}</b> · {p.text}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}
