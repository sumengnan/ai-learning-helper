# 数据表字段明细

各表逐字段说明。表的**职责**与三个库的分工见 [数据表一览](data-model.md)。

> 字段会随功能演进增删，本文可能落后于代码。**建表语句才是真相**：
> `app/db.py` 的 `migrate()`、`harness/memory/sqlite_backend.py`、
> `harness/persistence/{trajectory,checkpoint}.py`。
> 想看某表当前的确切结构：`sqlite3 app.db ".schema <表名>"`。
>
> 约定：时间戳除特别说明外均为 ISO 8601 字符串；标注 *JSON* 的列存 JSON 文本。

---

## app.db

### users —— 账号

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | 用户 id |
| `username` | TEXT UNIQUE | 登录名，唯一 |
| `password_hash` | TEXT | 加盐后的口令哈希，**不存明文** |
| `salt` | TEXT | 每用户独立的盐 |
| `created_at` | TEXT | 注册时间 |

### user_profiles —— 学习者画像

每用户一行，内容会注入系统提示词以定制回答风格。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `user_id` | TEXT PK | 用户 id |
| `identity` | TEXT | 身份，如「大二学生」「后端工程师」 |
| `goal` | TEXT | 学习目标 |
| `explain_prefs` | TEXT | 讲解偏好，如「多举例」「少用术语」 |
| `tone` | TEXT | 语气偏好 |
| `notes` | TEXT | 其它备注 |
| `updated_at` | TEXT | 最后更新时间 |

### conversations —— 会话

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | 会话 id |
| `user_id` | TEXT | 归属用户 |
| `title` | TEXT | 标题，由快速档模型据首轮内容自动生成 |
| `created_at` | TEXT | 创建时间 |

### conversation_messages —— 消息与整轮状态

主键 `(conv_id, seq)`。**全库最宽的一张表**：除正文外还存下重放该轮所需的全部状态，
刷新页面后聊天记录里的任务步骤块、工具明细、来源、耗时全靠它复原。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `conv_id` | TEXT PK | 所属会话 |
| `seq` | INTEGER PK | 会话内序号，从 0 递增 |
| `role` | TEXT | `user` / `assistant` / `tool` |
| `content` | TEXT | 消息正文 |
| `tool_calls` | TEXT *JSON* | 本条发起的工具调用 |
| `tool_call_id` | TEXT | 工具结果消息对应的调用 id |
| `steps` | TEXT *JSON* | 扁平工具步骤列表，前端「工具调用」块的数据源 |
| `progress` | TEXT *JSON* | 完整进度事件流。前端据此重建任务步骤块：`scope="plan"` 的条目里步骤**带 `id` 即编排器计划**（工具明细挂到步下合并成树），无 id 则是 ReAct 清单（工具块独立显示） |
| `sources` | TEXT *JSON* | 参考来源列表，正文里的 `[n]` 角标指向它 |
| `verify` | TEXT *JSON* | 结果校验留痕：`{attempts, retries, ok, degraded, gate_error, history:[{failed:[层]}]}`。首页「回答质量」由此聚合；`gate_error=true` 表示校验器自身故障、本轮的「通过」不代表真校验过 |
| `context` | TEXT *JSON* | 上下文组装结果（策略、历史条数、裁剪情况），供统计与排查 |
| `reasoning` | TEXT | 思考链正文 |
| `reasoning_ms` | INTEGER | 思考耗时（毫秒） |
| `attachments` | TEXT *JSON* | 本条携带的附件 id 列表 |
| `run_id` | TEXT | 所属运行，关联 `conversation_runs` 与 `harness.db` 的轨迹 |
| `status` | TEXT | 该轮结局：正常完成 / 用户停止 / 服务重启中断 |
| `tokens` | INTEGER | 本轮 token 合计 |
| `cost` | REAL | 本轮成本估算（按分层计费表算出） |
| `elapsed_ms` | INTEGER | 本轮总耗时 |
| `created_at` | TEXT | 落库时间 |

### conversation_runs —— 会话与运行的对应

主键 `(conv_id, run_id)`。一次生成即一个 run；断点续传与运行统计据此归集。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `conv_id` | TEXT PK | 会话 id |
| `run_id` | TEXT PK | 运行 id |
| `created_at` | TEXT | 发起时间 |

### conversation_summaries —— 滚动摘要

仅 `layered` 上下文策略使用，见 [上下文管理](context-management.md)。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `conv_id` | TEXT PK | 会话 id，每会话一行 |
| `up_to_seq` | INTEGER | 已摘要到第几条消息；新消息超出后增量续摘 |
| `summary` | TEXT | 摘要正文 |
| `tokens` | INTEGER | 摘要占用 token，计入预算 |
| `created_at` | TEXT | 生成时间 |

### documents —— 知识库文档元信息

**正文与向量不在这里**，在 `memory.db`；本表是索引与去重依据。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | 文档 id |
| `user_id` | TEXT | 归属用户 |
| `filename` | TEXT | 文件名（文本保存时为条目标题） |
| `size` | INTEGER | 字节数 |
| `num_chunks` | INTEGER | 切分块数 |
| `chunk_ids` | TEXT *JSON* | 各块在 `memory_records` 中的 id，删除文档时据此清理向量 |
| `excerpt` | TEXT | 摘录，列表页预览用 |
| `content_hash` | TEXT | 内容哈希，同一份资料重复入库时据此去重 |
| `uploaded_at` | TEXT | 入库时间 |

### attachments —— 聊天附件

只服务于当轮对话，**不入知识库检索**。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | 附件 id |
| `user_id` / `conv_id` | TEXT | 归属用户 / 会话 |
| `filename` | TEXT | 原文件名 |
| `size` | INTEGER | 字节数 |
| `content_type` | TEXT | MIME 类型 |
| `created_at` | TEXT | 上传时间 |

### downloads —— AI 生成的可下载文件

`save_download` 的产出。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | 下载 id，正文里以 `〔下载ID:x〕` 标记，前端据此渲染下载按钮 |
| `user_id` | TEXT | 归属用户 |
| `filename` | TEXT | 文件名 |
| `size` | INTEGER | 字节数 |
| `content_type` | TEXT | 由文件名推断的 MIME |
| `seq` | INTEGER | 列表排序用序号 |
| `content_hash` | TEXT | 内容哈希，避免同一份内容重复占位 |
| `created_at` | TEXT | 生成时间 |

### questions —— 题库

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | 题目 id，正文里以 `〔题目ID:x,y〕` 标记 |
| `user_id` | TEXT | 归属用户 |
| `type` | TEXT | 题型：单选 / 多选 / 判断 / 简答 |
| `stem` | TEXT | 题干 |
| `options` | TEXT *JSON* | 选项列表；简答题为空 |
| `answer` | TEXT | 标准答案 |
| `explanation` | TEXT | 解析 |
| `source` | TEXT | 来源：基于知识库生成 / 批量导入 |
| `created_at` | TEXT | 入库时间 |

### exam_sessions —— 进行中的考试

每会话至多一场进行中的考试（`conversation_id` 为主键）。
**游标与判分由服务端托管**，不依赖模型自觉。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `conversation_id` | TEXT PK | 所在会话 |
| `user_id` | TEXT | 考生 |
| `mode` | TEXT | 考试模式 |
| `questions` | TEXT *JSON* | 本场题目快照，开考时固定，中途改题库不影响 |
| `cursor` | INTEGER | 当前进行到第几题，**服务端推进** |
| `results` | TEXT *JSON* | 逐题作答与判分结果 |
| `status` | TEXT | `active` 进行中 / `ended` 已结束 |
| `created_at` / `updated_at` | TEXT | 开考 / 最后更新时间 |

### wrong_answers —— 错题集

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | 错题记录 id |
| `user_id` | TEXT | 归属用户 |
| `question_id` | TEXT | 原题 id，**仅作追溯线索** |
| `exam_id` | TEXT | 来自哪场考试 |
| `snapshot` | TEXT *JSON* | **题目快照**（题干、选项、答案、解析）。存快照而非外键：原题日后被改被删，错题仍完整可复习；同题去重也按快照里的题型 + 题干判定 |
| `user_answer` | TEXT | 用户当时的作答 |
| `seq` | INTEGER | 列表排序用序号 |
| `created_at` | TEXT | 记录时间 |

### pending_actions —— 待确认的破坏性操作

AI 只登记，用户在前端点确认后才真正执行。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | 待确认项 id，正文里以 `〔待确认:x〕` 标记 |
| `user_id` | TEXT NOT NULL | 归属用户；确认时必须同时匹配，防越权 |
| `conv_id` | TEXT | 发起会话 |
| `kind` | TEXT NOT NULL | 操作类型，如删题库 / 删错题 |
| `payload` | TEXT *JSON* NOT NULL | 执行所需参数（目标 id 列表等） |
| `status` | TEXT NOT NULL | 待确认 / 已确认 / 已取消 / 已过期 |
| `created_at` | TEXT NOT NULL | 登记时间 |
| `expires_at` | TEXT NOT NULL | 过期时间，逾期不可再确认 |
| `decided_at` | TEXT | 用户决定的时间 |

> 确认走的是一条**带条件的 UPDATE**（同时校验 id、user_id、status、未过期），
> 靠 `rowcount` 判断是否抢到——这是「不会重复执行」的唯一保证，别绕过它直接改 status。

### url_blocklist —— 抓取失败登记

按失败原因分级 TTL（1 小时 ~ 30 天）而非永久拉黑。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `key` | TEXT PK | 归一化后的网址 |
| `scope` | TEXT NOT NULL | 登记粒度，当前为 `url` |
| `reason` | TEXT NOT NULL | 失败原因，如「HTTP 404（页面不存在）」「抓取出错：ConnectError」 |
| `status` | INTEGER | HTTP 状态码；网络层错误时为空 |
| `until` | TEXT NOT NULL | 短路到何时；到期后可再试 |
| `hits` | INTEGER NOT NULL | 累计命中次数，重复失败时递增 |
| `created_at` / `updated_at` | TEXT NOT NULL | 首次 / 最近一次登记时间 |

---

## memory.db

同一套表按 `owner_id` + `kind` 分区，承载知识库、AI 长期记忆、会话检索层三类数据，
分区规则见 [数据表一览](data-model.md#memorydb--向量库)。

### memory_records —— 记录正文与元数据

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rowid` | INTEGER PK | 与 `memory_vec` 对齐的行号 |
| `id` | TEXT UNIQUE | 记录 id |
| `owner_id` | TEXT | 归属（用户 id 或会话 id），**跨用户隔离靠它** |
| `kind` | TEXT | `knowledge` / `memory` / `conversation` |
| `mem_type` | TEXT | 记忆类型：语义 / 情景 / 程序 |
| `text` | TEXT | 正文（知识库为切分后的一块） |
| `entity_key` | TEXT | 实体键，供按实体精确召回；无则为空串 |
| `version` | INTEGER | 版本号，记忆被更新时递增 |
| `superseded` | INTEGER | 是否已被新版取代；取代的旧记忆不参与检索但保留可追溯 |
| `importance` | REAL | 重要度，默认 0.5，参与排序加权 |
| `created_at` | TEXT | 写入时间，参与时效性加权 |
| `last_accessed_at` | TEXT | 最后命中时间 |
| `access_count` | INTEGER | 累计命中次数 |
| `expires_at` | INTEGER | 过期时间戳，0 表示不过期；按类型设不同 TTL |
| `source` | TEXT | 来源标记 |
| `metadata` | TEXT *JSON* | 附加元数据，如原文件名（检索结果里的「来源」） |

### memory_vec —— 向量索引（sqlite-vec 虚拟表）

按 `owner_id` 分区。检索返回**真实 L2 距离**（向量已归一化，故 `cos = 1 - d²/2`）。

| 字段 | 说明 |
| --- | --- |
| `owner_id` | 分区键 |
| `kind` / `mem_type` / `superseded` / `expires_at` | 与 `memory_records` 同义，冗余在此供**查询期过滤**，避免先取回再筛 |
| `embedding` | `float[N]`，N 由 `HARNESS_EMBEDDING_DIMENSION` 决定 |

### memory_fts —— 全文索引

FTS5 + trigram 分词，支撑关键词召回；与向量召回经 RRF 融合，见 [RAG 检索](rag-retrieval.md)。

> `memory_vec_*` / `memory_fts_*` 开头的其余表由扩展自动维护，**不要直接读写**。

---

## harness.db

### trajectory_events —— 运行事件流

主键 `(run_id, seq)`。首页「AI 运行统计」的用量、成本、延迟、工具分布全部由此聚合。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_id` | TEXT PK | 运行 id，对应 `conversation_runs.run_id` |
| `seq` | INTEGER PK | 运行内序号 |
| `type` | TEXT | 事件类型（模型用量、工具调用、进度、结束等） |
| `data` | TEXT *JSON* | 事件负载。模型用量事件里的 `latency_ms` 为 0 表示上报方未测（如 embedding/rerank），统计时会被剔除，不参与均值与 p95 |
| `created_at` | TEXT | 事件时间 |

### checkpoints —— 运行断点

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_id` | TEXT PK | 运行 id |
| `state` | TEXT *JSON* | 状态快照，供断点续传 |
| `step` | INTEGER | 已执行步数 |
| `updated_at` | TEXT | 最后写入时间；服务重启后据此判定中断，前端显示「已中断」 |
