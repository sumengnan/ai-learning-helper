// 首页 / 系统监控 共用的格式化、卡片与图表件。
// 图表统一带横轴/纵轴刻度，并支持鼠标悬停显示具体数值（MUI Tooltip + followCursor）。
import { Box, Card, CardContent, Typography, Stack, Tooltip, useTheme } from "@mui/material";
import type { Theme } from "@mui/material/styles";
import type { DailyPoint, StepBucket } from "../api/stats";

// ---------- 时间范围 ----------
export const RANGES: { label: string; days: number }[] = [
  { label: "今日", days: 1 },
  { label: "近 3 天", days: 3 },
  { label: "近 7 天", days: 7 },
  { label: "近 14 天", days: 14 },
  { label: "近 30 天", days: 30 },
];
export const DEFAULT_DAYS = 3;
export const rangeLabel = (days: number): string =>
  RANGES.find((r) => r.days === days)?.label ?? `近 ${days} 天`;

// ---------- 格式化 ----------
export const fmtTokens = (n: number): string =>
  n >= 1e8 ? `${(n / 1e8).toFixed(2)} 亿` : n >= 1e4 ? `${(n / 1e4).toFixed(1)} 万` : String(n);
export const fmtPct = (r: number): string => `${Math.round(r * 100)}%`;
export const fmtLatency = (ms: number): string =>
  ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
// "2026-07-11" → "07-11"
export const shortDate = (iso: string): string => (iso || "").slice(5);

const _pad2 = (n: number) => String(n).padStart(2, "0");

// 绝对日期时间：YYYY-MM-DD HH:mm（本地时区）
function fmtDateTime(d: Date): string {
  return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())} `
    + `${_pad2(d.getHours())}:${_pad2(d.getMinutes())}`;
}

export function fromNow(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  const t = d.getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} 天前`;
  return fmtDateTime(d);   // 超过 7 天：显示具体日期和时间，而非“N 天前”
}

// 时间远近上色：越新越「暖绿」，越旧越淡，一眼看出新鲜度（返回 MUI palette 路径，可用于 color/sx）
export function recencyColor(iso?: string): string {
  if (!iso) return "text.disabled";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 86400) return "success.main";        // 一天内
  if (s < 7 * 86400) return "info.main";        // 一周内
  if (s < 30 * 86400) return "warning.main";    // 一月内
  return "text.disabled";                       // 更久
}

export const cardSx = (t: Theme) => ({
  border: 1, borderColor: "divider", borderRadius: 3,
  bgcolor: "background.paper", boxShadow: t.palette.mode === "light"
    ? "0 1px 2px rgba(20,22,34,.04), 0 6px 20px -14px rgba(20,22,34,.22)"
    : "0 1px 2px rgba(0,0,0,.3), 0 8px 24px -16px rgba(0,0,0,.7)",
});

// ---------- 小标题 ----------
export function Eyebrow({ children, note }: { children: React.ReactNode; note?: string }) {
  return (
    <Stack direction="row" spacing={1.2} sx={{ alignItems: "center", mt: 3.5, mb: 1.5, px: 0.5 }}>
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "primary.main" }} />
      <Typography sx={{ fontSize: 11.5, fontWeight: 700, letterSpacing: ".12em",
        textTransform: "uppercase", color: "text.secondary" }}>{children}</Typography>
      {note && <Typography sx={{ fontSize: 12.5, color: "text.disabled" }}>· {note}</Typography>}
    </Stack>
  );
}

// ---------- 指标卡 ----------
export function StatTile({ label, value, unit, hint, stripe }: {
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

// 悬停命中条：透明矩形覆盖单个数据点区域，hover 时浅色高亮并弹出数值。
function HoverBand({ x, w, h, title }: { x: number; w: number; h: number; title: string }) {
  return (
    <Tooltip title={title} arrow followCursor enterDelay={0} placement="top">
      <rect x={x} y={0} width={w} height={h} fill="transparent" style={{ cursor: "pointer" }}
        onMouseOver={(e) => { (e.currentTarget as SVGRectElement).style.fill = "rgba(128,128,140,.12)"; }}
        onMouseOut={(e) => { (e.currentTarget as SVGRectElement).style.fill = "transparent"; }} />
    </Tooltip>
  );
}

// 纵轴刻度列（顶=max，中，底=0）
function YAxis({ max, fmt, unit, align = "right" }:
  { max: number; fmt: (n: number) => string; unit?: string; align?: "left" | "right" }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", justifyContent: "space-between",
      height: 150, minWidth: 40, textAlign: align, fontSize: 10, color: "text.disabled",
      fontVariantNumeric: "tabular-nums", py: "2px" }}>
      <span>{fmt(max)}{unit ? ` ${unit}` : ""}</span>
      <span>{fmt(Math.round(max / 2))}</span>
      <span>0</span>
    </Box>
  );
}

// ---------- 迷你趋势线（每日 token）：带 y 轴峰值 + x 轴起止日期 + 悬停数值 ----------
export function Sparkline({ data }: { data: DailyPoint[] }) {
  const t = useTheme();
  const color = t.palette.primary.main;
  const vals = data.map((d) => d.tokens);
  const w = 300, h = 46, n = data.length, mx = Math.max(1, ...vals);
  const pts = vals.map((v, i) => [n > 1 ? (i / (n - 1)) * w : 0, h - 4 - (v / mx) * (h - 9)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const area = `M0 ${h} ${pts.map((p) => `L${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ")} L${w} ${h} Z`;
  const last = pts[pts.length - 1] ?? [0, h];
  const bandW = n > 0 ? w / n : w;
  return (
    <Box>
      <Stack direction="row" spacing={0.75}>
        <Box sx={{ display: "flex", flexDirection: "column", justifyContent: "space-between",
          height: 46, fontSize: 10, color: "text.disabled", textAlign: "right", minWidth: 40,
          fontVariantNumeric: "tabular-nums" }}>
          <span>{fmtTokens(mx)}</span><span>0</span>
        </Box>
        <Box sx={{ position: "relative", flex: 1 }}>
          <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
            style={{ width: "100%", height: 46, display: "block" }}>
            <path d={area} fill={color} opacity={0.13} />
            <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
            <circle cx={last[0].toFixed(1)} cy={last[1].toFixed(1)} r={3} fill={color} />
            {data.map((d, i) => (
              <HoverBand key={i} x={i * bandW} w={bandW} h={h}
                title={`${shortDate(d.date)} · Token ${fmtTokens(d.tokens)}`} />
            ))}
          </svg>
        </Box>
      </Stack>
      {n > 0 && (
        <Stack direction="row" sx={{ justifyContent: "space-between", pl: "48px", mt: 0.3,
          fontSize: 10, color: "text.disabled" }}>
          <span>{shortDate(data[0].date)}</span>
          <span>{shortDate(data[data.length - 1].date)}</span>
        </Stack>
      )}
    </Box>
  );
}

// ---------- 趋势图：每日 token（面积）+ 每日运行数（柱），带双纵轴/横轴日期/悬停数值 ----------
export function TrendChart({ data }: { data: DailyPoint[] }) {
  const t = useTheme();
  const accent = t.palette.primary.main, warm = t.palette.warning.main, grid = t.palette.divider;
  const W = 560, H = 150, n = Math.max(1, data.length);
  const tok = data.map((d) => d.tokens), run = data.map((d) => d.runs);
  const mx = Math.max(1, ...tok), rmx = Math.max(1, ...run);
  const X = (i: number) => (data.length > 1 ? (i / (data.length - 1)) * W : W / 2);
  const Y = (v: number) => H - 4 - (v / mx) * (H - 12);
  const pts = tok.map((v, i) => [X(i), Y(v)]);
  const linePath = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const area = `M0 ${H - 4} ${pts.map((p) => `L${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ")} L${W} ${H - 4} Z`;
  const bandW = W / n, bw = bandW * 0.44;
  const last = pts[pts.length - 1] ?? [0, H];
  const mid = data[Math.floor((data.length - 1) / 2)];
  return (
    <Box>
      <Stack direction="row" spacing={0.75}>
        <YAxis max={mx} fmt={fmtTokens} />
        <Box sx={{ position: "relative", flex: 1 }}>
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
            style={{ width: "100%", height: H, display: "block" }}>
            {[0, 1, 2, 3].map((g) => {
              const y = 4 + (g / 3) * (H - 12);
              return <line key={g} x1={0} y1={y} x2={W} y2={y} stroke={grid} strokeWidth={1} />;
            })}
            {run.map((v, i) => {
              const bh = (v / rmx) * (H * 0.5);
              return <rect key={i} x={(X(i) - bw / 2).toFixed(1)} y={(H - 4 - bh).toFixed(1)}
                width={bw.toFixed(1)} height={bh.toFixed(1)} rx={2} fill={warm} opacity={0.5} />;
            })}
            <path d={area} fill={accent} opacity={0.12} />
            <path d={linePath} fill="none" stroke={accent} strokeWidth={2.2} strokeLinejoin="round" />
            <circle cx={last[0].toFixed(1)} cy={last[1].toFixed(1)} r={3.5} fill={accent} />
            {data.map((d, i) => (
              <HoverBand key={i} x={i * bandW} w={bandW} h={H}
                title={`${shortDate(d.date)} · Token ${fmtTokens(d.tokens)} · 运行 ${d.runs} 次`} />
            ))}
          </svg>
        </Box>
        <YAxis max={rmx} fmt={(n) => String(n)} unit="次" align="left" />
      </Stack>
      {data.length > 0 && (
        <Stack direction="row" sx={{ justifyContent: "space-between", px: "44px", mt: 0.4,
          fontSize: 10, color: "text.disabled" }}>
          <span>{shortDate(data[0].date)}</span>
          {data.length > 2 && <span>{shortDate(mid.date)}</span>}
          <span>{shortDate(data[data.length - 1].date)}</span>
        </Stack>
      )}
      <Stack direction="row" spacing={2} sx={{ mt: 0.8, fontSize: 12, color: "text.secondary" }}>
        <span><Box component="i" sx={{ display: "inline-block", width: 10, height: 3, borderRadius: 1, bgcolor: "primary.main", mr: 0.75, verticalAlign: "middle" }} />左轴：Token/天</span>
        <span><Box component="i" sx={{ display: "inline-block", width: 10, height: 3, borderRadius: 1, bgcolor: "warning.main", mr: 0.75, verticalAlign: "middle" }} />右轴：运行数/天</span>
      </Stack>
    </Box>
  );
}

// ---------- 每 run 步数分布：纵轴计数刻度 + 横轴桶 + 悬停数值 ----------
export function StepsHistogram({ data }: { data: StepBucket[] }) {
  const max = Math.max(1, ...data.map((s) => s.count));
  return (
    <Stack direction="row" spacing={0.75} sx={{ mt: 2 }}>
      <Box sx={{ display: "flex", flexDirection: "column", justifyContent: "space-between",
        height: 90, minWidth: 24, textAlign: "right", fontSize: 10, color: "text.disabled",
        fontVariantNumeric: "tabular-nums" }}>
        <span>{max}</span><span>0</span>
      </Box>
      <Box sx={{ flex: 1 }}>
        <Box sx={{ display: "flex", alignItems: "flex-end", gap: 1, height: 90 }}>
          {data.map((s) => (
            <Tooltip key={s.bucket} title={`${s.bucket} 步 · ${s.count} 次运行`} arrow placement="top">
              <Box sx={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center",
                justifyContent: "flex-end", height: "100%", cursor: "pointer" }}>
                <Typography sx={{ fontSize: 11, fontFamily: "monospace", color: "text.secondary" }}>{s.count}</Typography>
                <Box sx={{ width: "100%", height: `${(s.count / max) * 100}%`, minHeight: 4,
                  bgcolor: "action.hover", border: 1, borderColor: "primary.main", borderBottom: 0,
                  borderRadius: "5px 5px 0 0", transition: ".15s", "&:hover": { bgcolor: "action.selected" } }} />
              </Box>
            </Tooltip>
          ))}
        </Box>
        <Box sx={{ display: "flex", gap: 1, mt: 0.5 }}>
          {data.map((s) => (
            <Typography key={s.bucket} sx={{ flex: 1, textAlign: "center", fontSize: 11, color: "text.disabled" }}>{s.bucket}</Typography>
          ))}
        </Box>
        <Typography sx={{ fontSize: 10.5, color: "text.disabled", textAlign: "center", mt: 0.3 }}>
          横轴：每次运行的步数区间 · 纵轴：运行次数
        </Typography>
      </Box>
    </Stack>
  );
}
