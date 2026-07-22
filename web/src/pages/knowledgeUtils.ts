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

// 检索相关度配色：越相关颜色越突出。区间下含上不含（如 [95,100) 浅绿）。
// 暗色模式下把各档提亮、并把「黑」翻成近白，保证深色背景上清晰可读，
// 同时维持「越相关越突出」的明度层次。
//
// kind 决定量纲与阈值：
//  - "rank"（默认，向后兼容）：老的 minmax 相对排名分，量纲集中在 80-100 高位。
//  - "rerank"：精排（qwen3-rerank）绝对分转成的百分比，量纲 [0,100] 但相关性
//    集中在 ~25-55；min_score=0.35（即 35%）是「相关/无关」分界，故阈值围绕它展开，
//    保证 35-50 这段有明显区分而不是一片灰。
export function relevanceColor(
  relevance: number,
  mode: "light" | "dark" = "light",
  kind: "rerank" | "rank" = "rank",
): string {
  const dark = mode === "dark";
  if (kind === "rerank") {
    if (relevance >= 50) return dark ? "#a5d6a7" : "#1b5e20";  // 深绿：很相关
    if (relevance >= 40) return dark ? "#66bb6a" : "#2e7d32";  // 绿：相关
    if (relevance >= 35) return dark ? "#64b5f6" : "#1976d2";  // 蓝：勉强过线
    if (relevance >= 25) return dark ? "#ffb74d" : "#c77700";  // 灰黄：弱相关
    return dark ? "#bdbdbd" : "#9e9e9e";                       // 灰：基本无关
  }
  if (relevance >= 100) return dark ? "#a5d6a7" : "#1b5e20";  // 深绿
  if (relevance >= 95) return dark ? "#66bb6a" : "#66bb6a";   // 浅绿
  if (relevance >= 90) return dark ? "#ce93d8" : "#9c27b0";   // 紫色
  if (relevance >= 85) return dark ? "#64b5f6" : "#1976d2";   // 蓝色
  if (relevance >= 80) return dark ? "#f5f5f5" : "#212121";   // 黑 ↔ 近白
  return dark ? "#bdbdbd" : "#9e9e9e";                        // 灰色
}
