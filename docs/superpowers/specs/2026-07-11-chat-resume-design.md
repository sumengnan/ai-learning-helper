# AI 聊天断点续传设计

## Context

现状：一轮 AI 生成由 `POST /api/chat` 的请求协程直接驱动 AgentLoop，浏览器刷新 = SSE 断开 → 协程被取消 → 生成中止，`finally` 落库写入 "（本轮未完成）"。已流式的部分文本丢失，无法续上。

底座已有但未用：`run_id`、`conversation_runs`（run↔conv 映射）、`TrajectoryStore`（全事件流持久化）、`AgentLoop.resume`、`CheckpointStore`。

目标（用户选定 **方案 A：后台跑完 + 内存总线重连**）：生成脱离请求，跑成后台任务，事件走内存 pub-sub 总线；请求只是订阅者，断开只取消订阅、后台照跑到完并落库；刷新后前端自动接回——先回放已生成部分、再接实时流，直到完成。纯文本答案也能无缝续上。

## 架构

- **RunManager**（`app/run_manager.py`，进程内单例，挂 `app.state`）：`start/subscribe/cancel/is_active/active_run_for_conv/conv_of`。每个 run 一个 `_RunHandle{conv_id, events[缓冲], subscribers[队列集], done, task}`。`start` 用 `asyncio.create_task` 驱动一个「产出 Event 的 async generator」，逐事件缓冲+扇出给订阅者；跑完标 done、推哨兵、宽限期 ~60s 后清理。`subscribe` 加锁原子「快照已缓冲 + 注册队列」，先 yield 缓冲、再取实时，直到哨兵。

- **turn_run_id**：`POST /api/chat` 开头生成，作 RunManager key + assistant 消息 `run_id` + 前端接回/停止句柄。交付门内部多次 AgentLoop 尝试仍各用自己 trajectory run_id。

## 数据模型（`app/db.py` + `app/conversations.py`）

- `conversation_messages` 加列（`_COLUMN_MIGRATIONS`）：`run_id TEXT`、`status TEXT`（`done` 默认/历史，`streaming`/`error`/`stopped`/`interrupted`）。
- 两阶段写入替代一次性 append：
  - `start_turn(conv, user_msg, run_id, attachments)`：开头原子写 user + streaming 占位 assistant（空内容）。
  - `finish_turn(conv, run_id, content, steps, progress, status)`：结束按 run_id UPDATE 占位为最终态。
  - `flush_partial(conv, run_id, content)`：生成中去抖 ~1.5s 把已累积文本写进占位（仅为服务重启后能看到断点前部分兜底）。
  - `reconcile_streaming()`：启动时把残留 `status=streaming` 的消息标 `interrupted`（进程重启丢了在途任务）。
- `ui_messages()` 每条多返回 `run_id`、`status`。`conv_of_run(run_id)` 归属查询供 attach 鉴权。

## 后端 chat 改造（`app/api/chat.py`）

- `_drain`、`_emit_verify`、`gen` 全部改为 **yield Event 对象**（不再拼 SSE 串）；SSE 串化挪到 HTTP 边界。
- `gen`：累积所有 client 面 TextDelta 到 `parts`；finally 里 `finish_turn(content=delivered or "".join(parts) or 兜底, status)`；正常=`done`，出错=`error`，被 cancel=`stopped`（捕获 `CancelledError`）。L3 记忆仅正常完成时记。周期 flush_partial。
- `POST /api/chat`：并发守卫（该 conv 已有 active run → 409）→ 生成 turn_run_id → `start_turn` 落 user+占位 → `add_run` → `run_manager.start(turn_run_id, conv, gen())` → 返回 `StreamingResponse(subscribe→SSE, headers={"X-Run-Id": turn_run_id})`。
- `GET /api/chat/attach/{run_id}`（SSE）：`conv_of_run`→归属校验；`is_active` 假→409（前端改重载消息）；真→`subscribe` 回放缓冲+实时直到 done。
- `POST /api/chat/stop/{run_id}`：归属校验→`run_manager.cancel` 取消后台任务（触发 gen finally 落 stopped + 已生成部分）。

## 前端（`web/src`）

- `api/client.ts`：`attachChat(runId, onEvent, signal)`（GET SSE，复用 drainSSE）；`stopRun(runId)`（POST stop）；`streamChat` 读响应头 `X-Run-Id` 回传。
- `types.ts`：`ChatMessage` 已有 `status`，加载路径补 `runId`。
- `pages/ChatPage.tsx`：`select()` 把服务端 `run_id`/`status` 映射进 `ChatMessage`。
- `components/ChatView.tsx`：
  - 挂载/切换会话时，若最后一条助手消息 `status==="streaming"` → 自动 `attachChat(runId)` 接回：置 busy、事件流入该气泡、done 置状态；attach 返回 409 → 重载消息。
  - `send()` 记录 `X-Run-Id` 到 `runIdRef`；`stop()` 改为调 `stopRun(runId)` + abort 本地 fetch（不再靠断开取消后端）。
  - busy/StreamingHint/ReplyStatus 复用现有（已实现）。

## 边角

- **服务重启**：内存 RunManager 丢在途任务；启动 `reconcile_streaming()` 把残留 streaming 标 interrupted（配 flush_partial 保留的部分文本）；前端 attach 该 run→409→重载看到 interrupted + 部分。
- **多标签/多次 attach**：subscribe 扇出多队列，天然支持。
- **完成瞬间 attach**：宽限期内 handle 仍在，回放缓冲+立即结束；宽限后→409→重载看最终消息。
- **stop**：取消后台任务，落已生成部分 + status=stopped。

## 验证

- 单测：RunManager（缓冲/扇出/迟到 attach/cancel）；store（start/finish/flush/reconcile/ui 带状态）。
- 集成：起 run→断开原始 SSE 中途→后台跑完+消息 finalized；attach 中途→回放+续流+done；stop→stopped+部分留存。
- 前端：ChatView 加载 streaming 消息→自动 attach。
- 真实：聊天→中途刷新→自动接回续上（不再"本轮未完成"）。

## 关键文件

新增：`app/run_manager.py`、`web/src/api`(attach/stop)、测试。
改：`app/db.py`、`app/conversations.py`、`app/api/chat.py`、`app/main.py`（RunManager 装配 + expose X-Run-Id + startup reconcile）、`web/src/{types.ts, pages/ChatPage.tsx, components/ChatView.tsx}`。
