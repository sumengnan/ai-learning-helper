# RAG 检索

> 面向第一次接触本项目的人。读完你会明白:AI 回答问题时,是怎么"先查资料再作答"、
> 而不是凭空编的。

## 什么是 RAG

RAG = **检索增强生成**(Retrieval-Augmented Generation)。一句话:

> 让 AI 回答前**先去资料库里查一查**,把查到的相关内容作为依据再作答。

为什么需要它?大模型的知识是训练时"背下来"的,既可能过时,也不知道**你的**资料
(你上传的课件、笔记)。硬让它答,就容易"一本正经地胡说"。RAG 的做法是:把你的资料存起来,
提问时检索出最相关的几段喂给模型——**答案有据可查,还能标出处**。

本项目的知识库问答、基于资料出题,都建立在 RAG 之上。

## 本文讲的是"知识库"这一条链路

项目里有三处共用同一套向量设施:**知识库**(用户资料)、**长期记忆**(AI 记的偏好)、
**对话记忆**(长对话的历史召回)。本文只讲知识库——它是唯一**可作为作答依据被引用**的那个。
另外两个见 [记忆管理](memory-management.md) 与 [上下文管理](context-management.md)。

对应关系一句话:知识库存在 `knowledge:<用户>` 这个 scope,AI 用 `search_knowledge` 工具取,
grounding 检查也只认这个工具的命中。

## 整体流程

```
【建库】 上传资料 → 解析 → 去重 → 切块 → 向量化 → 存入向量库
                                                      │
【用库】 用户提问 → 检索最相关的若干片段 ────────────┘
                          │
                          └→ 连同问题一起喂给模型 → 生成有依据的答案（并标来源）
```

分两个阶段:先把资料**存进去**(建库),提问时再**找出来用**(检索 + 生成)。

## 阶段一:把资料存进知识库

对应 `app/knowledge.py` 的 `KnowledgeService.ingest`。上传一份文档后:

1. **解析**:把 PDF / Word / Markdown / txt 等解析成纯文本(`app/parsing.py`)。
2. **去重**:把正文的空白压平后算 SHA-256 指纹(`content_hash`)。同一份内容(哪怕改了几个
   空格重新导出)已经在库里,就直接回指原文档并标 `duplicate`,**不重复切块、不重复向量化**。
   这一步必须赶在向量化之前——走到那儿钱就已经花了,而且会把一模一样的向量再灌一遍进库,
   让同一内容在检索里命中两次、白占候选池名额。
   > 只压平空白,不做其他规范化:大小写、标点的差异是真实的内容差异,不能抹掉。
3. **切块(chunk)**:长文切成若干小块(`src/harness/memory/chunker.py`)。因为检索要精确到
   "相关的那一段",整篇太大既不精准也超长。切分是**结构感知**的,按文件后缀选策略:
   - `md/markdown/html` → `MarkdownSplitter`,按标题层级切,保表格/代码块/列表完整,
     **不做重叠**(避免污染结构);
   - `py/js/ts/java/go` → tree-sitter `CodeSplitter`,按语法边界切,不切断函数/类;
   - `txt/pdf/docx` → 递归 `TextSplitter`,按段落→句子逐级降,**带 overlap**;
   - 无后缀/未知后缀 → `auto`,嗅探内容里有没有 markdown 标题/表格/代码围栏,有则当 markdown。
   容量是一个区间 `(chunk_size, hard_max)`:尽量在语义边界填满,但**绝不超 hard_max**——
   表格/代码块在这个上限内保持完整(切断的表格 embedding 几乎没有检索价值)。
4. **向量化(embedding)**:每一块转成一串数字向量——**语义相近的文字,向量也相近**。
5. **入库**:向量连同来源(`source`/`doc_id`/`user_id`)存进 `knowledge:<用户>`,
   并建立文档记录(供知识库菜单列举、按分类筛选、预览、删除)。

> 聊天里"保存到知识库"(`save_to_knowledge` 工具 → `ingest_text`)走的是**同一条**实现,
> 只是输入为文本而非上传文件;同样去重、可检索、可删除,同样出现在知识库菜单里。

## 阶段二:检索(把最相关的片段找出来)

对应 `src/harness/memory/retriever.py` 的 `Retriever.retrieve`。这不是简单的关键词匹配,
而是一条多步流水线。用"招聘"来打个比方:

| 步骤 | 通俗类比 | 做了什么 |
| --- | --- | --- |
| **多路召回** | 多个渠道海选简历 | 向量检索(按语义)+ FTS5 关键词检索;可选再加"多查询改写 / HyDE / 实体精取"几路。每路各取 `max(k, candidate_pool)` 条 |
| **融合(RRF)** | 汇总各渠道的排名 | Reciprocal Rank Fusion:按各路名次累加 `1/(rrf_k + rank)`,合成一个总排名。鲁棒、免调参 |
| **归一 + 加权打分** | 综合评分 | 把 RRF 分做 min-max 归一,再叠加**新近度**(按半衰期指数衰减)和**重要性**:`w_rel×rel + w_rec×rec + w_imp×imp` |
| **去冗余(MMR)** | 别招一堆同类人 | 贪心选 `λ·相关度 − (1−λ)·与已选的最大相似度`,避免结果全是几乎一样的片段 |
| **重排(rerank)** | 终面,请资深评委精排 | 可选:调远程 rerank 端点对候选做更精准的相关性排序 |
| **取 top-k** | 发录用通知 | 返回最相关的前 k 条 |

注意顺序:**MMR 在重排之前**,重排拿到的是已去冗余的序;最终的 top-k 截断在最后一步做。

关于那几个**可选增强**(默认全关,开了每次多花一次 LLM,换更高召回):

- **多查询改写**:把问题换 `multi_query_n` 种说法各查一遍,每条改写的向量各算一路。
- **HyDE**:先让模型"假装知道答案"写一段假设答案,用它的向量去检索
  (往往比原问题更贴近资料的措辞)。
- **实体精取**:从问题里抽出点分实体键(如 `user.pref.language`),精确取该实体的记录。

三路**共享同一次 LLM 调用**(`QueryPlanner` 一次出全部产物),且带 `query_plan_timeout_s`
超时——检索卡在聊天首字的关键路径上,超时/失败/解析失败一律降级回基础召回,不打断检索。
规划用的是**快速模型档**,思考链恒关。

> 为什么要向量检索而不是关键词?问"怎么学好高数",关键词可能匹配不到写着"考研数学复习方法"
> 的资料,但它们**语义相近**,向量检索能找到。关键词检索则擅长专有名词/精确匹配——所以两路都用。

### 检索取几条:`search_knowledge` 有下限

`search_knowledge` 的 k 被**夹在 [10, 50]**:默认值取 `HARNESS_SEARCH_TOP_K`(10),
模型显式传更小的值(实测会传 3)会被**抬到 10**。原因是几条片段根本覆盖不住知识库里的相关内容,
回答就变成"资料里没提到"——而光调默认值没用,显式传参会盖掉默认值,所以要有下限兜底。
上限 50 防止把上下文撑爆。

(对比:`search_memory` 只夹在 [1, 50],不设下限——记忆是辅助信号,不是作答依据。)

### 关于知识库页面显示的"相关度百分比"

`KnowledgeService.search` 会给每个命中算一个 0–100 的相关度:

```
relevance = clip(round((1 − distance) × 100), 0, 100)
其中 distance = 1 − 融合分   ⇒   relevance = clip(round(融合分 × 100), 0, 100)
```

**这个百分比不是"匹配程度"**,读的时候务必注意:

- 这里的 `distance` **不是余弦距离**,只是把"越大越相关"的融合分翻转成"越小越相关"的序,
  好塞进原有的 `MemoryHit.distance` 字段。
- 融合分是 `w_rel×rel + w_rec×rec + w_imp×imp` 加权和,**可能大于 1**(默认权重合计 1.3),
  也可能为负,所以才需要裁剪到 0–100。
- 因此 **100% 不代表"完全匹配"**,只代表加权分被上限截断了;它也**不可跨查询比较**——
  相关度那一项经过了本次候选集内部的 min-max 归一,换一次检索基准就变了。

结论:**它只适合用来比较同一次检索内部各片段的相对高低**,别当成绝对的置信度。

## 阶段三:用检索结果生成答案

检索到的片段不是直接丢给用户,而是**连同问题一起喂给模型**,让它据此作答。
项目在这一步还做了几件事:

- **标注来源**:检索工具返回的每条片段都带 `（来源：xxx）`,AI 回答会标出参考了哪些资料,
  用户能看见"它依据了什么",也能点开片段抽屉看原文。
- **空命中是有意义的信号**:检索不到时工具返回固定文案"(未在知识库中检索到相关内容)"。
  这句文案被 `app/tools/validating.py` 和 `app/verify.py` **逐字匹配**用作哨兵,改动需同步。
  开启每步校验时,知识库检索还会被 `ValidatingTool` 包一层,空命中会驱动模型自纠正
  (记忆检索**不包**这层——记忆为空是常态,不是失败)。
- **grounding 检查**(交付提醒的一项,`app/verify.py`):答案交付后,核对其中的事实性陈述
  是否真能被资料支撑。几个要点:
  - **只有本轮 `search_knowledge` 有非空命中才触发**——避免给纯联网问答新增噪音;
  - 触发后,核查上下文会把**联网检索结果**与本轮 `read_attachment`/`read_file` 读入的
    文档正文一并算进去,否则"知识库+联网"混用时联网来的事实会被误判缺依据;
  - 拼接去重后截断到 12000 字符,防撑爆核查模型。
- **基于资料出题**:题库的"按知识点出题"同样先检索相关资料,再据资料生成题目,而非凭空出题。

## 用户视角:怎么用到 RAG

- **上传文档到知识库**:在知识库页上传,即完成"建库"。
- **聊天里"保存到知识库"**:把有用的回答/资料一键存入,后续可被检索。
- **提问**:问到与资料相关的问题时,AI 会通过 `search_knowledge` 工具检索知识库并据此回答。
- **片段浏览**:知识库页可分页浏览、按分类筛选(分类由文件名后缀运行时推导)、
  查看单个片段全文、删除单个片段或整份文档。

## 常用配置

均为 `HARNESS_` 前缀环境变量(定义见 `src/harness/config.py` 与 `app/config.py`,
示例见 [`.env.example`](../.env.example)):

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `HARNESS_EMBEDDING_MODEL` | `text-embedding-3-small` | 把文字转向量的模型 |
| `HARNESS_EMBEDDING_DIMENSION` | `1536` | 向量维度(要与模型匹配) |
| `HARNESS_MEMORY_DB_PATH` | `memory.db` | 向量库文件路径 |
| `HARNESS_CHUNK_SIZE` / `HARNESS_CHUNK_OVERLAP` | `1000` / `200` | 切块目标大小与相邻块重叠 |
| `HARNESS_CHUNK_HARD_MAX` | `2000` | 切块硬上限(表格/代码块在此内尽量整块保留) |
| `HARNESS_SEARCH_TOP_K` | `10` | 检索工具默认返回条数 |
| `HARNESS_RETRIEVAL_CANDIDATE_POOL` | `20` | 每路召回的候选池大小(实际取 `max(k, 该值)`) |
| `HARNESS_RETRIEVAL_W_RELEVANCE` | `1.0` | 打分权重:相关度 |
| `HARNESS_RETRIEVAL_W_RECENCY` | `0.2` | 打分权重:新近度 |
| `HARNESS_RETRIEVAL_W_IMPORTANCE` | `0.1` | 打分权重:重要性 |
| `HARNESS_RETRIEVAL_RECENCY_HALF_LIFE_DAYS` | `30.0` | 新近度的指数衰减半衰期(天);≤0=不衰减 |
| `HARNESS_RETRIEVAL_USE_KEYWORD` | `true` | 是否加 FTS5 关键词检索一路 |
| `HARNESS_RETRIEVAL_USE_MMR` | `true` | 是否做去冗余 |
| `HARNESS_RETRIEVAL_MMR_LAMBDA` | `0.7` | MMR 的 λ(越大越偏相关度、越小越偏多样性) |
| `HARNESS_RETRIEVAL_RRF_K` | `60` | RRF 融合常数 |
| `HARNESS_RETRIEVAL_USE_MULTI_QUERY` | `false` | 召回增强:多查询改写 |
| `HARNESS_RETRIEVAL_USE_HYDE` | `false` | 召回增强:HyDE 假设答案 |
| `HARNESS_RETRIEVAL_USE_ENTITY_RECALL` | `false` | 召回增强:实体键精取 |
| `HARNESS_RETRIEVAL_MULTI_QUERY_N` | `3` | 多查询改写产出几条 |
| `HARNESS_RETRIEVAL_QUERY_PLAN_TIMEOUT_S` | `2.0` | 查询规划 LLM 超时(秒),超时即降级 |

**重排(rerank)需要三个条件同时满足才生效**,缺一个就退回 `NoOpReranker`(不改序):

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `HARNESS_ENABLE_RERANK` | `false` | 总开关,**必须显式打开** |
| `HARNESS_RERANK_BASE_URL` | 空 | **必填**;`openai` 风格填到 `/v1`,`dashscope` 填完整 endpoint |
| `HARNESS_RERANK_MODEL` | 空 | **必填**,如 `BAAI/bge-reranker-v2-m3` 或 `qwen3-rerank` |
| `HARNESS_RERANK_STYLE` | `openai` | `openai`(兼容 Cohere/Jina/SiliconFlow)\| `dashscope` |
| `HARNESS_RERANK_API_KEY` | 空 | 空则回退 `embedding_api_key` → `api_key` |
| `HARNESS_RERANK_TIMEOUT` | `30.0` | 请求超时(秒) |
| `HARNESS_RERANK_TOP_N` | `0` | 0=送全部候选精排;>0 只精排前 N(省调用成本) |

启用后**全局作用于所有检索路径**(知识库/题库/对话记忆/长期记忆)。
网络/超时/HTTP/格式异常一律降级为"原序返回",绝不打断检索或聊天。

## 代码位置

| 文件 | 职责 |
| --- | --- |
| `app/knowledge.py` | 知识库入库:去重、切块、向量化、文档记录;片段检索与相关度 |
| `app/parsing.py` | 各格式文件 → 纯文本 |
| `src/harness/memory/chunker.py` | 结构感知切块(markdown / tree-sitter 代码 / 递归文本) |
| `src/harness/memory/embeddings.py` | 文字 → 向量 |
| `src/harness/memory/retriever.py` | 检索流水线:多路召回 → RRF → 加权 → MMR → 重排 → top-k |
| `src/harness/memory/query_planner.py` | 召回增强的查询规划:多查询 / HyDE / 实体键 |
| `src/harness/memory/reranker.py` | `NoOpReranker` / `HttpReranker`(openai / dashscope 两种风格) |
| `src/harness/memory/sqlite_backend.py` | sqlite-vec 向量检索 + FTS5 关键词检索后端 |
| `src/harness/tools/builtins/memory_search.py` | `search_knowledge` / `search_memory` 工具 |
| `app/tools/validating.py` | 空命中哨兵与每步校验包装 |
| `app/verify.py` | grounding 校验(答案是否有资料依据) |
| `app/assembly.py` | 检索设施装配:reranker / RetrievalConfig / Retriever / Memory |
