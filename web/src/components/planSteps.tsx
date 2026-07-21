import { type ReactNode } from "react";
import { Typography, CircularProgress } from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import HelpOutlineIcon from "@mui/icons-material/HelpOutlined";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import RemoveCircleOutlineIcon from "@mui/icons-material/RemoveCircleOutlined";
import { fmtDuration } from "./duration";

// scope="plan" 这条通道上跑着两种来源不同的清单，共用本模块的解析与状态裁决：
//   编排器计划（PlanBlock）—— 步骤带 id/depends_on，由状态机保证每步有终态；
//   模型自述清单（SelfPlanBlock）—— 模型在单循环里自己调 update_plan 发的，只有 title/status。
// 两者的**渲染**刻意不同（见各自组件），但「done/failed/skipped 怎么画」「模型不更新了
// 该如何如实呈现」是同一件事，必须只有一份实现——分开写迟早漂移成两套说法。

export type PlanStatus = "pending" | "running" | "done" | "failed" | "skipped";

// 计时字段由后端跨 update_plan 快照算出、烤进 plan JSON（见 app/tools/plan_tool.py）：
// 已结束的步骤给 elapsed_ms（定格值），进行中的给 started_at_ms（epoch 毫秒，供前端读秒）。
// plan 走 progress 通道落库，故刷新后耗时仍在、进行中的也能接着读。没走过 running 的两者皆无。
// id：编排器计划步带（见 orchestrator._plan_progress），供把 executor:<id> 的执行明细挂到该步下。
export type PlanStepData = {
  id?: string; title: string; status: PlanStatus;
  depends_on?: string[];   // 编排器计划步的依赖（步骤 id）；用于标并行/依赖关系
  elapsed_ms?: number | null; started_at_ms?: number | null;
};

export function parseSteps(text?: string | null): PlanStepData[] {
  if (!text) return [];
  try {
    const arr = JSON.parse(text);
    if (!Array.isArray(arr)) return [];
    return arr.filter((s) => s && typeof s.title === "string");
  } catch {
    return [];
  }
}

// 这份快照是编排器发的，还是模型自己列的？判据与后端 _plan_from_orchestrator（app/api/chat.py）
// 一致：只有编排器计划步带 id。判据变了必须同时改那边，否则「清单收尾 shim」会补错对象。
export function isOrchestratorPlan(text?: string | null): boolean {
  return parseSteps(text).some((s) => !!s.id);
}

// 步骤耗时多为秒级，fmtDuration 对不足 1 秒会显示「0 秒」，这里改用「<1 秒」避免误读
export const fmtStep = (ms: number) => (ms < 1000 ? "<1 秒" : fmtDuration(ms));

// 行尾耗时：tabular-nums 让读秒时数字不跳宽
export function StepDuration({ children }: { children: ReactNode }) {
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

// 清单是模型的自述：它每完成一步就得再调一次 update_plan 传完整清单，前端显示的永远是
// 它最后一次传的那份。而模型约六分之一的多步任务会中途停止更新（甚至列完清单一次没更过），
// 于是运行早已成功结束，清单却还停在「第3步进行中、第4步待办」。
//
// 关键：此时那些步骤到底做没做，前端无从得知——实测有的轮次第4步明明干完了（文件都生成了），
// 清单上仍是 pending。所以这里绝不能替模型断言，只能如实说「状态未知」：
// - 正常跑完(done) + 非终态步骤 → 未知（可能做了也可能没做，别画成从没开始的空心圈）
// - 失败/中断         + 非终态步骤 → 未完成（运行都没跑完，这步大概率真没做完）
// - 用户停止          + 进行中步骤 → 已取消
export type Fate = "unknown" | "incomplete" | "cancelled" | "pending" | "live";

export function fateOf(s: PlanStepData, live: boolean, status?: string): Fate {
  if (s.status === "done" || s.status === "failed" || s.status === "skipped") return "pending";  // 终态，不需裁决
  // 生成中：只有正在执行的那步转圈；待办步保持静态（等待），不要全都转圈
  if (live) return s.status === "running" ? "live" : "pending";
  if (status === "stopped") return s.status === "running" ? "cancelled" : "pending";
  if (status === "error" || status === "interrupted") return "incomplete";
  if (status === "done") return "unknown";   // 运行成功但模型没再更新清单
  return "pending";                          // 非 live 的 streaming（刷新等）：还没结论
}

// size 由调用方给：编排器计划是气泡里的主块（17px），模型自述清单是附带说明（15px）。
export function stepIcon(s: PlanStepData, fate: Fate, size = 17) {
  if (s.status === "done") return <CheckCircleIcon sx={{ fontSize: size }} color="success" />;
  if (s.status === "failed") return <CancelIcon sx={{ fontSize: size }} color="error" />;
  if (s.status === "skipped") return <RemoveCircleOutlineIcon sx={{ fontSize: size }} color="disabled" />;
  if (fate === "live") return <CircularProgress size={size - 3} />;
  if (fate === "cancelled") return <StopCircleIcon sx={{ fontSize: size }} color="disabled" />;
  // 未知用问号而非空心圈：空心圈=「没开始」，是个我们没资格下的断言
  if (fate === "unknown") return <HelpOutlineIcon sx={{ fontSize: size }} color="disabled" />;
  return <RadioButtonUncheckedIcon sx={{ fontSize: size }} color="disabled" />;
}

export const SUFFIX: Partial<Record<Fate, string>> = {
  cancelled: "（已取消）",
  unknown: "（状态未知）",
  incomplete: "（未完成）",
};

// 本轮最后一条 plan 快照的文本。plan 每次更新都追加一条，前端认最后那条为准。
export function lastPlanText(
  progress?: { scope: string; text?: string }[] | null,
): string | undefined {
  const items = (progress || []).filter((p) => p.scope === "plan");
  return items[items.length - 1]?.text;
}
