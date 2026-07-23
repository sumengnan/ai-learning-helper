# 采样温度：静态分档 + 意图路由 + 运行期增减

## 为什么要改

改之前，全项目 22 个 LLM 调用点共用一个 `HARNESS_TEMPERATURE=0.7`——`judge`/`fast` 档也不例外，
因为 `_alt_config` 只 `model_copy` 了 model/base_url/api_key，温度是继承的。于是：

- 同一份简答题答卷，两次判分可能给出不同结论（用户看来就是系统不讲理）；
- 记忆调和判 `REPLACE` 会**永久作废**旧记忆，却跑在跟闲聊一样的随机性上；
- 反过来，出题恒 0.7 也偏低——同一知识点反复出题容易出一模一样的题。

## 三层，越靠后越动态

| 层 | 谁定 | 作用范围 | 真源 |
|---|---|---|---|
| 角色档 | 装配期钉死 | 判断/机械/发散类的 18 处旁路调用 | `app/sampling_policy.ROLE_TEMPERATURES` |
| 意图档 | 每轮 triage 顺带产出 | 主循环 ReAct / 简单直答 / 最终汇总 | `app/sampling_policy.INTENT_TEMPERATURES` |
| 运行期增减 | 失败情形驱动 | 打转、重答、重试那几次调用 | `DELTA_*` 常量 |

最终值 `clamp(基准 + Σ增量, 0, 1)`，在 `harness/llm/sampling.py::resolve_sampling` 算出。

### 为什么上限是 1 而不是协议的 2

OpenAI 协议写的是 `temperature ∈ [0,2]`，但 Anthropic 只到 1、百炼不接受端点值 2，而 >1 的区间
实测只会让输出退化——没有正经用途，却是唯一可能因厂商差异报错的区间。统一收在 `[0,1]` 顺带
堵死「增量把温度累加到 1.8 变成乱码」这类事故。配置入口（`HarnessConfig.temperature`、两张
覆盖表）都过同一道 `clamp_temperature`，写超了告警并就地夹取。

`top_p` 与 `temperature` **二选一**（两家官方文档都写了 "alter this or top_p but not both"）：
显式设了 `top_p` 就只发 `top_p`。默认哪个调用点都不发 `top_p`。

## 角色档（与用户问什么无关）

判断/结构化输出恒 0：`triage`、`critic`、`grounding`、`judge`、`exam_grade`、`quiz_grade`、
`plan_finalize`、`memory_reconcile`、`question_import`。

机械转写偏低：`summary` 0.2、`titling` 0.2、`memory_extract` 0.1、`memory_consolidate` 0.2。

**两个不设 0 的**：`planner` 0.2（拆 DAG 要一点组合能力）、`executor` 0.2（带工具的循环温度
过低反而更容易卡在重复调同一个工具上——正是 `loop_detect_window` 那套防打转逻辑在治的事）。

**两个反而要高的**：`quiz_generate` 0.8（低温会每次出一样的题）、`query_plan` 0.5（multi-query
的几条改写太雷同就失去扩召回的意义；它和 HyDE 是同一次调用的两个字段，只能取折中）。

挂载方式是 `app/completion.py::with_role`，包在 completer 最外层、调用期设、调用后还原。
它留了 `__wrapped__` 指针，装配层的接线测试用 `unwrap_completer` 穿透。

## 意图档（只作用于面向用户的生成）

类别搭在 `Orchestrator._triage` 上——那是每轮都跑的调用，多要一个词零成本；单开一次分类
调用要在最卡首字的路径上再加一个往返。输出形如 `simple:factual`，老的 `simple`/`complex`
解析原样兼容。

`factual` 0.2｜`code` 0.2｜`rewrite` 0.3｜`exam` 0.2｜`creative` 0.9｜`chat` 0.7。

**误分类的代价必须有界**：拿不到类别、给了表外的词、triage 整个失败，一律回退 `chat`（=项目
原本的 0.7）。考试轮（`force_simple`）直接按 `exam` 给，本就不问 triage；命中技能剧本时按原
样跳过 triage，此时意图未知也回退 `chat`。

## 运行期增减（方向相反的两类）

**升温——陷在同一条路上，换条采样路径：**

| 触发 | 位置 | 幅度 |
|---|---|---|
| 打转纠偏 | `agent_loop.py` 循环检测 | +0.2，持续到本轮结束 |
| 整轮重答（交付门未过） | `api/chat.py` attempt 循环 | 每次 +0.15，封顶 +0.3 |
| 单步重试 | `orchestrator.py` `step.attempts` | 同上 |

打转那处尤其重要：只注入一句「请换个思路」而不动采样，模型很容易照着同一条路再走一遍。

**降温——输出结构崩了，收紧采样：**

| 触发 | 位置 | 幅度 |
|---|---|---|
| planner 出非法 DAG / 坏 JSON | `planner.py::_generate` 重试循环 | 每次 −0.1，地板 −0.3 |

降温**只对无工具的单发 JSON 调用**做，不进工具循环（理由同 `executor` 那条）。

`call_json`、题目抽取、`quiz` 出题解析这几处解析失败后**没有重试循环**（失败即降级返回空），
所以没有可挂的点——要给它们加降温重试是另一件事，本次没做。

**不该动的地方**：`reliability/retry.py::RetryingModelClient` 只重试瞬时网络错误（超时/限流/
5xx），且有「只在未产出任何 chunk 前才重试」的安全约束。在那里改温度会把「重发同一个请求」
悄悄变成「重新生成一次」。错的是网络，不是采样。

## 还原不能漏

采样状态走 contextvar，而生成器不自带上下文隔离——`AgentLoop.run` 与 `Orchestrator.run` 都有
十几个 return 出口，逐个补 reset 迟早漏一个，漏了就会把这轮的温度带给后面所有调用（包括判分）。
两处都用 `contextlib.ExitStack` 统一兜底，并各有一条测试专门盯着「离开后 delta 归零」。

## 开关

```bash
HARNESS_TEMPERATURE=0.7                 # 基准，[0,1]
HARNESS_ROLE_TEMPERATURES={}            # 角色覆盖，如 {"judge":0.0,"quiz_generate":0.9}
HARNESS_INTENT_TEMPERATURES={}          # 意图覆盖，如 {"creative":0.75}
HARNESS_ENABLE_DYNAMIC_TEMPERATURE=true # 关掉只保留静态分档
HARNESS_SAMPLING_UNSUPPORTED_MODELS=[]  # 命中则一个采样参数都不发（o1 系等）
```

## 期望值管理

真正肉眼可见的收益是**判分与 JSON 抽取从 0.7 降到 0**：判分可复现了，结构化解析失败少了。
「创作调到 0.9」更多是感觉层面的。升温也救不了缺信息——交付门重答失败的主因通常是检索没
命中或问题本身有歧义，升温只是让它换个说法把同样的空话再讲一遍。它是「再试一次」的增效
手段，不是失败的解药。
