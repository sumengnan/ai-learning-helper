import { useLayoutEffect, useRef, useState } from "react";
import { Typography, Tooltip } from "@mui/material";
import type { SxProps, Theme } from "@mui/material/styles";

// 单行文本：撑满可用宽度到最右侧才截断（…）。仅在确实被截断时，hover 显示完整内容；
// 未截断则不挂 Tooltip。依赖父容器为 flex 且允许收缩（minWidth:0）。
export function EllipsisText({ text, sx }: { text: string; sx?: SxProps<Theme> }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [truncated, setTruncated] = useState(false);

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
      variant="caption"
      color="text.secondary"
      noWrap
      sx={{ minWidth: 0, flex: 1, ...sx }}
    >
      {text}
    </Typography>
  );

  return truncated ? <Tooltip title={text} placement="top">{node}</Tooltip> : node;
}
