# 多层结果正确性校验 + 前端校验状态展示 设计

> 面向 AI 代理的工作者：实现时用 superpowers:writing-plans 生成分步计划，逐步实现。

**目标：** 在现有「最终交付门」之外，补上**每步（高风险步）的实时正确性校验**，**加固最终结果校验**，并在前端**常驻展示**每步与最终的校验/质量状态（含「验证中」loading）。

**核心约束：** `src/harness` 内核**零改动**——所有校验落在应用层（`app/`）与前端（`web/`），复用内核既有的 `progress.emit` 旁路与 `ToolError → is_error` 语义。

**规格：** 本文档。

---

## 1. 背景与现状

现有校验只有一道**最终交付门** `app/verify.py::AnswerVerifier`（`app/config.py::enable_answer_gate` 控制），四项：format / grounding / code / judge；不过则回灌重答 `answer_gate_max_retries` 次，用尽降级交付带 ⚠️。

缺口（本设计填补）：
1. **每步中间结果无独立正确性拦截**——尤其检索命中为空/低相关时，模型可能据此臆造。
2. **最终 judge 是主对话模型单次自评**，存在「给自己打高分」偏差；grounding 只做整体判断；无「任务拆分/每步」维度的质量度量。
3. **前端校验状态受 `showTools` 开关控制**（`ChatView.tsx`），关掉即隐藏；且没有每步校验、无 judge 质量分展示。

现状可复用资产：
- `harness/progress.py::emit(Progress(scope,text,status,key))` —— 工具执行中向 SSE 流发进度事件的旁路，内核透明。
- `ToolError` —— 工具主动标记失败，非零退出/超时的代码工具已 `raise ToolError → is_error`。
- 前端 `ProgressBlock` / `CollapsibleBlock` —— 现有折叠块与状态图标（running/ok/error）。
- `memory/retriever.py::ScoredHit.score` —— 检索命中带相似度分。

## 2. 决策记录（来自澄清）

| # | 决策 | 取值 |
|---|---|---|
| D1 | 每步校验强度 | 只校高风险步 + 规则/阈值为主，**不加 LLM**；应用层实现，内核零改动 |
| D2 | 每步覆盖范围 | 检索结果相关性 + 代码/命令执行结果 |
| D3 | 最终校验加固方向 | judge 可信度↑ + grounding 逐句 + 新增维度 + 硬/软门；并引入独立 judge 模型 |
| D4 | 独立 judge 时机 | 交付前**一次性回看整轨迹**，对 拆分/关键步/最终 分层打分（非每步实时） |
| D5 | 前端展示形态 | **常驻徽章 + 可展开详情**，脱离 `showTools` 开关 |
| D6 | 每步校验落点 | **`ValidatingTool` 装饰器**包住高风险工具（非工具内建、非事件旁路只读） |

## 3. 非目标（YAGNI）

- 不给每个工具都加 LLM 校验。
- 不做每步实时 LLM judge（成本/延迟）。
- 不改内核 loop / executor / 事件类型。
- 不实现 judge 多次投票的完整投票（仅预留 `judge_samples` 配置位，起步取 1）。
- 新增事实维度起步只做「引用链接可达性」，其余（数值/日期一致）留扩展位。

## 4. 架构总览

```
实时层（每步·规则·app 工具层）
  ValidatingTool 装饰器 包住高风险工具
   ├ 检索相关性：空命中/低分 → 追加提示触发自纠正 + emit(check)
   └ 代码/命令：ToolError/is_error 语义 → emit(check, error)
                                          ↓ Progress scope="check"
评估层（交付前·独立模型·app/verify + chat）
  ① AnswerVerifier 加固：judge 独立模型+挑错视角 · grounding 逐句 · facts 维度 · 硬/软门
  ② TrajectoryJudge：一次回看整轨迹 → {plan,steps,final} 分层打分
                                          ↓ Progress scope="quality" + 门控
展示层（web·脱离 showTools）
  VerifyBadge 常驻徽章：验证中… → 通过/未通过 + 质量分
   └ 展开：每步校验标记 + judge 三层明细
```

## 5. 实时层：每步规则校验

### 5.1 `ValidatingTool` 装饰器 — `app/tools/validating.py`（新）

```python
@dataclass
class CheckResult:
    ok: bool
    text: str            # 前端展示文案（如「检索命中 3 条」「未命中知识库」）
    hint: str = ""       # 追加到工具结果尾部、驱动模型自纠正的提示（仅 ok=False）

class ValidatingTool(Tool):
    """包住 inner 工具：run() 后按 check 规则判定，emit 校验进度；失败时把 hint 并进
    结果尾部触发模型自纠正。校验逻辑异常绝不吞掉 inner 原结果（仅跳过该步校验）。"""
    def __init__(self, inner: Tool, check: Callable[[BaseModel, str], CheckResult]):
        self._inner, self._check = inner, check
        self.name, self.description, self.Params = inner.name, inner.description, inner.Params

    async def run(self, params):
        raw = await self._inner.run(params)          # 可能是 str 或 ToolOutput
        text = raw.text if isinstance(raw, ToolOutput) else raw
        try:
            v = self._check(params, text)
        except Exception as e:                       # 校验器自身故障 → 放行不拦截
            _log.warning("每步校验异常，跳过：%s", e)
            return raw
        emit(Progress(scope="check", text=v.text,
                      status="ok" if v.ok else "error",
                      key=f"check:{self._inner.name}"))
        if not v.ok and v.hint:
            return _append_hint(raw, v.hint)         # 保持 raw 的 str/ToolOutput 形态
        return raw
```

代码/命令工具执行失败时 inner 会 `raise ToolError`，此异常**穿透** `ValidatingTool.run`（不被上面的 try 捕获，因为 try 只包 `self._check`），由内核 executor 兜成 `is_error` 回填模型——沿用现有自纠正。为让前端把它标成「校验失败」，装饰器对代码类工具改用如下包法：

```python
async def run(self, params):          # 代码/命令类专用规则的实现分支
    try:
        raw = await self._inner.run(params)
    except ToolError as e:
        emit(Progress(scope="check", text=f"{self.name} 执行未通过", status="error",
                      key=f"check:{self.name}"))
        raise                          # 语义不变，继续走 is_error 自纠正
    emit(Progress(scope="check", text=f"{self.name} 执行通过", status="ok",
                  key=f"check:{self.name}"))
    return raw
```

> 实现上用两个 check 策略区分：`relevance_check`（检索类，判文本）与 `exec_check`（代码类，捕 ToolError）。装饰器接受一个 `mode` 或不同工厂函数装配。

### 5.2 规则

**检索相关性** `relevance_check(params, text)`：
- 空命中：`text` 含 `_NO_HIT`（"（未在知识库中检索到相关内容）"）或为空 → `ok=False`，`text="未命中知识库"`，`hint="本次未检索到知识库依据，请勿臆造，必要时明确告知用户资料不足。"`
- 命中：`ok=True`，`text=f"检索命中"`。
- 低分（增强，需工具暴露分数）：若 `search_memory` 经 `ToolOutput` 带出 top `score < config.step_relevance_min` → `ok=False`（warn 级）。**起步不改工具，仅做空命中**；分数阈值列为后续增强。

**代码/命令** `exec_check`：见 5.1 第二种包法，捕 `ToolError` 标 error。

### 5.3 装配 — `app/assembly.py`（改）

注册工具时对高风险工具套装饰器：
- `search_memory` → `ValidatingTool(inner, relevance_check)`
- `run_python`/`run_node`/`run_java`/`run_shell` → `ValidatingTool(inner, mode="exec")`

装配受开关 `config.enable_step_check`（默认 True）控制，可整体关闭。

## 6. 评估层：最终校验加固 + 轨迹 judge

### 6.1 `AnswerVerifier` 加固 — `app/verify.py`（改）

- **judge 独立模型 + 挑错视角**：`AnswerVerifier` 接受可选 `judge_complete`（由独立模型 `config.judge_model` 构造的 completer）；无则回退主 completer。`JUDGE_SYSTEM` 改为挑错者口吻（"默认假设回答有缺陷，逐项找问题后再打分"）。
- **grounding 逐句归因**：`GROUNDING_SYSTEM` 改为要求逐条列出「无依据的论断」，输出 `{"grounded":bool,"unsupported":[...],"feedback":str}`；`feedback` 汇总。
- **新增 facts 维度**：`gate_check_facts` 开启时，抽取答案中的 http(s) 链接，逐个用抓取工具判可达（200 且非空）；不可达链接计入失败。复用现有抓取工具（`registry.get("fetch_url")` 或等价），无则跳过。
- **硬门/软门**：`Verdict` 增 `hard_failed: list[str]`。分类：`format`(截断)、`code` = 硬门；`grounding`、`judge`、`facts`、`trajectory` = 软门。交付门据此决定「硬门失败不降级」。

### 6.2 `TrajectoryJudge` — `app/verify.py`（新类）

```python
@dataclass
class TrajectoryScore:
    plan: int | None; steps: int | None; final: int | None
    feedback: str

class TrajectoryJudge:
    def __init__(self, judge_complete, config): ...
    async def score(self, question, plan, step_summaries, answer) -> TrajectoryScore:
        # 一次 LLM 调用（独立模型），结构化 JSON 输出三层分 + 反馈
        # 解析失败/基建抖动 → 返回全 None（跳过评分，不拦截）
```

`TRAJECTORY_SYSTEM`：给出「用户问题 / 任务拆分 / 关键步摘要 / 最终答案」，要求对**拆分合理性、每步有效性、最终答案质量**各打 0–100 分并简评，输出 `{"plan":..,"steps":..,"final":..,"feedback":".."}`。

### 6.3 交付门接入 — `app/api/chat.py`（改）

- **轨迹收集**：流式期间累积
  - `plan`：`Progress scope="plan"` 的 JSON（若有）
  - `step_summaries`：工具调用序列（工具名 + `check` 结果）
  - `answer`：缓冲的最终答案
- **门控顺序**（在现有 `verifier.verify` 后）：若 `config.enable_trajectory_judge`，调 `TrajectoryJudge.score`；`final < config.trajectory_pass_score` → 记入 `Verdict`（软门）。
- **emit**：`Progress(scope="quality", text=json({plan,steps,final,feedback}), status="ok"/"error", key="quality")` 发前端。
- **硬/软门决策**：重答用尽后，若仅软门失败 → 降级交付带 ⚠️；若含硬门失败 → 降级交付但徽章红色标注硬项（文案已含 `Verdict.summary`）。

### 6.4 配置项 — `app/config.py`（改）

```python
# 每步校验（实时层）
enable_step_check: bool = True
step_relevance_min: float = 0.0        # 检索低分阈值；0 表示只判空命中（起步）
# 轨迹 judge（评估层）
enable_trajectory_judge: bool = False  # 与 answer gate 独立，可单独开
trajectory_pass_score: int = 60
judge_model: str = ""                  # 独立 judge 模型；空则回退主 model
judge_samples: int = 1                 # 预留：多次取多数（起步 1）
# 加固维度
gate_check_facts: bool = False         # 引用链接可达性
```

## 7. 展示层：前端

### 7.1 事件契约

| scope | 载荷 | 用途 |
|---|---|---|
| `check`（新） | `text`, `status=ok/error`, `key=check:<tool>` | 每步校验标记 |
| `quality`（新） | `text=JSON{plan,steps,final,feedback}`, `status`, `key=quality` | judge 三层质量分 |
| `verify`（有） | 沿用 | 最终门 running/ok/error |

### 7.2 类型 — `web/src/types.ts`（改）

`ChatMessage` 增：
```ts
checks?: { tool: string; status: "ok"|"error"; text: string }[];
quality?: { plan?: number; steps?: number; final?: number; feedback?: string } | null;
```
`applyEvent`（`ChatView.tsx`）：`Progress scope==="check"` → push `checks`；`scope==="quality"` → `JSON.parse` 存 `quality`（容错，解析失败忽略）。

### 7.3 组件 — `web/src/components/VerifyBadge.tsx`（新）

- 常驻渲染于 assistant 气泡底部，**不在 `showTools` 条件内**。
- 状态：生成/校验中（本轮为最后一条且 streaming，或收到 `verify running`）→ spinner「验证中…」；收到 `verify ok` 或流结束且无 error → 「通过 ✓」+ 若有 `quality.final` 显示「质量 N」；`verify error` 或硬门失败 → 「未通过」红色 + summary。
- 点击展开：`checks[]` 列表（每步 ✓/⚠/✗ + text）+ `quality` 三层（拆分/关键步/最终 分数 + feedback）。
- 仅当本轮启用了 gate 或 step-check（有 `verify`/`check`/`quality` 任一事件）时显示徽章；否则不渲染。

### 7.4 `ChatView.tsx`（改）

- 现有 `verify` 折叠块（受 `showTools`）保留或由 `VerifyBadge` 详情替代——**改为**：`VerifyBadge` 常驻；原 `ProgressBlock verify` 从 `showTools` 分支移除，明细并入徽章展开面板。
- 历史消息回放：`api.messages` 还原 `checks`/`quality`（若后端持久化了这些 Progress；未持久化则仅当轮可见——与现有 progress 一致，不额外持久化）。

## 8. 错误处理

| 场景 | 处理 |
|---|---|
| 每步校验器异常 | 放行，返回 inner 原结果，记 warning |
| 检索空命中 | 追加 hint 触发自纠正，不硬阻断 |
| 代码 ToolError | 语义不变（is_error 自纠正）+ emit check error |
| judge/独立模型/抓取 基建抖动 | 沿用「放行不拦截、仅记日志」 |
| TrajectoryJudge 解析失败 | 三层分全 None，跳过评分，前端质量分显示「—」 |
| 硬门失败 + 重答用尽 | 降级交付，徽章红色标注硬项 |
| 前端 quality JSON 解析失败 | 忽略该事件，徽章不显示质量分 |

## 9. 测试计划

**后端（pytest）**
- `tests/app/test_validating.py`：`relevance_check` 空命中/命中；`exec_check` 捕 ToolError；装饰器透明性（校验异常不改 inner 结果、str/ToolOutput 形态保持）；emit 被调用。
- `tests/app/test_verify.py`（扩展）：judge 独立 completer 注入；grounding 逐句输出解析；facts 链接可达（mock 抓取）；硬/软门分类；`TrajectoryJudge.score` 结构化解析 + 失败降级（mock complete）。
- `tests/app/test_chat_gate.py`（扩展/新）：轨迹收集；trajectory 软门并入；硬门失败降级路径；`quality`/`check` emit。

**前端（vitest，先 `tsc` 构建避免过期 .js）**
- `VerifyBadge.test.tsx`：状态机（验证中→通过/未通过）；常驻（`showTools=false` 仍渲染）；`checks`/`quality` 事件消费与展开明细；quality 解析容错。

## 10. 文件清单

| 文件 | 动作 |
|---|---|
| `app/tools/validating.py` | 🆕 装饰器 + 规则 |
| `app/verify.py` | ✏️ judge 独立/挑错、grounding 逐句、facts、硬软门、`TrajectoryJudge` |
| `app/config.py` | ✏️ 新配置项 |
| `app/assembly.py` | ✏️ 装配 `ValidatingTool` |
| `app/api/chat.py` | ✏️ 轨迹收集 + trajectory 门控 + emit quality |
| `app/completion.py` | ✏️（如需）独立 judge completer 构造 |
| `web/src/types.ts` | ✏️ checks/quality 字段 |
| `web/src/components/VerifyBadge.tsx` | 🆕 常驻徽章 |
| `web/src/components/ChatView.tsx` | ✏️ 消费 check/quality + 常驻徽章 |
| `tests/app/test_validating.py` | 🆕 |
| `tests/app/test_verify.py` | ✏️ |
| `web/src/components/VerifyBadge.test.tsx` | 🆕 |

**内核 `src/harness`：零改动。**
