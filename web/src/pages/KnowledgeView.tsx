// web/src/pages/KnowledgeView.tsx
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Box, Typography, Button, Card, CardContent, TextField, InputAdornment,
  IconButton, CircularProgress, Alert, Chip, Stack, Pagination,
} from "@mui/material";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import DeleteIcon from "@mui/icons-material/Delete";
import SearchIcon from "@mui/icons-material/Search";
import DescriptionIcon from "@mui/icons-material/Description";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api/client";
import { listItemVariants } from "../components/motion";

const PAGE_SIZE = 8;

type Doc = {
  id: string; filename: string; uploaded_at: string;
  category: string; excerpt: string; relevance?: number;
};

function fmtDate(iso: string): string {
  return iso ? iso.slice(0, 10) : "";
}

export function KnowledgeView() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [total, setTotal] = useState(0);
  const [totalChunks, setTotalChunks] = useState(0);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Doc[] | null>(null); // 非 null = 搜索态
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const searching = results !== null;

  const loadList = useCallback((p: number) => {
    return api.documents.list(p, PAGE_SIZE).then((r) => {
      setDocs(r.items);
      setTotal(r.total);
      setTotalChunks(r.total_chunks);
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
          .then((r) => setResults(r as Doc[]))
          .catch((e: any) => { setError(String(e?.message || e)); setResults([]); })
          .finally(() => setBusy(false));
      } else {
        setResults(null);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [query]);

  // 列表态：翻页或回到列表态时按后端分页加载
  useEffect(() => {
    if (!searching) loadList(page).catch((e: any) => setError(String(e?.message || e)));
  }, [page, searching, loadList]);

  async function upload(file: File) {
    setBusy(true); setError(null);
    try {
      await api.documents.upload(file);
      setQuery(""); setResults(null);
      if (page === 1) await loadList(1); else setPage(1);
    } catch (e: any) { setError(String(e?.message || e)); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  async function remove(id: string) {
    await api.documents.remove(id);
    if (searching) {
      setResults((rs) => (rs ?? []).filter((d) => d.id !== id));
    } else {
      const next = docs.length === 1 && page > 1 ? page - 1 : page;
      if (next !== page) setPage(next); else await loadList(page);
    }
  }

  const shown = searching
    ? (results ?? []).slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
    : docs;
  const totalCount = searching ? (results ?? []).length : total;
  const pageCount = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2, maxWidth: 880, mx: "auto" }}>
      {/* 头部：标题 + 副标题 + 导入按钮 */}
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>知识库</Typography>
          <Typography color="text.secondary" variant="body2">共 {totalChunks} 篇文档片段</Typography>
        </Box>
        <Button component="label" variant="contained" startIcon={<UploadFileIcon />} disabled={busy}>
          导入文档
          <input
            ref={fileRef} hidden type="file" accept=".pdf,.docx,.txt,.md"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }}
          />
        </Button>
      </Box>

      {/* 搜索框 */}
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

      {error && <Alert severity="error">{error}</Alert>}

      {/* 文档卡片列 */}
      {shown.length === 0 ? (
        <Typography color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
          {searching ? "未找到相关文档" : "还没有导入文档"}
        </Typography>
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <AnimatePresence initial={false}>
            {shown.map((d) => (
              <motion.div key={d.id} layout variants={listItemVariants}
                initial="initial" animate="animate" exit="exit">
                <Card variant="outlined">
                  <CardContent sx={{ "&:last-child": { pb: 2 } }}>
                    <Stack direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
                      <DescriptionIcon color="action" fontSize="small" sx={{ mt: 0.3 }} />
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Stack direction="row" spacing={1}
                          sx={{ alignItems: "center", justifyContent: "space-between" }}>
                          <Typography sx={{ fontWeight: 600, wordBreak: "break-all" }}>
                            {d.filename}
                          </Typography>
                          <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", flexShrink: 0 }}>
                            {searching && d.relevance !== undefined && (
                              <Chip size="small" color="primary" variant="outlined"
                                label={`相关度 ${d.relevance}%`} />
                            )}
                            <IconButton size="small" color="error" aria-label="删除文档"
                              onClick={() => remove(d.id)}>
                              <DeleteIcon fontSize="small" />
                            </IconButton>
                          </Stack>
                        </Stack>
                        {d.excerpt && (
                          <Typography variant="body2" color="text.secondary" sx={{
                            mt: 0.5,
                            display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                            overflow: "hidden",
                          }}>
                            {d.excerpt}
                          </Typography>
                        )}
                        <Stack direction="row" spacing={1}
                          sx={{ mt: 1, alignItems: "center", color: "text.secondary" }}>
                          <Chip size="small" label={d.category} />
                          <Typography variant="caption">{fmtDate(d.uploaded_at)}</Typography>
                        </Stack>
                      </Box>
                    </Stack>
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
    </Box>
  );
}
