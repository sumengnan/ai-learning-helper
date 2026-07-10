import { useEffect, useState } from "react";
import {
  Box, Chip, CircularProgress, Dialog, DialogContent, IconButton, Typography,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import ImageIcon from "@mui/icons-material/Image";
import MovieIcon from "@mui/icons-material/Movie";
import PictureAsPdfIcon from "@mui/icons-material/PictureAsPdf";
import DescriptionIcon from "@mui/icons-material/Description";
import InsertDriveFileIcon from "@mui/icons-material/InsertDriveFile";
import type { Attachment } from "../types";
import { api } from "../api/client";

// 组件内部条目：服务端附件元数据 + 可选的本地 File（待发时用于即时预览）+ 上传状态
export type AttachmentItem = Attachment & {
  file?: File;
  status?: "uploading" | "error";
  error?: string;
};

function isImage(ct: string) { return ct.startsWith("image/"); }
function isVideo(ct: string) { return ct.startsWith("video/"); }
function isPdf(ct: string) { return ct === "application/pdf"; }
function isWord(ct: string) {
  return ct.includes("word") || ct.includes("officedocument.wordprocessing");
}
function isText(ct: string) {
  return ct.startsWith("text/") || ct === "application/json";
}

function typeIcon(ct: string) {
  if (isImage(ct)) return <ImageIcon fontSize="small" />;
  if (isVideo(ct)) return <MovieIcon fontSize="small" />;
  if (isPdf(ct)) return <PictureAsPdfIcon fontSize="small" />;
  if (isWord(ct)) return <DescriptionIcon fontSize="small" />;
  return <InsertDriveFileIcon fontSize="small" />;
}

function humanSize(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** 解析出可用于 <img>/<video>/<iframe> 的 URL：本地 File 直接 objectURL，否则鉴权取 blob。 */
function useAttachmentUrl(item: AttachmentItem | null, enabled: boolean): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!item || !enabled) { setUrl(null); return; }
    let revoked = false;
    let objectUrl: string | null = null;
    if (item.file) {
      objectUrl = URL.createObjectURL(item.file);
      setUrl(objectUrl);
    } else {
      api.attachments.blob(item.id).then((b) => {
        if (revoked) return;
        objectUrl = URL.createObjectURL(b);
        setUrl(objectUrl);
      }).catch(() => { if (!revoked) setUrl(null); });
    }
    return () => { revoked = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [item, enabled]);
  return url;
}

/** 图片缩略图（40x40）：本地或鉴权 blob。加载中显示图标位。 */
function Thumb({ item }: { item: AttachmentItem }) {
  const url = useAttachmentUrl(item, true);
  return (
    <Box sx={{
      width: 24, height: 24, borderRadius: 0.5, overflow: "hidden",
      bgcolor: "action.hover", display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      {url
        ? <Box component="img" src={url} alt={item.filename}
            sx={{ width: "100%", height: "100%", objectFit: "cover" }} />
        : typeIcon(item.content_type)}
    </Box>
  );
}

/** 文本类附件预览：鉴权取回后以纯文本展示。 */
function TextPreview({ item }: { item: AttachmentItem }) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const read = async () => {
      try {
        const b = item.file ?? (await api.attachments.blob(item.id));
        const t = await b.text();
        if (!cancelled) setText(t);
      } catch (e: any) {
        if (!cancelled) setErr(String(e));
      }
    };
    void read();
    return () => { cancelled = true; };
  }, [item]);
  if (err) return <Typography color="error">{err}</Typography>;
  if (text === null) return <CircularProgress size={24} />;
  return (
    <Box component="pre" sx={{
      m: 0, p: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word",
      fontFamily: "monospace", fontSize: 13, maxHeight: "70vh", overflow: "auto",
    }}>{text}</Box>
  );
}

/** 点击附件后的应用内预览弹窗：图片放大 / 视频内嵌 / pdf iframe / txt 文本。 */
export function AttachmentPreviewDialog(
  { item, open, onClose }: { item: AttachmentItem | null; open: boolean; onClose: () => void },
) {
  const ct = item?.content_type ?? "";
  const needsMedia = open && !!item && (isImage(ct) || isVideo(ct) || isPdf(ct));
  const mediaUrl = useAttachmentUrl(needsMedia ? item : null, needsMedia);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg"
      slotProps={{ paper: { sx: { width: isText(ct) ? 720 : "auto", maxWidth: "90vw" } } }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, px: 2, py: 1 }}>
        <Typography noWrap sx={{ flex: 1, fontWeight: 600 }}>{item?.filename}</Typography>
        <IconButton size="small" onClick={onClose} aria-label="关闭预览"><CloseIcon /></IconButton>
      </Box>
      <DialogContent dividers sx={{ p: isText(ct) ? 0 : 2 }}>
        {!item ? null
          : isImage(ct) ? (
            mediaUrl
              ? <Box component="img" src={mediaUrl} alt={item.filename}
                  sx={{ maxWidth: "100%", maxHeight: "75vh", display: "block", mx: "auto" }} />
              : <CircularProgress />
          ) : isVideo(ct) ? (
            mediaUrl
              ? <Box component="video" src={mediaUrl} controls
                  sx={{ maxWidth: "100%", maxHeight: "75vh", display: "block", mx: "auto" }} />
              : <CircularProgress />
          ) : isPdf(ct) ? (
            mediaUrl
              ? <Box component="iframe" src={mediaUrl} title={item.filename}
                  sx={{ width: "80vw", height: "75vh", border: 0 }} />
              : <CircularProgress />
          ) : isText(ct) ? (
            <TextPreview item={item} />
          ) : (
            <Typography color="text.secondary">
              该类型（{ct || "未知"}）暂不支持预览，可让助手在沙箱内处理该文件。
            </Typography>
          )}
      </DialogContent>
    </Dialog>
  );
}

/** 附件芯片列表：图片显缩略图，其它显类型图标；可删除；点击开预览弹窗。 */
export function AttachmentChips(
  { items, onDelete }: { items: AttachmentItem[]; onDelete?: (item: AttachmentItem) => void },
) {
  const [preview, setPreview] = useState<AttachmentItem | null>(null);
  if (items.length === 0) return null;
  return (
    <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}>
      {items.map((item) => {
        const uploading = item.status === "uploading";
        const errored = item.status === "error";
        return (
          <Chip
            key={item.id}
            variant="outlined"
            icon={uploading ? <CircularProgress size={14} sx={{ ml: 0.5 }} />
              : isImage(item.content_type) ? undefined : typeIcon(item.content_type)}
            avatar={!uploading && isImage(item.content_type)
              ? <Box sx={{ display: "flex" }}><Thumb item={item} /></Box> : undefined}
            label={
              <Box component="span" sx={{ display: "inline-flex", gap: 0.5, alignItems: "baseline" }}>
                <Box component="span" sx={{ maxWidth: 160, overflow: "hidden",
                  textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{item.filename}</Box>
                <Typography component="span" variant="caption" color="text.secondary">
                  {humanSize(item.size)}
                </Typography>
              </Box>
            }
            onClick={uploading || errored ? undefined : () => setPreview(item)}
            onDelete={onDelete ? () => onDelete(item) : undefined}
            sx={{
              maxWidth: 260,
              ...(errored && { borderColor: "error.main", color: "error.main" }),
              ...(!uploading && !errored && { cursor: "pointer" }),
            }}
          />
        );
      })}
      <AttachmentPreviewDialog item={preview} open={preview !== null}
        onClose={() => setPreview(null)} />
    </Box>
  );
}
