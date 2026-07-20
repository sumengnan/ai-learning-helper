# 记忆管理

> 面向第一次接触本项目的人。读完你会明白:AI 是怎么"记住"东西、又怎么在需要时"想起来"的,
> 以及**哪些东西算记忆、哪些不算**。

## 一句话理解

普通的聊天机器人是"金鱼记忆"——一轮对话结束就忘光。本项目给 AI 装了一套**长期记忆**:
它能把值得记的东西存下来,以后遇到相关问题时自动"想起来"。

这套记忆借鉴了人的记忆分类,并且会**自己整理**:零散的经验会被归纳成结论、过时的会被清理、
互相矛盾的会被更新。

## 先划清边界:三个都用向量库,但不是一回事

本项目有三处都建立在同一套向量设施上,新读者最容易混淆的就是它们。先记住这张表:

| | 装的是什么 | 谁写进去 | 怎么取出来 | 能当作答依据吗 |
| --- | --- | --- | --- | --- |
| **知识库** `knowledge:<用户>` | 用户上传/保存的**资料** | 用户上传、`save_to_knowledge` | AI 调 `search_knowledge` | ✅ 是,交付门 grounding 只认它 |
| **长期记忆** `memory:<用户>` | AI 记下的**偏好/结论** | AI 调 `remember` | AI 调 `search_memory` | ❌ 不是事实依据 |
| **对话记忆** `conversation:<会话>` | 本会话的历史内容 | 每轮结束后台自动写 | **无工具**,由上下文自动注入 | ❌ |

三条最关键的结论:

1. **`search_knowledge` 和 `search_memory` 是两个独立工具、查两个不相交的 scope。**
   查资料只能用前者,查 AI 自己记的偏好只能用后者,互相查不到。
2. **`remember` 写入的是 `memory:<用户>`,正好与 `search_memory` 成对。**
   (历史上二者 scope 不相交,存进去的东西没有任何路径能召回——已修复。)
3. **对话记忆没有对应的工具。** 模型无法"搜索对话历史",更早的对话是由
   [上下文管理](context-management.md) 的 L3 层按当前问题检索后**直接注入上下文**的。
   别指望 `search_memory` 能查到上一轮说过什么。

知识库那条链路(上传 → 解析 → 切块 → 检索 → 引用)详见 [RAG 检索](rag-retrieval.md);
本文只讲记忆。

## 三类记忆(像人一样)

| 类型 | 通俗说法 | 例子 |
| --- | --- | --- |
| **语义记忆**(semantic) | "我知道的事实" | 用户偏好用中文、用户在准备考研 |
| **情景记忆**(episodic) | "发生过的事" | 上次帮用户整理了一份线性代数笔记 |
| **程序记忆**(procedural) | "学会的做法" | 出这类题时先检索知识库再生成 |

分型定义在 `MemType`(`src/harness/memory/record.py`)。有一点值得注意:

> **只有"智能写入"这条路径才会产出 episodic / procedural。**
> `Memory.add_texts` 把 `mem_type` 写死为 `SEMANTIC`——也就是说 `remember` 写的、
> 知识库入库的,统统是 semantic。而记忆整合(下文)只吃 episodic,所以关掉智能写入,
> 整合就永远空转。

## 记忆从哪来:两条写入路径

### 路径一:主动写(`remember` 工具)

AI 判断某件事值得长期记住时,调用 `remember`,文本经切块 + 向量化后落入 `memory:<用户>`。
简单直接,没有提炼、没有去重判断——写什么存什么。

### 路径二:智能写入(从对话自动提炼)

开启后(`HARNESS_MEMORY_WRITE_EXTRACT`,默认开),**每轮对话结束后在后台**跑一条流水线:

```
提炼(extract) → 找相似的老记忆(gather) → 调和(reconcile) → 应用(apply)
```

- **提炼**:让 LLM 从这轮对话里挑出真正值得长期记的事实(忽略寒暄、一次性内容),
  并判类型(semantic/episodic/procedural)、抽实体键、打 0~1 的重要性分。
- **找候选**:对每条新事实,按实体键精确取 + 语义检索,凑出可能与之冲突/重复的老记忆。
- **调和**:让 LLM 逐条判定——
  - `ADD` 全新信息 → 存进去;
  - `NOOP` 已经记过了 → 不重复存;
  - `REPLACE` 是旧记忆的更新或与之矛盾 → 存新的,并把被取代的旧记忆标 `superseded`、
    新记录版本号 +1。
- **应用**:重新向量化后入库,`source="extract"`,按类型套 TTL。

> ⚠️ **重要且反直觉:智能写入的产物落在 `conversation:<会话id>`,不是 `memory:<用户>`。**
> 它服务的是"长对话不失忆"(L3 语义召回),按会话隔离、跨会话不共享。
> 想让某件事跨会话生效,得靠 `remember` 主动写进 `memory:<用户>`。

两步刻意用了不同档位的模型:**提炼**是机械活,走快速模型档;**调和**是判断题且后果不可逆
(判 REPLACE 会永久作废旧记忆,判错不是省钱是毁数据),留在主模型。

整条流水线是 best-effort 的:LLM 输出不是合法 JSON 就退化(提炼失败→本轮不写;
调和失败→全部按 ADD),但都会打 warning 日志,不会无声停工。它跑在答案交付**之后**的
后台任务里,不拖慢首字,也不阻塞用户发下一句。

## 记忆怎么被"想起来":语义检索

记忆不是靠关键词精确匹配,而是靠**语义相似**——把文字变成一串数字向量,找"意思相近"的。
所以问"怎么学好高数",能召回一条写着"用户在准备考研数学"的记忆,哪怕字面不一样。

检索走的是与知识库**完全同一条**流水线(`Retriever.retrieve`):多路召回 → RRF 融合 →
加权打分(相关度 / 新近度 / 重要性)→ MMR 去冗余 → 可选重排 → 取 top-k。
细节见 [RAG 检索](rag-retrieval.md#阶段二检索把最相关的片段找出来),此处不重复。

与知识库检索唯一的差别在**取几条**:

| 工具 | 默认 k | 夹取范围 | 为什么 |
| --- | --- | --- | --- |
| `search_knowledge` | `HARNESS_SEARCH_TOP_K`(10) | **[10, 50]** | 它是作答依据,片段太少会把本来有依据的问题答成"资料里没有"。模型实测会自作主张传 k=3,故设**下限 10** 兜底——显式传小值会被抬上来 |
| `search_memory` | `HARNESS_SEARCH_TOP_K`(10) | [1, 50] | 记忆是辅助信号,不设下限;模型想只取一两条就随它 |

上限 50 两者共用,防止把上下文撑爆。

## 记忆会自我整理

记忆不是只进不出,`MemoryMaintainer` 负责按需维护:

- **整合(consolidation)**:当某个会话攒够 `HARNESS_MEMORY_CONSOLIDATE_AFTER`(默认 20)条
  episodic,后台就把**语义相近的一簇**(余弦均值 ≥ 0.85、簇内至少 2 条)交给 LLM 蒸馏成
  **一条**稳定的语义事实,原簇标记 `superseded`。好比把一周的零散笔记归纳成一句结论。
  整合后 episodic 计数回落,所以不会每轮重触发。
- **过期清理(TTL)**:按类型给记忆设保质期(默认全为 0 = 永不过期),过期的自动剔除。
  TTL 只在智能写入落库时套用。
- **矛盾与版本**:被取代的旧记忆**不物理删除**,而是标 `superseded=1` 并让新记忆版本号 +1,
  留痕可追溯;检索默认不返回已取代的条目。

整合是 fire-and-forget 的后台任务,失败只记日志——它若 await 会占住并发守卫,让用户的
下一句直接吃 409。写入 → 整合按顺序串起来,保证"先写 episodic 再按数量整合"的因果。

> 另有一个**手动**入口:"整理相似偏好"(`consolidate_semantic`),把同主题的多条 **semantic**
> 合并成一条,由用户在界面触发,不自动跑。

## AI 能用的记忆工具

| 工具 | 作用 | scope |
| --- | --- | --- |
| `remember` | 把一段信息写入 AI 的私有长期记忆 | `memory:<用户>` |
| `search_memory` | 检索 AI 自己记下的偏好/结论,**不含**用户资料 | `memory:<用户>` |
| `search_knowledge` | 检索用户知识库(上传/保存的资料),作答可引用的依据 | `knowledge:<用户>` |
| `save_to_knowledge` | 把一段内容作为正式文档存入用户知识库 | `knowledge:<用户>` |
| `recall_episodes` | 检索过往相似任务的经验(做法与成败)来参考 | `episodes`(全局) |

几点容易踩的:

- **`recall_episodes` 查的是全局 `episodes` 集合**,不按用户隔离,与前面几个 scope 无关。
- **没有 `record_episode` 工具可用**。该工具类存在于代码中(`episode_tools.py`),但**未被注册**,
  所以经验记录目前没有主动写入入口。
- `remember` / `search_memory` / `recall_episodes` 被**排除在编排器的规划清单之外**
  (`_PLANNER_HIDDEN`):它们是服务 AI 自身的内部机制,不该被规划成用户可见的任务步骤。
  执行子步仍然握有这些工具,需要时自行调用。

## 存在哪、怎么隔离

- 底层是 **SQLite + sqlite-vec**,默认存在 `memory.db`。向量走 `vec0` 虚拟表,
  正文与元数据走 companion 数据表(rowid 对齐),关键词检索走 **FTS5 trigram** 索引。
- 每条记忆带 `owner_id`(归属谁)和 `kind`(哪类集合)。collection 字符串
  `"<kind>:<owner>"` 会被 `collection_to_scope` 拆成这两者;不带冒号的则归到
  `owner_id="_global"`。
- 隔离就靠这两个字段:`knowledge:<用户A>`、`memory:<用户A>`、`conversation:<会话id>`、
  `episodes` 互不串扰。检索时 `MemoryFilter` 恒带 `owner_id` + `kind`,
  且 **collection 刻意不做成模型可传的参数**——scope 由服务端按当前用户注入,
  模型不该也不能跨用户检索。
- 交付门的 grounding 校验**只认 `search_knowledge` 的命中**:AI 自己记的偏好、对话历史
  都不构成事实依据。
- 文字入库前会**切块(chunk)**,详见 [RAG 检索](rag-retrieval.md#阶段一把资料存进知识库)。

## 常用配置

均为 `HARNESS_` 前缀环境变量(定义见 `src/harness/config.py` 与 `app/config.py`,
示例见 [`.env.example`](../.env.example)):

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `HARNESS_EMBEDDING_MODEL` | `text-embedding-3-small` | 把文字转成向量的模型 |
| `HARNESS_EMBEDDING_DIMENSION` | `1536` | 向量维度(要和模型匹配) |
| `HARNESS_MEMORY_DB_PATH` | `memory.db` | 向量库文件路径 |
| `HARNESS_MEMORY_COLLECTION` | `knowledge` | 知识库默认 collection 名 |
| `HARNESS_SEARCH_TOP_K` | `10` | 两个检索工具的默认返回条数 |
| `HARNESS_MEMORY_WRITE_EXTRACT` | `true` | 是否开启"从对话智能提炼记忆" |
| `HARNESS_MEMORY_WRITE_SAMPLE_RATE` | `1.0` | 智能写入的采样率(1.0=每轮都写) |
| `HARNESS_MEMORY_WRITE_CANDIDATE_K` | `5` | 调和时每条新事实检索多少条老记忆做候选 |
| `HARNESS_MEMORY_CONSOLIDATE_AFTER` | `20` | 会话 episodic 攒到这么多条就触发整合;0=关 |
| `HARNESS_TTL_EPISODIC_DAYS` | `0` | 情景记忆保质期(天);0=永不过期 |
| `HARNESS_TTL_SEMANTIC_DAYS` | `0` | 语义记忆保质期(天);0=永不过期 |
| `HARNESS_TTL_PROCEDURAL_DAYS` | `0` | 程序记忆保质期(天);0=永不过期 |
| `HARNESS_CONSOLIDATION_SIM_THRESHOLD` | `0.85` | 聚类的平均余弦相似度阈值 |
| `HARNESS_CONSOLIDATION_MIN_CLUSTER` | `2` | 成簇的最小成员数 |
| `HARNESS_CONSOLIDATION_MAX_SOURCE` | `200` | 单次整合处理的记录上限 |
| `HARNESS_EPISODE_COLLECTION` | `episodes` | 任务经验的 collection 名 |
| `HARNESS_EPISODE_RECALL_K` | `3` | `recall_episodes` 默认返回条数 |
| `HARNESS_CHUNK_SIZE` / `HARNESS_CHUNK_OVERLAP` | `1000` / `200` | 入库切块大小与重叠 |

检索打分与重排相关的配置项,见 [RAG 检索的配置表](rag-retrieval.md#常用配置)——两边共用同一套。

## 代码位置

| 文件 | 职责 |
| --- | --- |
| `src/harness/memory/record.py` | `MemType` 三分类、`MemoryRecord` / `MemoryFilter` 数据结构 |
| `src/harness/memory/memory.py` | `Memory` 门面(切块+向量化+检索统一入口)、`collection_to_scope` |
| `src/harness/memory/writer.py` | 智能写入:提炼 → 找候选 → 调和 → 应用 |
| `src/harness/memory/retriever.py` | 语义检索:多路召回 → RRF → 加权 → MMR → 重排 |
| `src/harness/memory/maintainer.py` | 记忆维护:整合(蒸馏)+ 过期清理 |
| `src/harness/memory/episodic.py` | `EpisodicMemory` / `EpisodeRecorder` 任务经验门面 |
| `src/harness/memory/sqlite_backend.py` | SQLite + sqlite-vec / FTS5 存储后端 |
| `src/harness/tools/builtins/memory_search.py` | `search_knowledge` / `search_memory` 工具 |
| `src/harness/tools/builtins/memory_write.py` | `remember` 工具 |
| `src/harness/tools/builtins/episode_tools.py` | `recall_episodes`(已注册)/ `RecordEpisodeTool`(未注册) |
| `app/assembly.py` | 记忆设施装配:backend / retriever / writer / maintainer |
| `app/api/chat.py` | 按用户覆盖记忆工具的 scope;后台记忆写入与整合的触发 |
| `app/conversation_memory.py` | 对话记忆(L3)的写入与召回 |
