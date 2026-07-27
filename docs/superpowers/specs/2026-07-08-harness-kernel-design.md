> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# AI Harness 内核骨架（子项目①）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用工具（非多租户 / 非企业级）
- **模型**：OpenAI 兼容端点（OpenAI / DeepSeek / Qwen / Kimi / 本地 vLLM、Ollama）

---

## 0. 背景与全局拆分

最终目标是一个"AI 学习助手"（AI 聊天含抓数据/文件入库/执行代码/生成图/模拟考试，以及知识库/题库/错题/下载管理）。本项目**先造底层 harness 框架**，再在其上搭应用。

完整 harness 被拆成有依赖顺序的子项目，本规格只覆盖**子项目①**：

| 子项目 | 内容 | 状态 |
|---|---|---|
| **① 内核骨架** | Agent Loop + 模型抽象 + 工具系统 + 上下文管理（最小可跑闭环） | **本规格** |
| ② 可靠性 | 错误重试 / 自纠正 + 轨迹记录(可观测) + 资源上限(token/时间/步数) + 断点续跑(轨迹副产品) | 后续 |
| ③ 能力扩展 | 记忆(短/长/情景, 向量库) + 沙箱执行(文件/shell/代码/浏览器) + 多 Agent 编排 | 后续 |
| ~~企业层~~ | 无状态 runtime / 队列 / 认证配额 / 审计 / 轨迹评测 CI 门禁 | **砍掉**（自用定位不需要） |

设计通则：严格 YAGNI；每个单元职责单一、接口清晰、可独立测试；后续能力靠**扩展点**接入，不回头改内核。

---

## 1. 范围与验收

### IN（本子项目要做）
一个纯 Python、**全异步**的最小 Agent 运行时，跑通闭环：

```
用户消息 → 组装上下文 → 调 LLM(流式) → 若请求工具 → 执行工具 → 结果回填 → 再调 LLM → … → 无工具调用则终止 → 返回最终答案
```

全程对外**吐结构化事件流**（供 UI 展示 agent 进度）。自带 1 个玩具工具 `calculator` 证明工具机制。

### OUT（属后续子项目，但预留扩展点，不写死）
真实工具（抓网页 / 代码执行 / RAG）、重试与自纠正强化、记忆 / 向量库、沙箱、多 Agent、持久化 / 断点续跑、模型路由降级。

### 验收标准
1. "帮我算 (12+8)×3" → 触发 `calculator` → 回填结果 → LLM 用结果作答。
2. 纯闲聊 → 不触发工具 → 直接流式作答。
3. `examples/demo.py` 能订阅事件流并逐条打印全过程。
4. `max_steps` 上限能正确终止，防死循环。
5. 单元测试用 **MockModelClient**（不打真实 API）覆盖 loop 所有分支。

---

## 2. 架构与模块边界

四个单元，职责单一、接口清晰、可独立测试：

```
src/harness/
├── events.py    事件类型（跨单元公共契约）
├── types.py     核心数据类型（Message / ToolCall / ToolResult / Role）
├── state.py     RunState（会话状态：消息历史 + 步数）
├── config.py    HarnessConfig（pydantic-settings）
├── llm/         模型抽象：ModelClient 协议 + OpenAICompatibleClient
│                 职责：prompt 组装、请求/响应、流式解析归一化。只管"跟模型对话"。
├── tools/       工具系统：Tool 基类 + ToolRegistry + ToolExecutor
│                 职责：定义/注册/调用工具、参数校验。不认识 LLM。
├── context/     上下文管理：ContextManager
│                 职责：决定每轮发给模型的消息列表。v1 只累加，留裁剪/RAG 钩子。
└── loop/        编排内核：AgentLoop
                  职责：串起上面三者、推进循环、发事件、管终止。唯一"知道全局"的单元。
```

**依赖方向（单向无环）**：`loop → {llm, tools, context}`；三者互不依赖，只共享 `events.py` / `types.py` / `state.py`。任一单元可单独换实现而不动调用方。

---

## 3. 核心数据类型与事件流

### 消息类型（贴合 OpenAI 兼容格式）
```python
Role       = system | user | assistant | tool
Message    = {role, content?, tool_calls?, tool_call_id?}
ToolCall   = {id, name, arguments: dict}
ToolResult = {tool_call_id, content: str, is_error: bool}
```

### 事件流
`AgentLoop.run()` 是 `async generator`，逐条 yield：

```python
RunStarted(run_id)
StepStarted(step)
TextDelta(text)                 # 流式 token 增量 → UI 实时打字
ToolCallRequested(tool_call)    # 模型决定调工具
ToolStarted(tool_call)
ToolFinished(tool_result)       # 含成功/报错
StepFinished(step)
RunFinished(final_message)      # 正常终止
RunError(error)                 # 异常终止
```

消费者：`async for event in loop.run(user_msg): ...`。这条事件流同时是子项目②"可观测性/轨迹记录"的天然数据源——记录器只是另一个订阅者。

---

## 4. Agent Loop 控制流

`AgentLoop` 持有 `RunState`（累积消息历史 + 步数），核心循环：

```
run(user_message):
  RunState.append(user_message)
  yield RunStarted
  for step in range(max_steps):
      yield StepStarted
      messages = context.build(RunState)          # 决定这轮发什么
      assistant_msg = <空壳>
      async for chunk in llm.stream(messages, tools.schemas()):
          if chunk.is_text:  yield TextDelta;  拼进 assistant_msg.content
          if chunk.is_tool:  拼进 assistant_msg.tool_calls
      RunState.append(assistant_msg)

      if not assistant_msg.tool_calls:            # 终止条件①：模型不再要工具（正常）
          yield RunFinished(assistant_msg); return
      yield ToolCallRequested(...)
      for tc in assistant_msg.tool_calls:         # v1 顺序执行；并发/子 agent 留扩展点
          yield ToolStarted(tc)
          result = await tools.execute(tc)        # 工具异常在此兜成 is_error
          RunState.append(result as tool Message)
          yield ToolFinished(result)
      yield StepFinished
  yield RunError("达到 max_steps 上限")            # 终止条件②：防死循环
```

- **终止条件**：① 模型返回无工具调用；② 步数达 `max_steps`。（token/时间上限留②）
- **扩展点**：多工具并发执行、子 agent 派发（把某"工具"实现成内嵌 loop）——接口留好，v1 顺序执行、不实现子 agent。

---

## 5. 模型抽象接口

```python
class ModelClient(Protocol):
    async def stream(self, messages, tools) -> AsyncIterator[StreamChunk]: ...

class StreamChunk:                       # 归一化增量，屏蔽 provider 差异
    type: "text" | "tool_call" | "done"
    text: str | None
    tool_call_delta: ... | None
```

v1 只实现 `OpenAICompatibleClient`（官方 `openai` async client，`base_url` 可指向任意兼容端点）。职责边界：**只把"消息列表 + 工具 schema"变成"归一化增量流"**，不碰重试、不碰路由。

**扩展点（留接口不实现）**：多 provider 注册、"贵模型规划 / 便宜模型执行"路由、失败降级——未来给 `ModelClient` 加实现即可，不动 loop。

---

## 6. 工具系统

### 定义（Pydantic 驱动，自动生成 function-calling JSON schema）
```python
class Tool(ABC):
    name: str
    description: str
    Params: type[BaseModel]              # 参数用 pydantic 声明 → 自动出 JSON schema
    async def run(self, params) -> str: ...

class CalculatorTool(Tool):              # v1 唯一玩具工具
    name = "calculator"
    class Params(BaseModel): expression: str
    async def run(self, p): return str(安全求值(p.expression))
```

- **注册**：`ToolRegistry` 收集工具，`.schemas()` 出给 LLM，`.get(name)` 供执行。
- **调用**：`ToolExecutor.execute(tool_call)` → pydantic 校验参数（**失败则回填 `is_error=True` 结果让模型自纠正，而非崩溃**）→ `await tool.run()` → 异常兜成 `is_error` 结果。
- **边界**：工具系统完全不认识 LLM，可脱离 harness 单测。
- **安全提示**：`calculator` 的表达式求值**不得用 `eval`**，用受限 AST 求值或白名单运算，避免任意代码执行。

---

## 7. 上下文管理

`ContextManager.build(RunState) -> list[Message]`，v1 极简：

```
[system_prompt]  +  RunState 的完整历史（含工具结果）
```

- **v1 不做**裁剪 / 压缩 / RAG 注入。
- `build()` 是**未来所有上下文策略的唯一插入点**：历史裁剪、摘要压缩、记忆检索注入都在这里加，loop 无感知。
- **边界**：`build()` 是纯函数（RunState → 消息列表），极易测试和替换。

---

## 8. 配置 / 错误边界 / 测试 / 技术选型

### 配置（pydantic-settings，读 `.env` / 环境变量）
`api_key`、`base_url`、`model`、`system_prompt`、`max_steps`、`temperature`、`request_timeout`。打包成 `HarnessConfig` 注入 `AgentLoop`。

### 错误边界（v1 最小，完整重试留②）
- 工具异常 / 参数校验失败 → 兜成 `is_error=True` 回填给模型（自纠正种子）。
- LLM API 报错 → v1 直接发 `RunError` 终止（**暂不重试**），但 client 设 `request_timeout`。

### 测试策略（TDD，先写测试）
核心 `MockModelClient`（实现 `ModelClient` 协议，脚本化吐预设 chunk），让 loop 分支**不打真实 API 即可确定性测试**：
- 无工具即终止 / 有工具则执行并回填 / `max_steps` 保护 / 参数校验错误路径 / 事件顺序。
- 另留 1 个真实端点集成测试（需 key，可 `skip`）。
- 栈：`pytest` + `pytest-asyncio`。

### 技术选型
Python 3.11+ · `openai`(async) · `pydantic` v2 · `pydantic-settings` · `pytest`/`pytest-asyncio` · 包管理 `uv`。**不用 LangChain 等框架**——自己造 harness 正是目的。

---

## 9. 目录结构

```
ai-learning-helper/
├── pyproject.toml
├── .env.example
├── src/harness/
│   ├── __init__.py
│   ├── events.py           # RunStarted / TextDelta / ToolStarted / ... 事件类型
│   ├── types.py            # Role / Message / ToolCall / ToolResult
│   ├── state.py            # RunState
│   ├── config.py           # HarnessConfig (pydantic-settings)
│   ├── llm/
│   │   ├── base.py         # ModelClient 协议 + StreamChunk
│   │   └── openai_compat.py# OpenAICompatibleClient
│   ├── tools/
│   │   ├── base.py         # Tool / ToolRegistry / ToolExecutor
│   │   └── builtins/
│   │       └── calculator.py
│   ├── context/
│   │   └── manager.py      # ContextManager
│   └── loop/
│       └── agent_loop.py   # AgentLoop
├── tests/
│   ├── conftest.py         # MockModelClient
│   ├── test_loop.py
│   ├── test_tools.py
│   └── test_context.py
├── examples/
│   └── demo.py             # 订阅事件流逐条打印
└── docs/superpowers/specs/2026-07-08-harness-kernel-design.md
```

---

## 10. 后续子项目衔接（备忘，非本次范围）

- **②** 在 `ModelClient` 外包一层重试/退避；给事件流挂 `TrajectoryRecorder`（落 JSONL）；`AgentLoop` 加 token/时间预算；`RunState` 可序列化 → 断点续跑。
- **③** 新增真实工具（web fetch / code exec，走沙箱）；`ContextManager.build()` 注入向量库检索的记忆；把"子 agent"实现成一个特殊工具（内嵌 loop、独立 RunState）。
