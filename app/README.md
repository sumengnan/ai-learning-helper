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
HARNESS_ENABLE_BROWSER=false     # true 则注册 browse 工具；若同时启用沙箱则在容器内跑 Chromium（镜像须含 Playwright+curl、可出网、非只读），否则回退宿主 Playwright
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

## 下载管理（App-4）

让 agent 把聊天中整理好的产物存成可下载文件。

- **来源**：agent 调用 `save_download(filename, content, encoding)` 工具（应用核心工具，始终注册）。
  `content` 为文本内容；保存图片等二进制时先 base64 编码并令 `encoding=base64`。文件按
  内部 id 落 `downloads/` 目录（原文件名仅作元数据，杜绝路径穿越），元数据登记在 `downloads.db`。
- **上限**：单文件超过 `download_max_mb`（默认 25MB）时工具拒绝并返回提示。
- **下载页**：列表（文件名 · 类型 · 大小 · 时间）；图片类型显示缩略预览（轻量图库）；「下载」
  链接直接取 `/api/downloads/{id}`；「删除」同时删磁盘文件与登记。

### 验收方式（手动，接前）

9. 聊天中让助手「把这段整理成 markdown 并用 save_download 保存为 note.md」：下载页出现该文件，
   点「下载」得到内容；让助手保存一张 base64 图片：下载页显示缩略图；点「删除」后消失。

## 已知限制

- **单 worker uvicorn（同线程）假设**：harness 的 `CheckpointStore` / `TrajectoryStore`
  用默认的 sqlite3 连接（`check_same_thread=True`），本 App 假设以单 worker、单
  事件循环线程运行，故这些连接可安全共享。若要多线程 / 多 worker 部署，需要另行
  处理跨线程 sqlite 访问（每线程独立连接或换连接池），否则会抛
  `sqlite3.ProgrammingError`。
- **`enable_sandbox` 打开时的沙箱模型**：容器按会话（`conversation_id`）隔离——每会话
  惰性建一个基础容器（镜像 = `HARNESS_SANDBOX_IMAGE`，承载 shell/文件/http/browse 与
  工作区），删除会话即销毁、并有空闲驱逐安全阀。**跨会话已隔离**；但同一会话并发
  `/api/chat` 仍共享该会话基础工作区、无互斥，并发写可能相互覆盖（单用户自用可接受）。
- **按语言/版本的一次性子沙箱**：配了 `HARNESS_SANDBOX_LANG_IMAGES`（语言[+版本]→镜像）后，
  `run_python/run_node/run_java` 会按语言[+可选 `version`，如 java8/java21]另起一次性子沙箱
  执行：执行前把会话基础工作区拷入子沙箱，执行后把产物拷回基础工作区，跑完即销毁子沙箱。
  子沙箱默认禁网（`HARNESS_SANDBOX_SUB_NETWORK=none`，需 pip/maven 取包时置 `bridge`）。
  未配该配置时代码仍在会话基础容器内直接执行（向后兼容）。基础容器要跑 Java 等，
  建议把 `HARNESS_SANDBOX_IMAGE` 设为含 curl/chromium 的 ubuntu 基础镜像。
