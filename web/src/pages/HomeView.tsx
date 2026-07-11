import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box, Card, CardContent, Typography, Stack, Button, CircularProgress, Alert,
  ToggleButtonGroup, ToggleButton, useTheme, Select, MenuItem, IconButton, Tooltip,
} from "@mui/material";
import type { Theme } from "@mui/material/styles";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import AddCommentIcon from "@mui/icons-material/AddComment";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import EditNoteIcon from "@mui/icons-material/EditNote";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutlined";
import DownloadIcon from "@mui/icons-material/Download";
import VisibilityIcon from "@mui/icons-material/Visibility";
import { statsApi, type StatsOverview, type DailyPoint } from "../api/stats";
import { api } from "../api/client";
import { DownloadPreviewDialog, type PreviewFile } from "./DownloadPreviewDialog";
import { previewKind } from "./downloadsUtils";
import { MemoryDrawer } from "./MemoryDrawer";

// 时间范围选项（默认近 3 天）
const RANGES: { label: string; days: number }[] = [
  { label: "今日", days: 1 },
  { label: "近 3 天", days: 3 },
  { label: "近 7 天", days: 7 },
  { label: "近 14 天", days: 14 },
  { label: "近 30 天", days: 30 },
];
const DEFAULT_DAYS = 3;
const rangeLabel = (days: number): string => RANGES.find((r) => r.days === days)?.label ?? `近 ${days} 天`;

// ---------- 格式化 ----------
const fmtTokens = (n: number): string =>
  n >= 1e8 ? `${(n / 1e8).toFixed(2)} 亿` : n >= 1e4 ? `${(n / 1e4).toFixed(1)} 万` : String(n);
const fmtPct = (r: number): string => `${Math.round(r * 100)}%`;
const fmtLatency = (ms: number): string => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`);

function fromNow(iso?: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

const cardSx = (t: Theme) => ({
  border: 1, borderColor: "divider", borderRadius: 3,
  bgcolor: "background.paper", boxShadow: t.palette.mode === "light"
    ? "0 1px 2px rgba(20,22,34,.04), 0 6px 20px -14px rgba(20,22,34,.22)"
    : "0 1px 2px rgba(0,0,0,.3), 0 8px 24px -16px rgba(0,0,0,.7)",
});

function Eyebrow({ children, note }: { children: React.ReactNode; note?: string }) {
  return (
    <Stack direction="row" spacing={1.2} sx={{ alignItems: "center", mt: 3.5, mb: 1.5, px: 0.5 }}>
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "primary.main" }} />
      <Typography sx={{ fontSize: 11.5, fontWeight: 700, letterSpacing: ".12em",
        textTransform: "uppercase", color: "text.secondary" }}>{children}</Typography>
      {note && <Typography sx={{ fontSize: 12.5, color: "text.disabled" }}>· {note}</Typography>}
    </Stack>
  );
}

// ---------- 图表（纯 SVG，无第三方依赖）----------
function Sparkline({ data, color }: { data: number[]; color: string }) {
  const w = 300, h = 46, mx = Math.max(1, ...data);
  const pts = data.map((v, i) => [
    data.length > 1 ? (i / (data.length - 1)) * w : 0, h - 4 - (v / mx) * (h - 9)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const area = `M0 ${h} ${pts.map((p) => `L${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ")} L${w} ${h} Z`;
  const last = pts[pts.length - 1] ?? [0, h];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
      style={{ width: "100%", height: 46, display: "block" }} aria-hidden>
      <path d={area} fill={color} opacity={0.13} />
      <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
      <circle cx={last[0].toFixed(1)} cy={last[1].toFixed(1)} r={3} fill={color} />
    </svg>
  );
}

function TrendChart({ data }: { data: DailyPoint[] }) {
  const t = useTheme();
  const accent = t.palette.primary.main, warm = t.palette.warning.main, line = t.palette.divider;
  const W = 560, H = 150, pad = 6;
  const tok = data.map((d) => d.tokens), run = data.map((d) => d.runs);
  const mx = Math.max(1, ...tok), rmx = Math.max(1, ...run);
  const X = (i: number) => pad + (data.length > 1 ? (i / (data.length - 1)) * (W - 2 * pad) : 0);
  const Y = (v: number) => H - 12 - (v / mx) * (H - 24);
  const pts = tok.map((v, i) => [X(i), Y(v)]);
  const linePath = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const area = `M${pad} ${H - 12} ${pts.map((p) => `L${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ")} L${W - pad} ${H - 12} Z`;
  const bw = ((W - 2 * pad) / Math.max(1, data.length)) * 0.44;
  const last = pts[pts.length - 1] ?? [0, H];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
      style={{ width: "100%", height: 150, display: "block" }} aria-hidden>
      {[0, 1, 2, 3].map((g) => {
        const y = 12 + (g / 3) * (H - 24);
        return <line key={g} x1={pad} y1={y} x2={W - pad} y2={y} stroke={line} strokeWidth={1} />;
      })}
      {run.map((v, i) => {
        const bh = (v / rmx) * (H * 0.5);
        return <rect key={i} x={(X(i) - bw / 2).toFixed(1)} y={(H - 12 - bh).toFixed(1)}
          width={bw.toFixed(1)} height={bh.toFixed(1)} rx={2} fill={warm} opacity={0.5} />;
      })}
      <path d={area} fill={accent} opacity={0.12} />
      <path d={linePath} fill="none" stroke={accent} strokeWidth={2.2} strokeLinejoin="round" />
      <circle cx={last[0].toFixed(1)} cy={last[1].toFixed(1)} r={3.5} fill={accent} />
    </svg>
  );
}

// ---------- 卡片小件 ----------
function StatTile({ label, value, unit, hint, stripe }: {
  label: string; value: string; unit?: string; hint?: string; stripe: string;
}) {
  return (
    <Card sx={(t) => ({ ...cardSx(t), position: "relative", overflow: "hidden" })}>
      <Box sx={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 3, bgcolor: stripe }} />
      <CardContent sx={{ py: 1.75, "&:last-child": { pb: 1.75 } }}>
        <Typography sx={{ fontSize: 11.5, color: "text.secondary" }}>{label}</Typography>
        <Typography sx={{ fontSize: 25, fontWeight: 700, lineHeight: 1.1, letterSpacing: "-.02em",
          fontVariantNumeric: "tabular-nums", mt: 0.3 }}>
          {value}{unit && <Box component="span" sx={{ fontSize: 13, fontWeight: 600, color: "text.disabled" }}>{" "}{unit}</Box>}
        </Typography>
        {hint && <Typography sx={{ fontSize: 11.5, color: "text.disabled", mt: 0.2 }}>{hint}</Typography>}
      </CardContent>
    </Card>
  );
}

// ---------- 主组件 ----------
export default function HomeView() {
  const nav = useNavigate();
  const theme = useTheme();
  const [view, setView] = useState<"learn" | "ops">("learn");
  const [days, setDays] = useState<number>(DEFAULT_DAYS);
  const [data, setData] = useState<StatsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewFile | null>(null);
  const [memoryOpen, setMemoryOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    setError(null);
    statsApi.overview(days)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "加载失败"); });
    return () => { alive = false; };
  }, [days]);

  // 下载：需带 Bearer，取鉴权 blob 再触发保存
  async function download(id: string, filename: string) {
    const blob = await api.downloads.blob(id);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }

  const greeting = useMemo(() => {
    const h = new Date().getHours();
    return h < 6 ? "夜深了" : h < 12 ? "早上好" : h < 18 ? "下午好" : "晚上好";
  }, []);

  if (error) return <Box sx={{ p: 4 }}><Alert severity="error">概览加载失败：{error}</Alert></Box>;
  if (!data) return (
    <Box sx={{ display: "grid", placeItems: "center", height: "60vh" }}><CircularProgress /></Box>
  );

  const { learn, ops } = data;
  const abilityMax = Math.max(1, ...learn.abilities.map((a) => a.count));
  const toolMax = Math.max(1, ...ops.tools.map((t) => t.count));
  const stepMax = Math.max(1, ...ops.steps_histogram.map((s) => s.count));
  const okColor = (r: number) => (r >= 0.97 ? theme.palette.success.main
    : r >= 0.9 ? theme.palette.warning.main : theme.palette.error.main);

  return (
    <Box sx={{ width: "100%", maxWidth: 1600, mx: "auto", px: { xs: 2, sm: 2.5, md: 3 }, py: 3, pb: 8 }}>
      {/* 顶部：标题 + 视图切换 + 时间范围 */}
      <Stack direction="row" spacing={1.5} sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1, mb: 1 }}>
        <Typography sx={{ fontSize: 22, fontWeight: 700, letterSpacing: "-.02em", flex: 1 }}>概览台</Typography>
        <ToggleButtonGroup exclusive size="small" value={view}
          onChange={(_e, v) => v && setView(v)} aria-label="视图切换"
          sx={{ "& .MuiToggleButton-root": { textTransform: "none", px: 2, fontWeight: 600 } }}>
          <ToggleButton value="learn">学习主场</ToggleButton>
          <ToggleButton value="ops">工程台</ToggleButton>
        </ToggleButtonGroup>
        <Select size="small" value={days} onChange={(e) => setDays(Number(e.target.value))}
          aria-label="时间范围" sx={{ minWidth: 108, "& .MuiSelect-select": { py: 0.7 } }}>
          {RANGES.map((r) => <MenuItem key={r.days} value={r.days}>{r.label}</MenuItem>)}
        </Select>
      </Stack>

      {view === "learn" ? (
        <>
          {/* 欢迎 + 继续 + 快捷 */}
          <Typography sx={{ fontSize: 26, fontWeight: 700, letterSpacing: "-.02em", mt: 2 }}>
            {greeting}，继续保持
          </Typography>
          <Typography sx={{ color: "text.secondary", fontSize: 14.5, mb: 2 }}>
            下面是你的学习进度，以及 AI 最近为你做了什么
          </Typography>
          <Box sx={{ display: "grid", gap: 1.75, gridTemplateColumns: { xs: "1fr", md: "1.5fr 1fr" } }}>
            <Card sx={(t) => ({ ...cardSx(t), position: "relative", overflow: "hidden" })}>
              <Box sx={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 4, bgcolor: "primary.main" }} />
              <CardContent>
                <Typography sx={{ fontSize: 11.5, fontWeight: 700, letterSpacing: ".05em",
                  textTransform: "uppercase", color: "text.disabled" }}>继续上次</Typography>
                {learn.last_conversation ? (
                  <>
                    <Typography sx={{ fontSize: 17, fontWeight: 650, mt: 0.5 }}>
                      {learn.last_conversation.title}
                    </Typography>
                    <Typography sx={{ color: "text.secondary", fontSize: 13, mt: 0.3 }}>
                      {fromNow(learn.last_conversation.updated_at)} · {learn.last_conversation.message_count} 条消息
                    </Typography>
                    <Button variant="contained" endIcon={<ArrowForwardIcon />} sx={{ mt: 1.5, textTransform: "none" }}
                      onClick={() => nav("/chat")}>继续对话</Button>
                  </>
                ) : (
                  <>
                    <Typography sx={{ fontSize: 16, fontWeight: 600, mt: 0.5 }}>还没有对话</Typography>
                    <Typography sx={{ color: "text.secondary", fontSize: 13, mt: 0.3 }}>
                      开始你的第一次提问，AI 会用工具帮你查资料、跑代码、出题
                    </Typography>
                    <Button variant="contained" endIcon={<ArrowForwardIcon />} sx={{ mt: 1.5, textTransform: "none" }}
                      onClick={() => nav("/chat")}>开始对话</Button>
                  </>
                )}
              </CardContent>
            </Card>
            <Card sx={cardSx}>
              <CardContent sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1.25 }}>
                {[
                  { icon: <AddCommentIcon />, label: "新对话", to: "/chat" },
                  { icon: <UploadFileIcon />, label: "上传资料", to: "/knowledge" },
                  { icon: <EditNoteIcon />, label: "做题练习", to: "/questions" },
                  { icon: <ErrorOutlineIcon />, label: "看错题本", to: "/wrong" },
                ].map((q) => (
                  <Box key={q.label} onClick={() => nav(q.to)} sx={{
                    display: "flex", flexDirection: "column", gap: 0.75, p: 1.5, cursor: "pointer",
                    border: 1, borderColor: "divider", borderRadius: 2, transition: ".15s",
                    "&:hover": { borderColor: "primary.main", transform: "translateY(-1px)" },
                  }}>
                    <Box sx={{ color: "primary.main", display: "flex" }}>{q.icon}</Box>
                    <Typography sx={{ fontSize: 13.5, fontWeight: 600 }}>{q.label}</Typography>
                  </Box>
                ))}
              </CardContent>
            </Card>
          </Box>

          {/* 我的积累 */}
          <Eyebrow>我的积累</Eyebrow>
          <Box sx={{ display: "grid", gap: 1.75, gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4,1fr)" } }}>
            {[
              { lbl: "📚 学习资料", v: learn.assets.documents, sub: "已建索引 · 查看 →", onClick: () => nav("/knowledge") },
              { lbl: "🧠 AI 记的偏好", v: learn.assets.memory, sub: "点击查看它记住了什么 →", onClick: () => setMemoryOpen(true) },
              { lbl: "✏️ 题库", v: learn.assets.questions, sub: learn.assets.questions ? "去练习 →" : "生成一套 →", act: true, onClick: () => nav("/questions") },
              { lbl: "❌ 错题本", v: learn.assets.wrong_answers, sub: learn.assets.wrong_answers ? "去复习 →" : "目前全对 👍", onClick: () => nav("/wrong") },
            ].map((a) => (
              <Card key={a.lbl} onClick={a.onClick} sx={(t) => ({ ...cardSx(t), cursor: "pointer",
                transition: ".15s", "&:hover": { borderColor: "primary.main", transform: "translateY(-1px)" } })}>
                <CardContent>
                  <Typography sx={{ fontSize: 12.5, color: "text.secondary" }}>{a.lbl}</Typography>
                  <Typography sx={{ fontSize: 29, fontWeight: 700, letterSpacing: "-.025em",
                    fontVariantNumeric: "tabular-nums", mt: 0.3 }}>{a.v}</Typography>
                  <Typography sx={{ fontSize: 12, color: a.act ? "primary.main" : "text.disabled",
                    fontWeight: a.act ? 600 : 400 }}>{a.sub}</Typography>
                </CardContent>
              </Card>
            ))}
          </Box>
          {learn.recent_downloads.length > 0 && (
            <Card sx={(t) => ({ ...cardSx(t), mt: 1.75 })}>
              <CardContent sx={{ display: "flex", alignItems: "center", gap: 1.25, flexWrap: "wrap",
                py: 1.5, "&:last-child": { pb: 1.5 } }}>
                <Typography sx={{ fontSize: 12.5, color: "text.secondary", fontWeight: 600, mr: 0.5 }}>最近生成的产物</Typography>
                {learn.recent_downloads.map((d) => {
                  const canPreview = previewKind(d.content_type) !== "none";
                  return (
                    <Stack key={d.id} direction="row" spacing={0.25} sx={{ alignItems: "center",
                      border: 1, borderColor: "divider", borderRadius: 5, pl: 1.25, pr: 0.25, py: 0.25 }}>
                      <Typography noWrap sx={{ fontSize: 13, maxWidth: 220 }}>{d.filename}</Typography>
                      {canPreview && (
                        <Tooltip title="预览"><IconButton size="small" aria-label={`预览 ${d.filename}`}
                          onClick={() => setPreview({ id: d.id, filename: d.filename, content_type: d.content_type })}>
                          <VisibilityIcon sx={{ fontSize: 17 }} /></IconButton></Tooltip>
                      )}
                      <Tooltip title="下载"><IconButton size="small" aria-label={`下载 ${d.filename}`}
                        onClick={() => download(d.id, d.filename)}>
                        <DownloadIcon sx={{ fontSize: 17 }} /></IconButton></Tooltip>
                    </Stack>
                  );
                })}
                <Box sx={{ flex: 1 }} />
                <Typography onClick={() => nav("/downloads")}
                  sx={{ fontSize: 13, color: "primary.main", fontWeight: 600, cursor: "pointer" }}>
                  查看全部 →
                </Typography>
              </CardContent>
            </Card>
          )}

          {/* AI 在为我做什么 */}
          <Eyebrow note="把底层 harness 的工作讲给你听">AI 在为我做什么</Eyebrow>
          <Box sx={{ display: "grid", gap: 1.75, gridTemplateColumns: { xs: "1fr", md: "1.2fr 1fr" } }}>
            <Card sx={cardSx}>
              <CardContent>
                <Typography sx={{ fontSize: 15, fontWeight: 650 }}>它用过的能力</Typography>
                <Typography sx={{ fontSize: 12.5, color: "text.secondary", mb: 2 }}>
                  {rangeLabel(days)}，AI 为回答你的问题实际动用的工具 —— 用得越多条越长
                </Typography>
                {learn.abilities.length === 0 ? (
                  <Typography sx={{ fontSize: 13, color: "text.disabled" }}>还没有调用记录</Typography>
                ) : (
                  <Stack spacing={1.25}>
                    {learn.abilities.map((a) => (
                      <Stack key={a.label} direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
                        <Box sx={{ width: 30, height: 30, flex: "none", borderRadius: 2, display: "grid",
                          placeItems: "center", bgcolor: "action.hover", fontSize: 15 }}>{a.icon}</Box>
                        <Box sx={{ flex: 1, minWidth: 0 }}>
                          <Typography sx={{ fontSize: 13.5, fontWeight: 550 }}>{a.label}</Typography>
                          <Box sx={{ height: 5, borderRadius: 3, bgcolor: "action.selected", mt: 0.6, overflow: "hidden" }}>
                            <Box sx={{ height: "100%", width: `${(a.count / abilityMax) * 100}%`,
                              bgcolor: "primary.main", borderRadius: 3 }} />
                          </Box>
                        </Box>
                        <Typography sx={{ fontFamily: "monospace", fontSize: 12.5, color: "text.secondary",
                          fontVariantNumeric: "tabular-nums" }}>{a.count}</Typography>
                      </Stack>
                    ))}
                  </Stack>
                )}
              </CardContent>
            </Card>
            <Card sx={cardSx}>
              <CardContent>
                <Typography sx={{ fontSize: 15, fontWeight: 650 }}>它为你花的力气</Typography>
                <Typography sx={{ fontSize: 12.5, color: "text.secondary", mb: 2 }}>同一份运行数据，换成你能感知的说法</Typography>
                <Stack direction="row" spacing={2.5} sx={{ mb: 1.5 }}>
                  <Box>
                    <Typography sx={{ fontSize: 26, fontWeight: 700, letterSpacing: "-.025em",
                      fontVariantNumeric: "tabular-nums" }}>{learn.effort.runs}</Typography>
                    <Typography sx={{ fontSize: 12, color: "text.secondary" }}>次独立思考</Typography>
                  </Box>
                  <Box>
                    <Typography sx={{ fontSize: 26, fontWeight: 700, letterSpacing: "-.025em",
                      fontVariantNumeric: "tabular-nums" }}>{fmtTokens(learn.effort.total_tokens)}</Typography>
                    <Typography sx={{ fontSize: 12, color: "text.secondary" }}>token 投入</Typography>
                  </Box>
                </Stack>
                <Stack spacing={0.9} sx={{ fontSize: 13, color: "text.secondary", mb: 1 }}>
                  <Stack direction="row" sx={{ justifyContent: "space-between" }}>
                    <span>平均每个问题想</span><b style={{ color: theme.palette.text.primary }}>≈ {learn.effort.avg_steps} 步</b>
                  </Stack>
                  <Stack direction="row" sx={{ justifyContent: "space-between" }}>
                    <span>最深的一次推理</span><b style={{ color: theme.palette.text.primary }}>{learn.effort.max_steps} 步</b>
                  </Stack>
                  <Stack direction="row" sx={{ justifyContent: "space-between" }}>
                    <span>一次答对、没返工</span><b style={{ color: theme.palette.text.primary }}>{fmtPct(learn.effort.success_rate)}</b>
                  </Stack>
                </Stack>
                <Sparkline data={learn.activity.map((d) => d.tokens)} color={theme.palette.primary.main} />
                <Typography sx={{ fontSize: 11.5, color: "text.disabled", mt: -0.5 }}>
                  每日活跃度 · 越高说明这天 AI 为你干得越多
                </Typography>
              </CardContent>
            </Card>
          </Box>
        </>
      ) : (
        <>
          {/* 运行概览 tiles */}
          <Eyebrow note={rangeLabel(days)}>运行概览</Eyebrow>
          <Box sx={{ display: "grid", gap: 1.75,
            gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(3,1fr)", md: "repeat(6,1fr)" } }}>
            <StatTile label="运行次数" value={String(ops.totals.runs)} hint={`今日事件 ${learn.activity[learn.activity.length - 1]?.runs ?? 0}`} stripe={theme.palette.primary.main} />
            <StatTile label="总 Token" value={fmtTokens(ops.totals.total_tokens)} hint={`${ops.totals.model_calls} 次调用`} stripe={theme.palette.primary.main} />
            <StatTile label="估算成本" value={ops.totals.cost_usd == null ? "$ —" : `$${ops.totals.cost_usd}`} hint={ops.totals.cost_usd == null ? "待接单价表" : "累计"} stripe={theme.palette.warning.main} />
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
                <Typography sx={{ fontSize: 12, color: "text.secondary", mb: 1.5 }}>面积为每日 token，柱为每日运行次数</Typography>
                <TrendChart data={ops.daily} />
                <Stack direction="row" spacing={2} sx={{ mt: 1, fontSize: 12, color: "text.secondary" }}>
                  <span><Box component="i" sx={{ display: "inline-block", width: 10, height: 3, borderRadius: 1, bgcolor: "primary.main", mr: 0.75, verticalAlign: "middle" }} />Token/天</span>
                  <span><Box component="i" sx={{ display: "inline-block", width: 10, height: 3, borderRadius: 1, bgcolor: "warning.main", mr: 0.75, verticalAlign: "middle" }} />运行数/天</span>
                </Stack>
              </CardContent>
            </Card>
            <Card sx={cardSx}>
              <CardContent>
                <Typography sx={{ fontSize: 14, fontWeight: 650 }}>每 run 步数分布</Typography>
                <Typography sx={{ fontSize: 12, color: "text.secondary", mb: 2 }}>
                  识别"绕圈跑飞"的运行 · 平均 {learn.effort.avg_steps} 步
                </Typography>
                <Box sx={{ display: "flex", alignItems: "flex-end", gap: 1, height: 90, mt: 2.5, mb: 1 }}>
                  {ops.steps_histogram.map((s) => (
                    <Box key={s.bucket} sx={{ flex: 1, display: "flex", flexDirection: "column",
                      alignItems: "center", justifyContent: "flex-end", height: "100%" }}>
                      <Typography sx={{ fontSize: 11, fontFamily: "monospace", color: "text.secondary" }}>{s.count}</Typography>
                      <Box sx={{ width: "100%", height: `${(s.count / stepMax) * 100}%`, minHeight: 4,
                        bgcolor: "action.hover", border: 1, borderColor: "primary.main", borderBottom: 0,
                        borderRadius: "5px 5px 0 0" }} />
                      <Typography sx={{ fontSize: 11, color: "text.disabled", mt: 0.5 }}>{s.bucket}</Typography>
                    </Box>
                  ))}
                </Box>
                <Stack direction="row" sx={{ justifyContent: "space-between", fontSize: 12, color: "text.secondary", mt: 1 }}>
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
                harness 独有视角 —— 普通 LLM 面板看不到 · 条形 = 调用次数，右侧 = 成功率
              </Typography>
              {ops.tools.length === 0 ? (
                <Typography sx={{ fontSize: 13, color: "text.disabled" }}>还没有工具调用记录</Typography>
              ) : (
                <Stack spacing={1.1}>
                  {ops.tools.map((tl) => (
                    <Box key={tl.name} sx={{ display: "grid", gridTemplateColumns: "120px 1fr 92px",
                      alignItems: "center", gap: 1.5 }}>
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
                  ))}
                </Stack>
              )}
            </CardContent>
          </Card>
        </>
      )}

      <DownloadPreviewDialog file={preview} onClose={() => setPreview(null)} />
      <MemoryDrawer open={memoryOpen} onClose={() => setMemoryOpen(false)} />
    </Box>
  );
}
