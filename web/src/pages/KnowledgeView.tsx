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
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { listItemVariants } from "../components/motion";
import { categoryColor, fmtDate } from "./knowledgeUtils";

const PAGE_SIZE = 8;

// 一张卡片 = 一个切分后的片段（chunk），filename 是其来源文件名
type Fragment = {
  id: string; filename: string; uploaded_at: string;
  category: string; excerpt: string; relevance?: number;
};

export function KnowledgeView() {
  const [docs, setDocs] = useState<Fragment[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Fragment[] | null>(null); // 非 null = 搜索态
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const searching = results !== null;

  const loadList = useCallback((p: number) => {
    return api.documents.list(p, PAGE_SIZE).then((r) => {
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
          <Typography color="text.secondary" variant="body2">共 {total} 篇文档片段</Typography>
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
                <Card variant="outlined"
                  onClick={() => navigate(`/knowledge/${d.id}`)}
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
                            <Chip size="small" color="primary" variant="outlined"
                              label={`相关度 ${d.relevance}%`} />
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
    </Box>
  );
}
