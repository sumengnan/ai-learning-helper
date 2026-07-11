// 知识库片段：分类配色 + 日期格式化，卡片与详情页共用以保持一致
import type { ChipProps } from "@mui/material";

export function categoryColor(category: string): ChipProps["color"] {
  switch (category) {
    case "PDF": return "error";
    case "Word": return "info";
    case "文本": return "success";
    case "Markdown": return "secondary";
    default: return "default";
  }
}

export function fmtDate(iso: string): string {
  return iso ? iso.slice(0, 10) : "";
}
