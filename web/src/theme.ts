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

export function buildTheme(mode: "light" | "dark"): Theme {
  return createTheme({
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
      button: { textTransform: "none", fontWeight: 600 },
    },
    components: {
      MuiButton: { defaultProps: { disableElevation: true } },
    },
  });
}
