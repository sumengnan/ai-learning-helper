// web/src/theme.ts
import { createTheme, type Theme } from "@mui/material/styles";

const FONT_FAMILY = [
  "-apple-system",
  "BlinkMacSystemFont",
  '"PingFang SC"',
  '"Microsoft YaHei"',
  '"Segoe UI"',
  "system-ui",
  "sans-serif",
].join(",");

/** 整体缩放系数（原先是 body{zoom:0.93}，改由主题实现——zoom 会让 portal 到 body 的
 *  MUI 浮层坐标系与 getBoundingClientRect 打架，菜单/下拉统一往左上偏）。
 *  字号与间距都按这个系数缩，MUI 组件的内在尺寸（按钮高度、输入框高度等）由这两者算出，
 *  因此跟着一起缩。项目自己写死的 px 尺寸用 scalePx() 手动跟上。 */
export const UI_SCALE = 0.93;

/** 把写死的 px 尺寸按整体缩放系数换算（侧边栏宽度这类布局尺寸用）。 */
export const scalePx = (n: number) => Math.round(n * UI_SCALE);

export function buildTheme(mode: "light" | "dark"): Theme {
  return createTheme({
    // 8 是 MUI 默认间距基数；所有 sx 的 p/m/gap/spacing 都乘它，一处改全局跟着缩
    spacing: 8 * UI_SCALE,
    palette: {
      mode,
      primary: { main: "#4f46e5" },
      ...(mode === "light"
        ? { background: { default: "#f6f7f9", paper: "#ffffff" } }
        : { background: { default: "#121317", paper: "#1c1e24" } }),
    },
    shape: { borderRadius: 8 },
    typography: {
      fontFamily: FONT_FAMILY,
      // 14 是 MUI 默认基准字号，各 variant 按比例从它算出，缩它即整体缩字
      fontSize: 14 * UI_SCALE,
      button: { textTransform: "none", fontWeight: 600 },
    },
    components: {
      MuiButton: { defaultProps: { disableElevation: true } },
    },
  });
}
