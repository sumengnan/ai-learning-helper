# AI 学习助手 · App-1（聊天脊柱）

FastAPI 后端把 `src/harness`（作为库导入，零改动）装配成一个可通过网页多轮对话、
实时看到 agent 干活的服务；`web/` 是配套的 React SPA。

## 运行

### 后端

需要一个 `.env`（放在仓库根目录）配置真实聊天端点，至少：

```bash
HARNESS_API_KEY=sk-xxx
HARNESS_BASE_URL=https://api.openai.com/v1   # 或任意 OpenAI 兼容端点
HARNESS_MODEL=gpt-4o-mini
```

其余可选开关（默认关闭，不配也不会报错）：

```bash
HARNESS_ENABLE_BROWSER=false     # true 则注册 browse 工具（需 playwright 浏览器已安装）
HARNESS_ENABLE_SANDBOX=false     # true 且配了 HARNESS_SANDBOX_DOCKER_HOST 才注册代码执行工具
HARNESS_ENABLE_DISPATCH=false    # 多 agent 编排（App-1 暂不使用）
HARNESS_EMBEDDING_API_KEY=       # 留空则回退用 HARNESS_API_KEY；配了才注册 search_memory/remember
HARNESS_CONVERSATIONS_DB_PATH=conversations.db
HARNESS_CORS_ORIGINS=["http://localhost:5173"]
```

启动（**必须** `--factory`：`app.main` 不在模块级构造真实 harness，延迟到
uvicorn 调用工厂函数时才装配，避免空 api_key 在 import 期就抛错）：

```bash
uv run uvicorn --factory app.main:create_app --reload
```

默认监听 `127.0.0.1:8000`。也可用下面这种方式启动，它会读取 `AppConfig` 的
`app_host` / `app_port`（`HARNESS_APP_HOST` / `HARNESS_APP_PORT`）：

```bash
uv run python -m app
```

生产部署：先 `cd web && npm run build` 产出 `web/dist/`，`app/main.py` 会自动挂载
静态文件到 `/`（后端单独一个进程即可，无需前端 dev server）。

### 前端（开发模式）

```bash
cd web
npm install
npm run dev
```

打开 Vite 给出的 URL（默认 `http://localhost:5173`），已配置 `/api` 代理到
`http://127.0.0.1:8000`。

### 测试

```bash
uv run pytest -q              # 后端：全部用 MockModelClient，不打网络
cd web && npm run test        # 前端：Vitest（drainSSE 纯函数单测）
```

## 验收方式（手动）

1. 起后端 + 前端。
2. 网页新建对话，发 "帮我算 (12+8)*3"：应看到打字机效果的回答、可展开的
   "调用工具 calculator" 进度项、最终结果 60。
3. 再发一条消息：应能延续上一轮上下文。
4. 侧栏新建/切换/删除对话应正常工作。

## 已知限制

- **单 worker uvicorn（同线程）假设**：harness 的 `CheckpointStore` / `TrajectoryStore`
  用默认的 sqlite3 连接（`check_same_thread=True`），本 App 假设以单 worker、单
  事件循环线程运行，故这些连接可安全共享。若要多线程 / 多 worker 部署，需要另行
  处理跨线程 sqlite 访问（每线程独立连接或换连接池），否则会抛
  `sqlite3.ProgrammingError`。
- **`enable_sandbox` 打开时无并发互斥**：LocalSandbox 共享同一个 workspace，同一
  对话并发 `/api/chat` 之间没有互斥。单用户自用场景下可接受；多并发写同一
  workspace 可能相互覆盖。
