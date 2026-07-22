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

// 检索相关度配色：只看展示的百分数（不再区分 rerank/rank 量纲）。
//  - <50%：视为不够相关，一律灰。
//  - 50%–100%：连续过渡，越相关越绿越突出。色相从琥珀黄（50%，及格线）平滑推到
//    鲜绿（100%，最相关）；明色模式越相关越深越饱和（白底可读），暗色模式越相关越亮
//    （黑底可读）。用 HSL 连续插值而非分档，"过渡"更顺、且改配色只需动这几个数。
// 分界含 50：恰好 50% 上色，49% 及以下灰。
export function relevanceColor(
  relevance: number,
  mode: "light" | "dark" = "light",
): string {
  const dark = mode === "dark";
  if (relevance < 50) return dark ? "#bdbdbd" : "#9e9e9e";     // 灰：不够相关
  const t = Math.min(1, (relevance - 50) / 50);               // 0(50%)→1(100%)，>100% 封顶
  const hue = Math.round(45 + t * 95);                        // 45°琥珀黄 → 140°鲜绿
  const sat = dark ? Math.round(60 + t * 8) : Math.round(72 + t * 8);
  const light = dark ? Math.round(56 + t * 10) : Math.round(46 - t * 9);
  return `hsl(${hue}, ${sat}%, ${light}%)`;
}
