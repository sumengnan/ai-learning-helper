# 聊天任务步骤清单 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 复杂任务时 AI 开局先给出有序步骤清单并推到前端，按步实时刷新；某步失败并重试时，失败步保留、重试作为新步骤插入清单。

**架构：** 新增应用层 `update_plan` 工具，模型每次调用发一整份有序步骤快照，经 harness 已有的 `progress.emit(Progress(scope="plan", key="plan"))` 旁路并入 SSE；前端新增 `PlanBlock` 取最新快照解析渲染。harness 目录零改动；持久化复用 App-1 已有的 `progress[]` 落库。

**技术栈：** 后端 Python/pydantic/FastAPI + pytest（`MockModelClient`，不打网络）；前端 React/MUI/TypeScript + Vitest/RTL。

**规格：** `docs/superpowers/specs/2026-07-10-chat-task-plan-design.md`

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/tools/plan_tool.py` | `UpdatePlanTool` 工具 + `PLAN_SYSTEM_GUIDANCE` 提示常量 | 创建 |
| `app/assembly.py` | 常驻注册 `UpdatePlanTool`，system prompt 追加规划指引 | 修改 |
| `tests/app/test_plan_tool.py` | 工具单测 + `/api/chat` SSE 集成（推流 + 持久化） | 创建 |
| `tests/app/test_assembly.py` | 断言 `build_harness` 注册工具、prompt 含指引 | 修改 |
| `web/src/components/PlanBlock.tsx` | 解析 `plan` 快照渲染有序清单 + 4 态图标 | 创建 |
| `web/src/components/PlanBlock.test.tsx` | PlanBlock 渲染单测 | 创建 |
| `web/src/components/ChatView.tsx` | 在进度块之上渲染 `PlanBlock` | 修改 |
| `web/src/components/ChatView.test.tsx` | 断言 `Progress(scope=plan)` → 清单渲染 | 修改 |
| `web/src/components/ProgressBlock.tsx` | `ProgressItem.status` 增补 `"pending"` | 修改 |

**约束：`src/harness/` 目录必须零 diff。** 工具虽是 harness `Tool` 的子类，但文件落在 `app/`，仅 import harness 作为库。

---

### 任务 1：`update_plan` 工具

**文件：**
- 创建：`app/tools/plan_tool.py`
- 测试：`tests/app/test_plan_tool.py`

参考现有工具范式 `src/harness/tools/builtins/calculator.py`（`Tool` 子类 + `Params(BaseModel)` + `async run() -> str`）；旁路 API `src/harness/progress.py::emit`；事件 `src/harness/events.py::Progress`；失败标记用 `harness.tools.base.ToolError`。

- [ ] **步骤 1：编写失败的测试**

创建 `tests/app/test_plan_tool.py`：

```python
import json
import pytest
from pydantic import ValidationError

from harness import progress
from harness.events import Progress
from app.tools.plan_tool import UpdatePlanTool, PlanStep, PLAN_SYSTEM_GUIDANCE


async def test_update_plan_emits_ordered_snapshot():
    tool = UpdatePlanTool()
    got = []
    token = progress.set_emitter(got.append)
    try:
        r = await tool.run(tool.Params(steps=[
            PlanStep(title="查资料", status="running"),
            PlanStep(title="汇总", status="pending"),
        ]))
    finally:
        progress.reset_emitter(token)

    evs = [e for e in got if isinstance(e, Progress)]
    assert len(evs) == 1
    assert evs[0].scope == "plan" and evs[0].key == "plan"
    assert json.loads(evs[0].text) == [
        {"title": "查资料", "status": "running"},
        {"title": "汇总", "status": "pending"},
    ]
    assert "2 步" in r


async def test_update_plan_reports_failed_count():
    tool = UpdatePlanTool()
    got = []
    token = progress.set_emitter(got.append)
    try:
        r = await tool.run(tool.Params(steps=[
            PlanStep(title="算", status="failed"),
            PlanStep(title="重试：换沙箱", status="running"),
        ]))
    finally:
        progress.reset_emitter(token)
    assert "1 失败" in r


async def test_update_plan_empty_raises_and_emits_nothing():
    from harness.tools.base import ToolError
    tool = UpdatePlanTool()
    got = []
    token = progress.set_emitter(got.append)
    try:
        with pytest.raises(ToolError):
            await tool.run(tool.Params(steps=[]))
    finally:
        progress.reset_emitter(token)
    assert got == []


def test_update_plan_rejects_unknown_status():
    with pytest.raises(ValidationError):
        UpdatePlanTool.Params(steps=[{"title": "x", "status": "bogus"}])


def test_guidance_mentions_tool_and_gating():
    assert "update_plan" in PLAN_SYSTEM_GUIDANCE
    assert "简单问答" in PLAN_SYSTEM_GUIDANCE
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/app/test_plan_tool.py -v`
预期：FAIL，报 `ModuleNotFoundError: No module named 'app.tools.plan_tool'`

- [ ] **步骤 3：编写最少实现代码**

创建 `app/tools/plan_tool.py`：

```python
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from harness.events import Progress
from harness.progress import emit
from harness.tools.base import Tool, ToolError

PlanStatus = Literal["pending", "running", "done", "failed"]


class PlanStep(BaseModel):
    title: str = Field(min_length=1)
    status: PlanStatus = "pending"


PLAN_SYSTEM_GUIDANCE = (
    "\n\n## 任务步骤清单\n"
    "面对需要多步才能完成的任务时，第一步先调用 update_plan 工具，列出 3-6 个高层子任务"
    "（status 全为 pending）。之后每完成或失败一步，就再次调用 update_plan、传入完整的最新"
    "清单：已完成的置 done、正在做的置 running。若某步失败并需重试，把该步置 failed 并在其后"
    "新增一条重试步骤（running），继续执行。简单问答（打招呼、寒暄、单句事实问答）不要调用"
    "update_plan。已在清单里的步骤不要在正文里再逐条复述。"
)


class UpdatePlanTool(Tool):
    name = "update_plan"
    description = (
        "维护当前复杂任务的有序步骤清单并展示给用户。面对多步任务先调用它列出步骤；"
        "每完成/失败一步就再次调用、传入完整最新清单。简单问答不要调用。"
        "某步失败时把它标记为 failed 并在其后新增一条重试步骤。"
    )

    class Params(BaseModel):
        steps: list[PlanStep]

    async def run(self, params: "UpdatePlanTool.Params") -> str:
        if not params.steps:
            raise ToolError("steps 不能为空")
        steps = [{"title": s.title, "status": s.status} for s in params.steps]
        emit(Progress(scope="plan", text=json.dumps(steps, ensure_ascii=False), key="plan"))
        n = len(steps)
        failed = sum(1 for s in steps if s["status"] == "failed")
        return f"计划已更新（{n} 步{f'，{failed} 失败' if failed else ''}）"
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/app/test_plan_tool.py -v`
预期：5 个测试全 PASS

- [ ] **步骤 5：Commit**

```bash
git add app/tools/plan_tool.py tests/app/test_plan_tool.py
git commit -m "feat(plan): update_plan 工具经 Progress 旁路推有序步骤快照"
```

---

### 任务 2：装配注册 + system prompt 指引

**文件：**
- 修改：`app/assembly.py`（import 段；`_reg(CalculatorTool())` 附近 line 45；`system_prompt=config.app_system_prompt` line 149）
- 测试：`tests/app/test_assembly.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/app/test_assembly.py` 追加（沿用该文件现有 `build_harness` 调用范式）：

```python
def test_update_plan_registered_and_prompt_has_guidance():
    from app.assembly import build_harness
    from app.config import AppConfig
    h = build_harness(AppConfig(api_key="k", app_db_path=":memory:"))
    assert h.registry.get("update_plan") is not None
    assert "update_plan" in h.system_prompt
```

（若 `build_harness` 需要更多必填配置，参照本文件既有测试的构造方式补齐。）

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/app/test_assembly.py::test_update_plan_registered_and_prompt_has_guidance -v`
预期：FAIL（`registry.get("update_plan")` 为 None）

- [ ] **步骤 3：编写最少实现代码**

`app/assembly.py`：

import 段加：
```python
from app.tools.plan_tool import UpdatePlanTool, PLAN_SYSTEM_GUIDANCE
```

`_reg(CalculatorTool())`（line 45）后加一行：
```python
    _reg(UpdatePlanTool())
```

`return Harness(...)` 中把（line 149）：
```python
        system_prompt=config.app_system_prompt,
```
改为：
```python
        system_prompt=config.app_system_prompt + PLAN_SYSTEM_GUIDANCE,
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/app/test_assembly.py -v`
预期：全 PASS

- [ ] **步骤 5：Commit**

```bash
git add app/assembly.py tests/app/test_assembly.py
git commit -m "feat(plan): 常驻注册 update_plan 并向 system prompt 追加规划指引"
```

---

### 任务 3：`/api/chat` SSE 集成（推流 + 持久化）

**文件：**
- 测试：`tests/app/test_plan_tool.py`（追加）

参考 `tests/app/test_api.py`：`_fake_harness`(line 34)、`_auth_headers`(line 48)、`_sse_events`(line 187)、`tool_turn`/`text_turn` fixture（`tests/conftest.py`）。`_fake_harness` 仅注册 Calculator，故本测试自建含 `UpdatePlanTool` 的 registry。

- [ ] **步骤 1：编写失败的测试**

在 `tests/app/test_plan_tool.py` 追加：

```python
def _plan_client(make_mock, turns):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import AppConfig
    from app.assembly import Harness
    from app.conversations import ConversationStore
    from app.documents import DocumentStore
    from harness.tools.base import ToolRegistry
    from harness.persistence.checkpoint import CheckpointStore
    from harness.persistence.trajectory import TrajectoryStore, TrajectorySink

    reg = ToolRegistry(); reg.register(UpdatePlanTool())
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock(turns), registry=reg,
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="你是助手")
    store = ConversationStore(":memory:")
    app = create_app(config=AppConfig(api_key="k", app_db_path=":memory:"),
                     harness=harness, store=store, doc_store=DocumentStore(":memory:"))
    return TestClient(app)


def _sse(resp):
    import json as _j
    return [_j.loads(l[6:]) for l in resp.iter_lines() if l and l.startswith("data: ")]


def test_chat_emits_and_persists_plan(make_mock, tool_turn, text_turn):
    args = '{"steps":[{"title":"查资料","status":"running"},{"title":"汇总","status":"pending"}]}'
    client = _plan_client(make_mock, [tool_turn("update_plan", args, call_id="p1"),
                                      text_turn("完成")])
    r = client.post("/api/auth/register", json={"username": "u", "password": "pw1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]

    with client.stream("POST", "/api/chat",
                       json={"conversation_id": cid, "message": "帮我查并汇总"}, headers=h) as resp:
        events = _sse(resp)

    plan = [e for e in events if e["type"] == "Progress" and e["data"]["scope"] == "plan"]
    assert plan, "SSE 流应含 scope=plan 的 Progress"
    steps = json.loads(plan[-1]["data"]["text"])
    assert [s["title"] for s in steps] == ["查资料", "汇总"]

    # 持久化：切回对话，assistant 消息仍带 plan 进度
    msgs = client.get(f"/api/conversations/{cid}/messages", headers=h).json()
    assistant = next(m for m in msgs if m["role"] == "assistant")
    assert any(p["scope"] == "plan" for p in (assistant.get("progress") or []))
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/app/test_plan_tool.py::test_chat_emits_and_persists_plan -v`
预期：先跑一次；若因 `assistant.get("progress")` 结构与实际不符而失败，查看 `app/api/chat.py:138-153` 与会话 messages 端点确认字段名后修正断言（progress 与 steps 走同一 append 路径，应同样回填）。

- [ ] **步骤 3：使其通过**

本任务是纯集成测试，实现已在任务 1、2 完成。若断言字段名需微调（如 `progress` 的确切返回形状），只改测试断言以匹配现有 API，**不改** `chat.py`。

- [ ] **步骤 4：运行验证通过**

运行：`python -m pytest tests/app/test_plan_tool.py -v`
预期：全 PASS

- [ ] **步骤 5：Commit**

```bash
git add tests/app/test_plan_tool.py
git commit -m "test(plan): /api/chat SSE 推 plan 快照并持久化的集成测试"
```

---

### 任务 4：前端 `PlanBlock` 组件

**文件：**
- 创建：`web/src/components/PlanBlock.tsx`
- 测试：`web/src/components/PlanBlock.test.tsx`

参考 `web/src/components/ProgressBlock.tsx` 的 MUI 图标风格与 `stepIcon`；测试范式见 `web/src/components/ChatView.test.tsx`（Vitest + RTL）。

- [ ] **步骤 1：编写失败的测试**

创建 `web/src/components/PlanBlock.test.tsx`：

```tsx
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { PlanBlock } from "./PlanBlock";

afterEach(cleanup);

describe("PlanBlock", () => {
  it("空或非法快照渲染 null", () => {
    expect(render(<PlanBlock text={undefined} />).container.firstChild).toBeNull();
    cleanup();
    expect(render(<PlanBlock text="not json" />).container.firstChild).toBeNull();
    cleanup();
    expect(render(<PlanBlock text="[]" />).container.firstChild).toBeNull();
  });

  it("有序渲染步骤、计数正确、重试步在失败步之后", () => {
    const snap = JSON.stringify([
      { title: "查资料", status: "done" },
      { title: "计算", status: "failed" },
      { title: "重试：换沙箱", status: "running" },
      { title: "汇总", status: "pending" },
    ]);
    render(<PlanBlock text={snap} />);
    expect(screen.getByText("1/4 完成")).toBeTruthy();
    const order = screen
      .getAllByText(/查资料|计算|重试：换沙箱|汇总/)
      .map((e) => e.textContent);
    expect(order).toEqual(["查资料", "计算", "重试：换沙箱", "汇总"]);
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd web && npx vitest run src/components/PlanBlock.test.tsx`
预期：FAIL，无法解析 `./PlanBlock`

- [ ] **步骤 3：编写最少实现代码**

创建 `web/src/components/PlanBlock.tsx`：

```tsx
import { Box, Typography, CircularProgress } from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import PlaylistAddCheckIcon from "@mui/icons-material/PlaylistAddCheck";

type PlanStatus = "pending" | "running" | "done" | "failed";
type PlanStepData = { title: string; status: PlanStatus };

function parseSteps(text?: string | null): PlanStepData[] {
  if (!text) return [];
  try {
    const arr = JSON.parse(text);
    if (!Array.isArray(arr)) return [];
    return arr.filter((s) => s && typeof s.title === "string");
  } catch {
    return [];
  }
}

function stepIcon(status: PlanStatus) {
  if (status === "done") return <CheckCircleIcon sx={{ fontSize: 15 }} color="success" />;
  if (status === "failed") return <CancelIcon sx={{ fontSize: 15 }} color="error" />;
  if (status === "running") return <CircularProgress size={12} />;
  return <RadioButtonUncheckedIcon sx={{ fontSize: 15 }} color="disabled" />;
}

export function PlanBlock({ text }: { text?: string | null }) {
  const steps = parseSteps(text);
  if (!steps.length) return null;
  const done = steps.filter((s) => s.status === "done").length;
  return (
    <Box sx={{ mb: 1, p: 1, borderRadius: 1, bgcolor: "background.paper",
               border: 1, borderColor: "divider" }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mb: 0.5 }}>
        <PlaylistAddCheckIcon sx={{ fontSize: 16 }} color="action" />
        <Typography variant="caption" sx={{ fontWeight: 600 }}>任务步骤</Typography>
        <Typography variant="caption" color="text.secondary">{done}/{steps.length} 完成</Typography>
      </Box>
      {steps.map((s, i) => (
        <Box key={i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.2 }}>
          {stepIcon(s.status)}
          <Typography
            variant="caption"
            sx={{
              color: s.status === "failed" ? "error.main" : "text.secondary",
              textDecoration: s.status === "done" ? "line-through" : "none",
            }}
          >
            {s.title}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd web && npx vitest run src/components/PlanBlock.test.tsx`
预期：全 PASS

- [ ] **步骤 5：Commit**

```bash
git add web/src/components/PlanBlock.tsx web/src/components/PlanBlock.test.tsx
git commit -m "feat(plan): PlanBlock 解析快照渲染有序步骤清单"
```

---

### 任务 5：ChatView 集成渲染

**文件：**
- 修改：`web/src/components/ChatView.tsx`（import 段；line 150 助手气泡内、现有进度 IIFE(line 151) 之前）
- 修改：`web/src/components/ProgressBlock.tsx:13`（`ProgressItem.status` 增补 `"pending"`）
- 测试：`web/src/components/ChatView.test.tsx`（追加）

- [ ] **步骤 1：编写失败的测试**

在 `web/src/components/ChatView.test.tsx` 的 `describe("ChatView", ...)` 内追加：

```tsx
it("Progress scope=plan → 渲染任务步骤清单", async () => {
  vi.mocked(streamChat).mockImplementationOnce(
    async (_cid: string, _msg: string, onEvent: (e: any) => void) => {
      onEvent({ type: "RunStarted", data: { run_id: "r1" } });
      onEvent({ type: "Progress", data: {
        scope: "plan", key: "plan",
        text: JSON.stringify([{ title: "第一步查资料", status: "running" }]),
      } });
      onEvent({ type: "TextDelta", data: { text: "好" } });
      onEvent({ type: "RunFinished", data: {} });
    });
  render(<ChatView conversationId="c1" initial={[]} />);
  fireEvent.change(screen.getByPlaceholderText("问点什么…"), { target: { value: "hi" } });
  fireEvent.click(screen.getByText("发送"));
  await waitFor(() => expect(screen.getByText("第一步查资料")).toBeTruthy());
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd web && npx vitest run src/components/ChatView.test.tsx`
预期：新用例 FAIL（"第一步查资料" 未渲染）

- [ ] **步骤 3：编写最少实现代码**

`web/src/components/ChatView.tsx` import 段（`ProgressBlock` 附近）加：
```tsx
import { PlanBlock } from "./PlanBlock";
```

在 line 150 `<Paper …>` 打开后、现有 `{showTools && … m.progress …}` 块（line 151）之前，插入独立的路线图渲染（不受 `showTools` 门控——路线图优先）：
```tsx
              {m.role === "assistant" && m.progress && (() => {
                const plan = m.progress.filter((p) => p.scope === "plan").at(-1);
                return plan ? <PlanBlock text={plan.text} /> : null;
              })()}
```

`web/src/components/ProgressBlock.tsx:13` 把：
```tsx
  status?: "running" | "ok" | "error" | null;
```
改为：
```tsx
  status?: "pending" | "running" | "ok" | "error" | null;
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd web && npx vitest run src/components/ChatView.test.tsx src/components/PlanBlock.test.tsx`
预期：全 PASS

- [ ] **步骤 5：Commit**

```bash
git add web/src/components/ChatView.tsx web/src/components/ChatView.test.tsx web/src/components/ProgressBlock.tsx
git commit -m "feat(plan): ChatView 在进度块之上渲染任务步骤清单"
```

---

### 任务 6：全量验证与收尾

- [ ] **步骤 1：后端全量测试**

运行：`python -m pytest -q`
预期：全绿（含新增 `test_plan_tool.py`、`test_assembly.py`）

- [ ] **步骤 2：harness 零 diff 断言**

运行：`git diff --stat main -- src/harness`
预期：无输出（harness 目录未改一行）

- [ ] **步骤 3：前端全量测试 + 构建**

运行：`cd web && npx vitest run && npm run build`
预期：测试全绿；`tsc` 无类型错误、`vite build` 成功

- [ ] **步骤 4：手动 E2E（真实链路验证）**

用 `/run` 或手动起 `uvicorn app.main:app --reload` + `cd web && npm run dev`，网页发一个多步问题（如"查一下知识库里关于 X 的资料，再算 (12+8)*3，最后汇总"）：
- 回复开头出现"任务步骤"清单，随执行逐步打勾；
- 再发"你好" → 不出现清单；
- 刷新页面、切回该会话 → 步骤清单原样回显。

- [ ] **步骤 5：收尾**

调用 superpowers:finishing-a-development-branch，按其结构化选项决定合并/PR/清理（本 worktree 已隔离，后台任务默认开草稿 PR）。

---

## 附录：验证命令速查

- 后端单测：`python -m pytest tests/app/test_plan_tool.py tests/app/test_assembly.py -v`
- 后端全量：`python -m pytest -q`
- harness 零改动：`git diff --stat main -- src/harness`（应为空）
- 前端单测：`cd web && npx vitest run src/components/PlanBlock.test.tsx src/components/ChatView.test.tsx`
- 前端构建：`cd web && npm run build`
