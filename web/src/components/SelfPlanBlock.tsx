import { Box, Typography } from "@mui/material";
import FormatListBulletedIcon from "@mui/icons-material/FormatListBulleted";
import { EllipsisText } from "./EllipsisText";
import { CollapsibleBlock } from "./CollapsibleBlock";
import { LiveDuration } from "./LiveDuration";
import { parseSteps, fateOf, stepIcon, SUFFIX, StepDuration, fmtStep } from "./planSteps";

// 模型在单循环（简单直答）里自己调 update_plan 发的清单。它和编排器计划走同一条
// scope="plan" 通道，此前也共用 PlanBlock 渲染——于是「⚡ 简单直答」下面赫然是一个
// 和编排器一模一样的「任务步骤」块，读起来自相矛盾。
//
// 两者不是一回事，不该长得一样：
//   编排器计划 —— 系统拆的，每步有 id、依赖、执行子代理，状态由状态机保证；
//   这份清单   —— 模型的自述，没有 id 也没人校验，它可能中途就不更新了。
// 故这里刻意退回编排器出现之前那版更朴素的呈现：完成项画删除线，无序号、无并行/依赖
// 徽章、不可展开执行明细（本来也没有明细可展）。视觉上一眼可辨，且不冒充系统的保证。
export function SelfPlanBlock({ text, live = false, status }: {
  text?: string | null; live?: boolean; status?: string;
}) {
  const steps = parseSteps(text);
  if (!steps.length) return null;
  const done = steps.filter((s) => s.status === "done").length;
  const anyFailed = steps.some((s) => s.status === "failed");
  const anyRunning = steps.some((s) => s.status === "running");
  const fates = steps.map((s) => fateOf(s, live, status));
  // 「运行早已结束、清单还停在半路」在这条路上尤其常见——这份清单全靠模型自觉更新。
  const staleList = fates.some((f) => f === "unknown");
  const blockStatus: "running" | "ok" | "error" | "stopped" =
    anyFailed ? "error"
      : live && anyRunning ? "running"
        : status === "stopped" && anyRunning ? "stopped"
          : "ok";
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
      icon={<FormatListBulletedIcon sx={{ fontSize: 16 }} color="action" />}
      title="AI 自述清单" status={blockStatus} summary={summary} defaultExpanded
    >
      {steps.map((s, i) => (
        <Box key={i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.2 }}>
          {stepIcon(s, fates[i], 15)}
          <Typography
            variant="caption"
            sx={{
              color: s.status === "failed" ? "error.main" : "text.secondary",
              textDecoration: s.status === "done" ? "line-through" : "none",
            }}
          >
            {s.title}{SUFFIX[fates[i]] ?? ""}
          </Typography>
          {/* 读秒严格以 fate==="live" 为闸：已停止/已中断/已结束的 run 快照里仍留着 running 步，
              照读会一直涨下去。没有 elapsed_ms 的步骤不显示时间，不编。 */}
          {fates[i] === "live" && s.status === "running" && s.started_at_ms != null ? (
            <StepDuration>
              <LiveDuration startedAt={s.started_at_ms} format={fmtStep} />
            </StepDuration>
          ) : s.elapsed_ms != null ? (
            <StepDuration>{fmtStep(s.elapsed_ms)}</StepDuration>
          ) : null}
        </Box>
      ))}
    </CollapsibleBlock>
  );
}
