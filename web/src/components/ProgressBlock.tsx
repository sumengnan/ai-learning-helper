import { Box, Typography, CircularProgress } from "@mui/material";
import StorageIcon from "@mui/icons-material/Storage";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import { CollapsibleBlock } from "./CollapsibleBlock";
import { EllipsisText } from "./EllipsisText";

type ProgressItem = {
  scope: string; text: string;
  status?: "running" | "ok" | "error" | null;
  key?: string | null;
};

// 每步状态图标：优先用后端下发的显式 status（子 agent 每步）；否则回退到文本启发式（沙箱）
function stepIcon(p: ProgressItem, isLast: boolean, running: boolean) {
  if (p.status) {
    if (p.status === "error") return <CancelIcon sx={{ fontSize: 14 }} color="error" />;
    if (p.status === "ok") return <CheckCircleIcon sx={{ fontSize: 14 }} color="success" />;
    // running：仅当它是最后一行且整块仍在跑才转圈，否则视为已完成
    return isLast && running
      ? <CircularProgress size={11} />
      : <CheckCircleIcon sx={{ fontSize: 14 }} color="success" />;
  }
  if (p.text.endsWith("…")) {
    return isLast && running
      ? <CircularProgress size={11} />
      : <CheckCircleIcon sx={{ fontSize: 14 }} color="success" />;
  }
  if (p.text.includes("失败") || p.text.includes("未产出"))
    return <CancelIcon sx={{ fontSize: 14 }} color="error" />;
  if (p.text.includes("完成") || p.text.includes("就绪"))
    return <CheckCircleIcon sx={{ fontSize: 14 }} color="success" />;
  return <Box sx={{ width: 4, height: 4, borderRadius: "50%", bgcolor: "text.disabled", mx: "5px" }} />;
}

// 把带 key 的「开始/完成」两条事件折叠为同一行（后到的完成状态覆盖先前的运行中）
function mergeByKey(items: ProgressItem[]): ProgressItem[] {
  const rows: ProgressItem[] = [];
  const pos = new Map<string, number>();
  for (const p of items) {
    if (p.key) {
      const at = pos.get(p.key);
      if (at !== undefined) rows[at] = p;
      else { pos.set(p.key, rows.length); rows.push(p); }
    } else {
      rows.push(p);
    }
  }
  return rows;
}

// 把同类进度（沙箱执行 / 子代理执行）整合成一个可折叠块，标题风格与「工具调用」一致
export function ProgressBlock({ title, kind, items, status }: {
  title: string;
  kind: "sandbox" | "subagent";
  items: ProgressItem[];
  status: "running" | "ok" | "error";
}) {
  if (!items.length) return null;
  // 折叠开始/完成为一行；成功的收尾文字（如「任务完成」）不进正文，状态已由块头图标表达
  const rows = mergeByKey(items).filter((p) => p.text !== "任务完成");
  if (!rows.length) return null;
  const icon = kind === "sandbox"
    ? <StorageIcon sx={{ fontSize: 15 }} color="action" />
    : <AccountTreeIcon sx={{ fontSize: 15 }} color="action" />;
  // 标题右侧显示最后一步进度
  const last = rows[rows.length - 1];
  const summaryText = kind === "subagent" && last.scope.startsWith("subagent:")
    ? `${last.scope.slice("subagent:".length)}: ${last.text}` : last.text;
  const summary = (
    <>
      {stepIcon(last, true, status === "running")}
      <EllipsisText text={summaryText} sx={{ ml: 0.5 }} />
    </>
  );
  return (
    <CollapsibleBlock icon={icon} title={title} status={status} summary={summary}>
      {rows.map((p, i) => {
        const agent = kind === "subagent" ? p.scope.slice("subagent:".length) : "";
        return (
          <Box key={p.key ?? i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.15 }}>
            {stepIcon(p, i === rows.length - 1, status === "running")}
            <Typography variant="caption" color="text.secondary">
              {agent ? <b>{agent}: </b> : null}{p.text}
            </Typography>
          </Box>
        );
      })}
    </CollapsibleBlock>
  );
}
