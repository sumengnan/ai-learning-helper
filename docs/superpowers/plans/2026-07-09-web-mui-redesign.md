# Web 前端 MUI 改造 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 `web/` 前端从裸 Tailwind 全面迁移到 MUI（Material UI），建立现代简洁生产力风的亮/暗双主题设计系统，重做侧边抽屉导航与全部 6 个页面，且保证现有 vitest 测试继续通过。

**架构：** 彻底移除 Tailwind（依赖 + 配置 + `@tailwind` 指令），引入 `@mui/material` + emotion 引擎 + `@mui/icons-material`。新增 `theme.ts`（亮/暗主题工厂）与 `ThemeModeProvider`（明暗切换 + 持久化）包裹全应用；抽出 `AppShell` 承载 220px `Drawer` 导航；逐页把 JSX 里的 `className` 替换为 MUI 组件 + `sx`。不动后端 `app/`、`api/client.ts`、`types.ts`。

**技术栈：** React 18 + TypeScript + Vite + vitest；MUI 9.2 + @emotion/react 11 + @emotion/styled 11 + @mui/icons-material 9.2。

**规格：** `docs/superpowers/specs/2026-07-09-web-mui-redesign-design.md`

**分支：** `feat/web-mui-redesign`（基于 `origin/main`），所有命令在 `web/` 目录下执行。

---

## 文件结构

**新建：**
- `web/src/theme.ts` — `buildTheme(mode)` 工厂，产出亮/暗两套 MUI 主题（唯一职责：设计令牌）
- `web/src/ThemeModeProvider.tsx` — 明暗模式 context + Provider（唯一职责：模式状态/持久化 + 包 ThemeProvider/CssBaseline）
- `web/src/ThemeModeProvider.test.tsx` — 明暗切换逻辑单测
- `web/src/components/AppShell.tsx` — 抽屉导航外壳（唯一职责：布局与导航）

**修改：**
- `web/package.json` — 增删依赖
- `web/src/index.css` — 移除 `@tailwind` 指令
- `web/src/main.tsx` — 包裹 `ThemeModeProvider`
- `web/src/App.tsx` — 改用 `AppShell`
- `web/src/pages/ChatPage.tsx`、`web/src/components/ConversationList.tsx`、`web/src/components/ChatView.tsx`、`web/src/components/AgentProgress.tsx`
- `web/src/pages/KnowledgeView.tsx`、`web/src/pages/QuestionBankView.tsx`、`web/src/pages/ExamView.tsx`、`web/src/pages/WrongAnswersView.tsx`、`web/src/pages/DownloadsView.tsx`

**删除：**
- `web/tailwind.config.js`、`web/postcss.config.js`

**不改动：** `web/src/api/client.ts`、`web/src/api/sse.test.ts`、`web/src/types.ts`、`web/vite.config.ts`、`web/tsconfig.json`、后端 `app/`。

---

## 硬约束（贯穿所有任务）

1. **保留文本节点**：现有 vitest 只按文本/placeholder 断言。必须原样保留 `问点什么…`、`发送`、`bio.txt`/`3 块`（KnowledgeView 由数据驱动）、`光合作用…`（QuestionBankView 由数据驱动）、`笔记.md`（DownloadsView 由数据驱动）等渲染文本。
2. **Chip 不可直接嵌在 `<Typography>`（默认渲染 `<p>`）里**，否则 `<div>` 套 `<p>` 触发 DOM 嵌套告警——凡在文本行内放 Chip，给该 Typography 加 `component="div"`。
3. **验证命令**（每个迁移任务结束都要跑）：
   - 类型检查：`npx tsc --noEmit`
   - 测试：`npx vitest run`
   - 两者都必须全绿再 commit。
4. commit 信息用中文 Conventional Commits，**不出现任何 AI/Claude 署名**。

---

## 任务 1：依赖切换 —— 卸 Tailwind、装 MUI

**文件：**
- 修改：`web/package.json`
- 删除：`web/tailwind.config.js`、`web/postcss.config.js`
- 修改：`web/src/index.css`

- [ ] **步骤 1：安装 MUI 依赖**

在 `web/` 下运行：

```bash
npm install @mui/material@^9.2.0 @emotion/react@^11.14.0 @emotion/styled@^11.14.1 @mui/icons-material@^9.2.0
```

预期：安装成功。若出现 `@mui/material-pigment-css` 的 peer 告警，**忽略**（我们用默认 emotion 引擎，不需要 pigment）。

- [ ] **步骤 2：卸载 Tailwind 依赖**

```bash
npm uninstall tailwindcss postcss autoprefixer
```

- [ ] **步骤 3：删除 Tailwind 配置文件**

```bash
git rm web/tailwind.config.js web/postcss.config.js
```

- [ ] **步骤 4：清理 index.css**

把 `web/src/index.css` 全文替换为（去掉 `@tailwind` 三行，保留基础重置）：

```css
html, body, #root { height: 100%; margin: 0; }
```

- [ ] **步骤 5：验证构建仍通过**

运行：`npx tsc --noEmit && npx vitest run`
预期：类型检查通过；所有现有测试通过（此时页面还带着无效的 Tailwind class，但 JSX 合法、文本不变，测试不受影响）。

- [ ] **步骤 6：Commit**

```bash
git add web/package.json web/package-lock.json web/src/index.css
git commit -m "chore(web): 移除 Tailwind 依赖与配置，引入 MUI"
```

---

## 任务 2：主题系统 + 明暗切换

**文件：**
- 创建：`web/src/theme.ts`
- 创建：`web/src/ThemeModeProvider.tsx`
- 测试：`web/src/ThemeModeProvider.test.tsx`
- 修改：`web/src/main.tsx`

- [ ] **步骤 1：编写 `theme.ts`**

创建 `web/src/theme.ts`：

```ts
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
```

- [ ] **步骤 2：编写 `ThemeModeProvider.tsx`**

创建 `web/src/ThemeModeProvider.tsx`：

```tsx
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
```

- [ ] **步骤 3：编写失败的测试**

创建 `web/src/ThemeModeProvider.test.tsx`：

```tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeModeProvider, useColorMode } from "./ThemeModeProvider";

function Probe() {
  const { mode, toggleMode } = useColorMode();
  return (
    <div>
      <span data-testid="mode">{mode}</span>
      <button onClick={toggleMode}>toggle</button>
    </div>
  );
}

describe("ThemeModeProvider", () => {
  beforeEach(() => localStorage.clear());

  it("点击切换在 light/dark 间翻转并写入 localStorage", () => {
    render(<ThemeModeProvider><Probe /></ThemeModeProvider>);
    const before = screen.getByTestId("mode").textContent;
    fireEvent.click(screen.getByText("toggle"));
    const after = screen.getByTestId("mode").textContent;
    expect(after).not.toBe(before);
    expect(localStorage.getItem("color-mode")).toBe(after);
  });
});
```

- [ ] **步骤 4：运行测试验证通过**

运行：`npx vitest run src/ThemeModeProvider.test.tsx`
预期：PASS（切换后 mode 翻转，且 `localStorage["color-mode"]` 等于新值）。

- [ ] **步骤 5：接入 `main.tsx`**

把 `web/src/main.tsx` 全文替换为：

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ThemeModeProvider } from "./ThemeModeProvider";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeModeProvider>
      <App />
    </ThemeModeProvider>
  </React.StrictMode>,
);
```

- [ ] **步骤 6：验证 + Commit**

运行：`npx tsc --noEmit && npx vitest run`
预期：全绿。

```bash
git add web/src/theme.ts web/src/ThemeModeProvider.tsx web/src/ThemeModeProvider.test.tsx web/src/main.tsx
git commit -m "feat(web): 新增 MUI 亮/暗主题与明暗切换 Provider"
```

---

## 任务 3：AppShell 抽屉导航 + App.tsx

**文件：**
- 创建：`web/src/components/AppShell.tsx`
- 修改：`web/src/App.tsx`

- [ ] **步骤 1：编写 `AppShell.tsx`**

创建 `web/src/components/AppShell.tsx`：

```tsx
// web/src/components/AppShell.tsx
import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box, Drawer, List, ListItemButton, ListItemIcon, ListItemText,
  Toolbar, Typography, IconButton, Tooltip, Divider,
} from "@mui/material";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import MenuBookIcon from "@mui/icons-material/MenuBook";
import QuizIcon from "@mui/icons-material/Quiz";
import AssignmentIcon from "@mui/icons-material/Assignment";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import DownloadIcon from "@mui/icons-material/Download";
import Brightness4Icon from "@mui/icons-material/Brightness4";
import Brightness7Icon from "@mui/icons-material/Brightness7";
import { useColorMode } from "../ThemeModeProvider";

const WIDTH = 220;

const NAV: { to: string; label: string; icon: ReactNode }[] = [
  { to: "/", label: "聊天", icon: <ChatBubbleOutlineIcon /> },
  { to: "/knowledge", label: "知识库", icon: <MenuBookIcon /> },
  { to: "/questions", label: "题库", icon: <QuizIcon /> },
  { to: "/exam", label: "考试", icon: <AssignmentIcon /> },
  { to: "/wrong", label: "错题集", icon: <ErrorOutlineIcon /> },
  { to: "/downloads", label: "下载", icon: <DownloadIcon /> },
];

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { mode, toggleMode } = useColorMode();
  const isActive = (to: string) =>
    to === "/" ? location.pathname === "/" : location.pathname.startsWith(to);

  return (
    <Box sx={{ display: "flex", height: "100%" }}>
      <Drawer
        variant="permanent"
        sx={{
          width: WIDTH,
          flexShrink: 0,
          "& .MuiDrawer-paper": { width: WIDTH, boxSizing: "border-box" },
        }}
      >
        <Toolbar sx={{ px: 2 }}>
          <Typography variant="h6" noWrap fontWeight={700}>
            AI 学习助手
          </Typography>
        </Toolbar>
        <Divider />
        <List sx={{ flex: 1 }}>
          {NAV.map((n) => (
            <ListItemButton
              key={n.to}
              selected={isActive(n.to)}
              onClick={() => navigate(n.to)}
            >
              <ListItemIcon sx={{ minWidth: 40 }}>{n.icon}</ListItemIcon>
              <ListItemText primary={n.label} />
            </ListItemButton>
          ))}
        </List>
        <Divider />
        <Box sx={{ p: 1, display: "flex", justifyContent: "flex-end" }}>
          <Tooltip title={mode === "light" ? "切换到暗色" : "切换到亮色"}>
            <IconButton onClick={toggleMode} aria-label="切换明暗主题">
              {mode === "light" ? <Brightness4Icon /> : <Brightness7Icon />}
            </IconButton>
          </Tooltip>
        </Box>
      </Drawer>
      <Box component="main" sx={{ flex: 1, minWidth: 0, height: "100%", overflow: "hidden" }}>
        {children}
      </Box>
    </Box>
  );
}
```

- [ ] **步骤 2：改造 `App.tsx`**

把 `web/src/App.tsx` 全文替换为：

```tsx
// web/src/App.tsx
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ChatPage } from "./pages/ChatPage";
import { KnowledgeView } from "./pages/KnowledgeView";
import QuestionBankView from "./pages/QuestionBankView";
import ExamView from "./pages/ExamView";
import WrongAnswersView from "./pages/WrongAnswersView";
import DownloadsView from "./pages/DownloadsView";

export default function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/knowledge" element={<KnowledgeView />} />
          <Route path="/questions" element={<QuestionBankView />} />
          <Route path="/exam" element={<ExamView />} />
          <Route path="/wrong" element={<WrongAnswersView />} />
          <Route path="/downloads" element={<DownloadsView />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
```

- [ ] **步骤 3：验证 + Commit**

运行：`npx tsc --noEmit && npx vitest run`
预期：全绿（页面测试单独渲染组件，不经过 App/AppShell，因此不受影响）。

```bash
git add web/src/components/AppShell.tsx web/src/App.tsx
git commit -m "feat(web): 用 MUI Drawer 重做侧边导航外壳"
```

---

## 任务 4：聊天页迁移（ChatPage + ConversationList + ChatView + AgentProgress）

**文件：**
- 修改：`web/src/pages/ChatPage.tsx`
- 修改：`web/src/components/ConversationList.tsx`
- 修改：`web/src/components/ChatView.tsx`
- 修改：`web/src/components/AgentProgress.tsx`
- 相关测试：`web/src/components/ChatView.test.tsx`（不改，作为回归守卫）

- [ ] **步骤 1：改写 `ConversationList.tsx`**

全文替换为：

```tsx
import type { Conversation } from "../types";
import { Box, Button, List, ListItemButton, ListItemText, IconButton } from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import CloseIcon from "@mui/icons-material/Close";

export function ConversationList({ items, activeId, onSelect, onNew, onDelete }: {
  items: Conversation[]; activeId: string | null;
  onSelect: (id: string) => void; onNew: () => void; onDelete: (id: string) => void;
}) {
  return (
    <Box sx={{
      width: 240, borderRight: 1, borderColor: "divider",
      display: "flex", flexDirection: "column", height: "100%",
    }}>
      <Button startIcon={<AddIcon />} variant="contained" onClick={onNew} sx={{ m: 1 }}>
        新对话
      </Button>
      <List sx={{ flex: 1, overflowY: "auto", py: 0 }}>
        {items.map((c) => (
          <ListItemButton
            key={c.id}
            selected={c.id === activeId}
            onClick={() => onSelect(c.id)}
            sx={{ "&:hover .conv-del": { opacity: 1 } }}
          >
            <ListItemText primary={c.title} slotProps={{ primary: { noWrap: true } }} />
            <IconButton
              size="small" className="conv-del" sx={{ opacity: 0 }}
              onClick={(e) => { e.stopPropagation(); onDelete(c.id); }}
              aria-label="删除对话"
            >
              <CloseIcon fontSize="small" />
            </IconButton>
          </ListItemButton>
        ))}
      </List>
    </Box>
  );
}
```

- [ ] **步骤 2：改写 `AgentProgress.tsx`**

全文替换为：

```tsx
import type { ChatMessage } from "../types";
import { Accordion, AccordionSummary, AccordionDetails, Typography, Box } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";

export function AgentProgress({ steps }: { steps: NonNullable<ChatMessage["steps"]> }) {
  if (!steps.length) return null;
  return (
    <Box sx={{ mt: 1 }}>
      {steps.map((s, i) => (
        <Accordion
          key={i} disableGutters elevation={0}
          sx={{ bgcolor: "transparent", "&:before": { display: "none" } }}
        >
          <AccordionSummary expandIcon={<ExpandMoreIcon fontSize="small" />} sx={{ minHeight: 0, px: 0 }}>
            <Typography variant="caption" color={s.isError ? "error" : "text.secondary"}>
              {s.result === undefined ? "调用工具" : "工具完成"}：{s.tool}
            </Typography>
          </AccordionSummary>
          <AccordionDetails sx={{ px: 0, pt: 0 }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", wordBreak: "break-all" }}>
              参数：{JSON.stringify(s.args)}
            </Typography>
            {s.result !== undefined && (
              <Typography variant="caption" sx={{ display: "block", wordBreak: "break-all" }}>
                结果：{s.result}
              </Typography>
            )}
          </AccordionDetails>
        </Accordion>
      ))}
    </Box>
  );
}
```

- [ ] **步骤 3：改写 `ChatView.tsx`**

全文替换为（**保留 placeholder `问点什么…` 与按钮文案 `发送`；助手气泡文本节点保持为纯 `content`，确保 `getByText("你好")` 命中**）：

```tsx
import { useEffect, useRef, useState } from "react";
import { Box, Paper, TextField, Button, Typography } from "@mui/material";
import type { ChatMessage } from "../types";
import { streamChat } from "../api/client";
import { AgentProgress } from "./AgentProgress";

export function ChatView({ conversationId, initial }: { conversationId: string; initial: ChatMessage[] }) {
  const [messages, setMessages] = useState<ChatMessage[]>(initial);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // 卸载（含 App 用 key={activeId} 切换对话触发 remount）时取消在途流
  useEffect(() => () => abortRef.current?.abort(), []);

  const upd = (fn: (a: ChatMessage) => void) =>
    setMessages((m) => {
      const copy = [...m];
      const last = { ...copy[copy.length - 1] };
      if (last.steps) last.steps = last.steps.map((s) => ({ ...s }));
      fn(last);
      copy[copy.length - 1] = last;
      return copy;
    });

  async function send() {
    if (!input.trim() || busy) return;
    const userMsg: ChatMessage = { role: "user", content: input };
    const assistant: ChatMessage = { role: "assistant", content: "", steps: [] };
    setMessages((m) => [...m, userMsg, assistant]);
    const msg = input; setInput(""); setBusy(true);
    const controller = new AbortController();
    abortRef.current = controller;
    const onEvent = (e: any) => {
      if (e.type === "TextDelta") upd((a) => { a.content += e.data.text; });
      else if (e.type === "ToolStarted") upd((a) => a.steps!.push({ tool: e.data.tool_call.name, args: e.data.tool_call.arguments }));
      else if (e.type === "ToolFinished") upd((a) => {
        const s = a.steps![a.steps!.length - 1];
        if (s) { s.result = e.data.result.content; s.isError = e.data.result.is_error; }
      });
      else if (e.type === "ModelUsage") upd((a) => { a.usage = { tokens: e.data.usage.total, cost: e.data.cost_usd }; });
      else if (e.type === "RunError") upd((a) => { a.content += `\n[出错] ${e.data.error}`; });
    };
    try {
      await streamChat(conversationId, msg, onEvent, controller.signal);
    } catch (err: any) {
      if (err?.name !== "AbortError") upd((a) => { a.content += `\n[连接失败] ${err}`; });
    } finally { setBusy(false); }
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Box sx={{ flex: 1, overflowY: "auto", p: 2, display: "flex", flexDirection: "column", gap: 2 }}>
        {messages.map((m, i) => (
          <Box key={i} sx={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
            <Paper
              elevation={0}
              sx={{
                maxWidth: "80%", px: 1.5, py: 1, borderRadius: 2,
                bgcolor: m.role === "user" ? "primary.main" : "action.hover",
                color: m.role === "user" ? "primary.contrastText" : "text.primary",
              }}
            >
              <Typography component="div" sx={{ whiteSpace: "pre-wrap" }}>
                {m.content || (m.role === "assistant" ? "…" : "")}
              </Typography>
              {m.role === "assistant" && m.steps && <AgentProgress steps={m.steps} />}
              {m.usage && (
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                  tokens {m.usage.tokens}{m.usage.cost != null ? ` · $${m.usage.cost.toFixed(4)}` : ""}
                </Typography>
              )}
            </Paper>
          </Box>
        ))}
      </Box>
      <Box sx={{ p: 1.5, borderTop: 1, borderColor: "divider", display: "flex", gap: 1 }}>
        <TextField
          fullWidth size="small" value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          placeholder="问点什么…"
        />
        <Button variant="contained" onClick={send} disabled={busy}>发送</Button>
      </Box>
    </Box>
  );
}
```

- [ ] **步骤 4：改写 `ChatPage.tsx`**

全文替换为：

```tsx
// web/src/pages/ChatPage.tsx
import { useEffect, useState } from "react";
import { Box } from "@mui/material";
import type { Conversation, ChatMessage } from "../types";
import { api } from "../api/client";
import { ConversationList } from "../components/ConversationList";
import { ChatView } from "../components/ChatView";

export function ChatPage() {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [initial, setInitial] = useState<ChatMessage[]>([]);
  const refresh = () => api.list().then(setConvs);
  useEffect(() => { refresh(); }, []);
  async function select(id: string) {
    setActiveId(id);
    const msgs = await api.messages(id);
    setInitial(msgs.map((m) => ({ role: m.role as "user" | "assistant", content: m.content })));
  }
  async function newConv() { const { id } = await api.create(); await refresh(); await select(id); }
  async function del(id: string) {
    await api.remove(id); await refresh();
    if (id === activeId) { setActiveId(null); setInitial([]); }
  }
  return (
    <Box sx={{ display: "flex", height: "100%" }}>
      <ConversationList items={convs} activeId={activeId} onSelect={select} onNew={newConv} onDelete={del} />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {activeId
          ? <ChatView key={activeId} conversationId={activeId} initial={initial} />
          : (
            <Box sx={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "text.secondary" }}>
              新建或选择一个对话开始
            </Box>
          )}
      </Box>
    </Box>
  );
}
```

- [ ] **步骤 5：验证**

运行：`npx tsc --noEmit && npx vitest run src/components/ChatView.test.tsx`
预期：类型检查通过；`ChatView` 测试 PASS（`问点什么…`、`发送` 保留，助手气泡最终文本为 `你好`，无 `你你好好`）。

- [ ] **步骤 6：全量测试 + Commit**

运行：`npx vitest run`
预期：全绿。

```bash
git add web/src/pages/ChatPage.tsx web/src/components/ConversationList.tsx web/src/components/ChatView.tsx web/src/components/AgentProgress.tsx
git commit -m "feat(web): 聊天页迁移到 MUI（会话列表/气泡/输入/工具过程）"
```

---

## 任务 5：知识库页迁移

**文件：**
- 修改：`web/src/pages/KnowledgeView.tsx`
- 相关测试：`web/src/pages/KnowledgeView.test.tsx`（不改，回归守卫）

- [ ] **步骤 1：改写 `KnowledgeView.tsx`**

全文替换为（**保留 `filename` 与 `· N 块` 文本，测试断言 `bio.txt`、`3 块`**）：

```tsx
// web/src/pages/KnowledgeView.tsx
import { useEffect, useRef, useState } from "react";
import {
  Box, Typography, Button, List, ListItem, ListItemText, IconButton,
  CircularProgress, Alert,
} from "@mui/material";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import DeleteIcon from "@mui/icons-material/Delete";
import { api } from "../api/client";

type Doc = { id: string; filename: string; num_chunks: number; uploaded_at: string };

export function KnowledgeView() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const refresh = () => api.documents.list().then(setDocs);
  useEffect(() => { refresh(); }, []);

  async function upload(file: File) {
    setBusy(true); setError(null);
    try { await api.documents.upload(file); await refresh(); }
    catch (e: any) { setError(String(e?.message || e)); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }
  async function remove(id: string) { await api.documents.remove(id); await refresh(); }

  return (
    <Box sx={{ p: 3, maxWidth: 720 }}>
      <Typography variant="h5" fontWeight={700} gutterBottom>知识库</Typography>
      <Box sx={{ mb: 2, display: "flex", alignItems: "center", gap: 1 }}>
        <Button component="label" variant="outlined" startIcon={<UploadFileIcon />} disabled={busy}>
          上传文档
          <input
            ref={fileRef} hidden type="file" accept=".pdf,.docx,.txt,.md"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }}
          />
        </Button>
        {busy && <CircularProgress size={20} />}
      </Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {docs.length === 0 ? (
        <Typography color="text.secondary">还没有上传文档</Typography>
      ) : (
        <List sx={{ border: 1, borderColor: "divider", borderRadius: 2 }}>
          {docs.map((d) => (
            <ListItem
              key={d.id} divider
              secondaryAction={
                <IconButton edge="end" color="error" onClick={() => remove(d.id)} aria-label="删除文档">
                  <DeleteIcon />
                </IconButton>
              }
            >
              <ListItemText primary={d.filename} secondary={`· ${d.num_chunks} 块`} />
            </ListItem>
          ))}
        </List>
      )}
    </Box>
  );
}
```

- [ ] **步骤 2：验证**

运行：`npx tsc --noEmit && npx vitest run src/pages/KnowledgeView.test.tsx`
预期：PASS（`getByText(/bio\.txt/)` 命中 primary；`getByText(/3 块/)` 命中 secondary `· 3 块`）。

- [ ] **步骤 3：Commit**

```bash
git add web/src/pages/KnowledgeView.tsx
git commit -m "feat(web): 知识库页迁移到 MUI"
```

---

## 任务 6：题库页迁移

**文件：**
- 修改：`web/src/pages/QuestionBankView.tsx`
- 相关测试：`web/src/pages/QuestionBankView.test.tsx`（不改，回归守卫）

- [ ] **步骤 1：改写 `QuestionBankView.tsx`**

全文替换为（**题型改用 `ToggleButtonGroup` 多选；保留题干文本，测试断言 `光合作用在哪`**）：

```tsx
import { useEffect, useState } from "react";
import {
  Box, Typography, Card, CardContent, TextField, ToggleButton, ToggleButtonGroup,
  Button, Alert, List, ListItem, ListItemText, IconButton, Chip, Stack,
} from "@mui/material";
import DeleteIcon from "@mui/icons-material/Delete";
import { api } from "../api/client";

const TYPES: { key: string; label: string }[] = [
  { key: "single", label: "单选" },
  { key: "multiple", label: "多选" },
  { key: "truefalse", label: "判断" },
  { key: "short", label: "简答" },
];

interface Question { id: string; type: string; stem: string; source: string; }

export default function QuestionBankView() {
  const [questions, setQuestions] = useState<Question[]>([]);
  const [topic, setTopic] = useState("");
  const [count, setCount] = useState(5);
  const [types, setTypes] = useState<string[]>(["single"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = () => api.questions.list().then(setQuestions);
  useEffect(() => { refresh(); }, []);

  const generate = async () => {
    if (!topic.trim() || types.length === 0) return;
    setBusy(true); setError("");
    try {
      await api.questions.generate(topic.trim(), count, types);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "出题失败");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => { await api.questions.remove(id); await refresh(); };

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
      <Typography variant="h5" fontWeight={700}>题库</Typography>
      <Card variant="outlined">
        <CardContent>
          <Stack spacing={2}>
            <TextField
              fullWidth size="small" label="出题主题（从知识库检索）"
              value={topic} onChange={(e) => setTopic(e.target.value)}
            />
            <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
              <TextField
                type="number" size="small" label="题数" sx={{ width: 96 }}
                slotProps={{ htmlInput: { min: 1, max: 20 } }}
                value={count} onChange={(e) => setCount(Number(e.target.value))}
              />
              <ToggleButtonGroup
                size="small" value={types}
                onChange={(_, v: string[]) => setTypes(v)}
              >
                {TYPES.map((t) => (
                  <ToggleButton key={t.key} value={t.key}>{t.label}</ToggleButton>
                ))}
              </ToggleButtonGroup>
              <Button
                variant="contained" onClick={generate}
                disabled={busy || !topic.trim() || types.length === 0}
              >
                {busy ? "出题中…" : "出题"}
              </Button>
            </Stack>
            {error && <Alert severity="error">{error}</Alert>}
          </Stack>
        </CardContent>
      </Card>

      {questions.length === 0 ? (
        <Typography color="text.secondary">暂无题目，先出题吧。</Typography>
      ) : (
        <List sx={{ display: "flex", flexDirection: "column", gap: 1, py: 0 }}>
          {questions.map((q) => (
            <ListItem
              key={q.id}
              sx={{ border: 1, borderColor: "divider", borderRadius: 2 }}
              secondaryAction={
                <IconButton edge="end" color="error" onClick={() => remove(q.id)} aria-label="删除题目">
                  <DeleteIcon />
                </IconButton>
              }
            >
              <Chip size="small" label={q.type} sx={{ mr: 1 }} />
              <ListItemText
                primary={q.stem}
                secondary={q.source ? `· ${q.source}` : undefined}
              />
            </ListItem>
          ))}
        </List>
      )}
    </Box>
  );
}
```

- [ ] **步骤 2：验证**

运行：`npx tsc --noEmit && npx vitest run src/pages/QuestionBankView.test.tsx`
预期：PASS（`getByText(/光合作用在哪/)` 命中题干）。

- [ ] **步骤 3：Commit**

```bash
git add web/src/pages/QuestionBankView.tsx
git commit -m "feat(web): 题库页迁移到 MUI"
```

---

## 任务 7：考试页迁移

**文件：**
- 修改：`web/src/pages/ExamView.tsx`（无对应测试，靠类型检查 + 全量测试守卫）

- [ ] **步骤 1：改写 `ExamView.tsx`**

全文替换为（三态：配置 / 答题 / 成绩，全部 MUI；保留全部原文案）：

```tsx
import { useState } from "react";
import {
  Box, Typography, Card, CardContent, TextField, ToggleButton, ToggleButtonGroup,
  Button, Alert, Chip, Radio, RadioGroup, FormControlLabel, Checkbox, Stack,
} from "@mui/material";
import { api } from "../api/client";

interface PaperQ { id: string; type: string; stem: string; options: string[] | null; }
interface DetailItem {
  question_id: string; type?: string; stem?: string; correct: boolean;
  correct_answer?: unknown; explanation?: string; feedback?: string | null;
}
interface Result { total: number; correct: number; score: number; detail: DetailItem[]; }

const TYPES: { key: string; label: string }[] = [
  { key: "single", label: "单选" },
  { key: "multiple", label: "多选" },
  { key: "truefalse", label: "判断" },
  { key: "short", label: "简答" },
];

export default function ExamView() {
  const [count, setCount] = useState(5);
  const [types, setTypes] = useState<string[]>([]);
  const [paper, setPaper] = useState<PaperQ[]>([]);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const start = async () => {
    setBusy(true); setResult(null); setAnswers({}); setError("");
    try {
      const data = await api.exams.compose(count, types.length ? types : null);
      if (!data.questions.length) {
        setError("题库暂无符合条件的题，请先到题库出题。");
      } else {
        setPaper(data.questions);
      }
    } catch {
      setError("组卷失败，请重试。");
    } finally {
      setBusy(false);
    }
  };

  const setAns = (id: string, v: unknown) => setAnswers((a) => ({ ...a, [id]: v }));
  const toggleMulti = (id: string, idx: number) =>
    setAnswers((a) => {
      const cur = (a[id] as number[] | undefined) || [];
      return { ...a, [id]: cur.includes(idx) ? cur.filter((i) => i !== idx) : [...cur, idx] };
    });

  const submit = async () => {
    setBusy(true); setError("");
    try {
      const payload = paper.map((q) => ({ question_id: q.id, user_answer: answers[q.id] ?? null }));
      setResult(await api.exams.submit(payload));
    } catch {
      setError("交卷失败，请重试。");
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    return (
      <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
        <Typography variant="h5" component="div" fontWeight={700}>
          成绩：{result.correct}/{result.total}
          <Chip label={`${result.score} 分`} color="primary" sx={{ ml: 1 }} />
        </Typography>
        <Stack spacing={1.5}>
          {result.detail.map((d, i) => (
            <Card key={i} variant="outlined" sx={{ borderColor: d.correct ? "success.main" : "error.main" }}>
              <CardContent>
                <Typography>{d.correct ? "✅" : "❌"} {d.stem}</Typography>
                {!d.correct && (
                  <Typography variant="body2" color="text.secondary">
                    正确答案：{JSON.stringify(d.correct_answer)}
                  </Typography>
                )}
                {d.explanation && (
                  <Typography variant="body2" color="text.secondary">解析：{d.explanation}</Typography>
                )}
                {d.feedback && (
                  <Typography variant="body2" color="primary">点评：{d.feedback}</Typography>
                )}
              </CardContent>
            </Card>
          ))}
        </Stack>
        <Box>
          <Button variant="contained" onClick={() => { setPaper([]); setResult(null); }}>
            再考一次
          </Button>
        </Box>
      </Box>
    );
  }

  if (paper.length === 0) {
    return (
      <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
        <Typography variant="h5" fontWeight={700}>模拟考试</Typography>
        <Card variant="outlined">
          <CardContent>
            <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap" useFlexGap>
              <TextField
                type="number" size="small" label="题数" sx={{ width: 96 }}
                slotProps={{ htmlInput: { min: 1, max: 20 } }}
                value={count} onChange={(e) => setCount(Number(e.target.value))}
              />
              <ToggleButtonGroup size="small" value={types} onChange={(_, v: string[]) => setTypes(v)}>
                {TYPES.map((t) => (
                  <ToggleButton key={t.key} value={t.key}>{t.label}</ToggleButton>
                ))}
              </ToggleButtonGroup>
              <Button variant="contained" onClick={start} disabled={busy}>
                {busy ? "组卷中…" : "开始考试"}
              </Button>
            </Stack>
          </CardContent>
        </Card>
        <Typography variant="body2" color="text.secondary">
          不勾题型=全部题型。若无题，请先到题库出题。
        </Typography>
        {error && <Alert severity="error">{error}</Alert>}
      </Box>
    );
  }

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
      <Typography variant="h5" fontWeight={700}>答题（{paper.length} 题）</Typography>
      {paper.map((q, qi) => (
        <Card key={q.id} variant="outlined">
          <CardContent>
            <Typography gutterBottom>{qi + 1}. {q.stem}</Typography>
            {q.type === "single" && (
              <RadioGroup onChange={(e) => setAns(q.id, Number(e.target.value))}>
                {q.options?.map((o, i) => (
                  <FormControlLabel key={i} value={i} control={<Radio />} label={o} />
                ))}
              </RadioGroup>
            )}
            {q.type === "multiple" && q.options?.map((o, i) => (
              <FormControlLabel
                key={i}
                control={<Checkbox onChange={() => toggleMulti(q.id, i)} />}
                label={o}
              />
            ))}
            {q.type === "truefalse" && (
              <RadioGroup row onChange={(e) => setAns(q.id, e.target.value === "true")}>
                <FormControlLabel value="true" control={<Radio />} label="对" />
                <FormControlLabel value="false" control={<Radio />} label="错" />
              </RadioGroup>
            )}
            {q.type === "short" && (
              <TextField fullWidth multiline minRows={2} onChange={(e) => setAns(q.id, e.target.value)} />
            )}
          </CardContent>
        </Card>
      ))}
      <Box>
        <Button variant="contained" color="success" onClick={submit} disabled={busy}>
          {busy ? "判分中…" : "交卷"}
        </Button>
      </Box>
      {error && <Alert severity="error">{error}</Alert>}
    </Box>
  );
}
```

- [ ] **步骤 2：验证 + Commit**

运行：`npx tsc --noEmit && npx vitest run`
预期：全绿。

```bash
git add web/src/pages/ExamView.tsx
git commit -m "feat(web): 考试页迁移到 MUI"
```

---

## 任务 8：错题集页迁移

**文件：**
- 修改：`web/src/pages/WrongAnswersView.tsx`（无对应测试，靠类型检查 + 全量测试守卫）

- [ ] **步骤 1：改写 `WrongAnswersView.tsx`**

全文替换为（**题干行含 Chip，故该 Typography 用 `component="div"` 避免 `<div>` 套 `<p>`**）：

```tsx
import { useEffect, useState } from "react";
import { Box, Typography, Button, Card, CardContent, Checkbox, Chip, Stack } from "@mui/material";
import DeleteSweepIcon from "@mui/icons-material/DeleteSweep";
import { api } from "../api/client";

interface Wrong {
  id: string;
  user_answer: unknown;
  snapshot: { type: string; stem: string; answer: unknown; explanation: string };
}

export default function WrongAnswersView() {
  const [items, setItems] = useState<Wrong[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const refresh = () => api.wrong.list().then(setItems);
  useEffect(() => { refresh(); }, []);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const removeSelected = async () => {
    if (selected.size === 0) return;
    await api.wrong.removeMany([...selected]);
    setSelected(new Set());
    await refresh();
  };

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Typography variant="h5" fontWeight={700}>错题集</Typography>
        <Button
          variant="contained" color="error" startIcon={<DeleteSweepIcon />}
          onClick={removeSelected} disabled={selected.size === 0}
        >
          批量删除（{selected.size}）
        </Button>
      </Box>
      {items.length === 0 ? (
        <Typography color="text.secondary">暂无错题。</Typography>
      ) : (
        <Stack spacing={1.5}>
          {items.map((w) => (
            <Card key={w.id} variant="outlined">
              <CardContent sx={{ display: "flex", gap: 1 }}>
                <Checkbox
                  sx={{ p: 0, mt: 0.25 }}
                  checked={selected.has(w.id)}
                  onChange={() => toggle(w.id)}
                />
                <Box>
                  <Typography component="div">
                    <Chip size="small" label={w.snapshot.type} sx={{ mr: 1 }} />
                    {w.snapshot.stem}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    你的作答：{JSON.stringify(w.user_answer)}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    正确答案：{JSON.stringify(w.snapshot.answer)}
                  </Typography>
                  {w.snapshot.explanation && (
                    <Typography variant="body2" color="text.disabled">
                      解析：{w.snapshot.explanation}
                    </Typography>
                  )}
                </Box>
              </CardContent>
            </Card>
          ))}
        </Stack>
      )}
    </Box>
  );
}
```

- [ ] **步骤 2：验证 + Commit**

运行：`npx tsc --noEmit && npx vitest run`
预期：全绿。

```bash
git add web/src/pages/WrongAnswersView.tsx
git commit -m "feat(web): 错题集页迁移到 MUI"
```

---

## 任务 9：下载页迁移

**文件：**
- 修改：`web/src/pages/DownloadsView.tsx`
- 相关测试：`web/src/pages/DownloadsView.test.tsx`（不改，回归守卫）

- [ ] **步骤 1：改写 `DownloadsView.tsx`**

全文替换为（**保留 `filename` 文本，测试断言 `笔记.md`**）：

```tsx
import { useEffect, useState } from "react";
import { Box, Typography, Card, CardContent, IconButton, Tooltip } from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import DeleteIcon from "@mui/icons-material/Delete";
import { api } from "../api/client";

interface Download {
  id: string;
  filename: string;
  size: number;
  content_type: string;
  created_at: string;
}

export default function DownloadsView() {
  const [items, setItems] = useState<Download[]>([]);

  const refresh = () => api.downloads.list().then(setItems);
  useEffect(() => { refresh(); }, []);

  const remove = async (id: string) => { await api.downloads.remove(id); await refresh(); };

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
      <Typography variant="h5" fontWeight={700}>下载管理</Typography>
      {items.length === 0 ? (
        <Typography color="text.secondary">
          暂无文件。聊天中让助手用 save_download 保存内容后会出现在这里。
        </Typography>
      ) : (
        items.map((d) => (
          <Card key={d.id} variant="outlined">
            <CardContent sx={{ display: "flex", alignItems: "center", gap: 2 }}>
              {d.content_type.startsWith("image/") && (
                <Box
                  component="img" src={`/api/downloads/${d.id}`} alt={d.filename}
                  sx={{ width: 48, height: 48, objectFit: "cover", borderRadius: 1, border: 1, borderColor: "divider" }}
                />
              )}
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Typography noWrap>{d.filename}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {d.content_type} · {d.size} 字节 · {d.created_at.slice(0, 10)}
                </Typography>
              </Box>
              <Tooltip title="下载">
                <IconButton component="a" href={`/api/downloads/${d.id}`} download={d.filename} aria-label="下载文件">
                  <DownloadIcon />
                </IconButton>
              </Tooltip>
              <IconButton color="error" onClick={() => remove(d.id)} aria-label="删除文件">
                <DeleteIcon />
              </IconButton>
            </CardContent>
          </Card>
        ))
      )}
    </Box>
  );
}
```

- [ ] **步骤 2：验证**

运行：`npx tsc --noEmit && npx vitest run src/pages/DownloadsView.test.tsx`
预期：PASS（`getByText(/笔记\.md/)` 命中文件名）。

- [ ] **步骤 3：Commit**

```bash
git add web/src/pages/DownloadsView.tsx
git commit -m "feat(web): 下载页迁移到 MUI"
```

---

## 任务 10：全量验收

**文件：** 无（仅验证）

- [ ] **步骤 1：确认无 Tailwind 残留**

运行：`grep -rn "className=" web/src && grep -rn "@tailwind" web/src; echo "exit=$?"`
预期：无输出（没有任何 `className=` Tailwind 类与 `@tailwind` 指令残留）。
说明：`AppShell` 里对 `.MuiDrawer-paper` 的 `sx` 选择器、`ConversationList` 里 `className="conv-del"` 属于 MUI/emotion 用法，不是 Tailwind，可保留——如需彻底干净可确认这两处即可。

- [ ] **步骤 2：完整构建**

运行：`npm run build`
预期：`tsc -b` 无类型错误；`vite build` 成功产出 `dist/`。

- [ ] **步骤 3：完整测试**

运行：`npm test`
预期：vitest 全绿（`ThemeModeProvider`、`ChatView`、`QuestionBankView`、`KnowledgeView`、`DownloadsView`、`sse` 全通过）。

- [ ] **步骤 4：人工目测**

运行：`npm run dev`，浏览器打开本地地址，逐页检查：
- 侧边抽屉 220px、图标+文字、当前页高亮
- 6 个页面均为 MUI 现代简洁风、无错乱布局
- 抽屉底部明暗切换按钮可用，刷新后保持所选模式（localStorage 持久化）

预期：观感符合「现代简洁生产力风」，明暗切换正常。

- [ ] **步骤 5：收尾 Commit（若目测后有微调）**

```bash
git add -A
git commit -m "chore(web): MUI 改造收尾与微调"
```

---

## 自检记录

**规格覆盖度：**
- 依赖与工具链变更（规格 §3）→ 任务 1 ✅
- 主题与设计令牌 + 明暗切换（§4）→ 任务 2 ✅
- 应用外壳/导航（§5）→ 任务 3 ✅
- 各页面组件映射（§6.1–6.6）→ 任务 4–9 ✅（聊天/知识库/题库/考试/错题集/下载逐一对应）
- 约束与兼容（§7）→ 贯穿「硬约束」+ 各任务验证步骤 ✅
- 验证（§8）→ 任务 10 ✅

**占位符扫描：** 无 TODO/待定/"类似任务 N"；每个改代码的步骤均含完整代码块。✅

**类型一致性：** `buildTheme`/`ThemeModeProvider`/`useColorMode` 命名跨任务一致；`AppShell` 消费 `useColorMode`（任务 2 定义，任务 3 使用）；各页面沿用 `api`/`types` 既有签名，未改动其接口。ToggleButtonGroup 多选 `onChange(_, v: string[])` 在题库与考试两处一致。✅
