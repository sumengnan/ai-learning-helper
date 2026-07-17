# 上下文管理

多轮对话越聊越长,历史迟早撑破模型的上下文窗口。上下文管理这层的职责,就是**在每一轮把
最合适的一份上下文喂给模型**:近的原样保留、远的压成摘要、更远但相关的按需检索回来——
既不超窗、也尽量不丢关键信息。

本项目提供三档策略,从"零改动的安全回退"到"分层裁剪+摘要+检索",按会话配置切换。

## 三种策略

由 `HARNESS_CONTEXT_STRATEGY` 配置(默认 `layered`):

| 策略 | 行为 | 适用 |
| --- | --- | --- |
| `full` | 全量拼接:`system + 完整历史 + 本轮消息`。与引入上下文管理之前**字节级一致**的历史行为。 | 短对话、想要绝对安全的回退 |
| `window` | 只做 **L1 token 预算滑动窗口**:按 token 预算保留最近的完整轮次,更早的直接丢弃。 | 长对话、不需要"记得更早"的场景 |
| `layered` | **L1 窗口 + L2 滚动摘要 + L3 语义检索**:被窗口挤出的历史,先压成摘要,并按当前问题检索出相关片段一并带回。 | 长对话且需要延续更早的目标/结论 |

`full` 或历史为空时直接走全量拼接,不启用任何裁剪逻辑。

## 分层结构(layered)

`layered` 下,每轮发给模型的消息布局如下:

```
[system 提示]
[L2 更早内容的摘要]        ← 可选:被挤出窗口的历史压成的一段摘要
[L3 相关历史片段]          ← 可选:按当前问题从窗口外检索到的对话片段
[L1 窗口内最近原文]        ← 按 token 预算保留的最近若干完整轮
[本轮新消息]
```

三层各司其职:

- **L1(最近原文)**:最近的完整轮次,原样保留——细节最全、最重要。
- **L2(滚动摘要)**:被 L1 挤出去的更早历史,压成一段连贯摘要,防止"凭空失忆一段"。
- **L3(语义检索)**:即便被摘要了,与当前问题特别相关的更早片段,再按语义检索原文带回来补细节。

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

> ⚠️ `working_ratio` 只保证"L1 最多用掉 available 的这个比例",**不保证**剩下那部分真被
> L2/L3 用满:L2 大小由 `context_summary_max_tokens` 单独限、L3 由 `context_retrieval_top_k`
> 条数单独限,都不看这里的剩余额度。把比例调小只是让 L1 少占,多出来的通常闲置。

## L1:滑动窗口(`WindowStrategy`)

`src/harness/context/windowing.py`。按 token 预算保留最近的完整轮次,**切割只落在干净的
user 边界**——绝不从一轮中间切开,否则会留下孤儿 `tool` 结果或被截断的 `tool_calls`,
OpenAI 端会报 400。

- 轮边界 = `role==user` 且非 tool 续接的消息。
- 从最新边界往前扩,取能放下的最大后缀;**至少保留最后一个完整轮**(哪怕它已超预算)。
- 产出 `WindowResult(kept, evicted)`:`kept` 进 L1,`evicted` 交给 L2/L3 处理。

## L2:滚动摘要(`RollingSummarizer`)

`app/summarizer.py` + `app/summaries.py`(`conversation_summaries` 表)。**增量**摘要,成本有界:

- 用**水位** `up_to_seq` 记录"已被摘要覆盖的历史前缀长度"。每轮只摘水位之后新增(delta)的那段,
  再与已有摘要合并成一份统一摘要——**永不重摘全历史**。
- 摘要本身若超过 `context_summary_max_tokens`,再压一轮(`_COMPRESS_SYS`)确保有界。
- 摘要走"快速模型"档(`build_fast_completer`),思考链恒关——它是机械活,不值得为它烧 token。

## L3:语义检索(`ConversationMemoryService`)

`app/conversation_memory.py`,复用 harness 的向量记忆设施(`harness.memory`)。

- 每轮结束后把对话文本写入 `conversation:<conv_id>` 向量集合(该步骤在**后台**异步执行,
  不阻塞本轮完成)。
- 组装下一轮时,按当前问题做语义检索,并用 `before_seq` 只召回**窗口外**(比 L1 更早)的片段——
  避免和 L1 里已有的原文重复。
- 检索条数由 `context_retrieval_top_k` 限。

> 开启"智能记忆写入"(`HARNESS_MEMORY_WRITE_EXTRACT`)时,写入会先由 LLM 把对话提炼成分型
> 事实(semantic/episodic/procedural)再入库,而非原文入库;这些提炼事实位置无关、恒可召回。

## 降级与可观测

**降级但绝不无声**——摘要/检索失败一律降级、绝不打断聊天,但按危害度分级记录:

- **L2 摘要失败**危害重:被挤出 L1 的历史已经不在上下文里,摘要再没有,这一轮模型就凭空失忆
  一段、且它不知道自己不知道 → 打 **warning** 日志 + 记进 trace。
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

## 配置项

均为 `HARNESS_` 前缀的环境变量(见 [`.env.example`](../.env.example));下表列默认值:

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `HARNESS_CONTEXT_STRATEGY` | `layered` | `full` / `window` / `layered` |
| `HARNESS_CONTEXT_WINDOW_TOKENS` | `1000000` | 模型上下文窗口(按实际模型调) |
| `HARNESS_CONTEXT_RESPONSE_RESERVE_TOKENS` | `56000` | 给回复预留的 token(思考链也算输出) |
| `HARNESS_CONTEXT_MAX_PROMPT_TOKENS` | `240000` | 输入总量策略上限;0=不设 |
| `HARNESS_CONTEXT_WORKING_RATIO` | `0.9` | L1 最近原文占可用预算的比例 |
| `HARNESS_CONTEXT_SUMMARY_MAX_TOKENS` | `2000` | L2 摘要块 token 上限 |
| `HARNESS_CONTEXT_RETRIEVAL_TOP_K` | `5` | L3 召回条数 |
| `HARNESS_CONTEXT_ENABLE_SUMMARY` | `true` | layered 下是否启用 L2 摘要 |
| `HARNESS_CONTEXT_ENABLE_RETRIEVAL` | `true` | layered 下是否启用 L3 检索 |

## 代码位置

| 文件 | 职责 |
| --- | --- |
| `app/context_assembly.py` | `ContextAssembler`:按策略异步组装、降级、写 trace |
| `app/context.py` | `ConversationContextManager` / `LayeredContextManager`:同步拼装 |
| `src/harness/context/budget.py` | `ContextBudget`:token 预算切分 |
| `src/harness/context/windowing.py` | `WindowStrategy`:L1 滑动窗口 |
| `src/harness/context/manager.py` | harness 内核自带的最简 `ContextManager` |
| `app/summarizer.py` / `app/summaries.py` | L2 滚动摘要与存储 |
| `app/conversation_memory.py` | L3 会话语义检索/写入 |
