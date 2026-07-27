> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# AI 学习助手 · App-1（后端骨架 + AI 聊天流式闭环）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用，在已完成的 harness 框架之上搭应用层的第一个子项目（脊柱）
- **前置**：harness ①②③a③b③c③a-follow③d 已完成并在 `main`

---

## 0. 背景与范围

应用层拆成：**App-1 后端骨架 + AI 聊天流式闭环**（本规格，脊柱）、App-2 文件上传→知识库、App-3 题库/错题/模拟考试、App-4 下载/图库。App-1 把 harness 装配成一个能**通过网页多轮对话、并实时看到 agent 干活**的服务。

**技术选型**：后端 FastAPI（异步、SSE）；前端 React SPA（Vite + TypeScript + Tailwind）；对话历史 App 层拥有（SQLite）。**harness 作为库导入，零改动、零加依赖。**

设计通则：严格 YAGNI；harness 零改动（App 只做装配 + 一个注入式 ContextManager）；SSE 复用 ③d 的 `event_to_dict`；测试用 harness 的 `MockModelClient`，不打网络。

---

## 1. 范围与验收

### IN
- **FastAPI 后端**：`build_harness(config)` 启动装配（config 门控工具集）；`ConversationStore`（SQLite）多轮对话；`ConversationContextManager` 注入历史。
- **`/api/chat` SSE**：POST 消息 → 跑 agent → 事件流（`event_to_dict`）经 `TrajectorySink` 落库 + SSE 推前端；结束后本轮消息落对话库。
- **会话 CRUD**：新建/列表/切换/删除。
- **React SPA**：聊天页 + agent 进度实时渲染 + 对话侧栏；dev Vite 代理、prod FastAPI 托管静态。

### OUT（后续子项目）
文件上传→知识库（App-2）、题库/模拟考试（App-3）、下载/图库（App-4）、生成图、认证。

### 验收标准
1. 起后端 + 前端 → 网页发消息 → SSE 实时渲染打字 + 工具调用进度 + 最终答案。
2. 真工具调用："算 (12+8)×3" 触发 calculator、结果显示。
3. 多轮：第二条消息能看到第一轮历史（context 注入）。
4. 会话 CRUD 全通。
5. 配置门控：未配 embedding/docker/chromium 时对应工具不注册、不报错、聊天照常。
6. 后端 pytest（MockModelClient）覆盖 `/api/chat` SSE + 会话 CRUD + 历史注入，不打网络。

---

## 2. 架构与目录

**设计取向**：启动时装配一次共享组件（client/memory/工具 registry/persistence）；per-turn 只建一个注入历史的 `ContextManager` + `AgentLoop`；事件经 `TrajectorySink` 落库 + 转 SSE。**harness 零改动**。

```
app/                              [新增] FastAPI 后端
├── __init__.py
├── main.py            FastAPI 应用、CORS、静态托管、路由挂载
├── config.py          AppConfig（HarnessConfig + host/port/enable_*/cors）
├── assembly.py        build_harness(config) → Harness（共享组件包）
├── conversations.py   ConversationStore(SQLite) + 消息模型（复用 serialize）
├── context.py         ConversationContextManager
└── api/
    ├── __init__.py
    ├── chat.py        POST /api/chat（SSE）
    └── conversations.py  会话 CRUD
web/                              [新增] React SPA（Vite+TS+Tailwind）
├── src/{App.tsx, api/chat.ts, components/*, types.ts, main.tsx, index.css}
├── index.html, vite.config.ts, tailwind.config.js, tsconfig.json, package.json
src/harness/                      不变（作为库被 app 导入）
```

**依赖新增**：后端 `fastapi`、`uvicorn[standard]`；前端 `vite`/`react`/`react-dom`/`typescript`/`tailwindcss`。harness 不加依赖、不改代码。

**数据流（一轮对话）**：
```
前端 POST /api/chat {conversation_id, message}
 → 后端 载入历史 → ConversationContextManager(system, 历史)
        → AgentLoop.run(message)（共享 registry + 新 BudgetTracker + checkpoint/trajectory）
        → TrajectorySink 落库 + event_to_dict → SSE `data: {...}\n\n`
 → 前端 fetch 流式读 → 按事件类型渲染进度
 → run 结束 → ConversationStore.append(本轮 user + 最终 assistant)
```

---

## 3. 后端核心

**`assembly.py::build_harness(config) -> Harness`**（启动装配一次）：
- 共享组件包 `Harness`：`client`、`registry`、`memory?`、`checkpoint_store`、`trajectory_store`、`sink`、`system_prompt`。
- 必装：`CalculatorTool`、`HttpRequestTool`；`client = RetryingModelClient(OpenAICompatibleClient(cfg))`。
- **config 门控**（缺配置跳过注册、不报错）：
  - embedding 配了 → `Memory`/`EpisodicMemory` + 注册 `search_memory`/`remember`/`recall_episodes`。
  - `enable_browser` → `browse`（`build_browser`）。
  - `enable_sandbox` 且有 `sandbox_docker_host` → `Sandbox` + `run_python`/`run_shell`/`write_file`/`read_file`/`list_files`。
  - `enable_dispatch` → 角色花名册 + `dispatch`。

**`ConversationStore`（SQLite）**：
```sql
conversations(id TEXT PRIMARY KEY, title TEXT, created_at TEXT)
conversation_messages(conv_id TEXT, seq INTEGER, role TEXT, content TEXT,
                      tool_calls TEXT, tool_call_id TEXT, created_at TEXT,
                      PRIMARY KEY(conv_id, seq))
```
方法：`create(title) -> id`、`list() -> [{id,title,created_at}]`、`messages(conv_id) -> list[Message]`、`append(conv_id, msgs)`、`delete(conv_id)`。消息 ↔ 行复用 `persistence.serialize.message_to_dict/from_dict`（`tool_calls` 存 JSON）。

**`ConversationContextManager`**（鸭子类型 harness `ContextManager`，有 `build(state)`）：
```python
class ConversationContextManager:
    def __init__(self, system_prompt: str, history: list[Message]): ...
    def build(self, state) -> list[Message]:
        return [Message(role=Role.SYSTEM, content=self._system), *self._history, *state.messages]
```

---

## 4. API（SSE + 会话 CRUD）

**`POST /api/chat`** body `{conversation_id: str, message: str}` → `StreamingResponse(media_type="text/event-stream")`：
```python
history = store.messages(conv_id)
ctx = ConversationContextManager(harness.system_prompt, history)
loop = AgentLoop(client=harness.client, registry=harness.registry, context=ctx,
                 max_steps=cfg.max_steps, budget=BudgetTracker(cfg.max_tokens_budget, cfg.max_wall_seconds),
                 checkpoint_store=harness.checkpoint_store, model_name=cfg.model,
                 price_map=cfg.price_map, tool_result_max_chars=cfg.tool_result_max_chars)

async def gen():
    final = None
    async for ev in harness.sink.wrap(loop.run(message)):
        if isinstance(ev, RunFinished):
            final = ev.message.content
        elif isinstance(ev, RunError):
            final = final or f"[出错] {ev.error}"
        yield f"data: {json.dumps(event_to_dict(ev), ensure_ascii=False)}\n\n"
    store.append(conv_id, [Message(role=Role.USER, content=message),
                           Message(role=Role.ASSISTANT, content=final or "")])

return StreamingResponse(gen(), media_type="text/event-stream")
```
- **对话历史只存"用户问 + 最终答"**（工具细节在轨迹库，不进对话历史——多轮上下文够用、干净）。
- 未知 `conversation_id` → 404（或先 `POST /api/conversations` 建）。

**会话 CRUD**：
- `GET /api/conversations` → 列表（含 id/title/created_at）。
- `POST /api/conversations` {title?} → 新建、返回 id。
- `GET /api/conversations/{id}/messages` → `list[{role, content}]`（渲染历史）。
- `DELETE /api/conversations/{id}` → 删对话及其消息。

---

## 5. 前端（React SPA）

- **`api/chat.ts::streamChat(convId, message, onEvent)`**：`fetch("/api/chat", {method:"POST", body})` → 读 `response.body` 的 `ReadableStream`，按 `\n\n` 切、剥 `data:` 前缀、`JSON.parse` → `onEvent({type, data})`。
- **`types.ts`**：`AgentEvent = {type: "TextDelta"|"ToolStarted"|"ToolFinished"|"RunFinished"|"ModelUsage"|..., data: ...}`，对齐后端 `event_to_dict`。
- **`ChatView.tsx`**：维护 messages 状态；发送时乐观加 user 气泡 + 空 assistant 气泡；`onEvent` 分发——`TextDelta`→追加 assistant 文本；`ToolStarted`→插入"调用 xx 工具…"进度项；`ToolFinished`→填结果（可折叠）；`ModelUsage`→页脚 token/成本；`RunFinished`→定型。
- **`AgentProgress.tsx`**：渲染一轮内交错的工具步骤（可折叠）。
- **`ConversationList.tsx`**：侧栏，`GET /api/conversations` 列表 + 新建/删除/切换；切换 `GET .../messages` 渲染。
- Tailwind 干净聊天布局；`main.tsx` 挂载 `App.tsx`。

---

## 6. 配置 / 测试 / 依赖 / 运行

### `AppConfig`（`app/config.py`，pydantic-settings；含 harness 配置）
```
（复用 HarnessConfig 全部字段）+
app_host: str = "127.0.0.1"
app_port: int = 8000
conversations_db_path: str = "conversations.db"
app_system_prompt: str = "你是一个 AI 学习助手，可用工具检索知识、联网、计算来帮助用户学习。"
enable_browser: bool = False
enable_sandbox: bool = False
enable_dispatch: bool = False
cors_origins: list = ["http://localhost:5173"]
```

### 测试（后端 pytest + FastAPI `TestClient`/httpx，注入用 `MockModelClient` 的假 `Harness`，不打网络）
- `POST /api/chat`：解析 SSE 流，断言含 `TextDelta` + `RunFinished`；含工具的轮断言 `ToolFinished`。
- 多轮：turn1 后，turn2 的 `ConversationContextManager` 历史含 turn1（用捕获 messages 的假 client 或直接断言 store）。
- `ConversationStore` CRUD 往返；`ConversationContextManager.build` 注入顺序（`[system]+历史+本轮`）。
- 门控：未配 embedding 时 registry 不含 memory 工具、`/api/chat` 仍正常。
- 前端：Vitest + React Testing Library 覆盖 `ChatView` 渲染流式事件（1-2 个组件测试）；其余手动 E2E。

### 依赖
后端 `fastapi`、`uvicorn[standard]`（加进 pyproject）；前端 `web/package.json`（vite/react/react-dom/typescript/tailwindcss/vitest）。

### 运行
- dev：`uvicorn app.main:app --reload` + `cd web && npm run dev`（`vite.config.ts` 代理 `/api`→`127.0.0.1:8000`）。
- prod：`cd web && npm run build` → FastAPI `StaticFiles` 托管 `web/dist`。

---

## 7. 后续衔接（非本次范围）

- **App-2**：`POST /api/upload` + PDF/docx 解析 → `Memory.add_texts`；知识库管理页。
- **App-3**：出题 agent + 题库/错题域模型 + CRUD + 模拟考试；App-4：下载/图库。
- **人在环**：工具执行前审批（需 SSE→双向，改 WebSocket 或加审批端点）。
- **企业级**：轨迹评测读 `TrajectoryStore` 打分。
