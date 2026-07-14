// 首页「概览」：合并「概览」（学习主场）与「AI 运行统计」（原系统监控）两个页签，
// 共用一个时间范围选择器（默认近 3 天）与同一份 statsApi.overview 数据。
// 路由 `/` 默认落在「概览」页签，`/monitor` 落在「AI 运行统计」页签；点页签会切换路由，
// 侧栏高亮随之更新，也支持直接深链。
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box, Typography, Stack, CircularProgress, Alert, Select, MenuItem, Tabs, Tab,
} from "@mui/material";
import { statsApi, type StatsOverview } from "../api/stats";
import { RANGES, DEFAULT_DAYS } from "./statsShared";
import { OverviewTab } from "./OverviewTab";
import { AiStatsTab } from "./AiStatsTab";

const DAYS_KEY = "overview_range_days";
type TabKey = "overview" | "ops";

// 时间范围持久化：两个页签共用，切换页签/刷新后保留选择
function loadDays(): number {
  try {
    const v = Number(localStorage.getItem(DAYS_KEY));
    if (RANGES.some((r) => r.days === v)) return v;
  } catch { /* ignore */ }
  return DEFAULT_DAYS;
}

export default function HomeView() {
  const nav = useNavigate();
  const location = useLocation();
  const tab: TabKey = location.pathname.startsWith("/monitor") ? "ops" : "overview";

  const [days, setDays] = useState<number>(loadDays);
  const [data, setData] = useState<StatsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try { localStorage.setItem(DAYS_KEY, String(days)); } catch { /* ignore */ }
    let alive = true;
    setError(null);
    statsApi.overview(days)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "加载失败"); });
    return () => { alive = false; };
  }, [days]);

  return (
    <Box sx={{ width: "100%", maxWidth: 1600, mx: "auto", px: { xs: 2, sm: 2.5, md: 3 }, py: 3, pb: 8 }}>
      {/* 顶部：页签切换 + 共用时间范围 */}
      <Stack direction="row" spacing={1.5}
        sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1, mb: 2,
          borderBottom: 1, borderColor: "divider" }}>
        <Tabs value={tab} sx={{ flex: 1, minHeight: 40 }}
          onChange={(_, v: TabKey) => nav(v === "ops" ? "/monitor" : "/")}>
          <Tab value="overview" label="概览" sx={{ minHeight: 40, fontWeight: 600 }} />
          <Tab value="ops" label="AI 运行统计" sx={{ minHeight: 40, fontWeight: 600 }} />
        </Tabs>
        <Select size="small" value={days} onChange={(e) => setDays(Number(e.target.value))}
          aria-label="时间范围" sx={{ minWidth: 108, mb: 0.75, "& .MuiSelect-select": { py: 0.7 } }}>
          {RANGES.map((r) => <MenuItem key={r.days} value={r.days}>{r.label}</MenuItem>)}
        </Select>
      </Stack>

      {error ? (
        <Alert severity="error">概览加载失败：{error}</Alert>
      ) : !data ? (
        <Box sx={{ display: "grid", placeItems: "center", height: "50vh" }}><CircularProgress /></Box>
      ) : tab === "ops" ? (
        <AiStatsTab data={data} days={days} />
      ) : (
        <OverviewTab data={data} days={days} />
      )}
    </Box>
  );
}
