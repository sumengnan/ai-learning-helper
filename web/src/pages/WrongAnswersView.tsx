import { useCallback, useEffect, useState } from "react";
import {
  Box, Typography, Card, CardContent, Chip, IconButton, Stack, TextField,
  InputAdornment, Pagination, Alert, MenuItem, Select, FormControl, InputLabel,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import DeleteIcon from "@mui/icons-material/Delete";
import SearchIcon from "@mui/icons-material/Search";
import SentimentSatisfiedAltOutlinedIcon from "@mui/icons-material/SentimentSatisfiedAltOutlined";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api/client";
import { listItemVariants } from "../components/motion";
import { EmptyState } from "../components/EmptyState";
import {
  WrongAnswerDetailDrawer, answerText, typeColor, type WrongItem,
} from "./WrongAnswerDetailDrawer";
import { fromNow } from "./statsShared";

const PAGE_SIZE = 10;
const TYPES = [
  { key: "single", label: "单选" },
  { key: "multiple", label: "多选" },
  { key: "truefalse", label: "判断" },
  { key: "short", label: "简答" },
];
const typeLabel = (t: string) => TYPES.find((x) => x.key === t)?.label ?? t;

// 多行截断（答案过长显示 …）
const clampSx = {
  display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" as const,
  overflow: "hidden", wordBreak: "break-all" as const,
};

export default function WrongAnswersView() {
  const [items, setItems] = useState<WrongItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [preview, setPreview] = useState<WrongItem | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((p: number, filters: { q: string; type: string }) => {
    return api.wrong.list({ page: p, size: PAGE_SIZE, q: filters.q, type: filters.type })
      .then((r: { items: WrongItem[]; total: number }) => { setItems(r.items); setTotal(r.total); });
  }, []);

  // 筛选变化（含防抖搜索）→ 回第 1 页并加载
  useEffect(() => {
    const t = setTimeout(() => {
      setPage(1);
      setError(null);
      load(1, { q: q.trim(), type }).catch((e: any) => setError(String(e?.message || e)));
    }, 300);
    return () => clearTimeout(t);
  }, [q, type, load]);

  // 翻页
  useEffect(() => {
    load(page, { q: q.trim(), type }).catch((e: any) => setError(String(e?.message || e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  async function removeOne(id: string) {
    await api.wrong.removeMany([id]);
    // 删掉本页最后一条时退回上一页（翻页 effect 负责重载），避免停在空页
    if (items.length === 1 && page > 1) setPage(page - 1);
    else await load(page, { q: q.trim(), type });
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2, maxWidth: 880, mx: "auto" }}>
      {/* 头部：标题 + 计数 */}
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>错题集</Typography>
        <Typography color="text.secondary" variant="body2">共 {total} 道错题</Typography>
      </Box>

      {/* 筛选工具条：题名 + 题型 */}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <TextField fullWidth size="small" placeholder="搜索题名…"
          value={q} onChange={(e) => setQ(e.target.value)}
          slotProps={{ input: {
            startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>,
          } }} />
        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>题型</InputLabel>
          <Select label="题型" value={type} onChange={(e) => setType(e.target.value)}>
            <MenuItem value="">全部题型</MenuItem>
            {TYPES.map((t) => <MenuItem key={t.key} value={t.key}>{t.label}</MenuItem>)}
          </Select>
        </FormControl>
      </Stack>

      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

      {items.length === 0 ? (
        (q || type)
          ? <EmptyState icon={<SentimentSatisfiedAltOutlinedIcon />} title="未找到符合条件的错题"
              hint="试试调整搜索词或筛选条件" />
          : <EmptyState icon={<SentimentSatisfiedAltOutlinedIcon />} title="暂无错题"
              hint="答错的题会自动收集到这里，方便你复习巩固" />
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <AnimatePresence initial={false}>
            {items.map((w) => (
              <motion.div key={w.id} layout variants={listItemVariants}
                initial="initial" animate="animate" exit="exit">
                <Card variant="outlined"
                  sx={{ "&:hover": { borderColor: "primary.main", boxShadow: 2 } }}>
                  <CardContent sx={{ display: "flex", gap: 1, "&:last-child": { pb: 2 } }}>
                    <Box sx={{ minWidth: 0, flex: 1, cursor: "pointer" }} onClick={() => setPreview(w)}>
                      {/* 题目：正文色加粗，作为主内容突出 */}
                      <Typography variant="body1" sx={{
                        fontWeight: 700, color: "text.primary",
                        display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                        overflow: "hidden", wordBreak: "break-all",
                      }}>
                        {w.snapshot.stem}
                      </Typography>

                      {/* 我的答案：红色淡块 + 左侧红条 */}
                      <Box sx={{
                        mt: 0.75, px: 1, py: 0.5, borderRadius: 1,
                        borderLeft: "2px solid", borderColor: "error.light",
                        bgcolor: (t) => alpha(t.palette.error.main, 0.05),
                      }}>
                        <Typography variant="body2" sx={{ color: "text.secondary", ...clampSx }}>
                          <Box component="span" sx={{ color: "error.main", fontWeight: 600 }}>我的答案：</Box>
                          {answerText(w.snapshot.type, w.snapshot.options, w.user_answer)}
                        </Typography>
                      </Box>

                      {/* 正确答案：绿色淡块 + 左侧绿条 */}
                      <Box sx={{
                        mt: 0.5, px: 1, py: 0.5, borderRadius: 1,
                        borderLeft: "2px solid", borderColor: "success.light",
                        bgcolor: (t) => alpha(t.palette.success.main, 0.05),
                      }}>
                        <Typography variant="body2" sx={{ color: "text.secondary", ...clampSx }}>
                          <Box component="span" sx={{ color: "success.main", fontWeight: 600 }}>正确答案：</Box>
                          {answerText(w.snapshot.type, w.snapshot.options, w.snapshot.answer)}
                        </Typography>
                      </Box>

                      {/* 底部：题型标签 + 入集时间，分隔线与上方隔开 */}
                      <Stack direction="row" spacing={1} sx={{
                        mt: 1, pt: 1, alignItems: "center", flexWrap: "wrap",
                        borderTop: "1px dashed", borderColor: "divider",
                      }}>
                        <Chip size="small" variant="outlined"
                          label={typeLabel(w.snapshot.type)} color={typeColor(w.snapshot.type)} />
                        {/* 相对时间：复习场景下「多久以前错的」比具体日期更有用 */}
                        <Typography variant="caption" sx={{ color: "text.secondary" }}>
                          {fromNow(w.created_at)}
                        </Typography>
                      </Stack>
                    </Box>
                    <IconButton size="small" color="error" aria-label="删除错题"
                      onClick={() => removeOne(w.id)}>
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

      <WrongAnswerDetailDrawer item={preview} onClose={() => setPreview(null)} />
    </Box>
  );
}
