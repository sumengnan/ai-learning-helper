# 确定性考试判分与「答错必存」设计

## Context（背景与目标）

现状：模拟考试是**模型驱动**的自由聊天——模型自己判对错、自己决定调用 `save_wrong_answer`
把错题存入错题集。即便前端开关、后端工具注册、`SaveWrongAnswerTool` 全部正常，模型仍常
在「即时式」里漏调保存工具，导致答错未入库。提示词加固只能提升概率，无法保证。

**目标**：让「考试答错 → 存入错题集」变成**服务端确定性执行**的动作，不依赖模型自觉。

**已与用户确认的决策**：
1. **覆盖所有题型**：客观题（单选/多选/判断）服务端字面确定性判分；简答题由服务端 judge
   模型判对错。无论哪种，「答错→保存」都由服务端执行。
2. **接受有状态考试流程**：服务端记住「当前题 + 正确答案」，把用户下一条消息当作对该题的作答。

**架构选型**：方案 B —— 聊天内嵌考试状态 + 服务端判分中间件。不新增前端页面，复用现有
`/api/chat` 流；判分与保存全在服务端，模型只负责「呈现题目 + 讲解」。

## 核心分工

- **服务端拥有**：考试状态、作答解析、判分、保存错题、推进题目、计分与小结。
- **模型只负责**：调用 `start_exam` 开考、呈现题目、依据服务端注入的判定结果做讲解。
- **不变量**：考试激活期间，用户每答一题，服务端**必然**完成判分；答错**必然**入错题集
  （受「答错自动保存」开关约束）。模型讲错、讲漏、不讲，都不影响已完成的保存。

## 现有可复用件

- `QuestionStore.sample(user_id, count, types)`：题库抽题（含答案）。
- `WrongAnswerStore.create/sample`：错题集读写；`snapshot` 结构 `{type,stem,options,answer,explanation}`。
- `app/quiz_service.py::_valid(q, types)`：题目合法性校验。
- `app/completion.py::build_judge_completer`：独立 judge 单发调用（简答判分复用）。
- `/api/chat` 现有「按会话取历史 → 组装上下文 → 跑模型流」骨架（`app/api/chat.py::chat`）。
- 附件把 `[本轮附件…]` 追加进 `model_message` 的模式（判定结果注入照此办理）。
- `app/db.py::_SCHEMA` + `migrate`：新增表沿用同一迁移机制。

## 数据模型

新增表 `exam_sessions`（每会话至多一条 active）：

| 列 | 类型 | 说明 |
|---|---|---|
| conversation_id | TEXT PK | 一会话一场考试 |
| user_id | TEXT | 归属校验 |
| mode | TEXT | `instant`（即时）/ `graded`（打分） |
| questions | TEXT(JSON) | 题目快照数组：`[{type,stem,options,answer,explanation,question_id?}]`（含答案） |
| cursor | INTEGER | 当前待作答题下标（0-based） |
| results | TEXT(JSON) | 每题结果 `[{user_answer, is_correct}]`，供打分小结 |
| status | TEXT | `active` / `ended` |
| created_at, updated_at | TEXT | ISO8601 |

## 组件

### 1. `app/exam_session.py` — `ExamSessionStore`
- `start(user_id, conv_id, questions: list[dict], mode: str) -> None`：新建（覆盖同会话旧 active）。
- `get_active(user_id, conv_id) -> dict | None`：取 active 会话（含 questions/cursor/mode/results）。
- `current(session) -> dict | None`：`questions[cursor]`，越界返回 None。
- `record(user_id, conv_id, user_answer, is_correct: bool) -> None`：追加 results、cursor+1、更新 updated_at；cursor 到末尾不改 status（结束由调用方判定）。
- `end(user_id, conv_id) -> None`：status=ended（保留记录供小结后清理或直接删除）。
- 与其它 Store 一致：`__init__(db_path=None, *, conn=None)`，用户隔离。

### 2. `app/exam_grader.py` — 判分与作答解析（纯函数为主，便于单测）
- `parse_choice(text: str, q: dict) -> object | None`：把用户自由文本解析成可比对的作答：
  - `truefalse`：对/正确/是/√/true/T→True；错/错误/否/×/false/F→False。
  - `single`：识别选项字母 A–Z、序号 1–N、或与某选项文本精确/包含匹配 → 返回选项**下标**。
  - `multiple`：识别多个字母/序号/选项文本 → 返回下标**集合**（去重排序）。
  - `short`：原样返回用户文本（交给 judge）。
  - 解析不出（客观题）→ 返回 `None`（触发 judge 兜底或要求重答）。
- `grade_objective(q: dict, parsed) -> bool`：`single` 比下标相等；`multiple` 比集合相等；
  `truefalse` 比布尔。**确定性**。
- `async grade_short(judge_complete, q, user_text) -> tuple[bool, str]`：judge 模型判对错 + 反馈。
  judge 失败（基建抖动/解析失败）→ 保守判为**不算错**（不误存），并记 warning。
- `SHORT_JUDGE_SYSTEM`：判对错的系统提示（抓要点/语义等价即对，返回 `{"correct": bool,"feedback": str}`）。
- `END_INTENT_RE`：识别「结束/退出/不考了/停止考试」等结束意图（中间件用，先于判分）。

### 3. `app/tools/exam_tools.py` — 新增 `StartExamTool`（`name="start_exam"`）
- 用途（description）：用户想开始模拟考试/刷题时调用，初始化一场服务端托管的考试；
  开考后每题的判分与「答错自动入错题集」由系统完成，**你无需再调用 save_wrong_answer**。
- Params：
  - `source: str = "bank"`（`bank` 题库 / `wrong` 错题集 / `adhoc` 即席）。
  - `count: int = 5`、`types: list[str] | None = None`（bank/wrong 抽样用；夹取 1–50）。
  - `mode: str = "instant"`（`instant`/`graded`）。
  - `questions: list[dict] | None = None`（**source=adhoc 时必填**：模型给出的题目含答案，
    `{type,stem,options,answer,explanation}`，逐题 `_valid` 校验）。
- `run`：
  - `bank`→`question_store.sample`；`wrong`→`wrong_store.sample` 取 `snapshot`；`adhoc`→用 `questions`。
  - 校验后写入 `ExamSessionStore.start`；返回**第一题的呈现文本**（题干 + 选项，客观题标 A/B/C/D，
    不含答案）+ 一句「已开始考试（共 N 题，即时/打分式）」。空题源返回引导文案，不建会话。
- 注入 `make_chat_router` 时装配（需 `exam_session_store` + `question_store`/`wrong_store`）。
- 可选新增 `EndExamTool`（`end_exam`）：模型在用户明确要求结束时调用；与中间件的结束意图识别互为兜底。

### 4. `app/api/chat.py` — 判分中间件（关键，纯服务端，先于模型）
在 `chat()` 取到 `history` 后、组装上下文前插入：

```
exam = exam_session_store.get_active(user_id, conv_id) if exam_session_store else None
verdict_note = ""
if exam:
    if END_INTENT_RE.search(req.message):        # 结束意图 → 结束，不判分
        exam_session_store.end(...); verdict_note = "【考试系统】考试已结束，请给出简短小结。"
    else:
        q = current(exam)
        parsed = parse_choice(req.message, q)
        if q.type in 客观 and parsed is None:
            verdict_note = "【考试系统】未能识别你的作答，请提示用户明确作答（如选项字母），本题不推进。"
            # 不 record、不 save、不推进
        else:
            if q.type == "short":
                is_correct, fb = await grade_short(judge_complete, q, req.message)
            else:
                is_correct = grade_objective(q, parsed)
            # user_answer 存「与答案同构」的值：客观题存 parsed（下标/下标集合/布尔），
            # 简答题存原始文本 —— 使错题集详情弹框能按 answerText(type,options,value) 正确渲染。
            user_answer = parsed if q["type"] != "short" else req.message
            if (not is_correct) and req.save_wrong and wrong_store is not None:
                wrong_store.create(user_id, q.get("question_id",""), "exam", snapshot(q), user_answer)  # 确定性保存
            exam_session_store.record(user_id, conv_id, user_answer, is_correct)
            verdict_note = 组装(模式, 本题对错, 正确答案, 是否最后一题, 打分小结)
            if 队列耗尽: exam_session_store.end(...)
```
- `verdict_note` 追加进 `model_message`（与附件提示同法），作为**系统权威判定**指引模型讲解/呈现下一题。
- **即时式**：note 含「本题：对/错 + 正确答案 + 让你据此讲解并出下一题」。
- **打分式**：note 仅「已记录，请出下一题、不要公布对错」；末题后 note 含「总分 X/N + 逐题结果，请给小结」。
- **防重复保存**：`exam` 为 active 时，`_build_registry` **不注册** `save_wrong_answer`（保存归服务端）；
  同时该 turn 的 EXAM 指引说明「判分与保存已由系统自动完成」。非考试态仍保留该工具（零散答错场景）。

### 5. `app/main.py` — 装配
- 构造 `exam_session_store = ExamSessionStore(conn=app_conn)`（沿用 need_db 逻辑）。
- 传入 `make_chat_router(..., exam_session_store=exam_session_store)`。
- judge completer：复用 `build_judge_completer`（若已装配）供简答判分；未装配则简答判分降级为「不算错」。

### 6. 前端（最小改动）
- 不新增页面。可选：`/api/chat` 响应或一个轻端点暴露「考试中 第 c/共 n 题」，聊天页顶部显示小徽标。
- 本期可先不做前端徽标（后端能力优先），或仅加只读徽标。**标记为可选，不阻塞核心。**

## 开关与既有行为

- 「答错自动保存错题集」开关（`req.save_wrong`）：开→服务端自动存；关→**照常判分/推进但不保存**。
- 非考试态：现有 `save_wrong_answer` 工具与 EXAM_GUIDE 行为保留（供零散练习），本设计不回退它们。
- EXAM_GUIDE：新增一节说明「正式考试请调用 start_exam 开始，开考后判分与保存由系统自动完成，
  你无需也不要调用 save_wrong_answer」。

## 边界与错误处理

- **作答无法解析（客观题）**：不判分、不保存、不推进；提示模型请用户明确作答（避免误存/误跳）。
- **judge 失败（简答/兜底）**：保守判为不算错，记 warning；宁可漏存不可误存。
- **并发/刷新**：考试状态存 DB，按 (user, conversation) 键；刷新后仍可续考。
- **一会话一考**：`start_exam` 覆盖同会话旧 active，避免多考并行。
- **越界/空队列**：`current` 返回 None → 视为已结束。

## 测试（TDD，先写后实现）

- `tests/app/test_exam_session.py`：start/get/current/record/推进/end、用户与会话隔离、覆盖旧 active。
- `tests/app/test_exam_grader.py`：
  - `parse_choice`：字母/序号/选项文本/对错 各分支；多选集合；无法解析→None。
  - `grade_objective`：单选/多选/判断 正误；多选少选多选均判错。
  - `grade_short`：stub judge 判对/错/异常降级。
  - `END_INTENT_RE`：命中结束意图。
- `tests/app/test_exam_tools.py`（扩展）：`StartExamTool` 三种 source 建会话、adhoc 逐题校验、空题源文案。
- `tests/app/test_api.py`（扩展，端到端确定性验证 —— **核心**）：
  - 起一场 instant 考试（注入 question_store/wrong_store/exam_session_store），
    用户答错客观题 → **无任何模型 save 调用**下，错题集 +1；答对 → 不增。
  - 关掉 save_wrong → 答错不入库但推进。
  - graded 模式：中途不泄露对错；末题后小结含总分；session 结束。
  - 考试 active 时 `save_wrong_answer` 未注册（防重复保存）。
  - 结束意图消息 → 结束、不判分。

## 验证

1. `uv run pytest tests/app/test_exam_session.py tests/app/test_exam_grader.py tests/app/test_exam_tools.py tests/app/test_api.py -q`。
2. 端到端手测：开关开 → 「考我 3 道单选」→ 逐题答错 → 错题集实时 +1（即使 AI 只字未提保存）。

## 非目标（YAGNI）

- 不做计时、防作弊、题目乱序权重等考试增强。
- 不做独立考试页/成绩册 UI（方案 A 的范畴）。
- 不改 harness 内核。
