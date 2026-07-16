import { useEffect, useState } from "react";
import {
  Box, Typography, Card, CardContent, Chip, Stack, Checkbox, Button, Pagination,
  IconButton, Tooltip, Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
} from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import DeleteIcon from "@mui/icons-material/Delete";
import DeleteSweepIcon from "@mui/icons-material/DeleteSweep";
import VisibilityIcon from "@mui/icons-material/Visibility";
import FolderOpenOutlinedIcon from "@mui/icons-material/FolderOpenOutlined";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api/client";
import { listItemVariants } from "../components/motion";
import { EmptyState } from "../components/EmptyState";
import { formatBytes, fileMeta, previewKind } from "./downloadsUtils";
import { DownloadPreviewDialog, type PreviewFile } from "./DownloadPreviewDialog";

interface Download {
  id: string;
  filename: string;
  size: number;
  content_type: string;
  created_at: string;
}

const PAGE_SIZE = 8;

export default function DownloadsView() {
  const [items, setItems] = useState<Download[]>([]);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<PreviewFile | null>(null);
  const [confirmIds, setConfirmIds] = useState<string[] | null>(null);

  const refresh = () => api.downloads.list().then((list: Download[]) => {
    // 按时间倒排（后端已 seq DESC，这里再按 created_at 兜底保证最新在前）
    list.sort((a, b) => b.created_at.localeCompare(a.created_at));
    setItems(list);
  });
  useEffect(() => { refresh(); }, []);

  const pageCount = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const shown = items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const pageAllSelected = shown.length > 0 && shown.every((d) => selected.has(d.id));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }
  function togglePage() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (pageAllSelected) shown.forEach((d) => next.delete(d.id));
      else shown.forEach((d) => next.add(d.id));
      return next;
    });
  }

  async function download(d: Download) {
    await api.downloads.save(d.id, d.filename);   // 鉴权 blob → 触发保存（与聊天页共用）
  }

  async function confirmDelete() {
    const ids = confirmIds ?? [];
    await Promise.all(ids.map((id) => api.downloads.remove(id)));
    setConfirmIds(null);
    setSelected((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => next.delete(id));
      return next;
    });
    await refresh();
    setPage((p) => Math.min(p, Math.max(1, Math.ceil((items.length - ids.length) / PAGE_SIZE))));
  }

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2, maxWidth: 880, mx: "auto" }}>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>下载管理</Typography>
          <Typography color="text.secondary" variant="body2">共 {items.length} 个文件</Typography>
        </Box>
        {selected.size > 0 && (
          <Button variant="contained" color="error" startIcon={<DeleteSweepIcon />}
            onClick={() => setConfirmIds([...selected])}>
            删除选中（{selected.size}）
          </Button>
        )}
      </Box>

      {items.length === 0 ? (
        <EmptyState icon={<FolderOpenOutlinedIcon />} title="暂无文件"
          hint="聊天中让助手用 save_download 保存内容后会出现在这里" />
      ) : (
        <>
          {/* 本页全选 */}
          <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", color: "text.secondary" }}>
            <Checkbox size="small" checked={pageAllSelected}
              indeterminate={!pageAllSelected && shown.some((d) => selected.has(d.id))}
              onChange={togglePage} slotProps={{ input: { "aria-label": "本页全选" } }} />
            <Typography variant="body2">
              {selected.size > 0 ? `已选 ${selected.size} 项` : "本页全选"}
            </Typography>
          </Stack>

          <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
            <AnimatePresence initial={false}>
            {shown.map((d) => {
              const meta = fileMeta(d.content_type);
              const canPreview = previewKind(d.content_type, d.filename) !== "none";
              return (
              <motion.div key={d.id} layout variants={listItemVariants}
                initial="initial" animate="animate" exit="exit">
              <Card variant="outlined">
                <CardContent sx={{ display: "flex", alignItems: "center", gap: 1.5, "&:last-child": { pb: 2 } }}>
                  <Checkbox size="small" checked={selected.has(d.id)}
                    onChange={() => toggle(d.id)}
                    slotProps={{ input: { "aria-label": `选择 ${d.filename}` } }} />
                  {/* 类型图标（按类彩色） */}
                  <Box sx={{
                    width: 44, height: 44, borderRadius: 1.5, flexShrink: 0,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    bgcolor: "action.hover",
                  }}>
                    <meta.Icon color={meta.color === "default" ? "action" : meta.color} />
                  </Box>
                  <Box sx={{ minWidth: 0, flex: 1 }}>
                    {/* 文件名：主色高亮 */}
                    <Typography noWrap sx={{ fontWeight: 600, color: "primary.main" }}>
                      {d.filename}
                    </Typography>
                    {/* 类型彩色 Chip + 人类可读大小 + 日期（弱化） */}
                    <Stack direction="row" spacing={1} sx={{ mt: 0.5, alignItems: "center", flexWrap: "wrap", rowGap: 0.5 }}>
                      <Chip size="small" color={meta.color} variant="outlined" label={meta.label} />
                      <Typography variant="caption" color="text.secondary">{formatBytes(d.size)}</Typography>
                      <Typography variant="caption" color="text.disabled">·</Typography>
                      <Typography variant="caption" color="text.secondary">{d.created_at.slice(0, 10)}</Typography>
                    </Stack>
                  </Box>
                  {canPreview && (
                    <Tooltip title="预览">
                      <IconButton onClick={() => setPreview(d)} aria-label="预览文件">
                        <VisibilityIcon />
                      </IconButton>
                    </Tooltip>
                  )}
                  <Tooltip title="下载">
                    <IconButton onClick={() => download(d)} aria-label="下载文件">
                      <DownloadIcon />
                    </IconButton>
                  </Tooltip>
                  <IconButton color="error" onClick={() => setConfirmIds([d.id])} aria-label="删除文件">
                    <DeleteIcon />
                  </IconButton>
                </CardContent>
              </Card>
              </motion.div>
              );
            })}
            </AnimatePresence>
          </Box>

          {pageCount > 1 && (
            <Box sx={{ display: "flex", justifyContent: "center", mt: 1 }}>
              <Pagination color="primary" count={pageCount} page={page}
                onChange={(_, p) => setPage(p)} />
            </Box>
          )}
        </>
      )}

      {/* 预览弹窗 */}
      <DownloadPreviewDialog file={preview} onClose={() => setPreview(null)} />

      {/* 删除确认 */}
      <Dialog open={confirmIds !== null} onClose={() => setConfirmIds(null)}>
        <DialogTitle>删除确认</DialogTitle>
        <DialogContent>
          <DialogContentText>
            确认删除选中的 {confirmIds?.length ?? 0} 个文件？此操作不可撤销。
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmIds(null)}>取消</Button>
          <Button color="error" variant="contained" onClick={confirmDelete}>删除</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
