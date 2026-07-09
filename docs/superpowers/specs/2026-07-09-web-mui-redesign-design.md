# Web 前端 MUI 改造 · 设计规格

- 日期：2026-07-09
- 分支：`feat/web-mui-redesign`（基于 `origin/main`）
- 范围：`web/` 目录，纯前端样式/组件层改造，不动后端与 API

## 1. 目标与背景

当前 `web/` 是一个功能完整但样式极简的 React 应用，技术栈为 React 18 + TypeScript + Vite + **Tailwind CSS** + react-router。样式全部是裸 Tailwind 类内联在 JSX 中（灰色窄边栏、蓝色按钮、原生 input/checkbox），无主题、无设计系统（`tailwind.config.js` 的 `theme.extend` 为空），整体观感粗糙。

本次改造：**彻底移除 Tailwind，全面改用 MUI（Material UI）**，建立统一的现代简洁生产力风格设计系统，支持亮/暗主题切换，并重做导航外壳与 6 个页面，同时保证现有 vitest 测试全部继续通过。

**非目标（YAGNI）**：不改任何后端逻辑、不改 `api/client.ts` 与 `types.ts` 的接口、不新增业务功能、不引入路由变化、不做移动端专门适配（响应式仅做基本保证）。

## 2. 关键决策（已与用户确认）

| 决策项 | 选择 |
|---|---|
| Tailwind 与 MUI 共存策略 | **彻底换成 MUI**，移除 Tailwind |
| 整体视觉风格 | **现代简洁生产力风**（类 Linear/Notion：中性配色、适中圆角、弱阴影、专注不花哨）|
| 明暗主题 | **亮色 + 暗色可切换**，默认跟随系统，选择持久化 |
| 导航布局 | **220px 图标+文字侧边抽屉**（MUI permanent Drawer）|

## 3. 依赖与工具链变更

**移除：**
- npm 依赖：`tailwindcss`、`postcss`、`autoprefixer`
- 文件：`web/tailwind.config.js`、`web/postcss.config.js`
- `web/src/index.css` 中的 `@tailwind base/components/utilities` 三行指令（保留 `html, body, #root { height:100%; margin:0 }` 基础重置）

**新增 npm 依赖：**
- `@mui/material`
- `@emotion/react`、`@emotion/styled`（MUI 默认样式引擎）
- `@mui/icons-material`（导航与操作图标）

**改造：** 所有页面/组件 JSX 中的 Tailwind `className="..."` 逐个替换为 MUI 组件 + `sx` 属性。替换完成后，全局不应再残留任何 Tailwind 工具类。

## 4. 主题与设计令牌

新增 `web/src/theme.ts`，用 MUI `createTheme` 产出亮/暗两套主题，共享同一组令牌：

- **主色 primary**：indigo/蓝紫系（基准 `#4f46e5`），用于主按钮、当前导航高亮、用户气泡
- **中性底色**：亮色为近白灰底 + 白色卡片；暗色为深灰底（非纯黑）+ 略浅卡片
- **语义色**：`success`（考试正确/绿）、`error`（错误/删除/红）、`warning` 沿用 MUI 默认微调
- **圆角**：`shape.borderRadius = 8`
- **按钮**：`components.MuiButton.styleOverrides` 关闭全大写（`textTransform: "none"`），默认 `disableElevation`
- **字体**：`typography.fontFamily` 优先系统中文字体栈（如 `-apple-system, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif`），避免 Roboto 渲染中文发虚；字号/行高做轻度调优
- **密度**：舒适但紧凑（沿用 MUI 默认间距，不启用 dense 全局）

### 明暗切换机制

- 新增 `web/src/ThemeModeProvider.tsx`：React context，持有 `mode: "light" | "dark"` 状态
- 初始值：读 `localStorage["color-mode"]`；无值时跟随 `window.matchMedia("(prefers-color-scheme: dark)")`
- 提供 `toggleMode()`；切换后写回 `localStorage`
- Provider 内根据 `mode` 选择对应 theme，包一层 MUI `<ThemeProvider>` + `<CssBaseline>`
- `main.tsx` 用 `ThemeModeProvider` 包裹 `<App />`
- 切换入口：抽屉底部一个 `IconButton`（`Brightness4`/`Brightness7` 太阳/月亮图标）

## 5. 应用外壳 / 导航

从 `App.tsx` 抽出布局组件 `web/src/components/AppShell.tsx`：

- MUI `Drawer variant="permanent"`，宽度 220px
- 顶部一个应用标题区（应用名，如「AI 学习助手」）
- `List` + `ListItemButton`，6 项，每项 `ListItemIcon`（图标）+ `ListItemText`（中文标签）
- 当前路由项高亮（`selected` 属性，由 react-router `useLocation`/`NavLink` 驱动）
- 抽屉底部：明暗切换 `IconButton`
- 右侧 `Box component="main"` 承载 `<Routes>`，撑满剩余宽高

**导航项与图标（`@mui/icons-material`）：**

| 路由 | 标签 | 图标（建议）|
|---|---|---|
| `/` | 聊天 | `ChatBubbleOutline` |
| `/knowledge` | 知识库 | `MenuBook` |
| `/questions` | 题库 | `Quiz` / `ListAlt` |
| `/exam` | 考试 | `Assignment` |
| `/wrong` | 错题集 | `ErrorOutline` |
| `/downloads` | 下载 | `Download` |

`App.tsx` 保留 `BrowserRouter` + `Routes` 路由定义，外层结构改为 `<AppShell>{routes}</AppShell>` 或 AppShell 内部渲染 `<Outlet>`/children。路由路径与元素保持不变。

## 6. 各页面组件映射

所有页面：容器统一用 MUI `Box`/`Container` + `sx` 控制内边距（替换 `p-6` 等），标题用 `Typography variant="h5"/"h6"`。

### 6.1 聊天（ChatPage / ConversationList / ChatView / AgentProgress）
- **ConversationList**：`Drawer`-风格的 `Box`（宽 240），顶部「+ 新对话」`Button`（保留文案 `+ 新对话`），会话用 `List` + `ListItemButton`（`selected` 高亮当前），悬停显示删除 `IconButton`（`Close`/`Delete`）
- **ChatView 气泡**：`Box` 左右对齐，气泡用 `Paper`——用户消息 `bgcolor: primary.main` + 白字，助手消息 `bgcolor: background.paper`/`grey`；保留 `whiteSpace: pre-wrap`
- **输入区**：`TextField`（保留 placeholder `问点什么…`，回车发送）+ 发送 `Button`/`IconButton`（保留文案 `发送`，`disabled` 时禁用）
- **AgentProgress**：每步 `Accordion`（替换 `<details>`），错误步骤标题用 `error.main` 色；参数/结果放 `AccordionDetails`，`Typography variant="caption"`
- **用量**：`Chip` 或 `Typography variant="caption"` 展示 tokens/cost

### 6.2 知识库（KnowledgeView）
- 上传：`Button component="label"` 包裹隐藏 `<input type="file">`（保留 `accept`），上传中 `CircularProgress` + 文案
- 错误：`Alert severity="error"`
- 文档列表：`List` + `ListItem`，`ListItemText`（文件名 + `N 块` 保留原文案），次级删除 `IconButton`
- 空态：`Typography color="text.secondary"`（保留「还没有上传文档」）

### 6.3 题库（QuestionBankView）
- 出题表单进 `Card`/`Paper`：主题 `TextField`（保留 placeholder），题数 `TextField type="number"`，题型用 `ToggleButtonGroup`（多选），出题 `Button`（保留 `出题`/`出题中…` 文案与禁用逻辑）
- 错误：`Alert severity="error"`
- 题目列表：每题 `Card`，类型用 `Chip`，来源 `Typography variant="caption"`，删除 `IconButton`
- 空态保留「暂无题目，先出题吧。」

### 6.4 考试（ExamView）
三态复用同一套 MUI 组件：
- **配置态**：`Card` + 题数 `TextField` + 题型 `ToggleButtonGroup` + `开始考试` `Button`；说明 `Typography variant="body2"`
- **答题态**：每题 `Card`；单选 `RadioGroup`/`Radio`，多选 `Checkbox`，判断 `RadioGroup`（对/错），简答多行 `TextField`；`交卷` `Button`
- **成绩态**：标题 `Typography` + 分数 `Chip`；每题 `Card`，正确/错误用 `borderColor: success.main / error.main` 或左侧色条；正确答案/解析/点评用分级 `Typography`；`再考一次` `Button`
- 保留全部原文案（`开始考试`/`组卷中…`/`交卷`/`判分中…`/`再考一次` 等）

### 6.5 错题集（WrongAnswersView）
- 列表用 `Card`/`List` 呈现错题，操作（如移除/重做）用 `IconButton`/`Button`，保留原文案与文本内容

### 6.6 下载（DownloadsView）
- `Card` 列表；图片项内嵌预览（`Box component="img"` + `maxWidth:100%`），下载/删除用 `IconButton`
- 保留文件名等渲染文本（测试断言 `笔记.md`）

## 7. 约束与兼容

- **不修改**：`web/src/api/client.ts`、`web/src/api/sse.test.ts`、`web/src/types.ts`、`web/vite.config.ts`（除非移除 Tailwind 需要）、后端 `app/`
- **文案保留（硬约束）**：现有 vitest 测试仅按文本/placeholder 查询，不查 className/DOM 结构。必须原样保留：
  - `发送`、`问点什么…`（`ChatView.test.tsx`）
  - `+ 新对话`、各题型标签
  - 各页面渲染文本（`光合作用…`、`bio.txt`、`3 块`、`笔记.md` 等由数据驱动，天然保留）
- **可访问性**：图标按钮补 `aria-label`；表单控件用 MUI 自带 label 关联

## 8. 验证

实现完成后依次运行并确认输出：

```bash
cd web
npm install          # 安装 MUI，移除 Tailwind 后 lockfile 更新
npm run build        # tsc -b && vite build 全绿（无 TS 错误、无残留 Tailwind 引用）
npm test             # vitest：ChatView / QuestionBank / Knowledge / Downloads / sse 全通过
npm run dev          # 人工目测 6 个页面 + 明暗切换观感
```

验收标准：
1. 构建通过，无 TypeScript 错误
2. 现有 4 个组件/页面测试 + sse 测试全部通过
3. 全局无 Tailwind 类残留、无 `tailwindcss`/`postcss`/`autoprefixer` 依赖
4. 6 个页面均为 MUI 现代简洁风；抽屉导航含图标高亮；亮/暗切换正常且持久化

## 9. 实现顺序（供 writing-plans 参考）

1. 依赖切换（装 MUI、卸 Tailwind、清配置与 `@tailwind` 指令）
2. `theme.ts` + `ThemeModeProvider` + `main.tsx` 接入 + `CssBaseline`
3. `AppShell` 抽屉导航 + `App.tsx` 改造
4. 逐页改造：聊天（含 3 个子组件）→ 知识库 → 题库 → 考试 → 错题集 → 下载
5. 跑测试与构建，修复回归；人工目测明暗观感
