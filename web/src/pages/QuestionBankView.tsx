import { useCallback, useEffect, useRef, useState } from "react";
import {
  Box, Typography, Button, Card, CardContent, TextField, InputAdornment,
  IconButton, Checkbox, Chip, Stack, Pagination, Alert, MenuItem, Select,
  CircularProgress, FormControl, InputLabel,
} from "@mui/material";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import DeleteIcon from "@mui/icons-material/Delete";
import DeleteSweepIcon from "@mui/icons-material/DeleteSweep";
import SearchIcon from "@mui/icons-material/Search";
import { AnimatePresence, motion } from "framer-motion";
import { api, type Question } from "../api/client";
import { listItemVariants } from "../components/motion";
import { QuestionDetailDrawer } from "./QuestionDetailDrawer";

const PAGE_SIZE = 10;
const TYPES = [
  { key: "single", label: "单选" },
  { key: "multiple", label: "多选" },
  { key: "truefalse", label: "判断" },
  { key: "short", label: "简答" },
];
const typeLabel = (t: string) => TYPES.find((x) => x.key === t)?.label ?? t;

function answerText(q: Question): string {
  if (q.type === "truefalse") return q.answer ? "正确" : "错误";
  if (q.type === "single" && q.options && typeof q.answer === "number")
    return q.options[q.answer] ?? String(q.answer);
  if (q.type === "multiple" && q.options && Array.isArray(q.answer))
    return (q.answer as number[]).map((i) => q.options![i] ?? i).join("、");
  return String(q.answer ?? "");
}

// 单行截断（题干/答案过长显示 …）
const clampSx = {
  display: "-webkit-box", WebkitLineClamp: 1, WebkitBoxOrient: "vertical" as const,
  overflow: "hidden", wordBreak: "break-all" as const,
};

export default function QuestionBankView() {
  const [items, setItems] = useState<Question[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [source, setSource] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<Question | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback((p: number, filters: { q: string; type: string; source: string }) => {
    return api.questions.list({ page: p, size: PAGE_SIZE, q: filters.q, type: filters.type, source: filters.source })
      .then((r) => { setItems(r.items); setTotal(r.total); });
  }, []);

  const refreshSources = useCallback(() => api.questions.sources().then(setSources), []);
  useEffect(() => { refreshSources(); }, [refreshSources]);

  // 筛选变化（含防抖搜索）→ 回第 1 页并加载
  useEffect(() => {
    const t = setTimeout(() => {
      setPage(1);
      setError(null);
      load(1, { q: q.trim(), type, source }).catch((e: any) => setError(String(e?.message || e)));
    }, 300);
    return () => clearTimeout(t);
  }, [q, type, source, load]);

  // 翻页
  useEffect(() => {
    load(page, { q: q.trim(), type, source }).catch((e: any) => setError(String(e?.message || e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const toggle = (id: string) => setSelected((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n;
  });

  async function reload() {
    setSelected(new Set());
    await Promise.all([load(page, { q: q.trim(), type, source }), refreshSources()]);
  }

  async function removeOne(id: string) { await api.questions.remove(id); await reload(); }

  async function removeSelected() {
    if (selected.size === 0) return;
    await api.questions.removeMany([...selected]);
    await reload();
  }

  async function upload(file: File) {
    setBusy(true); setError(null); setNotice(null);
    try {
      const r = await api.questions.import(file);
      setNotice(`导入完成：新增 ${r.imported} 道，跳过重复 ${r.skipped_duplicate} 道，无效 ${r.skipped_invalid} 道。`);
      setPage(1);
      await reload();
    } catch (e: any) { setError(String(e?.message || e)); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2, maxWidth: 880, mx: "auto" }}>
      {/* 头部：标题 + 计数 + 导入 */}
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>题库</Typography>
          <Typography color="text.secondary" variant="body2">共 {total} 道题目</Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <Button variant="outlined" color="error" startIcon={<DeleteSweepIcon />}
            onClick={removeSelected} disabled={selected.size === 0}>
            批量删除（{selected.size}）
          </Button>
          <Button component="label" variant="contained" startIcon={<UploadFileIcon />} disabled={busy}>
            导入题库
            <input ref={fileRef} hidden type="file" accept=".txt,.md,.pdf,.docx"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
          </Button>
        </Stack>
      </Box>

      {/* 筛选工具条：题名 + 题型 + 来源 */}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <TextField fullWidth size="small" placeholder="搜索题名…"
          value={q} onChange={(e) => setQ(e.target.value)}
          slotProps={{ input: {
            startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>,
            endAdornment: busy ? <CircularProgress size={18} /> : undefined,
          } }} />
        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>题型</InputLabel>
          <Select label="题型" value={type} onChange={(e) => setType(e.target.value)}>
            <MenuItem value="">全部题型</MenuItem>
            {TYPES.map((t) => <MenuItem key={t.key} value={t.key}>{t.label}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel>来源</InputLabel>
          <Select label="来源" value={source} onChange={(e) => setSource(e.target.value)}>
            <MenuItem value="">全部来源</MenuItem>
            {sources.map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </Select>
        </FormControl>
      </Stack>

      {notice && <Alert severity="success" onClose={() => setNotice(null)}>{notice}</Alert>}
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

      {items.length === 0 ? (
        <Typography color="text.secondary" sx={{ py: 4, textAlign: "center" }}>暂无题目</Typography>
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <AnimatePresence initial={false}>
            {items.map((item) => (
              <motion.div key={item.id} layout variants={listItemVariants}
                initial="initial" animate="animate" exit="exit">
                <Card variant="outlined"
                  sx={{ "&:hover": { borderColor: "primary.main", boxShadow: 2 } }}>
                  <CardContent sx={{ display: "flex", gap: 1, "&:last-child": { pb: 2 } }}>
                    <Checkbox sx={{ p: 0, mt: 0.25 }} checked={selected.has(item.id)}
                      onChange={() => toggle(item.id)} />
                    <Box sx={{ minWidth: 0, flex: 1, cursor: "pointer" }} onClick={() => setPreview(item)}>
                      <Stack direction="row" spacing={1} sx={{ alignItems: "center", minWidth: 0 }}>
                        <Chip size="small" label={typeLabel(item.type)} />
                        <Typography variant="body2" sx={{ fontWeight: 600, ...clampSx }}>{item.stem}</Typography>
                      </Stack>
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, ...clampSx }}>
                        答案：{answerText(item)}
                      </Typography>
                      {item.source && (
                        <Typography variant="caption" color="text.secondary">· {item.source}</Typography>
                      )}
                    </Box>
                    <IconButton size="small" color="error" aria-label="删除题目"
                      onClick={() => removeOne(item.id)}>
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </CardContent>
                </Card>
              </motion.div>
            ))}
          </AnimatePresence>
        </Box>
      )}

      {pageCount > 1 && (
        <Box sx={{ display: "flex", justifyContent: "center", mt: 1 }}>
          <Pagination color="primary" count={pageCount} page={page} onChange={(_, p) => setPage(p)} />
        </Box>
      )}

      <QuestionDetailDrawer question={preview} onClose={() => setPreview(null)} />
    </Box>
  );
}
