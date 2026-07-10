import { memo } from "react";
import { Box } from "@mui/material";
import type { SxProps, Theme } from "@mui/material/styles";
import { motion } from "framer-motion";
import { springSoft } from "./motion";

// 单个数位：0–9 纵向排列成一列，按目标数字上滚，露出对应一格
function Digit({ value }: { value: number }) {
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        flexDirection: "column",
        height: "1em",
        lineHeight: 1,
        overflow: "hidden",
        verticalAlign: "bottom",
      }}
    >
      <motion.span
        style={{ display: "flex", flexDirection: "column" }}
        animate={{ y: `${-value * 10}%` }}
        transition={springSoft}
      >
        {Array.from({ length: 10 }, (_, n) => (
          <Box
            key={n}
            component="span"
            sx={{ height: "1em", display: "flex", alignItems: "center", justifyContent: "center" }}
          >
            {n}
          </Box>
        ))}
      </motion.span>
    </Box>
  );
}

// 里程表式滚动数字：数字位平滑上滚，非数字字符（千分位逗号等）静态渲染。
// token 用量等数值变化时用它替代瞬间跳变。
export const RollingNumber = memo(function RollingNumber(
  { value, sx }: { value: number; sx?: SxProps<Theme> },
) {
  const text = new Intl.NumberFormat("en-US").format(Math.max(0, Math.round(value)));
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "flex-end",
        fontVariantNumeric: "tabular-nums",
        ...sx,
      }}
    >
      {text.split("").map((ch, i) =>
        ch >= "0" && ch <= "9"
          ? <Digit key={i} value={Number(ch)} />
          : <Box component="span" key={i}>{ch}</Box>,
      )}
    </Box>
  );
});
