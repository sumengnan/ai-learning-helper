import { useLayoutEffect, useRef, useState } from "react";
import { Typography, Tooltip } from "@mui/material";
import type { SxProps, Theme } from "@mui/material/styles";

// 单行文本：撑满可用宽度到最右侧才截断（…）。仅在确实被截断时，hover 显示完整内容；
// 未截断则不挂 Tooltip。依赖父容器为 flex 且允许收缩（minWidth:0）。
// maxChars：额外的硬字数上限——即使宽度足够，超出也截断为「前 N 字…」，避免最后一步预览过长。
export function EllipsisText({ text, sx, maxChars, variant = "caption", color = "text.secondary" }: {
  text: string; sx?: SxProps<Theme>; maxChars?: number;
  // 字号/颜色可覆盖，供任务步骤标题、会话名等非「淡灰小字」场景复用同一套「截断才 hover」逻辑
  variant?: "caption" | "body2" | "body1"; color?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [truncated, setTruncated] = useState(false);
  const clamped = maxChars != null && text.length > maxChars;
  const display = clamped ? text.slice(0, maxChars) + "…" : text;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setTruncated(el.scrollWidth > el.clientWidth + 1);
    measure();
    // 容器宽度变化（窗口缩放 / 折叠块展开）时重新判断是否截断
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text]);

  const node = (
    <Typography
      ref={ref}
      variant={variant}
      color={color}
      noWrap
      sx={{ minWidth: 0, flex: 1, ...sx }}
    >
      {display}
    </Typography>
  );

  return truncated || clamped ? <Tooltip title={text} placement="top">{node}</Tooltip> : node;
}
