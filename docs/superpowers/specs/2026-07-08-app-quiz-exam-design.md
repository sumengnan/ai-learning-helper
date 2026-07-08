# AI 学习助手 · App-3（题库 / 模拟考试 / 错题集）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用，应用层第三个子项目
- **前置**：harness ①-③d + App-1 聊天脊柱 + App-2 知识库已完成并在 `main`

---

## 0. 背景与范围

在 App-1/App-2 之上加「出题 → 题库 → 模拟考试 → 错题集」闭环：基于 App-2 已入库的知识（harness `Memory` 的 `knowledge` collection）用 LLM 出题存入题库；从题库组卷做模拟考试，交卷自动判分（客观题精确匹配、简答题 LLM 判分）；判错的题自动进错题集。

**技术选型**：沿用 App-1/App-2（FastAPI + React + react-router）；出题/简答判分复用 harness `AgentLoop` 单轮无工具调用（无需给 harness 加能力）；数据存 SQLite。**harness 零改动**。

设计通则：严格 YAGNI；复用 harness `Memory`/`AgentLoop`（不重造 LLM 管道）；harness 零改动；错题存**题目快照**（原题删除后仍可回看）；判分统一由 `QuizService.grade` 出口，客观确定性、简答 LLM；测试用 fake completer + fake embedder + `:memory:`，不打网络。

---

## 1. 范围与验收

### IN
- **出题**：`QuizService.generate(topic, count, types)` → `Memory.search(topic, "knowledge", k)` 取 top-k 知识块 → 拼提示 → 单轮 `AgentLoop` 生成题目 JSON → 校验入 `QuestionStore`。
- **题库管理**：`QuestionStore`（SQLite CRUD）；列表 / 单题删 / 批量删。
- **模拟考试**：从题库组卷（随机抽 N，可按题型过滤，返回**不含答案**的卷子）→ 前端答题 → 交卷判分 → 存 `exam_results` 一条成绩 → 判错题写入错题集。
- **错题集**：`WrongAnswerStore`（存题目快照 + 用户作答）；列表 / 批量删。
- **App 装配小改**：`main.py` 构造 `QuizService`（注入 `memory` + `complete` 单轮 LLM 回调）、挂 questions/exams 路由。

### OUT（后续）
断点续考、按文档/知识点精确组卷、错题重做再考、成绩趋势图、题目编辑、导入导出、图库/下载（App-4）。

### 题型（4 种）
- `single` 单选：`options` = 选项文本数组，`answer` = 正确选项**索引**（int）。
- `multiple` 多选：`options` = 数组，`answer` = 正确索引**数组**（list[int]，判分需集合相等）。
- `truefalse` 判断：`options` = null，`answer` = **bool**。
- `short` 简答：`options` = null，`answer` = **参考答案文本**（str）；判分走 LLM。

### 验收标准
1. 知识库有内容时，`generate(topic, count, types)` 产出 `count` 道通过 schema 校验的题并入库；`GET /api/questions` 列出。
2. 出题时 memory 未装配 → **503**；知识库检索无命中 → **422**（提示先建知识库）；LLM 输出无法解析/校验 → **502**（干净提示，不落库半成品）。
3. 客观题判分正确：单选/判断精确匹配、多选集合相等（`QuizService.grade` 单测覆盖四型）。
4. 简答题判分：注入 fake completer 返回 `{correct, score, feedback}`，`grade` 据阈值定对错并带反馈。
5. 组卷 `POST /api/exams` 返回的题目**不含 `answer`/`explanation`**（防前端偷看）。
6. 交卷 `POST /api/exams/submit`：判分 → 返回逐题结果（对错 + 正确答案 + 解析 + 简答反馈）→ 存一条 `exam_results` → 判错题进 `wrong_answers`（含快照）。
7. `GET /api/exams` 列历史成绩；`GET /api/wrong-answers` 列错题；批量删可用。
8. 删题库中的题后，已在错题集的该题快照**仍可查看**（解耦验证）。
9. 前端题库/考试/错题集三页可用，经 `/questions`、`/exam`、`/wrong` 路由；聊天/知识库页不受影响。
10. harness 零改动；harness + App-1 + App-2 测试不回归。

---

## 2. 架构与模块

**设计取向**：出题 = RAG 检索 + 单轮 LLM 生成 JSON，`QuizService` 只依赖两个可注入接口——`memory`（检索）与 `complete(system, user) -> str`（单轮 LLM），与 `AgentLoop` 解耦，便于测试注入 fake。判分统一 `grade`。错题存快照。

```
app/                                   [改/增]
├── questions.py       [新] QuestionStore（题库 SQLite CRUD + 随机抽样组卷）
├── exams.py           [新] ExamStore（exam_results 成绩记录）
├── wrong_answers.py   [新] WrongAnswerStore（错题快照 + 批量删）
├── quiz_service.py    [新] QuizService（generate / grade）
├── completion.py      [新] build_completer(client, model_name) → async (system,user)->str（单轮 AgentLoop）
├── main.py            [改] 装配 QuizService + 挂 questions/exams 路由
└── api/
    ├── questions.py   [新] POST /api/questions/generate、GET /api/questions、DELETE /api/questions/{id}、POST /api/questions/delete
    └── exams.py       [新] POST /api/exams、POST /api/exams/submit、GET /api/exams、GET /api/wrong-answers、POST /api/wrong-answers/delete、DELETE /api/wrong-answers/{id}
web/                                   [改/增]
├── src/App.tsx        [改] 导航加「题库 / 考试 / 错题集」
├── src/pages/QuestionBankView.tsx  [新] 出题表单 + 题库列表 + 删除
├── src/pages/ExamView.tsx          [新] 组卷 + 答题 + 交卷 + 成绩
├── src/pages/WrongAnswersView.tsx  [新] 错题列表 + 批量删
└── src/api/client.ts  [改] questions / exams API
```

**依赖新增**：无（后端复用现有 openai/pydantic/fastapi；前端复用 react-router）。harness 不加依赖不改代码。

**数据流**：
```
出题：POST /api/questions/generate {topic,count,types}
 → Memory.search(topic,"knowledge",k) → 拼提示 → complete(system,user) → JSON
 → 解析+校验（逐题按 type 校验 schema）→ QuestionStore.create → 返回题目列表

考试：POST /api/exams {count,types?} → QuestionStore.sample → 去 answer/explanation → 卷子
 交卷：POST /api/exams/submit {answers:[{question_id,user_answer}]}
   → 逐题 QuestionStore.get → QuizService.grade(q,user_answer) → {correct,score,feedback}
   → 汇总 → ExamStore.create(result) → 判错题 WrongAnswerStore.create(snapshot) → 返回逐题结果
```

---

## 3. 后端

### 3.1 `questions.py::QuestionStore`（SQLite）
```sql
questions(id TEXT PRIMARY KEY, type TEXT, stem TEXT,
          options TEXT,        -- JSON 数组；判断/简答为 NULL
          answer TEXT,         -- JSON：single=int / multiple=list[int] / truefalse=bool / short=str
          explanation TEXT, source TEXT, created_at TEXT)
```
方法：
- `create(q: dict) -> str`：生成 `id=uuid4().hex`，`options`/`answer` 存 JSON，返回 id。
- `list() -> list[dict]`：全部题（含答案，供题库管理查看）。
- `get(id) -> dict | None`：单题（判分/组卷用）。
- `sample(count: int, types: list[str] | None) -> list[dict]`：`ORDER BY RANDOM() LIMIT count`，`types` 非空则 `WHERE type IN (...)`。
- `delete(id) -> None`、`delete_many(ids: list[str]) -> None`。

### 3.2 `exams.py::ExamStore`（SQLite）
```sql
exam_results(id TEXT PRIMARY KEY, created_at TEXT,
             total INTEGER, correct INTEGER, score REAL,
             detail TEXT)      -- JSON：[{question_id,type,stem,user_answer,correct,correct_answer,explanation,feedback}]
```
方法：`create(total, correct, score, detail) -> str`、`list() -> list[dict]`（按时间倒序）。

### 3.3 `wrong_answers.py::WrongAnswerStore`（SQLite）
```sql
wrong_answers(id TEXT PRIMARY KEY, question_id TEXT, exam_id TEXT,
              snapshot TEXT,   -- JSON：{type,stem,options,answer,explanation}
              user_answer TEXT, created_at TEXT)
```
方法：`create(question_id, exam_id, snapshot: dict, user_answer) -> str`、`list() -> list[dict]`、`delete_many(ids: list[str]) -> None`、`delete(id) -> None`。

### 3.4 `completion.py`
```python
def build_completer(client, model_name: str):
    async def complete(system_prompt: str, user_prompt: str) -> str:
        loop = AgentLoop(client=client, registry=ToolRegistry(),
                         context=ContextManager(system_prompt),
                         max_steps=1, model_name=model_name)
        final = ""
        async for ev in loop.run(user_prompt):
            if isinstance(ev, RunFinished):
                final = ev.message.content or ""
            elif isinstance(ev, RunError):
                raise RuntimeError(ev.error)
        return final
    return complete
```

### 3.5 `quiz_service.py::QuizService`
```python
class QuizError(Exception): ...            # 生成/解析/校验失败
class NoKnowledge(Exception): ...          # 检索无命中

class QuizService:
    def __init__(self, memory, question_store, complete, collection="knowledge",
                 retrieve_k=6, short_pass_score=60): ...

    async def generate(self, topic: str, count: int, types: list[str]) -> list[dict]:
        hits = await self._memory.search(topic, self._collection, self._retrieve_k)
        if not hits: raise NoKnowledge(topic)
        context = "\n\n".join(h.text for h in hits)
        raw = await self._complete(GEN_SYSTEM, gen_user(topic, count, types, context))
        items = _parse_questions(raw)          # 去 ```json 围栏 → json.loads → 必须是数组
        valid = [q for q in items if _valid(q, types)]   # 逐题按 type 校验 schema
        if not valid: raise QuizError("生成结果无有效题目")
        for q in valid: q["source"] = topic; q["id"] = self._store.create(q)
        return valid

    async def grade(self, question: dict, user_answer) -> dict:
        t = question["type"]
        if t == "single":     correct = user_answer == question["answer"]
        elif t == "truefalse":correct = bool(user_answer) == question["answer"]
        elif t == "multiple": correct = sorted(user_answer or []) == sorted(question["answer"])
        elif t == "short":
            raw = await self._complete(GRADE_SYSTEM, grade_user(question, user_answer))
            verdict = _parse_grade(raw)        # {correct?,score,feedback}
            correct = verdict.get("score", 0) >= self._short_pass_score
            return {"correct": correct, "score": verdict.get("score"), "feedback": verdict.get("feedback")}
        else: raise QuizError(f"未知题型 {t}")
        return {"correct": correct, "feedback": None}
```
- `_parse_questions`：容错剥离 markdown ```json 围栏后 `json.loads`；非数组或非法 JSON → `QuizError`。
- `_valid(q, types)`：`type ∈ types`、`stem` 非空、`answer` 与题型匹配（single→int 在选项范围、multiple→非空 int 数组、truefalse→bool、short→非空 str）；single/multiple 要求 `options` 至少 2 项。
- 生成提示词要求**严格输出 JSON 数组**、字段 `type/stem/options/answer/explanation`，并注明各题型 answer 格式。

### 3.6 `api/questions.py::make_questions_router(quiz_service, question_store)`
- `POST /api/questions/generate`（body `{topic, count, types}`）：`quiz_service is None`（memory 未装配）→ **503**；`NoKnowledge` → **422**；`QuizError` → **502**；成功 → `{"questions": [...]}`。`count` 上限（如 ≤20）、`types` 默认 `["single"]`。
- `GET /api/questions` → `question_store.list()`。
- `DELETE /api/questions/{id}` → `delete(id)` → `{"ok": True}`。
- `POST /api/questions/delete`（body `{ids: [...]}`）→ `delete_many(ids)` → `{"ok": True}`。

### 3.7 `api/exams.py::make_exams_router(quiz_service, question_store, exam_store, wrong_store)`
- `POST /api/exams`（body `{count, types?}`）：`question_store.sample(count, types)` → 逐题剔除 `answer`/`explanation` → `{"questions": [{id,type,stem,options}]}`。题库空 → `{"questions": []}`（前端提示先出题）。
- `POST /api/exams/submit`（body `{answers: [{question_id, user_answer}]}`）：`quiz_service is None` → **503**；逐题 `question_store.get`（题已删则跳过并计入 detail `missing`）→ `await quiz_service.grade` → 汇总 `total/correct/score(=correct/total*100)` → `exam_store.create(...)` → 每道判错 `wrong_store.create(question_id, exam_id, snapshot, user_answer)` → 返回 `{exam_id, total, correct, score, detail:[...]}`。
- `GET /api/exams` → `exam_store.list()`。
- `GET /api/wrong-answers` → `wrong_store.list()`。
- `POST /api/wrong-answers/delete`（body `{ids}`）→ `delete_many` → `{"ok": True}`；`DELETE /api/wrong-answers/{id}` → `delete` → `{"ok": True}`。

### 3.8 `main.py` 改
构造 `QuestionStore(config.questions_db_path)`、`ExamStore(...)`、`WrongAnswerStore(...)`；若 `harness.memory` 存在则 `complete = build_completer(harness.client, config.model)`、`quiz_service = QuizService(harness.memory, question_store, complete)`，否则 `quiz_service = None`（生成/交卷 503）。挂两个路由。DB 路径进 `config.py`（`questions_db_path`/`exams_db_path`/`wrong_answers_db_path`，默认同目录文件；测试传 `:memory:`）。

---

## 4. 前端

- **`App.tsx`**：导航加 `题库 /questions`、`考试 /exam`、`错题集 /wrong`（`NavLink`）；`<Routes>` 增三条。
- **`pages/QuestionBankView.tsx`**：出题表单（主题输入 + 题数 + 题型多选复选框）→ `POST /generate`（生成中态/错误态：503→「未启用知识库」、422→「知识库无相关内容」、502→「生成失败请重试」）；题库列表（题干 · 题型 · 来源 + 单删 + 多选批量删）；空态。
- **`pages/ExamView.tsx`**：设置（题数 + 题型）→ 开始考试 `POST /api/exams` → 渲染卷子（单选 radio / 多选 checkbox / 判断 / 简答 textarea）→ 交卷 `POST /submit` → 成绩页（总分 + 逐题对错 + 正确答案 + 解析 + 简答反馈）。
- **`pages/WrongAnswersView.tsx`**：错题列表（快照题干/题型/你的作答/正确答案）+ 多选批量删 + 空态。
- **`api/client.ts`** 增 `questions.{generate,list,remove,removeMany}`、`exams.{compose,submit,history}`、`wrong.{list,removeMany}`。

---

## 5. 配置 / 测试 / 依赖

### 配置（`config.py`）
`questions_db_path/exams_db_path/wrong_answers_db_path`（默认独立文件）、`quiz_max_count: int = 20`、`quiz_retrieve_k: int = 6`、`short_pass_score: int = 60`。

### 测试（后端 pytest，fake completer + fake embedder + `:memory:`，不打网络）
- `QuestionStore`：CRUD + `options`/`answer` JSON 往返 + `sample`（数量/题型过滤）+ `delete_many`。
- `QuizService.grade`：四题型各断言对错（single/truefalse/multiple 确定性；short 注入 fake completer 返回定分 → 阈值判对错 + 反馈透传）。
- `QuizService.generate`：真 `Memory(:memory:)` + fake embedder 先 `add_texts` 建知识 → fake completer 返回定制 JSON（含围栏）→ 断言解析/校验/入库；无命中 → `NoKnowledge`；非法 JSON → `QuizError`；生成结果全不合法 → `QuizError`。
- `ExamStore`/`WrongAnswerStore`：create + list + `delete_many`；错题快照 JSON 往返。
- `/api/questions` + `/api/exams`（TestClient + 注入含 Memory 的假 harness + fake completer）：生成→列出→删；组卷返回**不含 answer**；交卷→逐题结果 + 成绩落库 + 错题入集；memory=None → 生成/交卷 503；无知识 → 422。删题后错题快照仍可查（对应验收 8）。
- 前端：Vitest 覆盖 `questions`/`exams` API 客户端或某页渲染（1-2 用例）。

### 依赖
无新增后端依赖；前端复用 react-router-dom。

---

## 6. 后续衔接（非本次范围）
- **App-4**：下载 / 图库。
- 断点续考、错题重做组卷、按文档组卷、成绩趋势、题目人工编辑、更多题型（连线/排序）。
