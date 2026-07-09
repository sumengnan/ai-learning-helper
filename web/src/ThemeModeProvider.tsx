// web/src/ThemeModeProvider.tsx
import {
  createContext, useContext, useMemo, useState, type ReactNode,
} from "react";
import { ThemeProvider, CssBaseline } from "@mui/material";
import { buildTheme } from "./theme";

type Mode = "light" | "dark";
const STORAGE_KEY = "color-mode";

function initialMode(): Mode {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "light" || saved === "dark") return saved;
  // jsdom 下 matchMedia 可能不存在，做防御式判断
  if (typeof window.matchMedia === "function") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return "light";
}

const ModeContext = createContext<{ mode: Mode; toggleMode: () => void }>({
  mode: "light",
  toggleMode: () => {},
});

export function useColorMode() {
  return useContext(ModeContext);
}

export function ThemeModeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const toggleMode = () =>
    setMode((m) => {
      const next: Mode = m === "light" ? "dark" : "light";
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  const theme = useMemo(() => buildTheme(mode), [mode]);
  return (
    <ModeContext.Provider value={{ mode, toggleMode }}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </ModeContext.Provider>
  );
}
