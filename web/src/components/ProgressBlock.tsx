import { Box, Typography } from "@mui/material";
import StorageIcon from "@mui/icons-material/Storage";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import { CollapsibleBlock } from "./CollapsibleBlock";

// 把同类进度（沙箱执行 / 子代理执行）整合成一个可折叠块，标题风格与「工具调用」一致
export function ProgressBlock({ title, kind, items, running }: {
  title: string;
  kind: "sandbox" | "subagent";
  items: { scope: string; text: string }[];
  running: boolean;
}) {
  if (!items.length) return null;
  const icon = kind === "sandbox"
    ? <StorageIcon sx={{ fontSize: 15 }} color="action" />
    : <AccountTreeIcon sx={{ fontSize: 15 }} color="action" />;
  return (
    <CollapsibleBlock icon={icon} title={title} status={running ? "running" : "ok"}>
      {items.map((p, i) => {
        const agent = kind === "subagent" ? p.scope.slice("subagent:".length) : "";
        return (
          <Box key={i} sx={{ py: 0.15 }}>
            <Typography variant="caption" color="text.secondary">
              {agent ? <b>{agent}: </b> : null}{p.text}
            </Typography>
          </Box>
        );
      })}
    </CollapsibleBlock>
  );
}
