// 「AI 运行统计」页签（原「系统监控」）：运维口径的运行概览、趋势、工具调用与成功率。
// 数据与时间范围由父组件 HomeView 统一拉取并下发，本组件不自取数。
import { Box, Card, CardContent, Typography, Stack, Tooltip, useTheme } from "@mui/material";
import type { StatsOverview } from "../api/stats";
import {
  rangeLabel, fmtTokens, fmtPct, fmtLatency,
  cardSx, Eyebrow, StatTile, TrendChart, StepsHistogram,
} from "./statsShared";

export function AiStatsTab({ data, days }: { data: StatsOverview; days: number }) {
  const theme = useTheme();
  const { learn, ops } = data;
  const toolMax = Math.max(1, ...ops.tools.map((t) => t.count));
  const okColor = (r: number) => (r >= 0.97 ? theme.palette.success.main
    : r >= 0.9 ? theme.palette.warning.main : theme.palette.error.main);
  const cur = ops.totals.cost_currency || "¥";
  const q = ops.quality;
  const gate = ops.gate;
  const scoreColor = (s: number) => (s >= 80 ? theme.palette.success.main
    : s >= 60 ? theme.palette.warning.main : theme.palette.error.main);
  const layerMax = Math.max(1, ...gate.layer_failures.map((l) => l.count));

  return (
    <>
      {/* 运行概览 tiles */}
      <Eyebrow note={rangeLabel(days)}>运行概览</Eyebrow>
      <Box sx={{ display: "grid", gap: 1.75,
        gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(3,1fr)", md: "repeat(6,1fr)" } }}>
        <StatTile label="运行次数" value={String(ops.totals.runs)} hint={`今日事件 ${learn.activity[learn.activity.length - 1]?.runs ?? 0}`} stripe={theme.palette.primary.main} />
        <StatTile label="总 Token" value={fmtTokens(ops.totals.total_tokens)} hint={`${ops.totals.model_calls} 次调用`} stripe={theme.palette.primary.main} />
        <StatTile label="估算成本"
          value={ops.totals.cost_usd == null ? `${cur} —` : `${cur}${ops.totals.cost_usd}`}
          hint={ops.totals.cost_usd == null ? "待配置单价" : "按 token 分层估算"}
          stripe={theme.palette.warning.main} />
        <StatTile label="成功率" value={fmtPct(ops.totals.success_rate)} hint={`${ops.totals.runs_finished} / ${ops.totals.runs}`} stripe={theme.palette.success.main} />
        <StatTile label="P95 延迟" value={fmtLatency(ops.totals.p95_latency_ms)} hint={`均值 ${fmtLatency(ops.totals.avg_latency_ms)}`} stripe={theme.palette.warning.main} />
        <StatTile label="重试次数" value={String(ops.totals.retries)} hint={`活跃会话 ${ops.totals.conversations}`} stripe={theme.palette.primary.main} />
      </Box>

      {/* 趋势 + 步数 */}
      <Eyebrow>运行趋势</Eyebrow>
      <Box sx={{ display: "grid", gap: 1.75, gridTemplateColumns: { xs: "1fr", md: "1.35fr 1fr" } }}>
        <Card sx={cardSx}>
          <CardContent>
            <Typography sx={{ fontSize: 14, fontWeight: 650 }}>Token 消耗 &amp; 运行数（按天）</Typography>
            <Typography sx={{ fontSize: 12, color: "text.secondary", mb: 1.5 }}>面积为每日 token，柱为每日运行次数 · 悬停查看当日数值</Typography>
            <TrendChart data={ops.daily} />
          </CardContent>
        </Card>
        <Card sx={cardSx}>
          <CardContent>
            <Typography sx={{ fontSize: 14, fontWeight: 650 }}>每 run 步数分布</Typography>
            <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
              识别"绕圈跑飞"的运行 · 平均 {learn.effort.avg_steps} 步
            </Typography>
            <StepsHistogram data={ops.steps_histogram} />
            <Stack direction="row" sx={{ justifyContent: "space-between", fontSize: 12, color: "text.secondary", mt: 1.5 }}>
              <span>成功 {ops.totals.runs_finished} · 失败 {ops.totals.runs_error}</span>
              <span>最长 <b style={{ color: theme.palette.text.primary }}>{learn.effort.max_steps}</b> 步</span>
            </Stack>
          </CardContent>
        </Card>
      </Box>

      {/* 回答质量：轨迹 judge 分数 + 交付门拦截。两个门默认关，故常态可能为空。 */}
      <Eyebrow note="仅本账号">回答质量</Eyebrow>
      {q.scored_turns === 0 && gate.turns === 0 ? (
        <Card sx={cardSx}>
          <CardContent>
            <Typography sx={{ fontSize: 13, color: "text.disabled" }}>
              还没有质量评分记录。需在 .env 开启 <code>HARNESS_ENABLE_TRAJECTORY_JUDGE</code>
              （质量分）与 <code>HARNESS_ENABLE_ANSWER_GATE</code>（校验门）——两者默认关闭。
              轨迹 judge 仅在多步任务时才跑，简单问答会跳过以省成本。
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <>
          <Box sx={{ display: "grid", gap: 1.75, mb: 1.75,
            gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4,1fr)" } }}>
            <StatTile label="平均质量分"
              value={q.avg_final == null ? "—" : String(q.avg_final)}
              hint={q.scored_turns ? `${q.scored_turns} 轮已评` : "未开启轨迹 judge"}
              stripe={q.avg_final == null ? theme.palette.primary.main : scoreColor(q.avg_final)} />
            <StatTile label="拆分 / 步骤分"
              value={`${q.avg_plan ?? "—"} / ${q.avg_steps ?? "—"}`}
              hint="任务拆分 · 关键步执行" stripe={theme.palette.primary.main} />
            <StatTile label="一次过率" value={gate.turns ? fmtPct(gate.first_pass_rate) : "—"}
              hint={`${gate.turns} 轮经过交付门 · 共重答 ${gate.retries} 次`}
              stripe={gate.first_pass_rate >= 0.8 ? theme.palette.success.main : theme.palette.warning.main} />
            <StatTile label="降级交付" value={String(gate.degraded)}
              hint={gate.gate_errors > 0
                ? `另有 ${gate.gate_errors} 轮因校验器故障未真校验`
                : `占 ${fmtPct(gate.degraded_rate)} · 带 ⚠️ 告示交付`}
              stripe={gate.degraded > 0 ? theme.palette.error.main : theme.palette.success.main} />
          </Box>
          <Box sx={{ display: "grid", gap: 1.75, gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" } }}>
            <Card sx={cardSx}>
              <CardContent>
                <Typography sx={{ fontSize: 14, fontWeight: 650 }}>质量分分布</Typography>
                <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
                  轨迹 judge 给最终答案的打分 · 低分段越多越该查
                </Typography>
                <StepsHistogram data={q.distribution} />
              </CardContent>
            </Card>
            <Card sx={cardSx}>
              <CardContent>
                <Typography sx={{ fontSize: 14, fontWeight: 650 }}>哪一层拦下的</Typography>
                <Typography sx={{ fontSize: 12, color: "text.secondary", mb: 2 }}>
                  按次数排序 · 悬停查看具体数值
                </Typography>
                {gate.layer_failures.length === 0 ? (
                  <Typography sx={{ fontSize: 13, color: "text.disabled" }}>
                    没有被拦下的回答
                  </Typography>
                ) : (
                  <Stack spacing={1.1}>
                    {gate.layer_failures.map((l) => (
                      <Tooltip key={l.layer} arrow followCursor placement="top"
                        title={`${l.zh}（${l.layer}）· 未通过 ${l.count} 次`}>
                        <Box sx={{ display: "grid", gridTemplateColumns: "120px 1fr 92px",
                          alignItems: "center", gap: 1.5, cursor: "pointer" }}>
                          <Typography sx={{ fontSize: 12, whiteSpace: "nowrap",
                            overflow: "hidden", textOverflow: "ellipsis" }}>{l.zh}</Typography>
                          <Box sx={{ height: 9, bgcolor: "action.selected", borderRadius: 1.5, overflow: "hidden" }}>
                            <Box sx={{ height: "100%", width: `${(l.count / layerMax) * 100}%`,
                              bgcolor: theme.palette.error.main, borderRadius: 1.5 }} />
                          </Box>
                          <Typography sx={{ textAlign: "right", fontFamily: "monospace", fontSize: 11.5,
                            color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
                            {l.count} 次
                          </Typography>
                        </Box>
                      </Tooltip>
                    ))}
                  </Stack>
                )}
              </CardContent>
            </Card>
          </Box>
        </>
      )}

      {/* 工具调用 */}
      <Eyebrow>工具调用 &amp; 成功率</Eyebrow>
      <Card sx={cardSx}>
        <CardContent>
          <Typography sx={{ fontSize: 14, fontWeight: 650 }}>Agent 用了哪些工具</Typography>
          <Typography sx={{ fontSize: 12, color: "text.secondary", mb: 2 }}>
            按调用次数排序 · 悬停查看具体数值
          </Typography>
          {ops.tools.length === 0 ? (
            <Typography sx={{ fontSize: 13, color: "text.disabled" }}>还没有工具调用记录</Typography>
          ) : (
            <Stack spacing={1.1}>
              {/* 表头：把「调用次数 / 成功率」提示到对应的列位置 */}
              <Box sx={{ display: "grid", gridTemplateColumns: "120px 1fr 92px", alignItems: "center",
                gap: 1.5, fontSize: 11, color: "text.disabled", fontWeight: 600 }}>
                <span>工具</span>
                <span>调用次数（条越长越多）</span>
                <Box component="span" sx={{ textAlign: "right" }}>成功率</Box>
              </Box>
              {ops.tools.map((tl) => (
                <Tooltip key={tl.name} arrow followCursor placement="top"
                  title={`${tl.name} · 调用 ${tl.count} 次 · 成功率 ${fmtPct(tl.success_rate)}（失败 ${tl.errors} 次）`}>
                  <Box sx={{ display: "grid", gridTemplateColumns: "120px 1fr 92px",
                    alignItems: "center", gap: 1.5, cursor: "pointer" }}>
                    <Typography sx={{ fontFamily: "monospace", fontSize: 12, whiteSpace: "nowrap",
                      overflow: "hidden", textOverflow: "ellipsis" }}>{tl.name}</Typography>
                    <Box sx={{ height: 9, bgcolor: "action.selected", borderRadius: 1.5, overflow: "hidden" }}>
                      <Box sx={{ height: "100%", width: `${(tl.count / toolMax) * 100}%`,
                        bgcolor: okColor(tl.success_rate), borderRadius: 1.5 }} />
                    </Box>
                    <Typography sx={{ textAlign: "right", fontFamily: "monospace", fontSize: 11.5,
                      color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
                      {tl.count} 次 · <Box component="span" sx={{ color: okColor(tl.success_rate) }}>{fmtPct(tl.success_rate)}</Box>
                    </Typography>
                  </Box>
                </Tooltip>
              ))}
            </Stack>
          )}
        </CardContent>
      </Card>
    </>
  );
}
