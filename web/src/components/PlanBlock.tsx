import { type ReactNode } from "react";
import { Box, Typography, CircularProgress, Chip, Accordion, AccordionSummary, AccordionDetails } from "@mui/material";
import { EllipsisText } from "./EllipsisText";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import HelpOutlineIcon from "@mui/icons-material/HelpOutlined";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import RemoveCircleOutlineIcon from "@mui/icons-material/RemoveCircleOutlined";
import PlaylistAddCheckIcon from "@mui/icons-material/PlaylistAddCheck";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { CollapsibleBlock } from "./CollapsibleBlock";
import { fmtDuration } from "./duration";
import { LiveDuration } from "./LiveDuration";
import { ToolCallRows, mergeByKey, type ToolRow } from "./ToolCallRows";

type PlanStatus = "pending" | "running" | "done" | "failed" | "skipped";
// 计时字段由后端跨 update_plan 快照算出、烤进 plan JSON（见 app/tools/plan_tool.py）：
// 已结束的步骤给 elapsed_ms（定格值），进行中的给 started_at_ms（epoch 毫秒，供前端读秒）。
// plan 走 progress 通道落库，故刷新后耗时仍在、进行中的也能接着读。没走过 running 的两者皆无。
// id：编排器计划步带（见 orchestrator._plan_progress），供把 executor:<id> 的执行明细挂到该步下；
// ReAct 的 update_plan 清单无 id，则各步照常渲染成不可展开的纯行。
type PlanStepData = {
  id?: string; title: string; status: PlanStatus;
  elapsed_ms?: number | null; started_at_ms?: number | null;
};

// 归属该计划步的 executor 执行明细（工具调用），来自 scope=subagent:executor:<id> 的进度行
type SubItem = ToolRow & { scope: string };

// 步骤耗时多为秒级，fmtDuration 对不足 1 秒会显示「0 秒」，这里改用「<1 秒」避免误读
const fmtStep = (ms: number) => (ms < 1000 ? "<1 秒" : fmtDuration(ms));

// 行尾耗时：tabular-nums 让读秒时数字不跳宽
function StepDuration({ children }: { children: ReactNode }) {
  return (
    <Typography
      variant="caption"
      color="text.secondary"
      sx={{ flexShrink: 0, ml: "auto", pl: 1, opacity: 0.7,
            fontVariantNumeric: "tabular-nums" }}
    >
      {children}
    </Typography>
  );
}

function parseSteps(text?: string | null): PlanStepData[] {
  if (!text) return [];
  try {
    const arr = JSON.parse(text);
    if (!Array.isArray(arr)) return [];
    return arr.filter((s) => s && typeof s.title === "string");
  } catch {
    return [];
  }
}

// 清单是模型的自述：它每完成一步就得再调一次 update_plan 传完整清单，前端显示的永远是
// 它最后一次传的那份。而模型约六分之一的多步任务会中途停止更新（甚至列完清单一次没更过），
// 于是运行早已成功结束，清单却还停在「第3步进行中、第4步待办」。
//
// 关键：此时那些步骤到底做没做，前端无从得知——实测有的轮次第4步明明干完了（文件都生成了），
// 清单上仍是 pending。所以这里绝不能替模型断言，只能如实说「状态未知」：
// - 正常跑完(done) + 非终态步骤 → 未知（可能做了也可能没做，别画成从没开始的空心圈）
// - 失败/中断         + 非终态步骤 → 未完成（运行都没跑完，这步大概率真没做完）
// - 用户停止          + 进行中步骤 → 已取消
type Fate = "unknown" | "incomplete" | "cancelled" | "pending" | "live";

function fateOf(s: PlanStepData, live: boolean, status?: string): Fate {
  if (s.status === "done" || s.status === "failed" || s.status === "skipped") return "pending";  // 终态，不需裁决
  // 生成中：只有正在执行的那步转圈；待办步保持静态（等待），不要全都转圈
  if (live) return s.status === "running" ? "live" : "pending";
  if (status === "stopped") return s.status === "running" ? "cancelled" : "pending";
  if (status === "error" || status === "interrupted") return "incomplete";
  if (status === "done") return "unknown";   // 运行成功但模型没再更新清单
  return "pending";                          // 非 live 的 streaming（刷新等）：还没结论
}

function stepIcon(s: PlanStepData, fate: Fate) {
  if (s.status === "done") return <CheckCircleIcon sx={{ fontSize: 17 }} color="success" />;
  if (s.status === "failed") return <CancelIcon sx={{ fontSize: 17 }} color="error" />;
  if (s.status === "skipped") return <RemoveCircleOutlineIcon sx={{ fontSize: 17 }} color="disabled" />;
  if (fate === "live") return <CircularProgress size={14} />;
  if (fate === "cancelled") return <StopCircleIcon sx={{ fontSize: 17 }} color="disabled" />;
  // 未知用问号而非空心圈：空心圈=「没开始」，是个我们没资格下的断言
  if (fate === "unknown") return <HelpOutlineIcon sx={{ fontSize: 17 }} color="disabled" />;
  return <RadioButtonUncheckedIcon sx={{ fontSize: 17 }} color="disabled" />;
}

const SUFFIX: Partial<Record<Fate, string>> = {
  cancelled: "（已取消）",
  unknown: "（状态未知）",
  incomplete: "（未完成）",
};

// 任务步骤：可折叠（issue 3）；用户停止后运行中的步骤标「已取消」（issue 2）
// subItems：编排器 executor 的执行明细；带 id 的计划步会把对应 executor:<id> 的工具调用
// 嵌到该步下、可逐层展开（计划步 → 执行 agent+工具 → 工具入参/返回）。
export function PlanBlock({ text, live = false, stopped = false, status, subItems = [] }: {
  text?: string | null; live?: boolean; stopped?: boolean; status?: string;
  subItems?: SubItem[];
}) {
  const steps = parseSteps(text);
  if (!steps.length) return null;
  // stopped 是 status 的旧式入口，保留以免调用方漏传时行为倒退
  const st = stopped ? "stopped" : status;
  const done = steps.filter((s) => s.status === "done").length;
  const anyFailed = steps.some((s) => s.status === "failed");
  const anyRunning = steps.some((s) => s.status === "running");
  const fates = steps.map((s) => fateOf(s, live, st));
  // 运行成功却还留着没收尾的步骤 → 模型半途不报了。这不是「任务没做完」，
  // 而是「我们不知道做没做」，标题上要说清，否则「2/4 完成」会被读成任务只干了一半。
  const staleList = fates.some((f) => f === "unknown");
  const blockStatus: "running" | "ok" | "error" | "stopped" =
    anyFailed ? "error"
      : live && anyRunning ? "running"
        : st === "stopped" && anyRunning ? "stopped"
          : "ok";
  // 标题后紧跟「当前步骤」预览：优先进行中，其次最后一个已完成，否则第一个；超长截断为 …
  const current = steps.find((s) => s.status === "running")
    ?? [...steps].reverse().find((s) => s.status === "done")
    ?? steps[0];
  const summary = (
    <>
      <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
        {done}/{steps.length} 完成
      </Typography>
      {staleList && (
        <Typography variant="caption" color="text.disabled" sx={{ flexShrink: 0, ml: 0.5 }}>
          · 清单未更新完
        </Typography>
      )}
      <Box sx={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", ml: 0.5 }}>
        <EllipsisText text={current.title} maxChars={24} sx={{ opacity: 0.85 }} />
      </Box>
    </>
  );
  return (
    <CollapsibleBlock
      icon={<PlaylistAddCheckIcon sx={{ fontSize: 20 }} color="primary" />}
      title="任务步骤" status={blockStatus} summary={summary} defaultExpanded large
    >
      {steps.map((s, i) => {
        // 步骤间加分隔线（最后一步不加）
        const sep = i < steps.length - 1
          ? { borderBottom: 1, borderColor: "divider" } : {};
        // 行内容（序号+图标+标题+耗时），纯行与可展开步的摘要共用
        const rowContent = (
          <>
            <Typography variant="body2"
              sx={{ fontWeight: 700, color: "text.secondary", flexShrink: 0,
                    minWidth: "1.4em", textAlign: "right",
                    fontVariantNumeric: "tabular-nums" }}>
              {i + 1}.
            </Typography>
            {stepIcon(s, fates[i])}
            <Typography
              variant="body2"
              sx={{
                fontWeight: 500,   // 着重突出步骤项，不再是淡灰小字
                color: s.status === "failed" ? "error.main"
                  : s.status === "skipped" ? "text.disabled" : "text.primary",
                // 完成后不加删除线（此前的 line-through 已去掉）
              }}
            >
              {s.title}{SUFFIX[fates[i]] ?? ""}
            </Typography>
            {/* 进行中且确实还在跑 → 读秒；已结束 → 定格耗时。
                读秒严格以 fate==="live" 为闸：已停止/已中断/已结束的 run 其快照里仍留着
                running 步骤，照读会一直涨下去（此时该步已按 已取消/状态未知 呈现，
                再给个跳动的秒数只会误导）。
                没有 elapsed_ms 的步骤不显示时间：模型跳过 running 直接置 done 时后端拿不到
                起点，宁可留空也不编（见 plan_tool._apply_timing）。 */}
            {fates[i] === "live" && s.status === "running" && s.started_at_ms != null ? (
              <StepDuration>
                <LiveDuration startedAt={s.started_at_ms} format={fmtStep} />
              </StepDuration>
            ) : s.elapsed_ms != null ? (
              <StepDuration>{fmtStep(s.elapsed_ms)}</StepDuration>
            ) : null}
          </>
        );
        // 该步对应的 executor 执行明细（工具调用）；无 id 或无匹配（如 ReAct 清单）→ 纯行
        const toolRows = s.id
          ? mergeByKey(subItems.filter((p) => p.scope === `subagent:executor:${s.id}`))
              .filter((r) => r.detail)
          : [];
        if (!toolRows.length) {
          return (
            <Box key={i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.5, ...sep }}>
              {rowContent}
            </Box>
          );
        }
        return (
          <Accordion key={i} disableGutters elevation={0}
            sx={{ bgcolor: "transparent", "&:before": { display: "none" }, ...sep }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon fontSize="small" />}
              sx={{ minHeight: 0, px: 0,
                    "& .MuiAccordionSummary-content": { my: 0.2, alignItems: "center", gap: 0.75 } }}>
              {rowContent}
            </AccordionSummary>
            <AccordionDetails sx={{ px: 0, pt: 0, pl: 2 }}>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.25 }}>
                <Chip label={`executor:${s.id}`} size="small" color="secondary" variant="outlined"
                  sx={{ height: 16, "& .MuiChip-label": { px: 0.5, fontSize: 10, fontWeight: 700 } }} />
                <Typography variant="caption" color="text.disabled">执行明细</Typography>
              </Box>
              <ToolCallRows rows={toolRows} live={live} />
            </AccordionDetails>
          </Accordion>
        );
      })}
    </CollapsibleBlock>
  );
}
