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
  depends_on?: string[];   // 编排器计划步的依赖（步骤 id）；用于标并行/依赖关系
  elapsed_ms?: number | null; started_at_ms?: number | null;
};

// 依赖层级：无依赖=0，否则 max(依赖层级)+1。同层且该层≥2 步 → 可并行。环由 validate_plan 挡掉，
// 这里仍加 computing 守卫防脏数据死循环。返回 {levelOf, levelCount, idToNum} 供渲染并行徽章/依赖标注。
function analyzeDag(steps: PlanStepData[]) {
  const idToNum = new Map<string, number>();
  steps.forEach((s, i) => { if (s.id) idToNum.set(s.id, i + 1); });
  const byId = new Map<string, PlanStepData>();
  steps.forEach((s) => { if (s.id) byId.set(s.id, s); });
  const levelOf = new Map<string, number>();
  const computing = new Set<string>();
  const level = (s: PlanStepData): number => {
    if (!s.id) return 0;
    if (levelOf.has(s.id)) return levelOf.get(s.id)!;
    if (computing.has(s.id)) return 0;   // 环保护（正常 DAG 不触发）
    computing.add(s.id);
    let lv = 0;
    for (const d of s.depends_on || []) {
      const dep = byId.get(d);
      if (dep) lv = Math.max(lv, level(dep) + 1);
    }
    levelOf.set(s.id, lv);
    return lv;
  };
  steps.forEach(level);
  const levelCount = new Map<number, number>();
  steps.forEach((s) => {
    if (!s.id) return;
    const lv = levelOf.get(s.id)!;
    levelCount.set(lv, (levelCount.get(lv) || 0) + 1);
  });
  return { levelOf, levelCount, idToNum };
}

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

// 任务步骤各行统一的最小高度（px）：让可展开步（Accordion）与纯行等高，
// 收起态不再「一会高一会低」；内容超高（标题换行）时仍可自然撑开
const STEP_ROW_MIN_H = 32;

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
  const { levelOf, levelCount, idToNum } = analyzeDag(steps);
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
            {/* 步骤标题只占一行：撑满宽度、超出才截断为 …，且仅在真被截断时才 hover 显示完整（issue 1）。
                EllipsisText 内部测量 scrollWidth>clientWidth 才挂 Tooltip，短标题不会误触发 hover。 */}
            <EllipsisText
              text={`${s.title}${SUFFIX[fates[i]] ?? ""}`}
              variant="body2"
              color={s.status === "failed" ? "error.main"
                : s.status === "skipped" ? "text.disabled" : "text.primary"}
              sx={{ fontWeight: 500 }}   // 着重突出步骤项，不再是淡灰小字
            />
            {/* 右侧簇：并行徽章 / 依赖标注 / 耗时，整体右对齐到本行最右侧。
                读秒严格以 fate==="live" 为闸：已停止/已中断/已结束的 run 快照里仍留着 running 步，
                照读会一直涨（此时该步已按 已取消/状态未知 呈现）；无 elapsed_ms 的不显示时间。 */}
            <Box sx={{ ml: "auto", display: "flex", alignItems: "center", gap: 0.75, flexShrink: 0, pl: 1 }}>
              {s.id != null && (levelCount.get(levelOf.get(s.id) ?? 0) ?? 0) >= 2 && (
                <Chip label="并行" size="small" color="info" variant="outlined"
                  sx={{ height: 16, "& .MuiChip-label": { px: 0.5, fontSize: 10, fontWeight: 700 } }} />
              )}
              {(() => {
                const nums = (s.depends_on || []).map((d) => idToNum.get(d)).filter((n): n is number => n != null);
                return nums.length > 0 ? (
                  <Typography variant="caption" color="text.disabled" sx={{ whiteSpace: "nowrap" }}>
                    依赖 {nums.join("·")}
                  </Typography>
                ) : null;
              })()}
              {fates[i] === "live" && s.status === "running" && s.started_at_ms != null ? (
                <StepDuration>
                  <LiveDuration startedAt={s.started_at_ms} format={fmtStep} />
                </StepDuration>
              ) : s.elapsed_ms != null ? (
                <StepDuration>{fmtStep(s.elapsed_ms)}</StepDuration>
              ) : null}
            </Box>
          </>
        );
        // 该步对应的 executor 执行明细（工具调用）；无 id 或无匹配（如 ReAct 清单）→ 纯行
        const toolRows = s.id
          ? mergeByKey(subItems.filter((p) => p.scope === `subagent:executor:${s.id}`))
              .filter((r) => r.detail)
          : [];
        if (!toolRows.length) {
          return (
            <Box key={i} sx={{ display: "flex", alignItems: "center", gap: 0.75, minHeight: STEP_ROW_MIN_H, py: 0.5, ...sep }}>
              {rowContent}
            </Box>
          );
        }
        return (
          <Accordion key={i} disableGutters elevation={0}
            sx={{ bgcolor: "transparent", "&:before": { display: "none" }, ...sep }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon fontSize="small" />}
              sx={{ minHeight: STEP_ROW_MIN_H, px: 0,
                    "&.Mui-expanded": { minHeight: STEP_ROW_MIN_H },
                    "& .MuiAccordionSummary-content": { my: 0, alignItems: "center", gap: 0.75 },
                    "& .MuiAccordionSummary-content.Mui-expanded": { my: 0 } }}>
              {rowContent}
            </AccordionSummary>
            <AccordionDetails sx={{ px: 0, pt: 0, pl: 2 }}>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.25 }}>
                {/* 编排器每步由一个通用「执行智能体」执行（无花名册角色名），
                    用中文角色名替代内部 id executor:sN，避免向用户暴露黑话 */}
                <Chip label="执行智能体" size="small" color="secondary" variant="outlined"
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
