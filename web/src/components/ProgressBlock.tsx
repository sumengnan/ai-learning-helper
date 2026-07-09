import { Box, Typography, CircularProgress } from "@mui/material";
import StorageIcon from "@mui/icons-material/Storage";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import { CollapsibleBlock } from "./CollapsibleBlock";

// 每行前的状态图标：仅“最后一行且块仍在跑”才转圈；带后续行的 … 表示该步已完成
function lineIcon(text: string, isLast: boolean, running: boolean) {
  if (text.endsWith("…")) {
    return isLast && running
      ? <CircularProgress size={11} />
      : <CheckCircleIcon sx={{ fontSize: 14 }} color="success" />;
  }
  if (text.includes("失败") || text.includes("未产出"))
    return <CancelIcon sx={{ fontSize: 14 }} color="error" />;
  if (text.includes("完成") || text.includes("就绪"))
    return <CheckCircleIcon sx={{ fontSize: 14 }} color="success" />;
  return <Box sx={{ width: 4, height: 4, borderRadius: "50%", bgcolor: "text.disabled", mx: "5px" }} />;
}

// 把同类进度（沙箱执行 / 子代理执行）整合成一个可折叠块，标题风格与「工具调用」一致
export function ProgressBlock({ title, kind, items, status }: {
  title: string;
  kind: "sandbox" | "subagent";
  items: { scope: string; text: string }[];
  status: "running" | "ok" | "error";
}) {
  if (!items.length) return null;
  const icon = kind === "sandbox"
    ? <StorageIcon sx={{ fontSize: 15 }} color="action" />
    : <AccountTreeIcon sx={{ fontSize: 15 }} color="action" />;
  // 标题右侧显示最后一步进度
  const last = items[items.length - 1];
  const summary = (
    <>
      {lineIcon(last.text, true, status === "running")}
      <Typography variant="caption" color="text.secondary" noWrap sx={{ ml: 0.5, maxWidth: 200 }}>
        {kind === "subagent" && last.scope.startsWith("subagent:")
          ? `${last.scope.slice("subagent:".length)}: ${last.text}` : last.text}
      </Typography>
    </>
  );
  return (
    <CollapsibleBlock icon={icon} title={title} status={status} summary={summary}>
      {items.map((p, i) => {
        const agent = kind === "subagent" ? p.scope.slice("subagent:".length) : "";
        return (
          <Box key={i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.15 }}>
            {lineIcon(p.text, i === items.length - 1, status === "running")}
            <Typography variant="caption" color="text.secondary">
              {agent ? <b>{agent}: </b> : null}{p.text}
            </Typography>
          </Box>
        );
      })}
    </CollapsibleBlock>
  );
}
