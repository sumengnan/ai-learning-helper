// web/src/components/MessageDownloads.tsx
// 助手在本轮生成的文件（save_download）在聊天里内联展示：预览 + 下载
import { useState } from "react";
import { Box, Stack, IconButton, Tooltip, Typography } from "@mui/material";
import VisibilityIcon from "@mui/icons-material/Visibility";
import DownloadIcon from "@mui/icons-material/Download";
import type { StepDownload } from "../types";
import { fileMeta, formatBytes, previewKind, downloadById } from "../pages/downloadsUtils";
import { DownloadPreviewDialog, type PreviewFile } from "../pages/DownloadPreviewDialog";

export function MessageDownloads({ items }: { items: StepDownload[] }) {
  const [preview, setPreview] = useState<PreviewFile | null>(null);
  if (items.length === 0) return null;
  return (
    <Box sx={{ mt: 1, display: "flex", flexDirection: "column", gap: 0.75 }}>
      {items.map((d) => {
        const meta = fileMeta(d.content_type);
        const canPreview = previewKind(d.content_type) !== "none";
        return (
          <Stack key={d.id} direction="row" spacing={1}
            sx={{
              alignItems: "center", maxWidth: 420,
              border: 1, borderColor: "divider", borderRadius: 1.5, px: 1, py: 0.5,
            }}>
            <meta.Icon color={meta.color === "default" ? "action" : meta.color} fontSize="small" />
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography variant="body2" noWrap sx={{ fontWeight: 600 }}>{d.filename}</Typography>
              <Typography variant="caption" color="text.secondary">
                {meta.label} · {formatBytes(d.size)}
              </Typography>
            </Box>
            {canPreview && (
              <Tooltip title="预览">
                <IconButton size="small" onClick={() => setPreview(d)} aria-label={`预览 ${d.filename}`}>
                  <VisibilityIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            <Tooltip title="下载">
              <IconButton size="small" onClick={() => downloadById(d.id, d.filename)}
                aria-label={`下载 ${d.filename}`}>
                <DownloadIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Stack>
        );
      })}
      <DownloadPreviewDialog file={preview} onClose={() => setPreview(null)} />
    </Box>
  );
}
