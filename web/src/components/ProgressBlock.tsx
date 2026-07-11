import { Box, Typography, CircularProgress, Chip } from "@mui/material";
import StorageIcon from "@mui/icons-material/Storage";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import ExtensionIcon from "@mui/icons-material/Extension";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import RemoveCircleOutlineIcon from "@mui/icons-material/RemoveCircleOutlined";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import { CollapsibleBlock } from "./CollapsibleBlock";
import { EllipsisText } from "./EllipsisText";

type ProgressItem = {
  scope: string; text: string;
  status?: "running" | "ok" | "error" | null;
  key?: string | null;
  agent?: string | null;   // 沙箱步骤归属的子 agent（后端在子 agent 执行期间打标）
};

// 标题右侧「最后一步」预览的硬字数上限
const SUMMARY_MAX = 24;

// 该步归属的子 agent 名：子代理块从 scope 取；沙箱块从后端打标的 agent 字段取
function agentOf(p: ProgressItem, kind: string): string {
  if (kind === "subagent") return p.scope.startsWith("subagent:") ? p.scope.slice("subagent:".length) : "";
  return p.agent || "";
}

// 每步状态图标：优先用后端下发的显式 status（子 agent 每步）；否则回退到文本启发式（沙箱）
// stopped：用户已停止——最后一条仍在进行中的步骤标灰色「停止」，不再转圈也不冒充成功
function stepIcon(p: ProgressItem, isLast: boolean, running: boolean, stopped = false) {
  if (stopped && isLast && (p.status === "running" || (!p.status && p.text.endsWith("…"))))
    return <StopCircleIcon sx={{ fontSize: 14 }} color="disabled" />;
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
  if (p.text.includes("卸载"))   // 技能卸载：中性的移除标识
    return <RemoveCircleOutlineIcon sx={{ fontSize: 14 }} color="disabled" />;
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
  kind: "sandbox" | "subagent" | "skill" | "verify";
  items: ProgressItem[];
  status: "running" | "ok" | "error" | "stopped";
}) {
  const stopped = status === "stopped";
  if (!items.length) return null;
  // 折叠开始/完成为一行；成功的收尾文字（如「任务完成」）不进正文，状态已由块头图标表达
  const rows = mergeByKey(items).filter((p) => p.text !== "任务完成");
  if (!rows.length) return null;
  const icon = kind === "sandbox"
    ? <StorageIcon sx={{ fontSize: 15 }} color="action" />
    : kind === "skill"
      ? <ExtensionIcon sx={{ fontSize: 15 }} color="action" />
      : kind === "verify"
        ? <FactCheckIcon sx={{ fontSize: 15 }} color="action" />
        : <AccountTreeIcon sx={{ fontSize: 15 }} color="action" />;
  // 标题右侧显示最后一步进度（含归属子 agent）
  const last = rows[rows.length - 1];
  const lastAgent = agentOf(last, kind);
  const summaryText = lastAgent ? `${lastAgent}: ${last.text}` : last.text;
  const lastCancelled = stopped
    && (last.status === "running" || (!last.status && last.text.endsWith("…")));
  const summary = (
    <>
      {stepIcon(last, true, status === "running", stopped)}
      <EllipsisText text={lastCancelled ? `${summaryText}（已取消）` : summaryText}
        sx={{ ml: 0.5 }} maxChars={SUMMARY_MAX} />
    </>
  );
  return (
    <CollapsibleBlock icon={icon} title={title} status={status} summary={summary}>
      {rows.map((p, i) => {
        const agent = agentOf(p, kind);
        return (
          <Box key={p.key ?? i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.15 }}>
            {stepIcon(p, i === rows.length - 1, status === "running", stopped)}
            {/* 子 agent 归属用彩色小标签区分（尤其沙箱执行中混入的子代理步骤） */}
            {agent ? (
              <Chip label={agent} size="small" color="secondary" variant="outlined"
                sx={{ height: 16, flexShrink: 0, "& .MuiChip-label": { px: 0.5, fontSize: 10, fontWeight: 700 } }} />
            ) : null}
            <Typography variant="caption" color="text.secondary">
              {p.text}
              {stopped && i === rows.length - 1 && lastCancelled ? "（已取消）" : ""}
            </Typography>
          </Box>
        );
      })}
    </CollapsibleBlock>
  );
}
