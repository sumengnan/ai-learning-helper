# AI Harness 可靠性层（子项目②）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用工具，在子项目①内核骨架之上扩展
- **前置**：① 内核骨架（Agent Loop + 模型抽象 + 工具系统 + 上下文管理 + 事件流）已完成并在 `main`

---

## 0. 背景与范围

在①内核之上加一层**可靠性**。本规格覆盖三块，checkpoint 断点续跑延后到③。

| 组件 | 内容 | 状态 |
|---|---|---|
| 可观测性 | OpenTelemetry 插桩：run/step/model/tool span 树 + token/成本/延迟/错误 | 本规格 |
| 重试 + 自纠正 | 传输层 `RetryingModelClient` 指数退避；循环层显式错误回填自纠正 | 本规格 |
| 资源上限 | token 预算 + 墙钟时间上限（+ 已有 max_steps）；循环/停滞检测（连续 N 步相同工具调用签名即 `RunError` 中止，`loop_detect_window` 配置，<2 关闭）；工具结果截断；calculator 幂运算守卫 | 本规格 |
| ~~断点续跑~~ | checkpoint / RunState 序列化 / 恢复 | **延后到 ③** |

**项目存储栈（全局约定）**：SQLite + sqlite-vec 存业务数据与向量（服务③）；**OpenTelemetry 存可观测性数据**（本规格采用）。

设计通则：严格 YAGNI；可观测性默认 no-op（不配 OTel 也能跑）；所有新增配置有安全默认；不回归①。

---

## 1. 范围与验收

### IN
1. **可观测性**：OTel 插桩，run→step→model-call/tool-call span 树，属性带 token 用量/成本/延迟/模型参数/错误。
2. **重试 + 自纠正**：传输层 `RetryingModelClient`（瞬时错误指数退避）；循环层把①的"JSON 解析失败静默降级为 `{}`"升级成显式错误回填。
3. **资源上限**：token 预算 + 墙钟时间上限（+ 已有 `max_steps`），每步边界检查超限即 `RunError`；工具结果长度截断；`calculator` 幂运算量级守卫。

### OUT（预留扩展点，不写死）
checkpoint 断点续跑（→③）、SQLite 全轨迹 sink、`ReplayModelClient`、模型路由降级、价格表以外的成本分析。

### 验收标准
1. 模型调用瞬时失败（超时/5xx/429/连接断开）→ 自动重试、每次尝试进 OTel、耗尽才 `RunError`（flaky mock 验证）。
2. 模型吐出非法 JSON 工具参数 → 回填明确 `is_error` 错误消息，模型下一步可自纠正（不再静默变 `{}`）。
3. token 累计超 `max_tokens_budget` → 下一步前 `RunError`（mock usage 验证）。
4. 墙钟超 `max_wall_seconds` → `RunError`。
5. 一次完整 run 产生预期 OTel span 树（`InMemorySpanExporter` 断言，不打网络）。
6. 超长工具结果被截断；`calculator` 对 `9**99999999` 类表达式拒绝/限幅。
7. ①全部原有测试不回归。

---

## 2. 架构与模块

**设计取向**：可观测性用 **OTel 注入式插桩**（给 loop/client/executor 注入一个 `Tracer`，默认 no-op），而非"从事件流反推 span"——这样 span 父子嵌套、上下文传播、重试挂载都符合 OTel 惯用法。①的**事件流保持不变、继续服务 UI**（也是未来 SQLite 全轨迹 sink 的订阅口）。即两个单一职责的接缝：**事件流 = UI / 未来轨迹**，**OTel 插桩 = 可观测性**。

```
src/harness/
├── telemetry/            [新增]
│   ├── tracer.py           get_tracer()：OTel 初始化，默认 no-op（无依赖也能跑）
│   └── setup.py            exporter 配置（console / otlp，可选）
├── reliability/          [新增]
│   ├── retry.py            RetryingModelClient（装饰 ModelClient，指数退避）
│   └── budget.py           BudgetTracker（token/时间累计 + 超限判定）+ BudgetExceeded
├── usage.py              [新增]  token 计数（真实 usage 优先 + tiktoken 兜底）+ 成本估算
├── llm/base.py           [改] StreamChunk 增加 usage / attempts（done chunk 携带）
├── llm/openai_compat.py  [改] 请求带 stream_options.include_usage；done 时吐 usage
├── loop/agent_loop.py    [改] 注入 tracer + BudgetTracker；步边界查预算；span 包裹；JSON 失败→自纠正回填
├── tools/base.py         [改] 工具结果超长截断
├── tools/builtins/calculator.py [改] 幂运算量级守卫
├── events.py             [改] 新增 ModelUsage 事件（供 UI 显示 token/成本/重试）
└── config.py             [改] 新增预算/重试/OTel/价格表配置项
```

**依赖新增**：`opentelemetry-api`、`opentelemetry-sdk`、`tiktoken`（`opentelemetry-exporter-otlp` 可选）。

**边界**：
- `RetryingModelClient` 仍实现 `ModelClient` 协议——loop 不知道下面是谁。
- `BudgetTracker` 是纯累计器，无 IO，可独立单测。
- `telemetry` 默认 no-op，删掉不影响 loop 逻辑（纯旁路观察）。

---

## 3. 可观测性（OpenTelemetry 注入式插桩）

**Tracer 接缝**：`telemetry/tracer.py::get_tracer()` 默认返回 OTel **no-op tracer**（不配时零开销）；`otel_enabled=True` 时按 `otel_exporter` 走 console 或 OTLP。loop/client/executor 各注入此 tracer。

**span 树**（每 run 一棵）：
```
run          {run_id, 最终状态, 总 token, 总成本, 总耗时}
├── step 1
│   ├── model_call {model, temperature, prompt/completion/total token, cost, latency, attempts}
│   │   └── [span event] messages 摘要 / 错误
│   └── tool_call:calculator {参数, is_error, 耗时}
├── step 2 ...
```

- **指标（metrics）**：`harness.tokens.total`、`harness.cost.usd`、`harness.model.latency`、`harness.retries`（OTel counter/histogram）。
- **完整 messages**：作为 span event 记录（重负载不进属性）。更完整的可查询留存 → 未来 `SQLiteTrajectorySink`（扩展点，本轮不做）。
- **错误**：异常 / `RunError` / 工具 `is_error` 记为 span status=ERROR + event。

---

## 4. 重试与自纠正（两层）

### 4.1 传输层 · `RetryingModelClient`（`reliability/retry.py`，装饰任意 `ModelClient`）
```
async def stream(messages, tools):
    for attempt in range(1, max_retries + 2):        # 1 次正常 + max_retries 次重试
        produced = False
        try:
            async for chunk in self._inner.stream(messages, tools):
                produced = True
                yield chunk
            return
        except <瞬时错误> as e:
            if produced:      raise                   # 流中途断裂：不可重试，避免重复 yield
            if attempt > max_retries: raise           # 耗尽 → 抛出 → loop 变 RunError
            await sleep(retry_base_delay * 2**(attempt-1) + 抖动)
            # OTel span event: 记录本次 retry 的原因/序号
```

- **安全约束**：只在**流尚未产出任何 chunk** 前失败才重试；**中途断裂**直接抛出（不重发半截输出）。
- **瞬时错误判定**：`openai` 的 `APITimeoutError` / `RateLimitError` / `APIConnectionError` / `InternalServerError`（5xx）；其余错误直接抛。
- 重试次数经 done chunk 的 `attempts` 字段上浮给 loop → `ModelUsage` 事件 + OTel。

### 4.2 循环层 · 自纠正（改 `loop/agent_loop.py`）
- ①的"工具参数 JSON 解析失败 → 静默 `{}`"改为：**回填一条 `is_error` 工具消息**（内容如"工具调用参数不是合法 JSON：<片段>，请重新调用"），模型下一步自行改正。
- 工具执行 `is_error`（含①已有的 pydantic 校验失败、非 dict 参数）沿用回填。
- 两者都受 `max_steps` + 预算兜底，防无限自纠正。

---

## 5. 资源上限

### 5.1 token 计数与成本（`usage.py`）
- 模型 done chunk 携带真实 `usage`（来自 `stream_options={"include_usage": true}`）；端点不返回时用 `tiktoken` 对 messages + 响应估算。归一成 `Usage(prompt, completion, total)`。
- 成本（可选）：`config.price_map`（`{model: [in_per_1k, out_per_1k]}`）配了算 `cost_usd`，否则 `None`。

### 5.2 `BudgetTracker`（`reliability/budget.py`，纯累计器）
```
累计 total_tokens、记录 start_time
check(): total_tokens > max_tokens_budget 或 elapsed > max_wall_seconds → raise BudgetExceeded
```
- **执行点**：loop 在**每步发起模型调用前** `check()`；命中 → `yield RunError("token/时间预算超限")` 终止（不启动付不起的一步）。model_call 结束后把 usage 累加进 tracker。
- `max_tokens_budget` / `max_wall_seconds` 为 `None` 时该维度不限。`max_steps`（已有）为第三道闸。

### 5.3 收尾项（代码审查推迟的）
- `tools/base.py`：工具结果超 `tool_result_max_chars` 截断并加"…(已截断)"标记，防超长结果撑爆上下文/预算。
- `tools/builtins/calculator.py`：幂运算前检查量级（指数过大或结果位数超模块常量上限）→ 抛 `ValueError`，防 `9**99999999` 类 DoS。

---

## 6. 配置 / 测试 / 依赖

### 新增配置（`config.py`，均有安全默认）
```
max_retries: int = 2
retry_base_delay: float = 0.5
max_tokens_budget: int | None = None       # None=不限
max_wall_seconds: float | None = None
tool_result_max_chars: int = 8000
otel_enabled: bool = False                  # 默认 no-op
otel_exporter: str = "console"              # console | otlp
otel_endpoint: str = ""
price_map: dict = {}                        # {model: [in_per_1k, out_per_1k]}
```
`calculator` 幂运算上限用模块常量（结果位数上限），不入 config。

### 测试策略（延续①：mock 优先、不打网络）
- `FlakyModelClient`（前 N 次抛瞬时错误后成功）→ 重试成功 / 耗尽 `RunError` / **流中途断裂不重试**。
- `BudgetTracker` 单测（token / 时间超限）；loop 用超预算 mock usage → 步边界 `RunError`。
- 自纠正：mock 吐非法 JSON 工具参数 → 断言回填 `is_error` 错误消息、模型下一步作答。
- 可观测性：OTel `InMemorySpanExporter` → 断言 span 树结构与属性（token / attempts），不打网络。
- `usage.py`：真实 usage 透传 + 无 usage 时 tiktoken 兜底。
- 工具结果截断、`calculator` 幂运算守卫单测。

### 依赖新增
`opentelemetry-api`、`opentelemetry-sdk`、`tiktoken`；`opentelemetry-exporter-otlp` 可选。

---

## 7. 后续衔接（备忘，非本次范围）

- **③** 用本层的 OTel 事件 + ①事件流接 `SQLiteTrajectorySink`（sqlite-vec），落全轨迹表 → 供 LLM-as-judge / 轨迹评测；checkpoint 用 `RunState` 序列化 + 轨迹恢复。
- 记忆（短/长/情景）、真实工具（web/code exec，走沙箱）、多 Agent 编排。
