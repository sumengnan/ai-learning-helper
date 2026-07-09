import { Box, Typography, CircularProgress } from "@mui/material";
import StorageIcon from "@mui/icons-material/Storage";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import { CollapsibleBlock } from "./CollapsibleBlock";

// 每行前的状态图标：进行中(…)转圈 / 失败红叉 / 完成绿勾 / 其它中性点
function lineIcon(text: string) {
  if (text.endsWith("…")) return <CircularProgress size={11} />;
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
  return (
    <CollapsibleBlock icon={icon} title={title} status={status}>
      {items.map((p, i) => {
        const agent = kind === "subagent" ? p.scope.slice("subagent:".length) : "";
        return (
          <Box key={i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.15 }}>
            {lineIcon(p.text)}
            <Typography variant="caption" color="text.secondary">
              {agent ? <b>{agent}: </b> : null}{p.text}
            </Typography>
          </Box>
        );
      })}
    </CollapsibleBlock>
  );
}
