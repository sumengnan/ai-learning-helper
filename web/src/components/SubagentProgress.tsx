import {
  Accordion, AccordionSummary, AccordionDetails, Box, Typography, Chip, CircularProgress,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import { CollapsibleBlock } from "./CollapsibleBlock";
import { ToolCallDetail } from "./ToolCallDetail";
import { EllipsisText } from "./EllipsisText";

type Item = {
  scope: string; text: string;
  status?: "running" | "ok" | "error" | null; key?: string | null;
  detail?: { tool: string; args?: unknown; result?: string; is_error?: boolean } | null;
};

// scope 形如 subagent:executor:s1 / subagent:研究员 → 取冒号后的 agent 标识
const agentOf = (scope: string) =>
  scope.startsWith("subagent:") ? scope.slice("subagent:".length) : scope;

// 按 agent 分组，保持首次出现顺序
function groupByAgent(items: Item[]): { agent: string; rows: Item[] }[] {
  const groups: { agent: string; rows: Item[] }[] = [];
  const at = new Map<string, number>();
  for (const p of items) {
    const ag = agentOf(p.scope);
    let i = at.get(ag);
    if (i === undefined) { i = groups.length; at.set(ag, i); groups.push({ agent: ag, rows: [] }); }
    groups[i].rows.push(p);
  }
  return groups;
}

// 同 key 的开始/完成折叠成一行（后到覆盖），保留末态（带 result 的完成行）
function mergeByKey(items: Item[]): Item[] {
  const rows: Item[] = [];
  const pos = new Map<string, number>();
  for (const p of items) {
    if (p.key) {
      const i = pos.get(p.key);
      if (i !== undefined) rows[i] = p;
      else { pos.set(p.key, rows.length); rows.push(p); }
    } else rows.push(p);
  }
  return rows;
}

function rowIcon(p: Item, live: boolean) {
  if (p.status === "error" || p.detail?.is_error)
    return <CancelIcon sx={{ fontSize: 16 }} color="error" />;
  if (p.status === "running")
    return live ? <CircularProgress size={12} /> : <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />;
  return <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />;
}

// 子代理执行进度：按 agent 分组，组标题用步骤描述（头行），每次工具调用渲染成可展开的明细块。
// 编排器（executor:sN）与 dispatch（角色名）两条 subagent: 通道共用。
export function SubagentProgress({ items, live, stopped, status }: {
  items: Item[]; live: boolean; stopped: boolean;
  status: "running" | "ok" | "error" | "stopped";
}) {
  void stopped;
  if (!items.length) return null;
  const groups = groupByAgent(items);
  const summary = (
    <Typography variant="caption" color="text.secondary">{groups.length} 个子步骤</Typography>
  );
  return (
    <CollapsibleBlock icon={<AccountTreeIcon sx={{ fontSize: 15 }} color="action" />}
      title="子代理执行" status={status} summary={summary}>
      {groups.map((g, gi) => {
        const rows = mergeByKey(g.rows);
        // 组标题：头行（__hdr__ 键 / 首个无 detail 的行，如 dispatch「开始任务：…」）文字，回退 agent 名
        const header = rows.find((r) => (r.key || "").startsWith("__hdr__"))
          ?? rows.find((r) => !r.detail);
        const title = header?.text || g.agent;
        const toolRows = rows.filter((r) => r.detail);
        return (
          <Box key={g.agent + gi} sx={{ mb: 0.5 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, py: 0.25 }}>
              <Chip label={g.agent} size="small" color="secondary" variant="outlined"
                sx={{ height: 16, flexShrink: 0, "& .MuiChip-label": { px: 0.5, fontSize: 10, fontWeight: 700 } }} />
              <EllipsisText text={title} sx={{ fontWeight: 600 }} />
            </Box>
            {toolRows.map((p, i) => (
              <Accordion key={p.key ?? i} disableGutters elevation={0}
                sx={{ bgcolor: "transparent", "&:before": { display: "none" }, pl: 1 }}>
                <AccordionSummary expandIcon={<ExpandMoreIcon fontSize="small" />}
                  sx={{ minHeight: 0, px: 0,
                        "& .MuiAccordionSummary-content": { my: 0.4, alignItems: "center", gap: 0.75 } }}>
                  {rowIcon(p, live)}
                  <Typography variant="caption">{p.detail?.tool || p.text}</Typography>
                </AccordionSummary>
                <AccordionDetails sx={{ px: 0, pt: 0 }}>
                  <ToolCallDetail args={p.detail?.args} result={p.detail?.result}
                    isError={p.detail?.is_error} />
                </AccordionDetails>
              </Accordion>
            ))}
          </Box>
        );
      })}
    </CollapsibleBlock>
  );
}
