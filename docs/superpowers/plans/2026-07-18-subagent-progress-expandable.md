> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# 子代理执行进度：人类可读 + 每步工具调用可展开 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 让"子代理执行"进度块（编排器 executor:sN 与 dispatch 两条 `subagent:` 通道）显示人类可读的步骤描述，并让每一次工具调用像主聊天"工具调用"块那样可展开查看入参/结果。

**架构：** 子代理工具明细走 **UI 专用的 `progress` 通道**，不走 `steps`——`steps` 会被 `conversations.py:57-66` 当作 LLM 历史回放，灌入子代理内部几十次工具调用会污染上下文。做法：给 `Progress` 事件加一个可选 `detail` 字段承载 `{tool,args,result,is_error}`；执行器/派发器每步先发一条"头行"（text=步骤描述/任务），工具行带 `detail`。前端把 `subagent:` 进度按 agent 分组、组标题用头行文字、带 detail 的行渲染成可展开 Accordion（复用主聊天工具块的参数/结果渲染）。

**技术栈：** 后端 Python（dataclass 事件 + pytest，`asyncio_mode=auto`）；前端 React + MUI + TypeScript + vitest。

**零行为变更保证：** `detail` 默认 `None`，非工具进度行（沙箱/技能/校验/来源等）不受影响；`steps` 通道与主聊天工具块一字不动。

---

## 文件结构

**后端：**
- `src/harness/events.py` — `Progress` dataclass 加可选 `detail` 字段
- `src/harness/persistence/serialize.py` — `event_to_dict` 的 Progress 分支带上 `detail`
- `app/orchestration/executor.py` — 每步发"头行"（步骤描述）+ 工具行带 `detail`
- `src/harness/orchestration/dispatch.py` — 工具行带 `detail`（任务头行已有）
- `app/api/chat.py` — `_drain` 落 `progress` 行时带上 `detail`

**前端：**
- `web/src/types.ts` — `ChatMessage.progress` 项加 `detail?`
- `web/src/api/client.ts` — 刷新接口 progress 类型加 `agent?` + `detail?`
- `web/src/components/ChatView.tsx` — live SSE 的 Progress 处理带上 `detail`；子代理块改用新组件
- `web/src/components/ToolCallDetail.tsx` — **新建**：从 `AgentProgress` 抽出的"参数/结果"展开渲染，供两处复用
- `web/src/components/AgentProgress.tsx` — 改用抽出的 `ToolCallDetail`（纯重构，行为不变）
- `web/src/components/SubagentProgress.tsx` — **新建**：按 agent 分组、组标题用步骤描述、工具行可展开
- `web/src/pages/ChatPage.tsx` — 刷新映射透传 `detail`（progress 已整体透传，仅类型放行）

每个任务产出独立、可测的变更。后端 4 个任务在前，前端 5 个任务在后（前端依赖后端 `detail` 字段存在，但因是可选字段，前端可独立开发用假数据测）。

---

## 任务 1：Progress 事件加 detail 字段 + 序列化

**文件：**
- 修改：`src/harness/events.py`
- 修改：`src/harness/persistence/serialize.py`
- 测试：`tests/test_serialize.py`（若无则新建）或追加到既有序列化测试

- [ ] **步骤 1：编写失败的测试**

在序列化测试文件追加（先确认既有测试文件名：`ls tests/ | grep -i serial`，无则新建 `tests/test_serialize_progress_detail.py`）：

```python
from harness.events import Progress
from harness.persistence.serialize import event_to_dict


def test_progress_detail_serialized():
    ev = Progress(scope="subagent:executor:s1", text="调用工具 x",
                  status="ok", key="c1",
                  detail={"tool": "x", "args": {"q": "a"}, "result": "r", "is_error": False})
    d = event_to_dict(ev)
    assert d["type"] == "Progress"
    assert d["data"]["detail"] == {"tool": "x", "args": {"q": "a"}, "result": "r", "is_error": False}


def test_progress_detail_defaults_none():
    d = event_to_dict(Progress(scope="sandbox", text="启动…"))
    assert d["data"]["detail"] is None
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_serialize_progress_detail.py -q`
预期：FAIL（`Progress.__init__` 不认 `detail` / 序列化无 `detail` 键）

- [ ] **步骤 3：实现**

`src/harness/events.py`，在 `Progress` 类末尾加字段：

```python
    agent: str | None = None    # 归属子 agent 名：沙箱步骤在子 agent 执行期间由 emit 自动打标
    detail: dict | None = None  # 工具调用明细（tool/args/result/is_error）供前端展开；普通进度行为 None
```

`src/harness/persistence/serialize.py`，Progress 分支（约 72-74 行）带上 detail：

```python
    elif isinstance(ev, Progress):
        data = {"scope": ev.scope, "text": ev.text, "status": ev.status, "key": ev.key,
                "agent": ev.agent, "detail": ev.detail}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_serialize_progress_detail.py -q`
预期：PASS（2 passed）

- [ ] **步骤 5：回归事件/序列化既有测试**

运行：`uv run pytest tests/ -k "serial or event" -q`
预期：PASS（既有事件消费者不受可选字段影响）

- [ ] **步骤 6：Commit**

```bash
git add src/harness/events.py src/harness/persistence/serialize.py tests/test_serialize_progress_detail.py
git commit -m "feat(events): Progress 加可选 detail 字段承载工具调用明细"
```

---

## 任务 2：Executor 发步骤头行 + 工具行带 detail

**文件：**
- 修改：`app/orchestration/executor.py`
- 测试：`tests/test_orchestration_executor.py`

- [ ] **步骤 1：编写失败的测试**

先看既有测试搭建（`tests/test_orchestration_executor.py` 里怎么造假 client/registry、怎么断言 yield 的事件），仿其风格追加。断言三点：(a) 首个进度是步骤描述头行；(b) 工具行 detail 带 tool+args；(c) 工具完成行 detail 带 result+is_error（且 args 仍在，因前端按 key 合并只留最后一条）。

```python
async def test_executor_emits_step_header_and_tool_detail(make_mock):
    # make_mock 造一个会调用一次工具再结束的 client（参照既有 executor 测试的 mock 用法）
    from app.orchestration.executor import Executor, StepArtifact
    from app.orchestration.plan import PlanStep, Artifact
    from harness.events import Progress
    from harness.tools.base import ToolRegistry
    # ……按既有测试方式构造 registry + client，使其发起一次名为 "calc" 的工具调用……
    ex = Executor(client=..., registry=..., system_prompt="sp", model="m")
    step = PlanStep(id="s1", description="调研快排", expected="要点")
    evs = [ev async for ev in ex.execute(step, {})]
    progs = [e for e in evs if isinstance(e, Progress)]
    # (a) 头行：文字是步骤描述
    assert progs[0].text == "调研快排" and progs[0].key == "__hdr__:s1"
    # (b)/(c) 工具行带 detail
    tool_rows = [p for p in progs if p.detail is not None]
    assert any(r.detail.get("tool") == "calc" and "args" in r.detail for r in tool_rows)
    fin = [r for r in tool_rows if "result" in (r.detail or {})]
    assert fin and fin[-1].detail["args"] is not None   # 完成行保留 args（前端按 key 合并只留最后一条）
    assert isinstance(evs[-1], StepArtifact)
```

> 注：如既有 executor 测试用的是别的 mock 夹具名（如 `make_mock`/`tool_turn`），照抄那套；关键是让子 loop 真的发一次 ToolStarted+ToolFinished。

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_orchestration_executor.py -k step_header -q`
预期：FAIL（无头行；工具行 detail 为 None）

- [ ] **步骤 3：实现**

`app/orchestration/executor.py` 的 `execute`，改事件循环这段（原 64-85 行）：

```python
        scope = f"subagent:executor:{step.id}"
        final_text = ""
        error = None
        tool_names: dict[str, str] = {}
        tool_args: dict[str, object] = {}   # 暂存入参，供完成行带全（前端按 key 合并只留最后一条）
        token = set_current_agent(f"executor:{step.id}")
        try:
            # 步骤头行：让前端分组标题显示步骤描述而非裸 id（s1）
            yield Progress(scope, step.description, status="running", key=f"__hdr__:{step.id}")
            async for ev in loop.run(prompt):
                if isinstance(ev, ToolStarted):
                    tc = ev.tool_call
                    tool_names[tc.id] = tc.name
                    tool_args[tc.id] = tc.arguments
                    yield Progress(scope, f"调用工具 {tc.name}", status="running", key=tc.id,
                                   detail={"tool": tc.name, "args": tc.arguments})
                elif isinstance(ev, ToolFinished):
                    r = ev.result
                    name = tool_names.get(r.tool_call_id, "工具")
                    yield Progress(scope, f"调用工具 {name}",
                                   status="error" if r.is_error else "ok", key=r.tool_call_id,
                                   detail={"tool": name, "args": tool_args.get(r.tool_call_id),
                                           "result": r.content, "is_error": r.is_error})
                elif isinstance(ev, RunFinished):
                    final_text = ev.message.content or ""
                elif isinstance(ev, RunError):
                    error = ev.error
        finally:
            reset_current_agent(token)
        yield StepArtifact(Artifact(summary=final_text, data={}, files=[]), error=error)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_orchestration_executor.py -q`
预期：PASS

- [ ] **步骤 5：回归编排器测试**

运行：`uv run pytest tests/test_orchestration_orchestrator.py -q`
预期：PASS（编排器只透传 executor 的 Progress，多出的头行/detail 不影响其调度与 StepArtifact 消费）

- [ ] **步骤 6：Commit**

```bash
git add app/orchestration/executor.py tests/test_orchestration_executor.py
git commit -m "feat(orchestration): executor 发步骤描述头行 + 工具行带 detail 明细"
```

---

## 任务 3：Dispatch 工具行带 detail

**文件：**
- 修改：`src/harness/orchestration/dispatch.py`
- 测试：`tests/test_dispatch.py`

- [ ] **步骤 1：编写失败的测试**

`tests/test_dispatch.py` 已有 `test_dispatch_emits_subagent_progress`（第 22 行）。追加一个断言工具行带 detail 的：

```python
async def test_dispatch_tool_progress_carries_detail(...):
    # 复用 test_dispatch_emits_subagent_progress 的搭建，让子 agent 调一次工具
    # 收集 emit 出的 Progress，断言工具行 detail 带 tool/args，完成行带 result/is_error
    ...
    tool_rows = [p for p in emitted if isinstance(p, Progress) and p.detail is not None]
    assert any(r.detail.get("tool") for r in tool_rows)
    assert any("result" in (r.detail or {}) for r in tool_rows)
```

> 按 `tests/test_dispatch.py` 既有 mock/emit 捕获方式落地（它已验证 subagent progress，扩一条 detail 断言即可）。

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_dispatch.py -k detail -q`
预期：FAIL（dispatch 工具行 detail 为 None）

- [ ] **步骤 3：实现**

`src/harness/orchestration/dispatch.py` 的 `run`，ToolStarted/ToolFinished 分支（约 80-89 行）加 detail + 暂存 args：

```python
        tool_names: dict[str, str] = {}
        tool_args: dict[str, object] = {}
        agent_token = set_current_agent(params.agent)
        try:
            async for ev in sub_loop.run(params.task):
                if isinstance(ev, ToolStarted):
                    tool_names[ev.tool_call.id] = ev.tool_call.name
                    tool_args[ev.tool_call.id] = ev.tool_call.arguments
                    emit(Progress(scope, f"调用工具 {ev.tool_call.name}",
                                  status="running", key=ev.tool_call.id,
                                  detail={"tool": ev.tool_call.name, "args": ev.tool_call.arguments}))
                elif isinstance(ev, ToolFinished):
                    r = ev.result
                    name = tool_names.get(r.tool_call_id, "工具")
                    emit(Progress(scope, f"调用工具 {name}",
                                  status="error" if r.is_error else "ok", key=r.tool_call_id,
                                  detail={"tool": name, "args": tool_args.get(r.tool_call_id),
                                          "result": r.content, "is_error": r.is_error}))
                elif isinstance(ev, RunFinished):
                    final = ev.message.content
                elif isinstance(ev, RunError):
                    error = ev.error
```

（`emit(Progress(scope, f"开始任务：{params.task}"))` 的任务头行保持不变，作分组标题。）

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_dispatch.py -q`
预期：PASS（既有 + 新增）

- [ ] **步骤 5：Commit**

```bash
git add src/harness/orchestration/dispatch.py tests/test_dispatch.py
git commit -m "feat(orchestration): dispatch 工具行带 detail 明细"
```

---

## 任务 4：chat.py 落库 progress 行带 detail

**文件：**
- 修改：`app/api/chat.py`
- 测试：`tests/test_orchestration_chat_route.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_orchestration_chat_route.py` 追加：FakeOrchestrator 多发一条带 detail 的 Progress，断言 SSE 事件里该 Progress 的 `data.detail` 到达，且落库 `ui_messages` 的 progress 行带 detail。

```python
class DetailOrchestrator:
    async def run(self, message):
        from harness.events import RunStarted, Progress, TextDelta, RunFinished
        from harness.types import Message, Role
        yield RunStarted(run_id="r1")
        yield Progress("subagent:executor:s1", "调用工具 calc", status="ok", key="c1",
                       detail={"tool": "calc", "args": {"x": 1}, "result": "2", "is_error": False})
        yield TextDelta(text="答复")
        yield RunFinished(message=Message(role=Role.ASSISTANT, content="答复"))


def test_orchestrator_progress_detail_streamed_and_persisted(make_mock, monkeypatch):
    c, store = _client(make_mock, monkeypatch,
                       enable_orchestrator=True, orchestrator=DetailOrchestrator())
    cid, events = _chat(c, _auth(c))
    prog = [e for e in events if e["type"] == "Progress"
            and e["data"].get("scope") == "subagent:executor:s1"]
    assert prog and prog[-1]["data"]["detail"]["tool"] == "calc"
    # 刷新后落库仍带 detail
    rows = [m for m in store.ui_messages(cid) if m["role"] == "assistant"]
    saved = [p for m in rows for p in (m.get("progress") or [])
             if p.get("scope") == "subagent:executor:s1"]
    assert saved and saved[-1]["detail"]["result"] == "2"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_orchestration_chat_route.py -k detail -q`
预期：FAIL（`_drain` 攒 progress 时丢了 detail → SSE/落库都无 detail）

- [ ] **步骤 3：实现**

`app/api/chat.py` 的 `_drain` 里 Progress 收集（约 779-782 行）带上 detail：

```python
                    elif isinstance(ev, Progress):
                        collect["progress"].append({"scope": ev.scope, "text": ev.text,
                                                    "status": ev.status, "key": ev.key,
                                                    "agent": ev.agent, "detail": ev.detail})
```

（SSE 下发用的是 `event_to_dict`（任务 1 已带 detail），故 SSE 侧无需再改；此处只补落库那份。）

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_orchestration_chat_route.py -q`
预期：PASS

- [ ] **步骤 5：回归 chat 测试**

运行：`uv run pytest tests/ -k chat -q`
预期：PASS（detail 为可选、既有进度行为 None，零行为变更）

- [ ] **步骤 6：Commit**

```bash
git add app/api/chat.py tests/test_orchestration_chat_route.py
git commit -m "feat(orchestration): chat 落库 progress 行带 detail"
```

---

## 任务 5：前端类型 + 数据管道透传 detail

**文件：**
- 修改：`web/src/types.ts`
- 修改：`web/src/api/client.ts`
- 修改：`web/src/components/ChatView.tsx`（live Progress 处理）
- 修改：`web/src/pages/ChatPage.tsx`（刷新映射——已整体透传 progress，仅确认类型放行）
- 测试：`web/src/components/ChatView.test.tsx`（追加）

- [ ] **步骤 1：编写失败的测试**

`web/src/components/ChatView.test.tsx` 追加：喂一个带 detail 的 Progress SSE 事件，断言消息的 `progress[0].detail` 被保留。参照该文件既有 SSE 事件注入方式。

```tsx
it("preserves Progress.detail through live SSE", async () => {
  // ……按既有测试注入一个 subagent:executor:s1 的 Progress 事件，data.detail 带 tool/args/result……
  // 断言渲染出的 message.progress 里该行 detail.tool === "calc"
  // （或直接断言 SubagentProgress 展开后能看到入参——见任务 7 后再补断言）
});
```

> 若 ChatView.test 难以直接断言内部 state，可把该断言并入任务 7 的 SubagentProgress 测试（端到端渲染断言）。此步至少保证类型编译通过 + 不丢字段。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd web && npx tsc -b && npm test -- ChatView`
预期：类型错误或断言失败（detail 未透传）

- [ ] **步骤 3：实现**

`web/src/types.ts`，`progress` 项加 detail：

```ts
  progress?: {
    scope: string; text: string;
    status?: "running" | "ok" | "error" | null;
    key?: string | null; agent?: string | null;
    detail?: { tool: string; args?: unknown; result?: string; is_error?: boolean } | null;
  }[];
```

`web/src/api/client.ts`，刷新 messages 的 progress 类型（约 190 行）补 agent+detail：

```ts
    progress?: { scope: string; text: string; status?: "running" | "ok" | "error" | null;
                 key?: string | null; agent?: string | null;
                 detail?: { tool: string; args?: unknown; result?: string; is_error?: boolean } | null }[] | null;
```

`web/src/components/ChatView.tsx`，通用 Progress 处理（约 275 行）带上 detail：

```ts
    else if (e.type === "Progress") upd((a) => { (a.progress ||= []).push({ scope: e.data.scope, text: e.data.text, status: e.data.status, key: e.data.key, agent: e.data.agent, detail: e.data.detail }); });
```

`web/src/pages/ChatPage.tsx`：`progress: m.progress ?? undefined`（约 43 行）已整体透传，detail 随之带入——无需改逻辑，仅任务 5 的类型改动使其编译通过。

- [ ] **步骤 4：运行测试验证通过**

运行：`cd web && npx tsc -b && npm test -- ChatView`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add web/src/types.ts web/src/api/client.ts web/src/components/ChatView.tsx
git commit -m "feat(web): progress 管道透传 detail 字段"
```

---

## 任务 6：抽出可复用的 ToolCallDetail 组件（纯重构）

**文件：**
- 新建：`web/src/components/ToolCallDetail.tsx`
- 修改：`web/src/components/AgentProgress.tsx`（改用抽出的组件）
- 测试：`web/src/components/AgentProgress.test.tsx`（既有，须继续通过）

- [ ] **步骤 1：先跑既有测试建立基线**

运行：`cd web && npm test -- AgentProgress`
预期：PASS（记录当前绿）

- [ ] **步骤 2：新建 ToolCallDetail**

`web/src/components/ToolCallDetail.tsx`——把 `AgentProgress.tsx` 的 `AccordionDetails` 内容（原 133-172 行的参数/结果两块）+ `stripIdMarkers`（16-17 行）搬过来：

```tsx
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";

const ID_MARKER_RE = /〔(?:下载|知识|题目)ID:[^〕]*〕/g;
export const stripIdMarkers = (t?: string) => (t || "").replace(ID_MARKER_RE, "").trimEnd();

// 工具调用的「参数 + 结果」明细：蓝框参数 / 绿成功·红失败结果。主聊天工具块与子代理块共用。
export function ToolCallDetail({ args, result, isError }: {
  args?: unknown; result?: string; isError?: boolean;
}) {
  return (
    <>
      <Box sx={{ mb: 0.5, px: 1, py: 0.5, borderRadius: 0.5, borderLeft: 3,
                 borderColor: "info.main", bgcolor: (t) => alpha(t.palette.info.main, 0.08) }}>
        <Typography variant="caption" sx={{ fontWeight: 700, color: "info.main" }}>参数</Typography>
        <Typography variant="caption" component="pre"
          sx={{ m: 0, fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
          {JSON.stringify(args, null, 2)}
        </Typography>
      </Box>
      {result !== undefined && (
        <Box sx={{ px: 1, py: 0.5, borderRadius: 0.5, borderLeft: 3,
                   borderColor: isError ? "error.main" : "success.main",
                   bgcolor: (t) => alpha((isError ? t.palette.error : t.palette.success).main, 0.1) }}>
          <Typography variant="caption"
            sx={{ fontWeight: 700, color: isError ? "error.main" : "success.main" }}>
            {isError ? "结果 · 失败" : "结果 · 成功"}
          </Typography>
          <Typography variant="caption" component="pre"
            sx={{ m: 0, fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all",
                  color: "text.primary" }}>
            {stripIdMarkers(result)}
          </Typography>
        </Box>
      )}
    </>
  );
}
```

- [ ] **步骤 3：AgentProgress 改用它**

`web/src/components/AgentProgress.tsx`：删掉本地 `ID_MARKER_RE`/`stripIdMarkers`（16-17 行），改 `import { ToolCallDetail } from "./ToolCallDetail"`；把 `AccordionDetails`（133-172 行）内容替换为：

```tsx
            <AccordionDetails sx={{ px: 0, pt: 0 }}>
              <ToolCallDetail args={s.args} result={s.result} isError={s.isError} />
            </AccordionDetails>
```

- [ ] **步骤 4：跑既有测试验证行为不变**

运行：`cd web && npx tsc -b && npm test -- AgentProgress`
预期：PASS（渲染结果与重构前一致）

- [ ] **步骤 5：Commit**

```bash
git add web/src/components/ToolCallDetail.tsx web/src/components/AgentProgress.tsx
git commit -m "refactor(web): 抽出 ToolCallDetail 供工具明细复用"
```

---

## 任务 7：新建 SubagentProgress——分组 + 步骤描述标题 + 可展开工具行

**文件：**
- 新建：`web/src/components/SubagentProgress.tsx`
- 测试：`web/src/components/SubagentProgress.test.tsx`

- [ ] **步骤 1：编写失败的测试**

`web/src/components/SubagentProgress.test.tsx`（参照 `ProgressBlock.test.tsx` 的渲染断言方式）：

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { SubagentProgress } from "./SubagentProgress";

const items = [
  { scope: "subagent:executor:s1", text: "调研快排", status: "running" as const, key: "__hdr__:s1" },
  { scope: "subagent:executor:s1", text: "调用工具 calc", status: "ok" as const, key: "c1",
    detail: { tool: "calc", args: { x: 1 }, result: "2", is_error: false } },
];

it("按步骤描述作标题、工具行可展开看入参结果", async () => {
  render(<SubagentProgress items={items} live={false} stopped={false} status="ok" />);
  // 展开外层折叠块（若默认折叠，先点开——参照 ProgressBlock.test 的展开方式）
  // 组标题用步骤描述而非裸 id
  expect(screen.getByText("调研快排")).toBeInTheDocument();
  // 工具行可展开出参数/结果
  fireEvent.click(screen.getByText(/调用工具|calc/));
  expect(await screen.findByText("参数")).toBeInTheDocument();
  expect(screen.getByText(/结果 · 成功/)).toBeInTheDocument();
});

it("无 items 渲染 null", () => {
  const { container } = render(<SubagentProgress items={[]} live={false} stopped={false} status="ok" />);
  expect(container.firstChild).toBeNull();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd web && npm test -- SubagentProgress`
预期：FAIL（组件不存在）

- [ ] **步骤 3：实现**

`web/src/components/SubagentProgress.tsx`：

```tsx
import { Accordion, AccordionSummary, AccordionDetails, Box, Typography, Chip, CircularProgress } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import { CollapsibleBlock } from "./CollapsibleBlock";
import { ToolCallDetail } from "./ToolCallDetail";

type Item = {
  scope: string; text: string;
  status?: "running" | "ok" | "error" | null; key?: string | null;
  detail?: { tool: string; args?: unknown; result?: string; is_error?: boolean } | null;
};

// scope 形如 subagent:executor:s1 / subagent:researcher → 取冒号后的 agent 标识
const agentOf = (scope: string) =>
  scope.startsWith("subagent:") ? scope.slice("subagent:".length) : scope;

// 按 agent 分组、保序（首次出现顺序）
function groupByAgent(items: Item[]): { agent: string; rows: Item[] }[] {
  const groups: { agent: string; rows: Item[] }[] = [];
  const at = new Map<string, number>();
  for (const p of items) {
    const ag = agentOf(p.scope);
    let i = at.get(ag);
    if (i === undefined) { i = groups.length; at.set(ag, i); groups.push({ agent: ag, rows: [] }); }
    groups[i].rows.push(p);
  }
  return groups;
}

// 同 key 的开始/完成折叠成一行（后到覆盖），保留末态（带 result 的完成行）
function mergeByKey(items: Item[]): Item[] {
  const rows: Item[] = []; const pos = new Map<string, number>();
  for (const p of items) {
    if (p.key) { const i = pos.get(p.key); if (i !== undefined) rows[i] = p; else { pos.set(p.key, rows.length); rows.push(p); } }
    else rows.push(p);
  }
  return rows;
}

function rowIcon(p: Item, live: boolean) {
  if (p.status === "error" || p.detail?.is_error) return <CancelIcon sx={{ fontSize: 16 }} color="error" />;
  if (p.status === "running") return live ? <CircularProgress size={12} /> : <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />;
  return <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />;
}

export function SubagentProgress({ items, live, stopped, status }: {
  items: Item[]; live: boolean; stopped: boolean;
  status: "running" | "ok" | "error" | "stopped";
}) {
  if (!items.length) return null;
  const groups = groupByAgent(items);
  const summary = (
    <Typography variant="caption" color="text.secondary">
      {groups.length} 个子步骤
    </Typography>
  );
  return (
    <CollapsibleBlock icon={<AccountTreeIcon sx={{ fontSize: 15 }} color="action" />}
      title="子代理执行" status={status} summary={summary}>
      {groups.map((g, gi) => {
        const rows = mergeByKey(g.rows);
        // 组标题：头行（__hdr__ / 无 detail 的首行）文字，回退到 agent 名
        const header = rows.find((r) => (r.key || "").startsWith("__hdr__")) ?? rows.find((r) => !r.detail);
        const title = header?.text || g.agent;
        const toolRows = rows.filter((r) => r.detail);
        return (
          <Box key={g.agent + gi} sx={{ mb: 0.5 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, py: 0.25 }}>
              <Chip label={g.agent} size="small" color="secondary" variant="outlined"
                sx={{ height: 16, "& .MuiChip-label": { px: 0.5, fontSize: 10, fontWeight: 700 } }} />
              <Typography variant="caption" sx={{ fontWeight: 600 }}>{title}</Typography>
            </Box>
            {toolRows.map((p, i) => (
              <Accordion key={p.key ?? i} disableGutters elevation={0}
                sx={{ bgcolor: "transparent", "&:before": { display: "none" }, pl: 1 }}>
                <AccordionSummary expandIcon={<ExpandMoreIcon fontSize="small" />}
                  sx={{ minHeight: 0, px: 0,
                        "& .MuiAccordionSummary-content": { my: 0.4, alignItems: "center", gap: 0.75 } }}>
                  {rowIcon(p, live)}
                  <Typography variant="caption">{p.detail?.tool || p.text}</Typography>
                </AccordionSummary>
                <AccordionDetails sx={{ px: 0, pt: 0 }}>
                  <ToolCallDetail args={p.detail?.args} result={p.detail?.result}
                    isError={p.detail?.is_error} />
                </AccordionDetails>
              </Accordion>
            ))}
          </Box>
        );
      })}
    </CollapsibleBlock>
  );
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd web && npx tsc -b && npm test -- SubagentProgress`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add web/src/components/SubagentProgress.tsx web/src/components/SubagentProgress.test.tsx
git commit -m "feat(web): SubagentProgress 分组+步骤描述标题+可展开工具行"
```

---

## 任务 8：ChatView 用 SubagentProgress 渲染子代理块

**文件：**
- 修改：`web/src/components/ChatView.tsx`
- 测试：`web/src/components/ChatView.test.tsx`（既有须通过）

- [ ] **步骤 1：改渲染**

`web/src/components/ChatView.tsx`：import SubagentProgress；把子代理那行（约 519 行）从 `ProgressBlock` 换成 `SubagentProgress`：

```tsx
                    <ProgressBlock title="技能" kind="skill" items={skill} status="ok" />
                    <ProgressBlock title="沙箱执行" kind="sandbox" items={sandbox} status={sbStatus} />
                    <SubagentProgress items={sub} live={live} stopped={stopped} status={subStatus} />
```

（`ProgressBlock` 仍服务 skill/sandbox/verify，保持不变；`subStatus` 计算逻辑复用原有。）

- [ ] **步骤 2：跑 ChatView 测试**

运行：`cd web && npx tsc -b && npm test -- ChatView`
预期：PASS（若既有测试断言了子代理块的旧文字结构，按新组件输出更新断言——旧断言若只查 scope/文字仍应命中）

- [ ] **步骤 3：Commit**

```bash
git add web/src/components/ChatView.tsx
git commit -m "feat(web): 子代理进度改用可展开的 SubagentProgress"
```

---

## 任务 9：全量回归 + 手动核对

- [ ] **步骤 1：后端全量**

运行：`uv run pytest -q`
预期：全绿（真实端点测试无 key 时 skip）

- [ ] **步骤 2：前端全量（先构建避免过期产物）**

运行：`cd web && npx tsc -b && npm test`
预期：全绿

- [ ] **步骤 3：手动核对（可选，需真实端点）**

`HARNESS_ENABLE_ORCHESTRATOR=true` 起服务，发一个多步问题，确认子代理块：标题是步骤描述、每个工具调用可点开看入参/结果、刷新后仍可展开。

- [ ] **步骤 4：最终 Commit（若手动核对有微调）**

```bash
git add -A && git commit -m "test: 子代理可展开进度全量回归"
```

---

## 附录 · 关键契约（防漂移）

```
# Progress.detail 形状（工具行）
{"tool": str, "args": any, "result": str|None, "is_error": bool}
# 步骤头行：executor 发 Progress(scope, step.description, key="__hdr__:<id>")，detail=None
#           dispatch 发 Progress(scope, "开始任务：<task>")，detail=None
# 前端分组：scope="subagent:<agent>"，agent = executor:s1 / <dispatch 角色名>
#   组标题 = 头行文字（回退 agent 名）；带 detail 的行 = 可展开工具块
# 数据通道：progress（UI 专用，不回放给模型）；绝不入 steps（steps 会回放为 LLM 历史）
```
