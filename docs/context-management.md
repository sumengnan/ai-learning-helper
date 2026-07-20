# 上下文管理

> 面向第一次接触本项目的人。读完你会明白:对话越聊越长,AI 为什么没被"撑爆",
> 又为什么还记得二十轮之前说过的话。

## 解决什么问题

每次请求都要把"系统提示 + 全部历史 + 本轮消息"整个发给模型。多轮对话越聊越长,历史迟早
撑破模型的上下文窗口——轻则涨价、重则报错。但简单粗暴地"只留最近几轮"又会让 AI 突然失忆,
忘掉用户早先交代的目标。

上下文管理这层的职责,就是**在每一轮把最合适的一份上下文喂给模型**:近的原样保留、
远的压成摘要、更远但相关的按需检索回来——既不超窗、也尽量不丢关键信息。

> 与另外两篇的分工:本文管的是**当前这次对话的历史**怎么裁。
> [记忆管理](memory-management.md) 管的是 AI 跨会话记住的偏好,
> [RAG 检索](rag-retrieval.md) 管的是用户上传的资料。三者共用同一套向量设施,用途不同。
> 特别注意:**本文说的"对话记忆"没有对应的工具,模型无法主动搜索对话历史**——
> 相关片段是由这一层检索后直接注入上下文的。

## 三种策略

由 `HARNESS_CONTEXT_STRATEGY` 配置(默认 `layered`):

| 策略 | 行为 | 适用 |
| --- | --- | --- |
| `full` | 全量拼接:`system + 完整历史 + 本轮消息`。与引入上下文管理之前**字节级一致**的历史行为。 | 短对话、想要绝对安全的回退 |
| `window` | 只做 **L1 token 预算滑动窗口**:按 token 预算保留最近的完整轮次,更早的直接丢弃。 | 长对话、不需要"记得更早"的场景 |
| `layered` | **L1 窗口 + L2 滚动摘要 + L3 语义检索**:被窗口挤出的历史,先压成摘要,并按当前问题检索出相关片段一并带回。 | 长对话且需要延续更早的目标/结论 |

策略为 `full`、**或历史为空**时直接走全量拼接(`ConversationContextManager`),
不启用任何裁剪逻辑。

## 分层结构(layered)

`layered` 下,每轮发给模型的消息布局如下:

```
[system 提示]
[L2 更早内容的摘要]        ← system 角色，可选
[L3 相关历史片段]          ← user 角色，可选
[L1 窗口内最近原文]        ← 按 token 预算保留的最近若干完整轮
[本轮新消息]
```

三层各司其职:

- **L1(最近原文)**:最近的完整轮次,原样保留——细节最全、最重要。
- **L2(滚动摘要)**:被 L1 挤出去的更早历史,压成一段连贯摘要,防止"凭空失忆一段"。
- **L3(语义检索)**:即便被摘要了,与当前问题特别相关的更早片段,再按语义检索原文带回来补细节。

> **L2/L3 只在真有内容被挤出窗口时才跑**(`window.evicted` 非空)。
> 对话还短、一条都没被挤出去时,这两层根本不启动,不会有任何额外开销。

## Token 预算(`ContextBudget`)

各层的额度由 token 预算切分(`src/harness/context/budget.py`):

```
available      = min(context_window − response_reserve, max_prompt_tokens) − system_tokens
working_tokens = available × working_ratio        # L1「最近原文」的额度
```

两个上限含义不同,不要混:

- **`context_window` / `response_reserve`** —— **物理**约束:塞不下模型会报错/截断。
  `response_reserve` 按模型的最大输出长度预留(思考模型的思维链也算输出)。
- **`max_prompt_tokens`** —— **策略**约束:塞得下,但不划算。典型是分档计价的档位阈值
  (如输入 ≤256K 一个价、超了三倍价),把阈值填这里即可,不必去谎报 `context_window`。
  `0` = 不设,只受物理窗口约束。
  > 它管的是"整个输入",所以和物理窗口一样要先扣掉 system 才可比。

> ⚠️ `working_ratio` 只保证"L1 最多用掉 available 的这个比例",**不保证**剩下那部分真被
> L2/L3 用满:L2 大小由 `context_summary_max_tokens` 单独限、L3 由 `context_retrieval_top_k`
> 条数单独限,都不看这里的剩余额度。把比例调小只是让 L1 少占,多出来的通常闲置。

## L1:滑动窗口(`WindowStrategy`)

`src/harness/context/windowing.py`。按 token 预算保留最近的完整轮次,**切割只落在干净的
user 边界**——绝不从一轮中间切开,否则会留下孤儿 `tool` 结果或被截断的 `tool_calls`,
OpenAI 端会报 400。

- 轮边界 = `role==user` 且 `tool_call_id is None` 的消息。
- 从最新边界往前扩,取能放下的最大后缀;**至少保留最后一个完整轮**(哪怕它已超预算)。
- 找不到任何 user 边界时(罕见)无法安全切割,**全部保留**。
- 窗口首部若仍出现孤儿 `tool` 消息,防御性剥掉并计入 evicted。
- 产出 `WindowResult(kept, evicted)`:`kept` 进 L1,`evicted` 交给 L2/L3 处理。

## L2:滚动摘要(`RollingSummarizer`)

`app/summarizer.py` + `app/summaries.py`(`conversation_summaries` 表)。**增量**摘要,成本有界:

- 用**水位** `up_to_seq` 记录"已被摘要覆盖的历史前缀长度"。`evicted` 恒为 history 的前缀,
  所以用前缀长度做水位就够,不必给每条消息挂 seq。
- 每轮只摘水位之后新增(delta)的那段,再与已有摘要合并成一份统一摘要——**永不重摘全历史**。
  被挤出的历史没超过水位时,直接返回旧摘要,**连 LLM 都不调**。
- 摘要本身若超过 `context_summary_max_tokens`,再压一轮(`_COMPRESS_SYS`)确保有界。
- 渲染成文本时,工具调用只留工具名与文本,图片略去。
- 摘要走"快速模型"档(`build_fast_completer`),思考链恒关——它是机械活,不值得为它烧 token。
  token 计数也跟着摘要模型走(`fast_model or model`),否则上限会按错误的分词器量。

## L3:语义检索(`ConversationMemoryService`)

`app/conversation_memory.py`,复用 harness 的向量记忆设施。

- 每轮结束后把对话文本写入 `conversation:<conv_id>` 向量集合。该步骤在**后台任务**里跑,
  不阻塞本轮 SSE 流关闭——否则用户会一直转圈、且这个 run 仍算"在途",连下一句都发不出去。
  代价:进程正好在写记忆时重启,会丢这条在写的记忆(best-effort)。
- 组装下一轮时,按当前问题做语义检索,并用 `before_seq` 只召回**窗口外**(比 L1 更早)的片段——
  避免和 L1 里已有的原文重复。因为过滤在应用层做,会先按 3 倍系数 over-fetch 再截断。
- 检索条数由 `context_retrieval_top_k` 限。

> 开启"智能记忆写入"(`HARNESS_MEMORY_WRITE_EXTRACT`,默认开)时,写入会先由 LLM 把对话
> 提炼成分型事实(semantic/episodic/procedural)再入库,而非原文入库。这些提炼事实**不带
> `seq`**,因而位置无关、不参与窗口外过滤、恒可召回。详见
> [记忆管理的智能写入](memory-management.md#路径二智能写入从对话自动提炼)。

## 降级与可观测

**降级但绝不无声**——摘要/检索失败一律降级、绝不打断聊天,但按危害度分级记录:

- **L2 摘要失败**危害重:被挤出 L1 的历史已经不在上下文里,摘要再没有,这一轮模型就凭空失忆
  一段、且它不知道自己不知道 → 打 **warning** 日志(含被丢失的条数)+ 记进 trace。
- **L3 检索失败**危害轻:只是少了"相关片段"这层增益,不等于失忆 → 记 trace,日志降到 **info**。

每轮上下文组装的结构化结果会落进 `conversation_messages.context` 列,供排查与统计:

| 字段 | 含义 |
| --- | --- |
| `strategy` | 本轮用的策略(full/window/layered) |
| `evicted` / `kept` | 被挤出 / 保留在 L1 的历史条数 |
| `summary` | L2 状态:`ok` / `none` / `off` / `error`(+ `summary_error`) |
| `retrieval` | L3 状态:`ok` / `none` / `off` / `error`(+ `retrieval_error`) |

`evicted` 同时是"L2 失败的危害度":为 0 时摘要根本不该跑,失败也无所谓。

## 关键设计:异步组装、同步 build

- **重活(窗口裁剪 / 摘要 / 检索)在请求前由 `ContextAssembler.build_manager` 异步做好**,
  产出一个**纯同步**的 manager(`LayeredContextManager` / `ConversationContextManager`)。
- `AgentLoop` 每步都同步调用 `manager.build(state)` 逐步拼消息——其中**绝不能触发 LLM/embedding**
  (否则每步都卡)。所以摘要/检索必须提前算好,`build()` 里只做纯拼装。
- `ContextAssembler` 在 `make_chat_router` 里只建一次、被所有并发请求共用,因此它**不持任何
  轮级状态**;每轮的 trace 通过传入的 dict 就地回填,避免串轮。

harness 内核自带的 `ContextManager`(`src/harness/context/manager.py`)是最简版(system + 全量历史);
应用层用鸭子类型的 `ConversationContextManager` / `LayeredContextManager` 扩展它,**内核零改动**。

## 补充:按更小的模型再收一道(`ClampedContextManager`)

`src/harness/context/clamp.py`。上面的预算是按**主模型**算的,但有两处的输入会交给
窗口更小的模型,需要再确定性地硬裁一次:

- **编排器的简单直答**走快速模型,用 `context_max_prompt_tokens_fast` 重裁;
- **单轮 judge** 的输入可能极大(大段 grounding 资料),用 `context_max_prompt_tokens_judge`
  加硬上限。

它包在现有 manager 外层,在 `build()` 之后动手,**只做确定性丢弃/截断,不摘要不检索**:
先从最旧的中间消息开始丢(保住首条 system 与末条本轮消息),仍超则对末条文本做**中段截断**
(保头保尾——judge 输入的末尾常是"只输出 JSON"的格式指令,砍中段才能保住它)。
两个配置默认都是 `0` = 关闭,透传不裁、零开销。

## 配置项

均为 `HARNESS_` 前缀的环境变量(定义见 `app/config.py`,示例见 [`.env.example`](../.env.example)):

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `HARNESS_CONTEXT_STRATEGY` | `layered` | `full` / `window` / `layered` |
| `HARNESS_CONTEXT_WINDOW_TOKENS` | `1000000` | 模型上下文窗口(按实际模型调) |
| `HARNESS_CONTEXT_RESPONSE_RESERVE_TOKENS` | `56000` | 给回复预留的 token(思考链也算输出) |
| `HARNESS_CONTEXT_MAX_PROMPT_TOKENS` | `240000` | 输入总量策略上限;0=不设 |
| `HARNESS_CONTEXT_MAX_PROMPT_TOKENS_FAST` | `0` | 快速模型口径的硬上限;0=关闭 |
| `HARNESS_CONTEXT_MAX_PROMPT_TOKENS_JUDGE` | `0` | judge 口径的硬上限;0=关闭 |
| `HARNESS_CONTEXT_WORKING_RATIO` | `0.9` | L1 最近原文占可用预算的比例 |
| `HARNESS_CONTEXT_SUMMARY_MAX_TOKENS` | `2000` | L2 摘要块 token 上限 |
| `HARNESS_CONTEXT_RETRIEVAL_TOP_K` | `5` | L3 召回条数 |
| `HARNESS_CONTEXT_ENABLE_SUMMARY` | `true` | layered 下是否启用 L2 摘要 |
| `HARNESS_CONTEXT_ENABLE_RETRIEVAL` | `true` | layered 下是否启用 L3 检索 |

L3 检索还受记忆侧配置影响:`HARNESS_MEMORY_WRITE_EXTRACT`(是否提炼后入库)、
`HARNESS_MEMORY_WRITE_SAMPLE_RATE`(写入采样率),见 [记忆管理](memory-management.md#常用配置)。

## 代码位置

| 文件 | 职责 |
| --- | --- |
| `app/context_assembly.py` | `ContextAssembler`:按策略异步组装、降级、写 trace |
| `app/context.py` | `ConversationContextManager` / `LayeredContextManager`:同步拼装 |
| `src/harness/context/budget.py` | `ContextBudget`:token 预算切分 |
| `src/harness/context/windowing.py` | `WindowStrategy`:L1 滑动窗口 |
| `src/harness/context/clamp.py` | `ClampedContextManager`:按更小模型口径确定性硬裁 |
| `src/harness/context/manager.py` | harness 内核自带的最简 `ContextManager` |
| `app/summarizer.py` / `app/summaries.py` | L2 滚动摘要与存储 |
| `app/conversation_memory.py` | L3 会话语义检索/写入 |
| `app/api/chat.py` | 装配 summarizer / conv_memory,触发后台记忆写入 |
