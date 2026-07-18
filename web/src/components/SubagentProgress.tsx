import { Box, Typography, Chip } from "@mui/material";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import { CollapsibleBlock } from "./CollapsibleBlock";
import { EllipsisText } from "./EllipsisText";
import { ToolCallRows, mergeByKey, type ToolRow } from "./ToolCallRows";

type Item = ToolRow & { scope: string };

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

// 子代理执行进度：按 agent 分组，组标题用步骤描述（头行），每次工具调用可展开看入参/返回。
// 编排器的 executor 步已并入 PlanBlock 的计划树，这里主要服务 dispatch 派发的子代理。
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
            <ToolCallRows rows={toolRows} live={live} />
          </Box>
        );
      })}
    </CollapsibleBlock>
  );
}
