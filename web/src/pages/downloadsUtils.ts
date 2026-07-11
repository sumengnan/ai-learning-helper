// 下载文件：大小格式化 + 按 MIME 类型派生标签/配色/图标，供卡片统一呈现
import type { ChipProps } from "@mui/material";
import type { SvgIconComponent } from "@mui/icons-material";
import ImageIcon from "@mui/icons-material/Image";
import PictureAsPdfIcon from "@mui/icons-material/PictureAsPdf";
import AudioFileIcon from "@mui/icons-material/AudioFile";
import VideoFileIcon from "@mui/icons-material/VideoFile";
import DescriptionIcon from "@mui/icons-material/Description";
import FolderZipIcon from "@mui/icons-material/FolderZip";
import InsertDriveFileIcon from "@mui/icons-material/InsertDriveFile";

export type PreviewKind = "image" | "text" | "pdf" | "none";

export function previewKind(contentType: string): PreviewKind {
  const c = (contentType || "").toLowerCase();
  if (c.startsWith("image/")) return "image";
  if (c.includes("pdf")) return "pdf";
  if (c.startsWith("text/") || c.includes("json") || c.includes("xml")
      || c.includes("markdown") || c.includes("csv") || c.includes("javascript"))
    return "text";
  return "none";
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export type FileMeta = { label: string; color: ChipProps["color"]; Icon: SvgIconComponent };

export function fileMeta(contentType: string): FileMeta {
  const ct = (contentType || "").toLowerCase();
  if (ct.startsWith("image/")) return { label: "图片", color: "success", Icon: ImageIcon };
  if (ct.startsWith("audio/")) return { label: "音频", color: "secondary", Icon: AudioFileIcon };
  if (ct.startsWith("video/")) return { label: "视频", color: "secondary", Icon: VideoFileIcon };
  if (ct.includes("pdf")) return { label: "PDF", color: "error", Icon: PictureAsPdfIcon };
  if (ct.includes("zip") || ct.includes("compressed") || ct.includes("tar") || ct.includes("gzip"))
    return { label: "压缩包", color: "warning", Icon: FolderZipIcon };
  if (ct.startsWith("text/") || ct.includes("markdown") || ct.includes("json") || ct.includes("xml"))
    return { label: "文本", color: "info", Icon: DescriptionIcon };
  return { label: "文件", color: "default", Icon: InsertDriveFileIcon };
}
