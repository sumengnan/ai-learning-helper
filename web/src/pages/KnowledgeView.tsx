// web/src/pages/KnowledgeView.tsx
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Box, Typography, Button, Card, CardContent, TextField, InputAdornment,
  IconButton, CircularProgress, Alert, Chip, Stack, Pagination,
  FormControl, InputLabel, Select, MenuItem, Tooltip,
} from "@mui/material";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import DeleteIcon from "@mui/icons-material/Delete";
import SearchIcon from "@mui/icons-material/Search";
import DescriptionIcon from "@mui/icons-material/Description";
import SearchOffOutlinedIcon from "@mui/icons-material/SearchOffOutlined";
import MenuBookOutlinedIcon from "@mui/icons-material/MenuBookOutlined";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api/client";
import { listItemVariants } from "../components/motion";
import { EmptyState } from "../components/EmptyState";
import { categoryColor, fmtDate, relevanceColor } from "./knowledgeUtils";
import { KnowledgeDetailDrawer } from "./KnowledgeDetailDrawer";

const PAGE_SIZE = 8;
const CATEGORIES = ["PDF", "Word", "文本", "Markdown", "其他"];

// 一张卡片 = 一个切分后的片段（chunk），filename 是其来源文件名
type Fragment = {
  id: string; filename: string; uploaded_at: string;
  category: string; excerpt: string; relevance?: number;
  // "rerank" = 精排绝对相关度；"rank" = 未开精排时的相对排名分（会误导，需在 UI 标明）
  relevance_kind?: "rerank" | "rank";
};

export function KnowledgeView() {
  const [docs, setDocs] = useState<Fragment[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");                    // 类型筛选（空=全部）
  const [results, setResults] = useState<Fragment[] | null>(null); // 非 null = 搜索态
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);

  const searching = results !== null;

  const loadList = useCallback((p: number, cat: string) => {
    return api.documents.list(p, PAGE_SIZE, cat).then((r) => {
      setDocs(r.items);
      setTotal(r.total);
    });
  }, []);

  // 输入防抖：非空进入搜索态（后端语义检索），清空回列表态
  useEffect(() => {
    const q = query.trim();
    const t = setTimeout(() => {
      setPage(1);
      setError(null);
      if (q) {
        setBusy(true);
        api.documents.search(q)
          .then((r) => setResults(r as Fragment[]))
          .catch((e: any) => { setError(String(e?.message || e)); setResults([]); })
          .finally(() => setBusy(false));
      } else {
        setResults(null);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [query]);

  // 列表态：翻页 / 回到列表态 / 切换类型时按后端分页加载
  useEffect(() => {
    if (!searching) loadList(page, category).catch((e: any) => setError(String(e?.message || e)));
  }, [page, searching, category, loadList]);

  async function upload(file: File) {
    setBusy(true); setError(null); setNotice(null);
    try {
      const r = await api.documents.upload(file);
      // 内容与已有文档完全相同 → 后端不会重复入库，如实说明，别让用户以为又存了一份
      setNotice(r.duplicate
        ? `《${r.filename}》已在知识库中（内容相同，未重复导入）`
        : `已导入《${r.filename}》，切分 ${r.num_chunks} 个片段`);
      setQuery(""); setResults(null);
      if (page === 1) await loadList(1, category); else setPage(1);
    } catch (e: any) { setError(`导入失败：${String(e?.message || e)}`); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  async function remove(id: string) {
    await api.documents.remove(id);
    if (searching) {
      setResults((rs) => (rs ?? []).filter((d) => d.id !== id));
    } else {
      const next = docs.length === 1 && page > 1 ? page - 1 : page;
      if (next !== page) setPage(next); else await loadList(page, category);
    }
  }

  // 搜索态结果在前端按类型过滤（列表态过滤在后端）
  const searchList = searching
    ? (category ? (results ?? []).filter((d) => d.category === category) : (results ?? []))
    : [];
  const shown = searching
    ? searchList.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
    : docs;
  const totalCount = searching ? searchList.length : total;
  const pageCount = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2, maxWidth: 880, mx: "auto" }}>
      {/* 头部：标题 + 副标题 + 导入按钮 */}
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>知识库</Typography>
          <Typography color="text.secondary" variant="body2">共 {total} 篇文档片段</Typography>
        </Box>
        <Button component="label" variant="contained" disabled={busy}
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <UploadFileIcon />}>
          {busy ? "导入中…" : "导入文档"}
          <input
            ref={fileRef} hidden type="file" accept=".pdf,.docx,.txt,.md"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }}
          />
        </Button>
      </Box>

      {/* 搜索框 + 类型筛选 */}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <TextField
          fullWidth size="small" placeholder="搜索文档…"
          value={query} onChange={(e) => setQuery(e.target.value)}
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
              ),
              endAdornment: busy ? <CircularProgress size={18} /> : undefined,
            },
          }}
        />
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel>类型</InputLabel>
          <Select label="类型" value={category}
            onChange={(e) => { setCategory(e.target.value); setPage(1); }}>
            <MenuItem value="">全部类型</MenuItem>
            {CATEGORIES.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
          </Select>
        </FormControl>
      </Stack>

      {notice && <Alert severity="success" onClose={() => setNotice(null)}>{notice}</Alert>}
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

      {/* 文档卡片列 */}
      {shown.length === 0 ? (
        searching
          ? <EmptyState icon={<SearchOffOutlinedIcon />} title="未找到相关文档"
              hint="换个关键词试试" />
          : <EmptyState icon={<MenuBookOutlinedIcon />} title="还没有导入文档"
              hint="点击上方「上传」导入资料，或在聊天中让助手保存到知识库" />
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <AnimatePresence initial={false}>
            {shown.map((d) => (
              <motion.div key={d.id} layout variants={listItemVariants}
                initial="initial" animate="animate" exit="exit">
                <Card variant="outlined"
                  onClick={() => setPreviewId(d.id)}
                  sx={{
                    cursor: "pointer", transition: "border-color .15s, box-shadow .15s",
                    "&:hover": { borderColor: "primary.main", boxShadow: 2 },
                  }}>
                  <CardContent sx={{ "&:last-child": { pb: 2 } }}>
                    <Box sx={{ minWidth: 0 }}>
                      {/* 顶行：来源文件名（主色，突出）+ 相关度 + 删除 */}
                      <Stack direction="row" spacing={1}
                        sx={{ alignItems: "center", justifyContent: "space-between" }}>
                        <Stack direction="row" spacing={0.5}
                          sx={{ alignItems: "center", minWidth: 0 }}>
                          <DescriptionIcon color="primary" fontSize="small" />
                          <Typography variant="body2" sx={{
                            fontWeight: 600, color: "primary.main", wordBreak: "break-all",
                          }}>
                            {d.filename}
                          </Typography>
                        </Stack>
                        <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", flexShrink: 0 }}>
                          {searching && d.relevance !== undefined && (
                            <Tooltip title={d.relevance_kind === "rank"
                              ? "未开精排，此为相对排名分，非绝对相关度" : ""}>
                              <Chip size="small" variant="outlined"
                                label={d.relevance_kind === "rank"
                                  ? `排名分 ${d.relevance}%` : `相关度 ${d.relevance}%`}
                                sx={(theme) => ({
                                  color: relevanceColor(d.relevance ?? 0, theme.palette.mode, d.relevance_kind ?? "rank"),
                                  borderColor: relevanceColor(d.relevance ?? 0, theme.palette.mode, d.relevance_kind ?? "rank"),
                                  fontWeight: 600,
                                })} />
                            </Tooltip>
                          )}
                          <IconButton size="small" color="error" aria-label="删除片段"
                            onClick={(e) => { e.stopPropagation(); remove(d.id); }}>
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </Stack>
                      </Stack>
                      {/* 片段正文：作为主内容用常规文字色 */}
                      {d.excerpt && (
                        <Typography variant="body2" color="text.primary" sx={{
                          mt: 0.75,
                          display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                        }}>
                          {d.excerpt}
                        </Typography>
                      )}
                      {/* 底部：分类（按类彩色）+ 日期（弱化灰） */}
                      <Stack direction="row" spacing={1}
                        sx={{ mt: 1, alignItems: "center" }}>
                        <Chip size="small" color={categoryColor(d.category)} variant="outlined"
                          label={d.category} />
                        <Typography variant="caption" color="text.secondary">
                          {fmtDate(d.uploaded_at)}
                        </Typography>
                      </Stack>
                    </Box>
                  </CardContent>
                </Card>
              </motion.div>
            ))}
          </AnimatePresence>
        </Box>
      )}

      {/* 底部居中分页 */}
      {pageCount > 1 && (
        <Box sx={{ display: "flex", justifyContent: "center", mt: 1 }}>
          <Pagination color="primary" count={pageCount} page={page}
            onChange={(_, p) => setPage(p)} />
        </Box>
      )}

      {/* 片段预览抽屉：右侧滑出，不跳转页面 */}
      <KnowledgeDetailDrawer id={previewId} onClose={() => setPreviewId(null)} />
    </Box>
  );
}
