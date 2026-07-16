import { Box, Typography, CircularProgress } from "@mui/material";
import { EllipsisText } from "./EllipsisText";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import HelpOutlineIcon from "@mui/icons-material/HelpOutlined";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import PlaylistAddCheckIcon from "@mui/icons-material/PlaylistAddCheck";
import { CollapsibleBlock } from "./CollapsibleBlock";

type PlanStatus = "pending" | "running" | "done" | "failed";
type PlanStepData = { title: string; status: PlanStatus };

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
  if (s.status === "done" || s.status === "failed") return "pending";  // 终态，不需裁决
  if (live) return "live";
  if (status === "stopped") return s.status === "running" ? "cancelled" : "pending";
  if (status === "error" || status === "interrupted") return "incomplete";
  if (status === "done") return "unknown";   // 运行成功但模型没再更新清单
  return "pending";                          // 非 live 的 streaming（刷新等）：还没结论
}

function stepIcon(s: PlanStepData, fate: Fate) {
  if (s.status === "done") return <CheckCircleIcon sx={{ fontSize: 15 }} color="success" />;
  if (s.status === "failed") return <CancelIcon sx={{ fontSize: 15 }} color="error" />;
  if (fate === "live") return <CircularProgress size={12} />;
  if (fate === "cancelled") return <StopCircleIcon sx={{ fontSize: 15 }} color="disabled" />;
  // 未知用问号而非空心圈：空心圈=「没开始」，是个我们没资格下的断言
  if (fate === "unknown") return <HelpOutlineIcon sx={{ fontSize: 15 }} color="disabled" />;
  return <RadioButtonUncheckedIcon sx={{ fontSize: 15 }} color="disabled" />;
}

const SUFFIX: Partial<Record<Fate, string>> = {
  cancelled: "（已取消）",
  unknown: "（状态未知）",
  incomplete: "（未完成）",
};

// 任务步骤：可折叠（issue 3）；用户停止后运行中的步骤标「已取消」（issue 2）
export function PlanBlock({ text, live = false, stopped = false, status }: {
  text?: string | null; live?: boolean; stopped?: boolean; status?: string;
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
      icon={<PlaylistAddCheckIcon sx={{ fontSize: 16 }} color="action" />}
      title="任务步骤" status={blockStatus} summary={summary} defaultExpanded
    >
      {steps.map((s, i) => (
        <Box key={i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.2 }}>
          {stepIcon(s, fates[i])}
          <Typography
            variant="caption"
            sx={{
              color: s.status === "failed" ? "error.main" : "text.secondary",
              textDecoration: s.status === "done" ? "line-through" : "none",
            }}
          >
            {s.title}{SUFFIX[fates[i]] ?? ""}
          </Typography>
        </Box>
      ))}
    </CollapsibleBlock>
  );
}
