# 数据表一览

系统的持久化分在三个 SQLite 文件里，各管一件事：

| 文件 | 职责 | 配置项 | 丢了会怎样 |
| --- | --- | --- | --- |
| `app.db` | 业务数据：账号、会话、题库、错题、产物 | `HARNESS_APP_DB_PATH` | 用户数据全失，不可恢复 |
| `memory.db` | 向量库：知识库与长期记忆 | `HARNESS_MEMORY_DB_PATH` | 知识库与记忆全失，需重新上传 |
| `harness.db` | 运行轨迹：事件流与断点 | `HARNESS_PERSISTENCE_DB_PATH` | 只丢统计与续传能力，业务不受影响 |

容器部署时三者都落在挂载卷 `/app/data` 下，见 [部署](DEPLOY.md)。

> 本文按表**职责**组织，回答「这张表存在是为了解决什么问题」。
> 需要逐字段说明见 **[数据表字段明细](data-model-fields.md)**；
> 而字段会变，最终以 `app/db.py` 的建表语句为准，或直接 `sqlite3 app.db ".schema <表名>"`。

---

## app.db — 业务数据

### 账号与会话

| 表 | 描述 |
| --- | --- |
| `users` | 账号。用户名唯一，口令按 salt 加盐哈希存储，不存明文 |
| `user_profiles` | 学习者画像：身份、目标、讲解偏好、语气、备注。每用户一行，注入系统提示词以定制回答风格 |
| `conversations` | 会话列表：标题、归属用户、创建时间。标题由快速档模型自动生成 |
| `conversation_messages` | 每条消息的全部内容。**这是最宽的一张表**，除正文外还存该轮的完整可复现状态：<br>工具调用与结果、进度事件流（`progress`，前端据此重建任务步骤块与工具块）、参考来源、思考链与其耗时、校验留痕（`verify`）、上下文组装结果（`context`）、tokens / 成本 / 耗时、附件、所属 run、状态 |
| `conversation_runs` | 会话与「运行」的对应关系。一次生成即一个 run，断点续传与运行统计据此归集 |
| `conversation_summaries` | 长对话的滚动摘要，记录已摘要到第几条（`up_to_seq`）。仅 `layered` 上下文策略使用，见 [上下文管理](context-management.md) |

### 知识库与产物

| 表 | 描述 |
| --- | --- |
| `documents` | 知识库文档的元信息：文件名、大小、切了多少块、块 id 列表、摘录、内容哈希。**正文与向量不在这里**，在 `memory.db`；本表只是索引与去重依据（`content_hash`） |
| `attachments` | 用户在聊天里上传的附件。与知识库文档不同：附件只服务于当轮对话，不入库检索 |
| `downloads` | AI 生成的可下载文件（`save_download` 产出）。`content_hash` 用于去重，避免同一份内容重复占位 |

### 题库与考试

| 表 | 描述 |
| --- | --- |
| `questions` | 题库：题型、题干、选项、答案、解析、来源。可由 AI 基于知识库生成，也可文本批量导入 |
| `exam_sessions` | 进行中的考试：题目列表、当前游标、逐题结果、状态。**游标与判分由服务端托管**，不依赖模型自觉——模型只负责讲解与呈现 |
| `wrong_answers` | 错题集。存的是**题目快照**而非外键引用：原题日后被改被删，错题记录仍完整可复习 |

### 机制类

| 表 | 描述 |
| --- | --- |
| `pending_actions` | 待用户确认的破坏性操作（删题库、删错题等）。AI 只登记，用户在前端点确认后才真正执行；带过期时间，状态转移是原子的，保证不会重复执行 |
| `url_blocklist` | 抓取失败的网址登记。按失败原因分级 TTL（1 小时 ~ 30 天）而非永久拉黑，下次抓前短路让模型换来源；`hits` 记录累计命中次数 |

---

## memory.db — 向量库

同一套表按 `owner_id` + `kind` 分区，承载三类互不串味的数据：

- `knowledge:{user_id}` —— 用户上传/保存的资料，`search_knowledge` 查这里，是回答的可引用依据
- `memory:{user_id}` —— AI 用 `remember` 记下的长期结论与偏好，`search_memory` 查这里
- `conversation:{conv_id}` —— 分层上下文的 L3 检索层，自动注入，无对应工具

| 表 | 描述 |
| --- | --- |
| `memory_records` | 记录正文与全部元数据：归属、类型、实体键、版本、是否被取代（`superseded`）、重要度、过期时间、访问计数、来源 |
| `memory_vec` | sqlite-vec 虚拟表，存嵌入向量并按 `owner_id` 分区。向量检索走这里，返回**真实 L2 距离**（向量已归一化） |
| `memory_fts` | FTS5 全文索引（trigram 分词），支撑关键词召回。与向量召回经 RRF 融合，见 [检索](rag-retrieval.md) |

> `memory_vec_*` / `memory_fts_*` 开头的其余表由 sqlite-vec 与 FTS5 扩展自动创建维护（分块、行号映射、倒排索引等），**不要直接读写**。

**旧格式（仅迁移时出现）**：`memory_items`、`memory_vectors`、`_memory_migrations` 属于早期
schema，只有从老库升级时才会被 `harness/memory/migrate.py` 读到。新部署不会创建它们；
若在现有库里看到，说明这个库来自升级前，迁移标记记在 `_memory_migrations` 里。

---

## harness.db — 运行轨迹

| 表 | 描述 |
| --- | --- |
| `trajectory_events` | 每次运行的完整事件流，按 `(run_id, seq)` 有序。首页「AI 运行统计」的用量、成本、延迟、工具调用分布全部由此聚合 |
| `checkpoints` | 运行断点：状态快照与已执行步数。服务重启后据此判定中断，前端显示「已中断」标记 |

---

## 几条贯穿设计

**按用户隔离是硬约束。** 业务表带 `user_id`，向量库靠 `owner_id` 分区且 `search_knowledge` 的 collection 由服务端按登录用户注入——模型不能也无法跨用户检索。

**快照优于外键。** 错题存题目快照、下载存内容哈希：被引用的原始对象日后变了或没了，历史记录依然自洽。

**副作用要先登记再执行。** 破坏性操作一律经 `pending_actions` 过一道用户确认，不给模型直接下手的机会。

**失败信息也是数据。** `url_blocklist` 记的是「哪些来源抓不到、为什么、多久后可再试」——它让下一轮少走弯路，而不是每次重蹈覆辙。
