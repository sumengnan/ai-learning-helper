# AI 学习助手 · 聊天任务步骤清单设计规格

- **日期**：2026-07-10
- **状态**：待实现（brainstorming 已定稿）
- **定位**：在 App-1 聊天流式闭环之上，让 AI 回复时先展示"任务总流程拆分后的多步清单"，按步执行、实时刷新；失败重试的临时步骤也并入总流程
- **前置**：App-1（`/api/chat` SSE + `ProgressBlock` 进度渲染）已在 `main`

---

## 0. 背景与范围

现状：聊天已能实时渲染打字、工具调用（`AgentProgress`）与分 scope 的进度块（沙箱 / 子代理 / 技能，`ProgressBlock`）。但用户看不到 AI 对一个复杂任务的**整体规划**——它做了哪几步、到哪步了、哪步失败又补了什么。

本规格新增：**复杂多步任务**时，AI 开局先给出有序步骤清单并推到前端，执行中每步状态实时刷新；某步失败并重试时，失败步保留、重试作为新步骤插入清单。简单问答（打招呼、单句问答）不生成清单。

**设计通则**：严格 YAGNI；**harness 零改动**（复用已有 `Progress` 事件 + `progress.emit` 旁路）；持久化复用 App-1 已有的 `progress[]` 落库；测试用 `MockModelClient`，不打网络。

### IN
- 应用层 `update_plan` 工具：模型自维护有序步骤清单，经 `emit(Progress(scope="plan", ...))` 推流。
- System prompt 规则：复杂任务开局先规划、执行中更新、失败插重试步；简单问答不调用。
- 前端 `PlanBlock` 组件：解析 `plan` 快照渲染有序清单 + 4 态图标，置于其他进度块之上。
- 持久化 + 历史回填（零成本，复用现有 `progress[]` 机制）。

### OUT
- 不改 harness、不改 `AgentLoop`、不加"计划驱动的执行引擎"（清单是模型自维护的**可视追踪器**，非约束执行的调度器）。
- 不做位置无关的花哨动画；不做多轮跨消息的全局计划。

### 验收标准
1. 复杂任务（如"查知识库里的 X，再算一下，最后汇总"）→ 回复开头先出现有序步骤清单，随执行逐步打勾。
2. 简单问答（"你好"）→ 不出现清单，界面干净。
3. 某步失败 → 该步标记为失败并保留，清单中在其后**新增**一条重试步骤。
4. 刷新页面重进会话 → 历史消息的步骤清单原样回显。
5. 后端 pytest（MockModelClient）覆盖 `update_plan` 发 `Progress(scope="plan")` + `/api/chat` SSE 含 plan 快照，不打网络。
6. harness 目录零 diff。

---

## 1. 核心架构：模型自维护的 `plan` 工具

新增应用层工具 `update_plan`（名字可调），模型像维护 todo 清单那样调用它。它通过 harness **已有**的进度旁路把清单推到 SSE 流——和沙箱 / 子代理进度走同一条通道，**不改 harness 一行**。

- **旁路 API**（`src/harness/progress.py`，已存在）：
  ```python
  from harness.progress import emit
  from harness.events import Progress
  emit(Progress(scope="plan", text=<JSON>, key="plan"))
  ```
  `chat.py` 进入 loop 前已 `set_emitter`，emit 出的事件自动并入 SSE。工具 `run()` 只能返回 str，旁路正是为此设计。
- **注册位置**：`app/assembly.py::build_harness` 的常驻工具（与 `CalculatorTool` 同列，无外部依赖、始终启用）。
- **System prompt 追加规则**（`AppConfig.app_system_prompt` 或装配时拼接）：
  - 面对**多步任务**：第一步先调 `update_plan` 列出有序步骤（`pending`）。
  - 每完成一步：再调 `update_plan`，把该步置 `done`、下一步置 `running`。
  - 某步失败：把该步置 `failed`，并在其**后**插入一条重试步骤（`running`），继续。
  - **简单问答**（打招呼、寒暄、单句事实问答）：**不要**调用，直接回答。
  - 已用清单表达的步骤，不要在正文里再逐条复述。

---

## 2. 关键取舍：整份快照，而非逐步累加

现有 `ProgressBlock.mergeByKey` 按 **first-seen 顺序** 渲染行。若重试步用新 key 追加，只会渲染到清单**末尾**，无法插在失败步之后——不满足"临时步骤加入总流程"的正确位置。

因此 `update_plan` **每次调用发一整份有序快照**，固定 `key="plan"`：

```python
emit(Progress(scope="plan", text=json.dumps(steps, ensure_ascii=False), key="plan"))
```

- 固定 key → 最新快照覆盖旧的（前端只取 `plan` scope 的最后一条）。
- 顺序由模型每次传的完整列表决定 → 重试步能精确插在失败步后面，其余步骤位置不变。
- 前端用**专用 `PlanBlock`** 解析这段 JSON 渲染，**不走** `mergeByKey`（`mergeByKey` 继续服务沙箱/子代理的"开始→完成"两事件折叠）。

**步骤 schema**（`text` 里的 JSON 数组）：

```json
[ { "title": "检索知识库中的相关资料", "status": "done" },
  { "title": "调用计算器求值",         "status": "failed" },
  { "title": "重试：改用沙箱重新求值",  "status": "running" },
  { "title": "汇总并给出答案",         "status": "pending" } ]
```

`status ∈ pending | running | done | failed`。`title` 为高层子任务（3–6 个），非逐条工具调用——工具细节仍在现有"工具调用"折叠区展示。

`update_plan` 工具签名与返回：

```python
# args: {"steps": [{"title": str, "status": "pending"|"running"|"done"|"failed"}, ...]}
# 校验 status 合法、title 非空；emit 快照；返回一句确认：
return f"计划已更新（{n} 步{f'，{failed} 失败' if failed else ''}）"
```

---

## 3. 数据流（一轮对话）

```
模型开局调 update_plan([...])
  → emit(Progress("plan", JSON, key="plan"))
  → chat.py 现有 pump 把旁路事件并入 SSE（零改动）
  → 前端 onEvent 收 Progress、scope==="plan" 存入 message.progress
  → PlanBlock 取最新 "plan" 快照、解析 JSON、渲染有序清单（置于工具/沙箱块之上，"路线图优先"）
执行中每步变化 → 模型再调 update_plan(整份新快照)
  → 同一 key="plan" 覆盖 → 清单原地刷新
run 结束 → progress[] 随本轮消息落库（App-1 现有机制）
```

---

## 4. 前端改动（均在 `web/src`，不碰 harness）

- **新增 `components/PlanBlock.tsx`**：
  - 入参：`plan` scope 的最新 `ProgressItem`（`text` 是 JSON 快照）。
  - 解析 JSON → 有序步骤列表；渲染状态图标：`pending` 空心圈 / `running` 转圈 / `done` 对勾 / `failed` 红叉。
  - 复用现有 `CollapsibleBlock` 外壳与 MUI 图标风格；标题如"任务步骤"，块头右侧显示"m/n 完成"。
  - JSON 解析失败或空数组 → 返回 `null`（容错，不炸 UI）。
- **`components/ChatView.tsx`**：
  - `const plan = m.progress.filter((p) => p.scope === "plan").at(-1)`。
  - 在技能/沙箱/子代理三块**之上**渲染 `<PlanBlock item={plan} />`（路线图优先）。
- **`components/ProgressBlock.tsx` 的 `ProgressItem` 类型**：`status` 增补 `"pending"` 取值（仅前端类型层，harness `Progress.status` 本就是 `str | None`）。

---

## 5. 持久化与错误处理

- **持久化零成本**：`plan` 是一个 progress scope，已被 `chat.py` 现有的 `progress[]` 聚合逻辑落库；`ChatPage` 加载历史时随消息回填 `progress`，`PlanBlock` 自动重渲。无需新增存储字段。
- 模型不调 `update_plan` → 无 `plan` 快照 → `PlanBlock` 返回 `null`，简单问答界面保持干净。
- `update_plan` 入参非法（未知 status、`steps` 非数组）→ 工具返回错误串、不 emit，模型可据错误自我修正。

---

## 6. 测试（TDD，先写测试）

**后端 pytest（不打网络）**：
- `update_plan` 工具单测：注入捕获式 emitter，调用后断言收到 `Progress(scope="plan", key="plan")` 且 `text` 是与入参一致的合法有序 JSON；非法 status 返回错误串且不 emit。
- `/api/chat` SSE 集成：`MockModelClient` 编排"先调一次 `update_plan` 再答"，解析 SSE 断言流中含 `{type:"Progress", data:{scope:"plan", ...}}`。
- harness 目录零 diff（`git diff --stat src/harness` 为空）。

**前端 Vitest + RTL**：
- `PlanBlock`：4 态图标正确；重试步渲染在失败步之后；空/非法 JSON 快照渲染 `null`；"m/n 完成"计数正确。

---

## 7. 已知风险与取舍

- **prompt 遵循度**：模型是否可靠地"开局先规划、失败后插重试步"取决于指令遵循。单用户自用可接受；必要时强化 system prompt 或加 few-shot。
- **清单是追踪器非调度器**：不强制模型严格按清单执行，清单反映模型自述的规划与进展。这是刻意的 YAGNI 取舍——避免引入计划驱动执行引擎、保持 harness 零改动。
- **顺序渲染偏离 `mergeByKey` 惯例**：`plan` scope 专用整份快照 + `PlanBlock`，与沙箱/子代理的按 key 折叠并存、互不影响。
