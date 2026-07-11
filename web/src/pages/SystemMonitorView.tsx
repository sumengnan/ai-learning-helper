// 系统监控（原首页「工程台」视角）：运维口径的运行概览、趋势、工具调用与成功率。
import { useEffect, useState } from "react";
import {
  Box, Card, CardContent, Typography, Stack, CircularProgress, Alert,
  Select, MenuItem, Tooltip, useTheme,
} from "@mui/material";
import { statsApi, type StatsOverview } from "../api/stats";
import {
  RANGES, DEFAULT_DAYS, rangeLabel, fmtTokens, fmtPct, fmtLatency,
  cardSx, Eyebrow, StatTile, TrendChart, StepsHistogram,
} from "./statsShared";

export default function SystemMonitorView() {
  const theme = useTheme();
  const [days, setDays] = useState<number>(DEFAULT_DAYS);
  const [data, setData] = useState<StatsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    statsApi.overview(days)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "加载失败"); });
    return () => { alive = false; };
  }, [days]);

  if (error) return <Box sx={{ p: 4 }}><Alert severity="error">监控数据加载失败：{error}</Alert></Box>;
  if (!data) return (
    <Box sx={{ display: "grid", placeItems: "center", height: "60vh" }}><CircularProgress /></Box>
  );

  const { learn, ops } = data;
  const toolMax = Math.max(1, ...ops.tools.map((t) => t.count));
  const okColor = (r: number) => (r >= 0.97 ? theme.palette.success.main
    : r >= 0.9 ? theme.palette.warning.main : theme.palette.error.main);
  const cur = ops.totals.cost_currency || "¥";

  return (
    <Box sx={{ width: "100%", maxWidth: 1600, mx: "auto", px: { xs: 2, sm: 2.5, md: 3 }, py: 3, pb: 8 }}>
      {/* 顶部：标题 + 时间范围 */}
      <Stack direction="row" spacing={1.5} sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1, mb: 1 }}>
        <Box sx={{ flex: 1 }}>
          <Typography sx={{ fontSize: 22, fontWeight: 700, letterSpacing: "-.02em" }}>系统监控</Typography>
          <Typography sx={{ fontSize: 12.5, color: "text.secondary" }}>
            harness 运行的运维口径 —— 成功率、延迟、Token 成本、工具调用
          </Typography>
        </Box>
        <Select size="small" value={days} onChange={(e) => setDays(Number(e.target.value))}
          aria-label="时间范围" sx={{ minWidth: 108, "& .MuiSelect-select": { py: 0.7 } }}>
          {RANGES.map((r) => <MenuItem key={r.days} value={r.days}>{r.label}</MenuItem>)}
        </Select>
      </Stack>

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
    </Box>
  );
}
