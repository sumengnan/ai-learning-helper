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
  const ctx = ops.context;
  const scoreColor = (s: number) => (s >= 80 ? theme.palette.success.main
    : s >= 60 ? theme.palette.warning.main : theme.palette.error.main);
  const layerMax = Math.max(1, ...gate.layer_failures.map((l) => l.count));

  return (
    <>
      {/* 运行概览 tiles */}
      <Eyebrow note={rangeLabel(days)}>运行概览</Eyebrow>
      <Box sx={{ display: "grid", gap: 1.75,
        gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(3,1fr)", md: "repeat(6,1fr)" } }}>
        <StatTile label="总聊天次数" value={String(ops.totals.runs)} hint={`总聊天会话 ${ops.totals.conversations}`}  stripe={theme.palette.primary.main} />
        <StatTile label="总 Token（所有模型）" value={fmtTokens(ops.totals.total_tokens)} hint={`${ops.totals.model_calls} 次调用 · 合计各模型`} stripe={theme.palette.primary.main} />
        <StatTile label="估算成本（所有模型）"
          value={ops.totals.cost_usd == null ? `${cur} —` : `${cur}${ops.totals.cost_usd}`}
          hint={ops.totals.cost_usd == null ? "待配置单价" : "各模型按各自单价合计"}
          stripe={theme.palette.warning.main} />
        <StatTile label="成功率" value={fmtPct(ops.totals.success_rate)} hint={`${ops.totals.runs_finished} / ${ops.totals.runs}`} stripe={theme.palette.success.main} />
        <StatTile label="P95 延迟" value={fmtLatency(ops.totals.p95_latency_ms)} hint={`均值 ${fmtLatency(ops.totals.avg_latency_ms)}`} stripe={theme.palette.warning.main} />
        <StatTile label="LLM 网络重试次数" value={String(ops.totals.retries)} hint={"主/快速/judge模型总和"} stripe={theme.palette.primary.main} />
      </Box>

      {/* 分模型用量：总 token/调用次数/成本按不同模型（主/快速/judge…）拆开，及汇总 */}
      <Eyebrow note={rangeLabel(days)}>分模型用量</Eyebrow>
      <Card sx={cardSx}>
        <CardContent>
          <Typography sx={{ fontSize: 14, fontWeight: 650 }}>各模型 Token / 调用次数 / 成本</Typography>
          <Typography sx={{ fontSize: 12, color: "text.secondary", mb: 2 }}>
            主/快速/judge 等各档模型按各自单价计费 · 底部为所有模型汇总
            {ops.by_model.length === 0 ? "" : " · 悬停查看输入/输出"}
          </Typography>
          {ops.by_model.length === 0 ? (
            <Typography sx={{ fontSize: 13, color: "text.disabled" }}>本区间还没有模型调用记录</Typography>
          ) : (
            <Stack spacing={0.75}>
              <Box sx={{ display: "grid", gridTemplateColumns: "1fr 88px 96px 108px", alignItems: "center",
                gap: 1.5, fontSize: 11, color: "text.disabled", fontWeight: 600 }}>
                <span>模型</span>
                <Box component="span" sx={{ textAlign: "right" }}>调用次数</Box>
                <Box component="span" sx={{ textAlign: "right" }}>Token</Box>
                <Box component="span" sx={{ textAlign: "right" }}>成本</Box>
              </Box>
              {ops.by_model.map((m) => (
                <Tooltip key={m.model} arrow followCursor placement="top"
                  title={`${m.model} · 输入 ${m.prompt} / 输出 ${m.completion} tokens · 调用 ${m.calls} 次`}>
                  <Box sx={{ display: "grid", gridTemplateColumns: "1fr 88px 96px 108px",
                    alignItems: "center", gap: 1.5, cursor: "pointer", py: 0.4 }}>
                    <Typography sx={{ fontFamily: "monospace", fontSize: 12.5, whiteSpace: "nowrap",
                      overflow: "hidden", textOverflow: "ellipsis" }}>{m.model}</Typography>
                    <Typography sx={{ textAlign: "right", fontFamily: "monospace", fontSize: 12,
                      color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>{m.calls}</Typography>
                    <Typography sx={{ textAlign: "right", fontFamily: "monospace", fontSize: 12,
                      color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>{fmtTokens(m.total_tokens)}</Typography>
                    <Typography sx={{ textAlign: "right", fontFamily: "monospace", fontSize: 12,
                      color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
                      {m.cost_usd == null ? `${cur} —` : `${cur}${m.cost_usd}`}
                    </Typography>
                  </Box>
                </Tooltip>
              ))}
              {/* 汇总行 */}
              <Box sx={{ display: "grid", gridTemplateColumns: "1fr 88px 96px 108px", alignItems: "center",
                gap: 1.5, pt: 0.75, mt: 0.25, borderTop: 1, borderColor: "divider" }}>
                <Typography sx={{ fontSize: 12.5, fontWeight: 700 }}>汇总（所有模型）</Typography>
                <Typography sx={{ textAlign: "right", fontFamily: "monospace", fontSize: 12, fontWeight: 700,
                  fontVariantNumeric: "tabular-nums" }}>{ops.totals.model_calls}</Typography>
                <Typography sx={{ textAlign: "right", fontFamily: "monospace", fontSize: 12, fontWeight: 700,
                  fontVariantNumeric: "tabular-nums" }}>{fmtTokens(ops.totals.total_tokens)}</Typography>
                <Typography sx={{ textAlign: "right", fontFamily: "monospace", fontSize: 12, fontWeight: 700,
                  fontVariantNumeric: "tabular-nums" }}>
                  {ops.totals.cost_usd == null ? `${cur} —` : `${cur}${ops.totals.cost_usd}`}
                </Typography>
              </Box>
            </Stack>
          )}
        </CardContent>
      </Card>

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
            <Typography sx={{ fontSize: 14, fontWeight: 650 }}>整轮总步数分布</Typography>
            <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
              一步 = 模型一轮「思考 → 调工具 → 读结果」；步数越多，这次任务越费周折
            </Typography>
            <StepsHistogram data={ops.steps_histogram} />
            <Stack direction="row" sx={{ justifyContent: "space-between", fontSize: 12, color: "text.secondary", mt: 1.5 }}>
              <span>成功 {ops.totals.runs_finished} · 失败 {ops.totals.runs_error}</span>
            </Stack>
          </CardContent>
        </Card>
      </Box>

      {/* 回答质量：轨迹 judge 分数 + 交付门拦截。与本页其它指标同为全局口径、随时间范围变。
          两个门默认关闭，常态可能全为空 —— 此时照常出表格、值显示「—」/0，不换成一段说明文案。 */}
      <Eyebrow note={rangeLabel(days)}>回答质量</Eyebrow>
      <>
          <Box sx={{ display: "grid", gap: 1.75, mb: 1.75,
            gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(5,1fr)" } }}>
            <StatTile label="平均质量分"
              value={q.avg_final == null ? "—" : String(q.avg_final)}
              hint={`${q.scored_turns} 轮已评`}
              stripe={q.avg_final == null ? theme.palette.primary.main : scoreColor(q.avg_final)} />
            <StatTile label="拆分 / 步骤分"
              value={`${q.avg_plan ?? "—"} / ${q.avg_steps ?? "—"}`}
              hint="任务拆分 · 关键步执行" stripe={theme.palette.primary.main} />
            <StatTile label="一次过率" value={gate.turns ? fmtPct(gate.first_pass_rate) : "—"}
              hint={`${gate.turns} 轮经过结果校验 · 共重答 ${gate.retries} 次`}
              stripe={gate.turns === 0 ? theme.palette.primary.main
                : gate.first_pass_rate >= 0.8 ? theme.palette.success.main : theme.palette.warning.main} />
            <StatTile label="降级交付" value={String(gate.degraded)}
              hint={gate.gate_errors > 0
                ? `另有 ${gate.gate_errors} 轮因校验器故障未真校验`
                : `占 ${fmtPct(gate.degraded_rate)} · 红徽章标未通过，正文原样交付`}
              stripe={gate.degraded > 0 ? theme.palette.error.main : theme.palette.success.main} />
            {/* 交付提醒：交付后的机械检查（完整性/检索依据/代码可运行/引用链接）。
                只提示、不拦截、不算本轮失败，故用警告色而非 error 色——后者会让人以为出了故障。 */}
            <StatTile label="交付提醒" value={String(gate.notice_turns)}
              hint={gate.notices.length
                ? `${gate.notice_turns} 轮触发交付后检查 · ${gate.notices.map((n) => `${n.zh} ${n.count}`).join(" · ")}`
                : "交付后查完整性·检索依据·代码可运行·引用链接 · 只提示不判失败"}
              stripe={gate.notice_turns > 0 ? theme.palette.warning.main : theme.palette.success.main} />
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
                  // 无数据也出表：表头 + 一行「—」，与有数据时同构，不换成说明文案
                  <Stack spacing={1.1}>
                    <Box sx={{ display: "grid", gridTemplateColumns: "120px 1fr 92px",
                      alignItems: "center", gap: 1.5, fontSize: 11, color: "text.disabled",
                      fontWeight: 600 }}>
                      <span>校验层</span>
                      <span>未通过次数</span>
                      <Box component="span" sx={{ textAlign: "right" }}>次数</Box>
                    </Box>
                    <Box sx={{ display: "grid", gridTemplateColumns: "120px 1fr 92px",
                      alignItems: "center", gap: 1.5 }}>
                      <Typography sx={{ fontSize: 12, color: "text.disabled" }}>—</Typography>
                      <Box sx={{ height: 9, bgcolor: "action.selected", borderRadius: 1.5 }} />
                      <Typography sx={{ textAlign: "right", fontFamily: "monospace",
                        fontSize: 11.5, color: "text.disabled" }}>—</Typography>
                    </Box>
                  </Stack>
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

      {/* 上下文健康度：与「回答质量」分开成节 —— 那节讲「答得好不好」，这节讲「答的时候
          还记不记得住」。二者是不同的失败面：上下文丢了，答案照样能拿高分（judge 只看当轮）。
          本节各项仅 layered 策略产出（config.context_strategy，**默认就是 layered**），
          故常态应当有值；全 0 通常意味着策略被显式改成了 full/window，或该时间范围内没有
          对话记录——不是「本就不该有值」。与本页其它块一致：照常出 tiles、值为 0，
          不换成一段说明文案。 */}
      <Eyebrow note={rangeLabel(days)}>上下文健康度</Eyebrow>
      {/* 四格连成一条链，让用户顺着读懂「历史是怎么保住/丢掉的」：
          ① 挤出 L1 窗口 → ② L2 摘要成功接住 → ③ L2 摘要失败＝失忆 → ④ L3 检索失败。
          分层轮数已去掉：系统固定 layered 策略，那格恒等于总轮数，没有信息量。
          标题用 L1/L2/L3 层级术语点名是哪一层，副标题用人话解释这层在做什么。 */}
      <Box sx={{ display: "grid", gap: 1.75, mb: 1.75,
        gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4,1fr)" } }}>
        {/* ① 挤出 L1 窗口条数：对话太长、装不下而被移出 AI 可见范围的消息条数（累计）。
            中性信息，非好非坏——长对话必然发生，故用中性色。 */}
        <StatTile label="L1窗口挤出" value={String(ctx.evicted_total)}
          hint="对话太长、最早的消息被挤出 L1 窗口的条数"
          stripe={theme.palette.primary.main} />
        {/* ② L2 摘要成功数：把挤出的历史压缩成摘要接住、避免丢失，成功的轮数。
            有失败时显示「成功 / 总计」，并点名未接住的即「L2摘要失败」，免得看着像重复计数。 */}
        <StatTile label="L2摘要成功"
          value={ctx.summary_errors ? `${ctx.summary_ok} / ${ctx.summary_ok + ctx.summary_errors}` : String(ctx.summary_ok)}
          hint={ctx.summary_errors > 0
            ? `压缩接住的轮数：${ctx.summary_errors} 轮没接住（即「L2摘要失败」）`
            : "把挤出的历史压缩成摘要的轮数"}
          stripe={ctx.summary_errors > 0 ? theme.palette.warning.main : theme.palette.success.main} />
        {/* ③ L2 摘要失败：头号告警——挤出的历史没被摘要接住，AI 真丢了一段还不自知（＝上下文失忆）。
            >0 一律标红，不设「少量可接受」的黄档：一次失忆就是一次事故。 */}
        <StatTile label="L2摘要失败" value={String(ctx.amnesia_turns)}
          hint={ctx.amnesia_turns > 0
            ? "这些轮丢了更早历史、AI 却不自知，需排查"
            : ctx.evicted_total === 0
              ? "还没有历史被挤出，未发生丢失"
              : "挤出的历史都被摘要接住了，未发生丢失"}
          stripe={ctx.amnesia_turns > 0 ? theme.palette.error.main : theme.palette.success.main} />
        {/* ④ L3 检索失败：再从更早历史里语义检索相关片段，失败的轮数。
            比失忆轻——丢了只是少一层参考，不等于失忆，故用黄档而非红档。 */}
        <StatTile label="L3检索失败" value={String(ctx.retrieval_errors)}
          hint={ctx.retrieval_errors > 0
            ? "从更早历史捞相关片段失败的轮数（只是少层参考，非失忆）"
            : "从更早历史捞相关片段：暂无失败"}
          stripe={ctx.retrieval_errors > 0 ? theme.palette.warning.main : theme.palette.success.main} />
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
    </>
  );
}
