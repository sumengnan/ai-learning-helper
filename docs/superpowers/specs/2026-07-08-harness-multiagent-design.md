# AI Harness 多 Agent 编排（子项目③c）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用工具，在①②③a③b 之上扩展
- **前置**：①②③a③b-1③b-2 已完成并在 `main`

---

## 0. 背景与范围

把"子 agent 派发"实现成主 loop 的一个特殊工具，**复用①的 `AgentLoop`**：子 agent = 又一个 `AgentLoop` + 独立 `RunState`。目的=突破单一上下文窗口限制 + 职责分工。本规格覆盖顺序派发的完整机制。

**存储/可观测性**：沿用 SQLite+sqlite-vec / OpenTelemetry（子 loop span 自动嵌套在 dispatch 的 `tool_call` span 下）。

设计通则：严格 YAGNI；loop 零改动；测试复用 MockModelClient 不打网络；共享预算 + 深度上限防烧钱/递归；不回归既有。

---

## 1. 范围与验收

### IN
- `AgentSpec`（具名角色：name/description/system_prompt/tool_names）+ `AgentRoster`（花名册）。
- `DispatchTool`（`dispatch(agent, task)` 工具）：查花名册 → 用角色 prompt + 受控工具子集建全新子 `AgentLoop`（独立 RunState）→ 跑到结束 → 返回子 agent 最终答案。
- **控制**：全树共享一个 `BudgetTracker`（token/时间总上限）；`max_dispatch_depth` 深度上限（达上限的子 agent 不再拥有 `dispatch`）。
- **可观测**：子 agent 运行的 span 自动嵌套在 `dispatch` 的 tool_call span 下。

### OUT（预留扩展点）
并行派发（`dispatch_parallel`）、每角色不同模型（贵/便宜路由）、子 agent 结果结构化 schema、跨 session 的 agent 记忆。

### 验收标准
1. `dispatch(agent="researcher", task="…")` → 子 loop 用该角色 prompt+工具子集跑完 → 返回其最终答案（单个共享 MockModelClient 脚本化：主派发→子作答→主汇总）。
2. 未知角色名 → `is_error` + 明确提示（列可用角色）。
3. 子 agent 工具子集受控：只拿到 spec 列的工具（断言子 registry）。
4. 深度上限：达 `max_dispatch_depth` 的子 agent registry **不含** `dispatch`。
5. 共享预算：主+子 token 消耗累加到同一 `BudgetTracker`。
6. ①②③a③b 原有测试不回归。

---

## 2. 架构与模块 + 装配

**核心洞察**：子 agent = 又一个 `AgentLoop` + 独立 `RunState`。`DispatchTool.run()` 把子 loop 跑到 `RunFinished`、取 `message.content` 返回。子 agent 的完整对话留在它自己的 RunState，只有最终答案回主上下文——突破单上下文。

```
src/harness/orchestration/     [新增]
├── __init__.py
├── spec.py       AgentSpec + AgentRoster
└── dispatch.py   DispatchTool（建子 loop + 跑 + 返回汇总）
src/harness/config.py          [改] max_dispatch_depth、sub_agent_max_steps
```

**依赖方向（无环）**：`orchestration → {loop, tools, context, state, events}`；`loop` 不依赖 `orchestration`（`DispatchTool` 只是又一个 `Tool`）。**依赖新增：无**（纯复用①②③）。

**装配数据流**（App 层）：
```
建全部工具实例 → 工具池 {name: Tool}
roster = [AgentSpec("researcher", "...", PROMPT, ["browse","http_request","search_memory"]),
          AgentSpec("coder", "...", PROMPT, ["run_python","run_shell","write_file","read_file"]), …]
DispatchTool(roster, 工具池, client, budget, tracer, depth=0, max_depth, sub_max_steps, model_name, price_map)
主 registry = [DispatchTool]（+ 可选主 agent 直接工具）
主 AgentLoop(client, 主 registry, ContextManager(主 prompt), budget=共享, …)
```

**边界**：
- `AgentSpec`/`AgentRoster` 纯数据/查找，独立单测。
- `DispatchTool` 是唯一"知道如何用 spec 建子 loop"的单元；是个 `Tool`（与①`CalculatorTool` 同构），内部编排子 loop。
- 子 loop 复用①②全部机制（事件流/重试/预算/OTel），零改动 loop。

---

## 3. AgentSpec + AgentRoster

```python
@dataclass
class AgentSpec:
    name: str
    description: str            # 给主 agent 看的能力说明
    system_prompt: str          # 子 agent 的 system prompt
    tool_names: list[str]       # 该角色可用的工具名（从工具池选子集）

class AgentRoster:
    def __init__(self, specs: list[AgentSpec]): ...
    def get(self, name: str) -> AgentSpec | None: ...
    def names(self) -> list[str]: ...
    def describe(self) -> str:   # "可派发角色清单"文本，注入 dispatch 工具 description
```

- 纯数据 + 查找，无 IO。
- `describe()` 让主 agent 在工具描述里看到有哪些角色、各自能干什么。

---

## 4. DispatchTool

```python
class DispatchTool(Tool):
    name = "dispatch"
    # description 动态含 roster.describe()

    class Params(BaseModel):
        agent: str
        task: str

    def __init__(self, roster, tool_pool, client, budget, tracer,
                 depth, max_depth, sub_max_steps, model_name, price_map): ...

    async def run(self, params) -> str:
        spec = self._roster.get(params.agent)
        if spec is None:
            raise ValueError(f"未知角色：{params.agent}。可用：{self._roster.names()}")  # → is_error
        sub_registry = self._build_sub_registry(spec)
        sub_loop = AgentLoop(
            client=self._client, registry=sub_registry,
            context=ContextManager(spec.system_prompt),
            max_steps=self._sub_max_steps, budget=self._budget,     # 共享预算
            tracer=self._tracer, model_name=self._model_name, price_map=self._price_map)
        final, error = None, None
        async for ev in sub_loop.run(params.task):
            if isinstance(ev, RunFinished):
                final = ev.message.content
            elif isinstance(ev, RunError):
                error = ev.error
        if final is None:
            raise RuntimeError(f"子 agent[{params.agent}] 未产出结果：{error or '未知'}")  # → is_error
        return final
```

**`_build_sub_registry(spec)`**：
```
reg = ToolRegistry()
for name in spec.tool_names:
    tool = tool_pool.get(name)
    if tool: reg.register(tool)          # 缺失名字跳过（装配错误，可日志）
if self._depth + 1 < self._max_depth:    # 未达上限才给下一层派发能力
    reg.register(DispatchTool(roster, tool_pool, client, budget, tracer,
                              depth=self._depth+1, max_depth=self._max_depth,
                              sub_max_steps, model_name, price_map))
return reg
```

- **深度**：递归构造 `DispatchTool(depth+1)`；达 `max_depth` 不再注入 → 子 agent 不能再派发。
- **预算**：同一 `BudgetTracker` 贯穿全树；子 loop 每步查预算、累加 usage（②机制原样生效）→ 总量受控。
- **可观测**：`DispatchTool.run` 在②的 `tool_call:dispatch` span 内执行，子 loop 的 run/step/model/tool span 自动嵌套；给 span 打 `agent` 名与子 `run_id`。
- **错误**：未知角色 / 子 agent 无结果（RunError/超预算）→ 抛异常 → ②的 `ToolExecutor` 兜成 `is_error` 回填，主 agent 可自纠正。
- **工具共享注意**：子 agent 复用工具池里**同一批工具实例**（如共享 `Sandbox`/`Memory`）——单用户顺序执行下安全；并行派发（OUT）才需考虑实例隔离。

---

## 5. 配置 / 测试 / 依赖

### 新增配置（`config.py`）
```
max_dispatch_depth: int = 2       # agent 树最大层数（防无限递归）
sub_agent_max_steps: int = 10     # 子 agent 单次 run 步数上限
```

### 测试策略（复用 MockModelClient，不打网络）
- `AgentSpec`/`AgentRoster`：`get`/`names`/`describe` 单测。
- `_build_sub_registry`：子 registry 只含 spec 列的工具；`depth+1<max` 含 `dispatch`、达上限**不含**（断言 `registry.get("dispatch")`）。
- `DispatchTool.run`：`MockModelClient([text_turn("子结果")])` → `run(agent="researcher", task="…")` 返回 "子结果"；未知角色经 `ToolExecutor` → `is_error`。
- **集成**：主 `AgentLoop` 注册 `DispatchTool` + 单个共享 `MockModelClient` 脚本 `[主派发turn, 子作答turn, 主汇总turn]` → 断言 dispatch `ToolFinished` 含子结果、末尾 `RunFinished` 是汇总。
- **共享预算**：带 usage 的 mock + 小 `BudgetTracker` → dispatch 后断言 `budget.total_tokens` 含子 agent 消耗。

### 依赖新增
无（纯复用①②③）。

---

## 6. 后续衔接（备忘，非本次范围）

- **并行派发**：给①加并发工具执行后，`dispatch_parallel([...])` 一步内并发多子 agent + 汇总。
- **模型路由**：`AgentSpec.model` + `DispatchTool` 持 client 解析器（贵模型规划、便宜模型执行）。
- **③d 持久化**：agent 树的 checkpoint/断点续跑（子 loop RunState 序列化）。
- App 层：主 agent 面向学习助手总任务，把"查资料"派 researcher、"跑代码/出题"派 coder，汇总成答案。
