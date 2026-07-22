# app —— FastAPI 应用层

把 `src/harness`（作为库导入，零改动）装配成一个学习助手产品：网页多轮对话、实时看到 agent
干活、上传资料建知识库、出题考试、生成可下载的产物。`web/` 是配套的 React SPA。

架构总览见 [`../docs/architecture-app.md`](../docs/architecture-app.md)；本文按**功能域**讲
每块怎么跑、怎么验收、有什么已知限制。

- [运行](#运行)
- [聊天与编排](#聊天与编排)
- [知识库](#知识库)
- [记忆](#记忆)
- [题库与考试](#题库与考试)
- [下载产物](#下载产物)
- [安全与人工确认](#安全与人工确认)
- [测试](#测试)
- [已知限制](#已知限制)

## 运行

需要一个 `.env`（放在**仓库根目录**）配置真实聊天端点，至少：

```bash
HARNESS_API_KEY=sk-xxx
HARNESS_BASE_URL=https://api.openai.com/v1   # 或任意 OpenAI 兼容端点
HARNESS_MODEL=gpt-4o-mini
```

启动后端（读 `AppConfig` 的 `app_host` / `app_port`，默认 `127.0.0.1:8000`）：

```bash
uv run python -m app
```

也可直接用 uvicorn。**必须带 `--factory`**：`app.main` 不在模块级构造真实 harness，延迟到
uvicorn 调用工厂函数时才装配，避免空 api_key 在 import 期就抛错。

```bash
uv run uvicorn --factory app.main:create_app --reload
```

前端开发模式（已配 `/api` 代理到 `http://127.0.0.1:8000`）：

```bash
cd web && npm install && npm run dev
```

生产部署：先 `cd web && npm run build` 产出 `web/dist/`，`app/main.py` 会自动挂载静态文件到
`/`，后端单独一个进程即可。

可选开关（默认关闭，不配也不会报错）见根目录 [`.env.example`](../.env.example) 与
[README 的环境变量表](../README.md#环境变量)。

**数据存储**：应用领域所有表（用户、会话、消息、文档、题目、错题、下载、附件、待确认操作、
考试会话、摘要、个性化、网址登记）统一在**单个** `app.db` 里，DDL 集中于 `app/db.py`，
启动时 `migrate()` 幂等建表 + 旧库补列。向量在 `memory.db`，运行轨迹与检查点在 `harness.db`。

## 聊天与编排

`POST /api/chat`（SSE 流式）是主入口。每请求组装两样东西再交给编排器：

- **上下文**：系统提示 + 各类指引（考试/引用/日期/个性化）+ 会话历史 + 记忆。
- **每请求工具表**：把用户级工具（知识库、题库、考试、附件、下载）按 `user_id` 绑定后注册。
  这一步不能省——装配层注册的是不带用户前缀的全局版本，直接用会检索不到任何东西。

编排器（`app/orchestration/`）是**唯一主流程**，`app/assembly.py` 恒构建、无开关：

| 组件 | 职责 | 模型档位 |
| --- | --- | --- |
| triage | 判简单/复杂；纯寒暄由正则零成本短路 | 快速档 |
| `Planner` | 拆成 2-10 步 DAG，落地即校验，无效重试 2 次 | 主模型 |
| `Executor` | 并行跑就绪步，每步一个独立上下文的 `AgentLoop` | 快速档 |
| `Critic.validate` | 单步质检，不过则重试（上限 2 次）；能判 impossible 终结该步 | judge 档 |
| `Critic.review` | 终局把关，有缺口则重规划（上限 2 轮） | judge 档 |
| synthesize | 流式汇总最终答复 | 主模型 |

要点：

- 简单请求短路成单个全能力 `AgentLoop` 直答，承载绝大多数流量。
- 考试等**有状态交互**走 `force_simple` + 主模型：拆成多步再汇总会把「原样呈现下一题」的
  指令吞掉，且单步重试会重置考试进度。
- 执行子步看不到 `update_plan`（调它会覆盖总计划）；用户没明确要求入库时也看不到
  `save_to_knowledge`（避免自作主张往用户的资料库里塞东西）。
- 预算超限或某步重试耗尽不硬失败：未完成步标 skipped，带现有成果尽力汇总。
- **断点续传**：生成跑在后台任务里，刷新/重连可 attach 接回在途流；进程重启时启动对账把残留
  的 streaming 消息标为中断。

### 验收方式（手动）

1. 起后端 + 前端，注册登录。
2. 新建对话发「帮我算 (12+8)*3」：看到打字机效果、可展开的「调用工具 calculator」进度项、结果 60。
3. 再发一条：应能延续上一轮上下文。
4. 发一个复杂请求（如「帮我做一份 Python 入门学习计划并整理成笔记」）：顶部出现任务计划，
   步骤从 pending → running → done，最后汇总成一段答复。
5. 生成中刷新页面：应接回在途生成而不是丢失。
6. 侧栏新建/切换/删除对话应正常工作。

## 知识库

知识库页（前端 `/knowledge`）上传文件 → 解析正文 → 分块 embedding 写入 harness `Memory` 的
`knowledge:{user_id}` collection。agent 聊天时用 `search_knowledge` 检索。

- **支持格式**：`.pdf` / `.docx` / `.txt` / `.md`；其他扩展名返回 **400**。
- **上传上限**：默认 20MB（`HARNESS_APP_MAX_UPLOAD_MB`），超限返回 **413**。
- **需要 embedding 端点**：`HARNESS_API_KEY` / `HARNESS_EMBEDDING_API_KEY` 都没配时
  `build_harness` 不装配 `Memory`，`POST /api/documents` 返回 **503**，列表/查询仍可用（返回空）。
- **去重**：按**解析后正文**的 sha256 判重，同一份内容存成 `.txt` 和 `.md` 各传一次也拦得住。
- **按片段管理**：`GET /api/documents` 返回的是分页的**片段**（chunk）列表，支持分类筛选与
  `GET /api/documents/search` 语义搜索；删除也是按片段（`DELETE /api/documents/{chunk_id}`）。
- **检索条数**：`search_knowledge` 的 k 被夹到 **[10, 50]**——模型常自作主张传很小的 k（实测 3），
  几条片段覆盖不住知识库，回答就变成「资料里没提到」。默认值 `HARNESS_SEARCH_TOP_K=10`。
- **写入工具**：`save_to_knowledge` 供「存进知识库/收藏资料」这类明确请求；生成给用户看的
  成品文档应改用 `save_download`。

**不支持**：pptx / xlsx / html / epub、后台异步解析大文件、按文档过滤检索。

### 验收方式（手动）

1. 知识库页上传一个 txt/pdf：片段列表出现内容，点开抽屉能看到原文。
2. 回聊天页问该文档内容：agent 应通过 `search_knowledge` 召回并作答，回复下方标注来源。
3. 回知识库页删除相关片段：再问同样内容应召回不到。

## 记忆

**知识库和记忆是两回事**，对应两个工具，查的是两个 collection：

| 工具 | collection | 装什么 | 谁写进去的 |
| --- | --- | --- | --- |
| `search_knowledge` | `knowledge:{user_id}` | 用户上传/保存的资料，作答时可引用的依据 | 用户上传、`save_to_knowledge` |
| `search_memory` | `memory:{user_id}` | AI 关于这位用户的偏好、习惯、过往结论，跨会话有效 | `remember` |

collection 是构造方按用户注入的，**不是模型可传的参数**——模型不该也不能跨用户检索。
`search_knowledge` 空命中意味着「本轮没有可引用依据」，会驱动模型自纠正；`search_memory` 空
是常态（新用户本就没记过什么），不算失败，故不包每步校验。

另有后台的记忆自动提炼与整合（三类：语义/情景/程序），详见
[`../docs/memory-management.md`](../docs/memory-management.md)。

## 题库与考试

「出题 → 题库 → 模拟考试 → 错题集」闭环，**出题依赖知识库**，需先上传资料。

- **题型（4 种）**：单选、多选、判断、简答。
- **出题**：在**聊天里**让 AI 出题，它调 `generate_questions`——检索知识库 top-k → 单轮 LLM
  生成结构化题目 → 校验后入库。主题在知识库里查不到相关内容则报错，不硬编。
- **批量导入**：题库页上传题目文本，`POST /api/questions/import` 分块并发抽取成结构化题目
  （`HARNESS_IMPORT_CHUNK_CHARS` / `HARNESS_IMPORT_MAX_CONCURRENCY`）。
- **模拟考试**：也在聊天里进行。AI 调 `start_exam` 建考试会话（`exam_sessions` 表，一会话一条
  active），此后**每轮用户消息由服务端判分中间件拦截**：判分、（答错）确定性存错题集、推进
  游标、注入判定提示。判分与存错题**不依赖模型调用任何工具**，模型只负责讲解和呈现下一题。
  - `instant`（即时式）：每题当场公布对错与解析。
  - `graded`（打分式）：作答完毕才公布总分与逐题结果，中途不透露答案。
  - 前端可查 `GET /api/exam/status?conversation_id=...`，聊天页据此显示「考试中」标识。
- **判分规则**：客观题精确匹配；简答题由 LLM 对照参考答案打分，默认 ≥60 判对
  （`HARNESS_SHORT_PASS_SCORE`）。
- **错题集**：存**题目快照**（题干/选项/答案/解析），不依赖原题还在，故批量删题后仍可回看。
  同题去重：已有同一道题（题型 + 题干相同）的错题则用新数据替换旧的。
- **题库页**：筛选（题型/来源/关键词）、分页、导入、删除。单题删除时若该题在错题集里有对应
  错题，先返回 `{deleted: false, related_wrong: N}` 让前端弹窗确认，确认后带 `?force=true`
  **连带删除**那些错题；批量删除 `POST /api/questions/delete` 不动错题集。

### 验收方式（手动）

1. 知识库有资料后，在聊天里说「基于知识库出 5 道题」：题库页出现生成的题（题型/题干/来源）。
2. 在聊天里说「开始考试」：逐题作答，即时式应当场给对错与解析，打分式应答完才公布总分。
3. 故意答错后到错题集页：错题在列，点开能看到快照。
4. 回题库勾选该原题批量删除：错题集里的快照仍可查看（快照不依赖原题）。
   若改用单题删除，前端会提示「有 N 道对应错题」，确认后错题一并删除。

## 下载产物

让 agent 把整理好的成品存成可下载文件。

- **来源**：agent 调 `save_download(filename, content, encoding)`（应用核心工具，始终注册）。
  `content` 为文本；存图片等二进制时先 base64 编码并令 `encoding=base64`。
- **落盘**：按内部 id 存进 `downloads/`（原文件名仅作元数据，杜绝路径穿越），元数据入 `app.db`。
- **上限**：单文件超过 `HARNESS_DOWNLOAD_MAX_MB`（默认 25MB）时工具拒绝并返回提示。
- **去重**：按正文 sha256 判重——编排器的单步重试会把带副作用的工具原样再调一遍，不去重就会
  在消息下方冒出两个一模一样的下载按钮。
- **下载页**：列表（文件名/类型/大小/时间），图片显示缩略预览，「下载」取 `/api/downloads/{id}`，
  「删除」同时删磁盘文件与登记。
- **交付门**：开了结果校验的轮次，本轮生成的文件在校验完成前**不显示**。轮次开头（编排器
  真正跑起来之前）就下发一个「开门」信号，前端据此盖住下载按钮——`save_download` 发生在执行
  子步里，远早于「结果校验中…」那条事件（后者要等所有步骤跑完），不先发信号就会在校验还没
  开始时把文件亮出来。这条信号刻意**不落库**：落库会在用户中途停止时留下一条永远转圈的
  「生成中…」，刷新后由已存的终态记录决定展示即可。关校验的轮次不发信号，文件照常显示。
- **失败轮次的副作用清理**：校验不过触发重答时，失败那次产生的下载/入库/出题会被删除，并把
  已持久化步骤里的 `〔下载ID:...〕` 标记一并剥掉，不留指向已删文件的按钮。

### 验收方式（手动）

1. 聊天中说「把这段整理成 markdown 并保存为 note.md」：回答**交付后**才出现下载按钮，
   点「下载」得到内容。
2. 让助手保存一张 base64 图片：下载页显示缩略图；点「删除」后消失。

## 安全与人工确认

三道机制，都是「在造成后果之前拦住」，而不是事后补救。

### 1. 破坏性删除的两段式确认

`delete_questions` / `delete_wrong_answers` 这两个工具**不直接删任何东西**。它们先把 ids 解析
成真实存在且属于该用户的行，登记进 `pending_actions` 表（`status=pending`，默认 24 小时过期），
返回给模型的文本明说「未删除」，并带机读标记 `〔待确认:{id}〕`。前端据此渲染确认卡片。

真正的删除由 API 执行：

```
GET  /api/pending-actions[?conversation_id=...]
GET  /api/pending-actions/{id}
POST /api/pending-actions/{id}/confirm     # 执行
POST /api/pending-actions/{id}/reject
```

`confirm` 先做条件 UPDATE（`WHERE status='pending' AND expires_at > now`）拿到唯一执行权，
**再**执行删除——顺序是关键，反过来会重复删除。重复点击返回 **409**，过期同样 **409**。
所有路由按 `user_id` 收敛，别人的 id 读出来是 404。

### 2. 危险命令人工审核

`run_shell` 执行前用正则策略（`src/harness/shell/policy.py`）分类命令，命中即
`request_approval` 挂起、发 `ApprovalRequired` 事件给前端弹窗，用户经
`POST /api/chat/{run_id}/decision` 表态。

- **任何 `rm` 调用都要审核**——包括 `rm file.txt`、`/bin/rm x`、`find . | xargs rm`。
  规则刻意不匹配带 `-` 前缀的形式，所以 `docker run --rm` 不会误报。
  其余规则：`mkfs`、`dd of=/dev/`、fork bomb、`chmod -R 777 /`、`shred`、`curl | sh`、`sudo` 等。
- **超时**：`HARNESS_SANDBOX_APPROVAL_TIMEOUT` 默认 120 秒，超时**自动拒绝**。
- **拒绝后**：命令不执行，工具抛 `ToolError` 并明确告诉模型「系统状态未改变，不要声称已执行」，
  同时落一条红色的 `Progress`（刷新后仍在）。编排器把这一步判为**终态失败、不重试**——
  拒绝是人做出的决定，重跑不会有不同结果，只会把同一个弹窗再怼给用户一次。
- **同一命令不再重复弹窗**：被拒的 `(工具, 命令)` 记在本次 run 的 `denied` 集合里，再遇到直接
  自动拒绝，不发新提示。

注意这是给人看的绊线，不是安全边界——真正的边界是容器隔离。

### 3. 抓取防护

`http_request` 和 `browse` 两个工具被包了两层守卫（`app/url_blocklist.py`）：

- **保留域名拦截**：URL 指向 `example.com` / `.org` / `.net` / `.edu`、`localhost`、`127.0.0.1`、
  `0.0.0.0`（含子域）时，**在发请求之前**抛错，并提示模型改用搜索工具找真实来源。模型编造的
  网址常落在这些真实存在、恒返回 200 的域名上，靠状态码根本拦不住。
- **失败网址登记**：抓失败的网址按原因分级记进 `url_blocklist` 表，下次抓前短路。
  超时/连接失败/5xx/429 记 1 小时（按 URL）；404/410 记 30 天（按 URL）；401/403 记 7 天
  （按**域名**）；空正文/拦截页记 7 天（按 URL）。到期即失效，**不永久拉黑**；内网拦截
  （`PolicyError`）不记录。这张表全局共享不分用户——网址抓不抓得到是网站的属性，不是用户的属性。

另有 SSRF 防护（`HARNESS_HTTP_BLOCK_PRIVATE=true` 默认开，拦内网/元数据地址）与可选的域名
白名单 `HARNESS_HTTP_ALLOWED_DOMAINS`。

## 测试

```bash
uv run pytest -q              # 后端：全部用 MockModelClient，不打网络
cd web && npm run test        # 前端：Vitest
```

## 已知限制

- **单 worker uvicorn（同线程）假设**：harness 的 `CheckpointStore` / `TrajectoryStore` 用默认的
  sqlite3 连接（`check_same_thread=True`），本 App 假设以单 worker、单事件循环线程运行，故这些
  连接可安全共享。多线程/多 worker 部署需另行处理跨线程 sqlite 访问，否则会抛
  `sqlite3.ProgrammingError`。（应用领域的 `app.db` 不受此限，走 WAL + `check_same_thread=False`。）
- **沙箱按会话隔离**：`enable_sandbox` 打开时每会话惰性建一个基础容器（镜像
  `HARNESS_SANDBOX_IMAGE`，承载 shell/文件/http/browse 与工作区），删除会话即销毁，另有空闲
  驱逐安全阀（默认 30 分钟）。**跨会话已隔离**；但同一会话并发 `/api/chat` 共享该会话工作区、
  无互斥，并发写可能相互覆盖（单用户自用可接受）。
- **按语言/版本的一次性子沙箱**：配了 `HARNESS_SANDBOX_LANG_IMAGES`（语言[+版本]→镜像）后，
  `run_python` / `run_node` / `run_java` 按语言[+可选 `version`，如 java8/java21]另起子沙箱执行：
  执行前把会话工作区拷入，执行后把产物拷回，按 (会话, 语言) 缓存复用，空闲 1 小时才销毁。
  子沙箱默认禁网（`HARNESS_SANDBOX_SUB_NETWORK=none`，需 pip/maven 取包时置 `bridge`）。
  未配该项时代码在会话基础容器内直接执行（向后兼容）。
- **浏览器沙箱是全局共用一个**（跨会话），懒加载启动、复用，空闲 24 小时才销毁，避免每次重建
  Chromium 容器。镜像须含 Playwright + Chromium + curl，内存单列一档（默认 1g，沿用小额度会 OOM）。
- **跨层字符串契约**：知识库空命中的哨兵文案由内核常量 `NO_KNOWLEDGE_HIT`
  （`harness/tools/builtins/memory_search.py`）单点定义，`app/tools/validating.py` 与
  `app/verify.py` 均从此导入。改文案只需改这一处；`tests/test_sources.py` 有护栏禁止
  在生产代码里重抄该字面量（重抄会让 grounding 校验在内核改文案时静默失效）。
