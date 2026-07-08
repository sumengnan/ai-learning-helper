# AI 学习助手 · App-1/App-2（聊天脊柱 + 知识库）

FastAPI 后端把 `src/harness`（作为库导入，零改动）装配成一个可通过网页多轮对话、
实时看到 agent 干活的服务，并支持上传文件建知识库供检索；`web/` 是配套的 React SPA
（左侧导航：聊天 / 知识库）。

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
HARNESS_APP_MAX_UPLOAD_MB=20             # 知识库上传大小上限（MB）
HARNESS_DOCUMENTS_DB_PATH=documents.db   # 文档登记表存储位置
```

### 知识库（App-2）

知识库页（前端 `/knowledge`）支持上传 **PDF / docx / txt / md** 文件：解析正文后
分块 embedding 写入 harness `Memory` 的 `knowledge` collection，agent 聊天时可通过
`search_memory` 工具检索到；文档管理页可查看已上传文档（文件名/块数/时间）并删除
（删除会同时清掉对应的 embedding chunk）。

- **需要 embedding 端点**：`HARNESS_API_KEY`/`HARNESS_EMBEDDING_API_KEY` 未配置时，
  `build_harness` 不会装配 `Memory`，此时上传接口 `POST /api/documents` 返回
  **503**（知识库未启用），列表/查询仍可用（返回空列表）。
- **支持格式**：`.pdf`、`.docx`、`.txt`、`.md`；其他扩展名返回 **400**（不支持的格式）。
- **上传大小上限**：默认 20MB（`HARNESS_APP_MAX_UPLOAD_MB` 可调），超限返回 **413**。
- **不支持**：pptx/xlsx/html/epub、后台异步解析大文件、rerank、按文档过滤检索——
  见规格 `docs/superpowers/specs/2026-07-08-app-knowledge-base-design.md` 的 OUT 范围。

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
5. 切到知识库页，上传一个 txt/pdf：列表出现该文档（含块数）；回聊天页问该文档
   内容：agent 应通过 `search_memory` 召回并作答；回知识库页删除该文档：再问
   同样内容应召回不到。

## 题库 / 模拟考试 / 错题集（App-3）

在知识库之上加「出题 → 题库 → 模拟考试 → 错题集」闭环。**出题依赖知识库**——需先在
知识库页上传资料。

- **题型（4 种）**：单选、多选、判断、简答。
- **出题**：题库页填主题 + 题数 + 题型 → 后端 `Memory.search` 检索知识库 top-k →
  单轮 LLM 生成结构化题目（校验后入库）。主题无相关知识返回 422；未配置 embedding
  返回 503；生成结果无法解析/校验返回 502。
- **判分**：客观题（单选/多选/判断）精确匹配自动判分；简答题由 LLM 对照参考答案打分
  （默认 ≥60 判对，阈值 `short_pass_score`）。
- **模拟考试**：从题库随机组卷（卷子不含答案）→ 答题 → 交卷判分 → 存一条成绩记录
  （可看历史）；判错的题自动进错题集（存**题目快照**，原题删除后仍可回看）。
- **错题集**：查看错题快照 + 批量删除。

相关 DB 文件：`questions.db` / `exams.db` / `wrong_answers.db`（默认与
`conversations.db` / `documents.db` 同目录）。

### 验收方式（手动，接前）

6. 知识库有资料后，切到题库页填主题出题：题库列表出现生成的题（题型/题干/来源）。
7. 切到考试页开始考试：作答后交卷，看到总分 + 逐题对错 + 正确答案 + 解析（简答带
   LLM 点评）。
8. 故意答错后到错题集页：错题在列；回题库删除该原题；错题集里的该题快照仍可查看；
   勾选批量删除可清空。

## 已知限制

- **单 worker uvicorn（同线程）假设**：harness 的 `CheckpointStore` / `TrajectoryStore`
  用默认的 sqlite3 连接（`check_same_thread=True`），本 App 假设以单 worker、单
  事件循环线程运行，故这些连接可安全共享。若要多线程 / 多 worker 部署，需要另行
  处理跨线程 sqlite 访问（每线程独立连接或换连接池），否则会抛
  `sqlite3.ProgrammingError`。
- **`enable_sandbox` 打开时无并发互斥**：LocalSandbox 共享同一个 workspace，同一
  对话并发 `/api/chat` 之间没有互斥。单用户自用场景下可接受；多并发写同一
  workspace 可能相互覆盖。
