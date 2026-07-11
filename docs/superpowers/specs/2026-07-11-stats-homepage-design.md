# 首页概览台设计（V1 工程台 + V2 学习主场整合）

日期：2026-07-11 · 状态：已实现

## 背景与目标

为 AI 学习助手增加一个统计首页。项目底层是一个 agent harness，每轮 AI 运行都在
`harness.db / trajectory_events` 留下完整轨迹（模型用量、工具调用、步数、成败）。
目标：把这份运行数据 + 应用业务数据（`app.db` / `memory.db`）聚合成一个登录后的落地页。

一页两视角，**共用同一份后端聚合**，只是换讲法：

- **学习主场（默认）**——面向学习者：继续上次对话、我的积累（资料/记忆/题库/错题）、
  「AI 在为我做什么」把 harness 工作翻译成产品语言（联网查资料 82 次、平均想 3 步…）。
- **工程台**——面向开发者：运行概览 tile（成功率、P95 延迟、token）、token/run 趋势、
  工具调用×成功率、每 run 步数分布。

## 适合统计的数据（按数据源）

| 来源 | 指标 | 说明 |
|------|------|------|
| `trajectory_events` | run 数、成功率、模型调用数、token、P95/均值延迟、重试、工具分布+成功率、步数分布 | 随每次使用自动增长，harness 架构的核心可观测性 |
| `app.db` | 会话/消息/文档/题库/错题/下载计数、继续上次对话、最近产物 | 按 user_id 归属 |
| `memory.db` | 语义记忆条数 | 全局 |

运行轨迹是全局的（harness.db 无 user_id，本应用单用户）；学习资产按 user_id 归属。

## 架构（三个单一职责单元）

- **`app/stats.py` · `StatsService`**——纯读聚合层。输入三个 sqlite 连接，输出
  `{range_days, learn, ops}` dict。无 HTTP、无副作用、可脱离 FastAPI 单测。
- **`app/api/stats.py`**——`GET /api/stats/overview?days=14`（1–90，鉴权），一次返回整屏。
- **`web/src/pages/HomeView.tsx`**——MUI 视图，`ToggleButtonGroup` 切两视角，
  纯 SVG 图表（无新依赖），随应用明暗主题自适应。

数据流：`HomeView` 挂载 → `authFetch(/api/stats/overview)` → `StatsService` 扫轨迹（按
`created_at` 过滤）解析事件 + `app.db`/`memory.db` 计数 → 合并 JSON → 渲染。

## 关键实现要点

- 轨迹 `data` 列存的是**整条**序列化事件 `{"type","data":{…}}`（`TrajectoryStore.append`），
  聚合时解开外层拿真正 payload——此处曾是实数据验证抓到的 bug。
- P95 延迟用线性插值分位数（真实值，非估算）。
- 工具成功率：`ToolStarted.tool_call.id` ↔ `ToolFinished.tool_call_id` 关联 `is_error`。
- 成本 `cost_usd`：事件里非空才求和，否则显示 "$ —"（当前生产数据全为 null）。
- 空数据（错题/题库 0 行、无对话）走友好空态，不报错。
- 路由：`/` → 首页，AI 聊天移到 `/chat`，侧栏新增「首页」入口。ChatPage 与路由无耦合，
  登录/注册跳 `/` 现落在首页。

## 边界与取舍（YAGNI）

- 当前全表扫描 `trajectory_events`（~94 run）性能无忧；**不预建 rollup 表**，
  数据涨大再加 `created_at` 索引/汇总表。
- 学习成效指标（错题趋势、掌握度）待错题/题库有数据后再补，现留空态占位。

## 测试

- `tests/app/test_stats.py`：聚合纯函数 + 组装（喂生产格式事件夹具）。
- `tests/app/test_stats_api.py`：端点鉴权 + 参数校验 + 两视角 payload。
- `web/src/pages/HomeView.test.tsx`：默认学习主场、切工程台、空态、错误态。
- 实数据端到端验证：对真实 `harness.db`/`app.db`/`memory.db` 跑聚合，核对 94 run /
  1,140,386 token / 工具分布一致。
