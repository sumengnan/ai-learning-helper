// web/src/pages/DownloadPreviewDialog.tsx
import { useEffect, useState } from "react";
import {
  Dialog, DialogTitle, DialogContent, IconButton, Box, Typography,
  CircularProgress, Alert,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { api } from "../api/client";
import { previewKind } from "./downloadsUtils";

export type PreviewFile = { id: string; filename: string; content_type: string };

export function DownloadPreviewDialog({ file, onClose }: {
  file: PreviewFile | null; onClose: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const kind = file ? previewKind(file.content_type) : "none";

  useEffect(() => {
    if (!file || kind === "none") return;
    let objUrl: string | null = null;
    let cancelled = false;
    setLoading(true); setError(null); setText(null); setUrl(null);
    api.downloads.blob(file.id)
      .then(async (blob) => {
        if (cancelled) return;
        if (kind === "text") {
          setText(await blob.text());
        } else {
          objUrl = URL.createObjectURL(blob);
          setUrl(objUrl);
        }
      })
      .catch((e: any) => { if (!cancelled) setError(String(e?.message || e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; if (objUrl) URL.revokeObjectURL(objUrl); };
  }, [file, kind]);

  return (
    <Dialog open={file !== null} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1 }}>
        <Typography component="span" noWrap sx={{ fontWeight: 600, minWidth: 0 }}>{file?.filename}</Typography>
        <IconButton size="small" onClick={onClose} aria-label="关闭"><CloseIcon /></IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {loading && <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}><CircularProgress /></Box>}
        {error && <Alert severity="error">{error}</Alert>}
        {!loading && !error && kind === "image" && url && (
          <Box component="img" src={url} alt={file?.filename}
            sx={{ maxWidth: "100%", maxHeight: "70vh", display: "block", mx: "auto" }} />
        )}
        {!loading && !error && kind === "pdf" && url && (
          <Box component="iframe" title={file?.filename} src={url}
            sx={{ width: "100%", height: "70vh", border: 0 }} />
        )}
        {!loading && !error && kind === "text" && text !== null && (
          <Box component="pre" sx={{
            m: 0, whiteSpace: "pre-wrap", wordBreak: "break-word",
            fontFamily: "monospace", fontSize: 13, maxHeight: "70vh", overflow: "auto",
          }}>
            {text}
          </Box>
        )}
        {kind === "none" && (
          <Typography color="text.secondary" sx={{ py: 2 }}>
            该文件类型不支持预览，请下载后查看。
          </Typography>
        )}
      </DialogContent>
    </Dialog>
  );
}
