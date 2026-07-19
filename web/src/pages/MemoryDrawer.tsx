import { useEffect, useMemo, useState } from "react";
import {
  Dialog, Box, Typography, IconButton, CircularProgress, Alert, Stack, Card, Chip, Divider,
  Checkbox, Button, Select, MenuItem, Tooltip,
} from "@mui/material";
import type { ChipProps } from "@mui/material";
import { alpha } from "@mui/material/styles";
import CloseIcon from "@mui/icons-material/Close";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutlined";
import PsychologyIcon from "@mui/icons-material/Psychology";
import ChecklistIcon from "@mui/icons-material/Checklist";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import { statsApi, type MemoryItem } from "../api/stats";
import { fromNow } from "./statsShared";
import { Markdown } from "../components/Markdown";

// 时间远近上色：越新越「暖绿」，越旧越淡，一眼看出记忆新鲜度
function recencyColor(iso?: string): string {
  if (!iso) return "text.disabled";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 86400) return "success.main";        // 一天内
  if (s < 7 * 86400) return "info.main";        // 一周内
  if (s < 30 * 86400) return "warning.main";    // 一月内
  return "text.disabled";                       // 更久
}

// 记忆类型（mem_type）→ 中文标签 / 配色
const TYPE_LABEL: Record<string, string> = { semantic: "语义", episodic: "情景", procedural: "程序" };
const typeLabel = (t: string) => TYPE_LABEL[t] || "记忆";
function typeColor(t: string): ChipProps["color"] {
  const map: Record<string, ChipProps["color"]> = {
    semantic: "info", episodic: "secondary", procedural: "warning",
  };
  return map[t] || "default";
}

// 时间筛选：按天数窗口过滤
type TimeFilter = "all" | "7d" | "30d" | "old";
function withinTime(iso: string, f: TimeFilter): boolean {
  if (f === "all") return true;
  const days = (Date.now() - new Date(iso).getTime()) / 86400000;
  if (f === "7d") return days <= 7;
  if (f === "30d") return days <= 30;
  return days > 30;   // "old"：30 天前
}

// 粗略判断是否为 markdown：出现标题/列表/代码围栏/表格/加粗/链接等标记即按 md 渲染
function looksLikeMarkdown(text: string): boolean {
  return /(^|\n)\s{0,3}#{1,6}\s|(^|\n)\s*[-*+]\s|(^|\n)\s*\d+\.\s|```|(^|\n)\s*>\s|\|.*\|.*\||\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)/.test(text);
}

// total：首页卡片显示的偏好总数，用作请求上限，确保详情页展示「全部」而非固定前 50 条。
// onCountDelta：删除/批量删除/整理后回传数量变化量（负=减少），让父组件同步首页卡片数字。
export function MemoryDrawer({ open, onClose, total, onCountDelta }: {
  open: boolean; onClose: () => void; total?: number;
  onCountDelta?: (delta: number) => void;
}) {
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [busy, setBusy] = useState<"consolidate" | "batch" | null>(null);
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [timeFilter, setTimeFilter] = useState<TimeFilter>("all");
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setItems(null); setError(null); setNotice(null);
    setSelectMode(false); setSelected(new Set()); setTypeFilter("all"); setTimeFilter("all");
    // 按总数请求以覆盖全部偏好（后端上限 1000 兜底）；总数未知时回退一个较大值
    statsApi.memory(Math.max(1, total ?? 200))
      .then((d) => { if (alive) setItems(d); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "加载失败"); });
    return () => { alive = false; };
  // total 仅在打开时读取；数量变化不应触发重拉（否则闪 loading）
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // 各类型条数（用于筛选按钮上的计数），基于全量 items
  const typeCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const m of items || []) c[m.mem_type] = (c[m.mem_type] || 0) + 1;
    return c;
  }, [items]);

  // 当前展示的条目：类型 + 时间双重过滤
  const filtered = useMemo(() => (items || []).filter(
    (m) => (typeFilter === "all" || m.mem_type === typeFilter) && withinTime(m.created_at, timeFilter),
  ), [items, typeFilter, timeFilter]);

  // 统一更新列表并把数量变化量回传父组件
  function applyItems(next: MemoryItem[], delta: number) {
    setItems(next);
    if (delta) onCountDelta?.(delta);
  }

  function toggleSelect(id: string) {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  }

  async function remove(id: string) {
    setDeleting(id); setError(null);
    try {
      await statsApi.deleteMemory(id);
      applyItems((items || []).filter((m) => m.id !== id), -1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setDeleting(null);
    }
  }

  async function removeSelected() {
    if (!selected.size) return;
    setBusy("batch"); setError(null);
    try {
      const { deleted } = await statsApi.deleteMemories([...selected]);
      const gone = new Set(deleted);
      applyItems((items || []).filter((m) => !gone.has(m.id)), -deleted.length);
      setSelected(new Set()); setSelectMode(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "批量删除失败");
    } finally {
      setBusy(null);
    }
  }

  async function consolidate() {
    setBusy("consolidate"); setError(null); setNotice(null);
    try {
      const r = await statsApi.consolidateMemory();
      // 整合改变了条数，重新拉取全量并回传新总数
      const fresh = await statsApi.memory(Math.max(1, total ?? 200));
      applyItems(fresh, -(r.merged - r.created));
      setNotice(r.created > 0
        ? `已把 ${r.merged} 条相似偏好合并为 ${r.created} 条，现在共 ${fresh.length} 条`
        : "没有找到可合并的相似偏好");
    } catch (e) {
      setError(e instanceof Error ? e.message : "整理失败");
    } finally {
      setBusy(null);
    }
  }

  const TYPE_TABS: { key: string; label: string }[] = [
    { key: "all", label: "全部" },
    { key: "semantic", label: "语义" },
    { key: "episodic", label: "情景" },
    { key: "procedural", label: "程序" },
  ];

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth
      slotProps={{ paper: { sx: { maxHeight: "85vh" } } }}>
      <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 1.25, maxHeight: "85vh" }}>
        <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
          <PsychologyIcon color="primary" />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>AI 记住的偏好</Typography>
            <Typography sx={{ fontSize: 12.5, color: "text.secondary" }}>
              它从你们的对话里沉淀下来的长期记忆
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="关闭"><CloseIcon /></IconButton>
        </Stack>
        <Divider />

        {/* 工具栏：类型筛选 + 时间筛选 + 整理 + 多选（列表非空时才显示） */}
        {items && items.length > 0 && (
          <Stack spacing={1}>
            <Stack direction="row" spacing={0.75} sx={{ flexWrap: "wrap", gap: 0.75 }}>
              {TYPE_TABS.map((t) => {
                const n = t.key === "all" ? items.length : (typeCounts[t.key] || 0);
                if (t.key !== "all" && n === 0) return null;   // 没有该类型就不显示按钮
                return (
                  <Chip key={t.key} size="small" clickable
                    label={`${t.label} ${n}`}
                    color={typeFilter === t.key ? "primary" : "default"}
                    variant={typeFilter === t.key ? "filled" : "outlined"}
                    onClick={() => setTypeFilter(t.key)} sx={{ height: 24 }} />
                );
              })}
            </Stack>
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
              <Select size="small" value={timeFilter} aria-label="时间范围"
                onChange={(e) => setTimeFilter(e.target.value as TimeFilter)}
                sx={{ minWidth: 120, "& .MuiSelect-select": { py: 0.5, fontSize: 13 } }}>
                <MenuItem value="all">全部时间</MenuItem>
                <MenuItem value="7d">最近 7 天</MenuItem>
                <MenuItem value="30d">最近 30 天</MenuItem>
                <MenuItem value="old">30 天前</MenuItem>
              </Select>
              <Box sx={{ flex: 1 }} />
              <Tooltip title="把同主题的多条相似偏好合并成一条（会作废被合并的旧条目）">
                <span>
                  <Button size="small" variant="outlined" startIcon={busy === "consolidate"
                    ? <CircularProgress size={14} /> : <AutoAwesomeIcon sx={{ fontSize: 16 }} />}
                    disabled={!!busy} onClick={consolidate} sx={{ textTransform: "none" }}>
                    整理相似偏好
                  </Button>
                </span>
              </Tooltip>
              <Button size="small" variant={selectMode ? "contained" : "outlined"}
                startIcon={<ChecklistIcon sx={{ fontSize: 16 }} />} disabled={!!busy}
                onClick={() => { setSelectMode((v) => !v); setSelected(new Set()); }}
                sx={{ textTransform: "none" }}>
                {selectMode ? "退出多选" : "多选"}
              </Button>
            </Stack>
            {selectMode && (
              <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                <Typography sx={{ fontSize: 12.5, color: "text.secondary" }}>已选 {selected.size} 条</Typography>
                <Box sx={{ flex: 1 }} />
                <Button size="small" color="error" variant="contained"
                  startIcon={busy === "batch" ? <CircularProgress size={14} color="inherit" />
                    : <DeleteOutlineIcon sx={{ fontSize: 16 }} />}
                  disabled={!selected.size || !!busy} onClick={removeSelected}
                  sx={{ textTransform: "none" }}>
                  删除选中
                </Button>
              </Stack>
            )}
            {notice && <Alert severity="success" onClose={() => setNotice(null)} sx={{ py: 0 }}>{notice}</Alert>}
          </Stack>
        )}

        <Box sx={{ overflowY: "auto", flex: 1 }}>
          {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
          {!items && !error && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}><CircularProgress /></Box>
          )}
          {items && items.length === 0 && (
            <Typography sx={{ color: "text.secondary", py: 4, textAlign: "center" }}>
              还没有沉淀任何记忆。多聊几次，AI 会逐渐记住你的偏好。
            </Typography>
          )}
          {items && items.length > 0 && filtered.length === 0 && (
            <Typography sx={{ color: "text.secondary", py: 4, textAlign: "center" }}>
              当前筛选下没有偏好。
            </Typography>
          )}
          {filtered.length > 0 && (
            <Stack spacing={1.5}>
              {filtered.map((m) => (
                <Card key={m.id} variant="outlined" sx={{ p: 1.75, borderRadius: 2 }}>
                  <Stack direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
                    {selectMode && (
                      <Checkbox size="small" sx={{ p: 0.25, mt: -0.25 }} checked={selected.has(m.id)}
                        onChange={() => toggleSelect(m.id)} slotProps={{ input: { "aria-label": "选择这条记忆" } }} />
                    )}
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Stack direction="row" spacing={0.5} sx={{ justifyContent: "space-between", alignItems: "center", mb: 0.75 }}>
                        <Chip size="small" label={typeLabel(m.mem_type)} color={typeColor(m.mem_type)}
                          variant="outlined" sx={{ height: 20, fontSize: 11 }} />
                        <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
                          <Typography sx={{ fontSize: 11.5, fontWeight: 600, color: recencyColor(m.created_at) }}>
                            {fromNow(m.created_at)}
                          </Typography>
                          <IconButton size="small" aria-label="删除这条记忆" disabled={deleting === m.id || !!busy}
                            onClick={() => remove(m.id)}
                            sx={{ p: 0.25, color: "error.light",
                              "&:hover": { color: "error.main", bgcolor: (t) => alpha(t.palette.error.main, 0.08) } }}>
                            <DeleteOutlineIcon sx={{ fontSize: 16 }} />
                          </IconButton>
                        </Stack>
                      </Stack>
                      {looksLikeMarkdown(m.text) ? (
                        <Box sx={{ fontSize: 13.5, lineHeight: 1.55,
                          "& p": { my: 0.5 }, "& :first-of-type": { mt: 0 }, "& :last-child": { mb: 0 },
                          "& pre": { overflowX: "auto" } }}>
                          <Markdown>{m.text}</Markdown>
                        </Box>
                      ) : (
                        <Typography sx={{ fontSize: 13.5, whiteSpace: "pre-wrap", wordBreak: "break-word",
                          color: "text.primary", lineHeight: 1.55 }}>{m.text}</Typography>
                      )}
                    </Box>
                  </Stack>
                </Card>
              ))}
            </Stack>
          )}
        </Box>
      </Box>
    </Dialog>
  );
}
