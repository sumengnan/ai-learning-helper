# App-3 题库 / 模拟考试 / 错题集 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 App-1/App-2 之上加「出题 → 题库 → 模拟考试 → 错题集」闭环：基于知识库用 LLM 出题入库，组卷考试自动判分（客观精确匹配 / 简答 LLM），错题自动入集。

**架构：** 沿用 flat `app/*.py` + 一 store 一 api 模式。三个 SQLite store（QuestionStore/ExamStore/WrongAnswerStore）+ 一个 QuizService（generate/grade，依赖注入 `memory` 检索与 `complete` 单轮 LLM 回调，与 AgentLoop 解耦）+ 两个 API 路由 + 三个 React 页。**harness 零改动**。

**技术栈：** Python 3.11+ · FastAPI · pydantic v2 · pytest · React + react-router + Vitest。复用 harness `Memory`（RAG 检索）与 `AgentLoop`（单轮生成/判分）。

**规格：** `docs/superpowers/specs/2026-07-08-app-quiz-exam-design.md`

**关键约定：**
- 提交署名必须 `sumengnan`（`git -c user.name=sumengnan -c user.email=2499165351@qq.com commit`），提交信息**禁止**出现 Claude/AI/Co-Authored/Generated。
- harness（`src/harness/`）零改动。
- 测试用 fake completer（普通 async 函数返回定制字符串）+ fake embedder（`mock_embedder` fixture）+ `:memory:`，不打网络。
- 共享 fixtures 在 `tests/conftest.py`：`mock_embedder`（`MockEmbeddingClient` 类，用 `mock_embedder(dimension=64)` 实例化）、`make_mock`（`lambda turns: MockModelClient(turns)`）、`text_turn`。
- `answer` 编码：single=int（选项索引）、multiple=list[int]、truefalse=bool、short=str（参考答案）。

---

### 任务 1：配置项

**文件：**
- 修改：`app/config.py`

- [ ] **步骤 1：加配置字段**

在 `app/config.py` 的 `AppConfig` 末尾（`documents_db_path` 之后）追加：

```python
    questions_db_path: str = "questions.db"
    exams_db_path: str = "exams.db"
    wrong_answers_db_path: str = "wrong_answers.db"
    quiz_max_count: int = 20
    quiz_retrieve_k: int = 6
    short_pass_score: int = 60
```

- [ ] **步骤 2：Commit**

```bash
git add app/config.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "chore: App-3 题库/考试配置项"
```

---

### 任务 2：QuestionStore（题库）

**文件：**
- 创建：`app/questions.py`
- 测试：`tests/app/test_questions.py`

- [ ] **步骤 1：编写失败的测试**

`tests/app/test_questions.py`：

```python
from app.questions import QuestionStore


def _q(type="single", stem="1+1=?", options=["1", "2", "3"], answer=1):
    return {"type": type, "stem": stem, "options": options, "answer": answer,
            "explanation": "因为等于二", "source": "算术"}


def test_create_and_get_roundtrip():
    s = QuestionStore(":memory:")
    qid = s.create(_q())
    got = s.get(qid)
    assert got["id"] == qid
    assert got["type"] == "single"
    assert got["options"] == ["1", "2", "3"]   # JSON 往返
    assert got["answer"] == 1


def test_answer_json_types_roundtrip():
    s = QuestionStore(":memory:")
    multi = s.get(s.create(_q(type="multiple", answer=[0, 2])))
    tf = s.get(s.create(_q(type="truefalse", options=None, answer=True)))
    short = s.get(s.create(_q(type="short", options=None, answer="光合作用")))
    assert multi["answer"] == [0, 2]
    assert tf["answer"] is True and tf["options"] is None
    assert short["answer"] == "光合作用"


def test_list_and_delete():
    s = QuestionStore(":memory:")
    a = s.create(_q()); b = s.create(_q(stem="2+2=?"))
    assert {q["id"] for q in s.list()} == {a, b}
    s.delete(a)
    assert {q["id"] for q in s.list()} == {b}


def test_delete_many():
    s = QuestionStore(":memory:")
    ids = [s.create(_q(stem=f"q{i}")) for i in range(3)]
    s.delete_many(ids[:2])
    assert [q["id"] for q in s.list()] == [ids[2]]


def test_sample_count_and_type_filter():
    s = QuestionStore(":memory:")
    for _ in range(5): s.create(_q(type="single"))
    for _ in range(5): s.create(_q(type="truefalse", options=None, answer=True))
    assert len(s.sample(3, None)) == 3
    only_tf = s.sample(10, ["truefalse"])
    assert len(only_tf) == 5 and all(q["type"] == "truefalse" for q in only_tf)
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/app/test_questions.py -q`
预期：FAIL（`ModuleNotFoundError: app.questions`）

- [ ] **步骤 3：实现 `app/questions.py`**

```python
# app/questions.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QuestionStore:
    def __init__(self, db_path: str) -> None:
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS questions(
                 id TEXT PRIMARY KEY, type TEXT, stem TEXT, options TEXT,
                 answer TEXT, explanation TEXT, source TEXT, created_at TEXT)""")
        self._db.commit()

    def create(self, q: dict) -> str:
        qid = uuid4().hex
        self._db.execute(
            "INSERT INTO questions VALUES (?,?,?,?,?,?,?,?)",
            (qid, q["type"], q["stem"],
             json.dumps(q.get("options"), ensure_ascii=False),
             json.dumps(q.get("answer"), ensure_ascii=False),
             q.get("explanation", ""), q.get("source", ""), _now()))
        self._db.commit()
        return qid

    def _row(self, r) -> dict:
        return {"id": r[0], "type": r[1], "stem": r[2],
                "options": json.loads(r[3]), "answer": json.loads(r[4]),
                "explanation": r[5], "source": r[6], "created_at": r[7]}

    def get(self, qid: str) -> dict | None:
        r = self._db.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        return self._row(r) if r else None

    def list(self) -> list[dict]:
        rows = self._db.execute("SELECT * FROM questions ORDER BY created_at").fetchall()
        return [self._row(r) for r in rows]

    def sample(self, count: int, types: list[str] | None) -> list[dict]:
        if types:
            ph = ",".join("?" * len(types))
            rows = self._db.execute(
                f"SELECT * FROM questions WHERE type IN ({ph}) ORDER BY RANDOM() LIMIT ?",
                (*types, count)).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM questions ORDER BY RANDOM() LIMIT ?", (count,)).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, qid: str) -> None:
        self._db.execute("DELETE FROM questions WHERE id=?", (qid,))
        self._db.commit()

    def delete_many(self, ids: list[str]) -> None:
        self._db.executemany("DELETE FROM questions WHERE id=?", [(i,) for i in ids])
        self._db.commit()
```

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/app/test_questions.py -q`
预期：PASS（5 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/questions.py tests/app/test_questions.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: QuestionStore 题库存储与随机组卷"
```

---

### 任务 3：ExamStore（成绩记录）

**文件：**
- 创建：`app/exams.py`
- 测试：`tests/app/test_exams.py`

- [ ] **步骤 1：编写失败的测试**

`tests/app/test_exams.py`：

```python
from app.exams import ExamStore


def test_create_and_list():
    s = ExamStore(":memory:")
    detail = [{"question_id": "q1", "correct": True}]
    eid = s.create(total=1, correct=1, score=100.0, detail=detail)
    rows = s.list()
    assert len(rows) == 1
    assert rows[0]["id"] == eid
    assert rows[0]["total"] == 1 and rows[0]["correct"] == 1
    assert rows[0]["score"] == 100.0
    assert rows[0]["detail"] == detail            # JSON 往返


def test_list_newest_first():
    s = ExamStore(":memory:")
    first = s.create(1, 0, 0.0, [])
    second = s.create(1, 1, 100.0, [])
    assert [r["id"] for r in s.list()] == [second, first]   # 倒序
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/app/test_exams.py -q`
预期：FAIL（`ModuleNotFoundError: app.exams`）

- [ ] **步骤 3：实现 `app/exams.py`**

```python
# app/exams.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExamStore:
    def __init__(self, db_path: str) -> None:
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS exam_results(
                 id TEXT PRIMARY KEY, created_at TEXT, seq INTEGER,
                 total INTEGER, correct INTEGER, score REAL, detail TEXT)""")
        self._db.commit()
        self._seq = self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM exam_results").fetchone()[0]

    def create(self, total: int, correct: int, score: float, detail: list) -> str:
        eid = uuid4().hex
        self._seq += 1
        self._db.execute(
            "INSERT INTO exam_results VALUES (?,?,?,?,?,?,?)",
            (eid, _now(), self._seq, total, correct, score,
             json.dumps(detail, ensure_ascii=False)))
        self._db.commit()
        return eid

    def list(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,created_at,total,correct,score,detail "
            "FROM exam_results ORDER BY seq DESC").fetchall()
        return [{"id": r[0], "created_at": r[1], "total": r[2], "correct": r[3],
                 "score": r[4], "detail": json.loads(r[5])} for r in rows]
```

> 说明：用单调 `seq` 而非仅 `created_at` 排序，避免同一 ISO 时间戳下倒序不稳定。

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/app/test_exams.py -q`
预期：PASS（2 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/exams.py tests/app/test_exams.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: ExamStore 考试成绩记录"
```

---

### 任务 4：WrongAnswerStore（错题集）

**文件：**
- 创建：`app/wrong_answers.py`
- 测试：`tests/app/test_wrong_answers.py`

- [ ] **步骤 1：编写失败的测试**

`tests/app/test_wrong_answers.py`：

```python
from app.wrong_answers import WrongAnswerStore


def _snap():
    return {"type": "single", "stem": "1+1=?", "options": ["1", "2"],
            "answer": 1, "explanation": "等于二"}


def test_create_list_snapshot_roundtrip():
    s = WrongAnswerStore(":memory:")
    wid = s.create(question_id="q1", exam_id="e1", snapshot=_snap(), user_answer=0)
    rows = s.list()
    assert len(rows) == 1
    assert rows[0]["id"] == wid
    assert rows[0]["question_id"] == "q1" and rows[0]["exam_id"] == "e1"
    assert rows[0]["snapshot"] == _snap()          # 快照 JSON 往返
    assert rows[0]["user_answer"] == 0


def test_delete_many():
    s = WrongAnswerStore(":memory:")
    ids = [s.create("q", "e", _snap(), 0) for _ in range(3)]
    s.delete_many(ids[:2])
    assert [r["id"] for r in s.list()] == [ids[2]]


def test_delete_one():
    s = WrongAnswerStore(":memory:")
    a = s.create("q", "e", _snap(), 0)
    s.delete(a)
    assert s.list() == []
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/app/test_wrong_answers.py -q`
预期：FAIL（`ModuleNotFoundError`）

- [ ] **步骤 3：实现 `app/wrong_answers.py`**

```python
# app/wrong_answers.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WrongAnswerStore:
    def __init__(self, db_path: str) -> None:
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS wrong_answers(
                 id TEXT PRIMARY KEY, question_id TEXT, exam_id TEXT,
                 snapshot TEXT, user_answer TEXT, created_at TEXT, seq INTEGER)""")
        self._db.commit()
        self._seq = self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM wrong_answers").fetchone()[0]

    def create(self, question_id: str, exam_id: str, snapshot: dict, user_answer) -> str:
        wid = uuid4().hex
        self._seq += 1
        self._db.execute(
            "INSERT INTO wrong_answers VALUES (?,?,?,?,?,?,?)",
            (wid, question_id, exam_id,
             json.dumps(snapshot, ensure_ascii=False),
             json.dumps(user_answer, ensure_ascii=False), _now(), self._seq))
        self._db.commit()
        return wid

    def list(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,question_id,exam_id,snapshot,user_answer,created_at "
            "FROM wrong_answers ORDER BY seq DESC").fetchall()
        return [{"id": r[0], "question_id": r[1], "exam_id": r[2],
                 "snapshot": json.loads(r[3]), "user_answer": json.loads(r[4]),
                 "created_at": r[5]} for r in rows]

    def delete(self, wid: str) -> None:
        self._db.execute("DELETE FROM wrong_answers WHERE id=?", (wid,))
        self._db.commit()

    def delete_many(self, ids: list[str]) -> None:
        self._db.executemany("DELETE FROM wrong_answers WHERE id=?", [(i,) for i in ids])
        self._db.commit()
```

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/app/test_wrong_answers.py -q`
预期：PASS（3 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/wrong_answers.py tests/app/test_wrong_answers.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: WrongAnswerStore 错题快照存储"
```

---

### 任务 5：completion.py（单轮 LLM 回调）

**文件：**
- 创建：`app/completion.py`
- 测试：`tests/app/test_completion.py`

- [ ] **步骤 1：编写失败的测试**

`tests/app/test_completion.py`（复用 `make_mock` + `text_turn` fixtures）：

```python
import pytest
from app.completion import build_completer


@pytest.mark.asyncio
async def test_completer_returns_final_text(make_mock, text_turn):
    complete = build_completer(make_mock([text_turn("你好世界")]), model_name="m")
    out = await complete("你是助手", "打个招呼")
    assert out == "你好世界"


@pytest.mark.asyncio
async def test_completer_raises_on_run_error():
    class Boom:
        async def stream(self, messages, tools):
            raise RuntimeError("boom")
            yield  # 成为异步生成器
    complete = build_completer(Boom(), model_name="m")
    with pytest.raises(RuntimeError):
        await complete("s", "u")
```

> 注：项目已配置 `pytest-asyncio`（App-1/②已用 `@pytest.mark.asyncio`）。若首次运行报未识别该 marker，检查 `pyproject.toml` 的 `asyncio_mode`——App-1 已启用，无需改动。

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/app/test_completion.py -q`
预期：FAIL（`ModuleNotFoundError: app.completion`）

- [ ] **步骤 3：实现 `app/completion.py`**

```python
# app/completion.py
from __future__ import annotations

from harness.context.manager import ContextManager
from harness.events import RunError, RunFinished
from harness.loop.agent_loop import AgentLoop
from harness.tools.base import ToolRegistry


def build_completer(client, model_name: str):
    """返回 async (system_prompt, user_prompt) -> str：跑一轮无工具 AgentLoop，取最终文本。

    复用 harness 的重试/预算/OTel 封装；不给 harness 加任何能力。
    """
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

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/app/test_completion.py -q`
预期：PASS（2 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/completion.py tests/app/test_completion.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: build_completer 单轮 LLM 回调（复用 AgentLoop）"
```

---

### 任务 6：QuizService.grade（判分）

**文件：**
- 创建：`app/quiz_service.py`（本任务先建含 `grade` + 异常类；任务 7 补 `generate`）
- 测试：`tests/app/test_quiz_grade.py`

- [ ] **步骤 1：编写失败的测试**

`tests/app/test_quiz_grade.py`：

```python
import pytest
from app.quiz_service import QuizService


def _svc(complete=None):
    # grade 不用 memory/store；generate 才用。这里传 None。
    return QuizService(memory=None, question_store=None, complete=complete,
                       short_pass_score=60)


@pytest.mark.asyncio
async def test_grade_single():
    svc = _svc()
    q = {"type": "single", "answer": 1}
    assert (await svc.grade(q, 1))["correct"] is True
    assert (await svc.grade(q, 0))["correct"] is False


@pytest.mark.asyncio
async def test_grade_truefalse():
    svc = _svc()
    q = {"type": "truefalse", "answer": True}
    assert (await svc.grade(q, True))["correct"] is True
    assert (await svc.grade(q, False))["correct"] is False


@pytest.mark.asyncio
async def test_grade_multiple_set_equality():
    svc = _svc()
    q = {"type": "multiple", "answer": [0, 2]}
    assert (await svc.grade(q, [2, 0]))["correct"] is True      # 顺序无关
    assert (await svc.grade(q, [0]))["correct"] is False        # 缺一个
    assert (await svc.grade(q, [0, 1, 2]))["correct"] is False  # 多一个


@pytest.mark.asyncio
async def test_grade_short_uses_llm_and_threshold():
    async def fake(system, user):
        return '{"score": 80, "feedback": "答得不错"}'
    svc = _svc(complete=fake)
    q = {"type": "short", "answer": "光合作用把光能转化为化学能"}
    res = await svc.grade(q, "植物用光能合成有机物")
    assert res["correct"] is True and res["score"] == 80
    assert res["feedback"] == "答得不错"


@pytest.mark.asyncio
async def test_grade_short_below_threshold_is_wrong():
    async def fake(system, user):
        return '{"score": 30, "feedback": "偏离要点"}'
    svc = _svc(complete=fake)
    q = {"type": "short", "answer": "ref"}
    assert (await svc.grade(q, "乱答"))["correct"] is False
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/app/test_quiz_grade.py -q`
预期：FAIL（`ModuleNotFoundError: app.quiz_service`）

- [ ] **步骤 3：实现 `app/quiz_service.py`（grade 部分）**

```python
# app/quiz_service.py
from __future__ import annotations

import json


class QuizError(Exception):
    """生成/解析/校验失败。"""


class NoKnowledge(Exception):
    """检索无命中，无法出题。"""


GRADE_SYSTEM = (
    "你是严格的阅卷老师。对照参考答案给学生的简答打分。"
    "只输出 JSON：{\"score\": 0-100 的整数, \"feedback\": \"一句话点评\"}，不要多余文字。")


def _grade_user(question: dict, user_answer) -> str:
    return (f"题目：{question['stem']}\n参考答案：{question['answer']}\n"
            f"学生作答：{user_answer}\n请打分并点评。")


def _strip_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[: s.rfind("```")]
    return s.strip()


class QuizService:
    def __init__(self, memory, question_store, complete, collection="knowledge",
                 retrieve_k=6, short_pass_score=60) -> None:
        self._memory = memory
        self._store = question_store
        self._complete = complete
        self._collection = collection
        self._retrieve_k = retrieve_k
        self._short_pass_score = short_pass_score

    async def grade(self, question: dict, user_answer) -> dict:
        t = question["type"]
        if t == "single":
            return {"correct": user_answer == question["answer"], "feedback": None}
        if t == "truefalse":
            return {"correct": bool(user_answer) == question["answer"], "feedback": None}
        if t == "multiple":
            correct = sorted(user_answer or []) == sorted(question["answer"])
            return {"correct": correct, "feedback": None}
        if t == "short":
            raw = await self._complete(GRADE_SYSTEM, _grade_user(question, user_answer))
            try:
                verdict = json.loads(_strip_fence(raw))
                score = int(verdict.get("score", 0))
            except (ValueError, TypeError, AttributeError):
                score, verdict = 0, {}
            return {"correct": score >= self._short_pass_score,
                    "score": score, "feedback": verdict.get("feedback")}
        raise QuizError(f"未知题型 {t}")
```

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/app/test_quiz_grade.py -q`
预期：PASS（5 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/quiz_service.py tests/app/test_quiz_grade.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: QuizService.grade 客观精确匹配 + 简答 LLM 判分"
```

---

### 任务 7：QuizService.generate（RAG 出题）

**文件：**
- 修改：`app/quiz_service.py`（加 `generate` + 生成提示 + 解析校验）
- 测试：`tests/app/test_quiz_generate.py`

- [ ] **步骤 1：编写失败的测试**

`tests/app/test_quiz_generate.py`（真 `Memory(:memory:)` + fake embedder + fake completer）：

```python
import json
import pytest
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
from app.questions import QuestionStore
from app.quiz_service import QuizService, QuizError, NoKnowledge


def _memory(mock_embedder):
    return Memory(MemoryStore(":memory:", dimension=64), mock_embedder(dimension=64), 1000, 0)


GEN_JSON = json.dumps([
    {"type": "single", "stem": "光合作用发生在?", "options": ["线粒体", "叶绿体", "核糖体"],
     "answer": 1, "explanation": "叶绿体"},
    {"type": "truefalse", "stem": "光合作用释放氧气", "options": None,
     "answer": True, "explanation": "是"},
], ensure_ascii=False)


async def _completer_returning(text):
    async def c(system, user):
        return text
    return c


@pytest.mark.asyncio
async def test_generate_parses_validates_and_stores(mock_embedder):
    mem = _memory(mock_embedder)
    await mem.add_texts(["光合作用在叶绿体进行，释放氧气"], "knowledge", {"source": "生物"})
    store = QuestionStore(":memory:")
    async def complete(system, user):
        return f"```json\n{GEN_JSON}\n```"       # 带 markdown 围栏
    svc = QuizService(mem, store, complete)
    out = await svc.generate("光合作用", 2, ["single", "truefalse"])
    assert len(out) == 2
    assert all("id" in q for q in out)
    assert {q["type"] for q in out} == {"single", "truefalse"}
    assert len(store.list()) == 2                # 已入库
    assert out[0]["source"] == "光合作用"         # source 记为 topic


@pytest.mark.asyncio
async def test_generate_no_knowledge_raises(mock_embedder):
    mem = _memory(mock_embedder)                 # 空知识库
    svc = QuizService(mem, QuestionStore(":memory:"), lambda s, u: None)
    with pytest.raises(NoKnowledge):
        await svc.generate("任意主题", 2, ["single"])


@pytest.mark.asyncio
async def test_generate_bad_json_raises(mock_embedder):
    mem = _memory(mock_embedder)
    await mem.add_texts(["有内容"], "knowledge", {})
    async def complete(system, user):
        return "这不是 JSON"
    svc = QuizService(mem, QuestionStore(":memory:"), complete)
    with pytest.raises(QuizError):
        await svc.generate("主题", 2, ["single"])


@pytest.mark.asyncio
async def test_generate_filters_invalid_items(mock_embedder):
    mem = _memory(mock_embedder)
    await mem.add_texts(["有内容"], "knowledge", {})
    bad = json.dumps([
        {"type": "single", "stem": "", "options": ["a", "b"], "answer": 0},   # stem 空 → 剔除
        {"type": "single", "stem": "有效吗", "options": ["a", "b"], "answer": 5},  # 索引越界 → 剔除
    ], ensure_ascii=False)
    async def complete(system, user):
        return bad
    svc = QuizService(mem, QuestionStore(":memory:"), complete)
    with pytest.raises(QuizError):               # 全不合法 → QuizError
        await svc.generate("主题", 2, ["single"])
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/app/test_quiz_generate.py -q`
预期：FAIL（`AttributeError: 'QuizService' object has no attribute 'generate'`）

- [ ] **步骤 3：在 `app/quiz_service.py` 补 generate**

在 `GRADE_SYSTEM` 附近加生成提示常量与校验函数，并给 `QuizService` 加 `generate` 方法。

文件顶部常量区加：

```python
GEN_SYSTEM = (
    "你是出题老师。只依据提供的资料出题，覆盖要点，难度适中。"
    "严格只输出一个 JSON 数组，每个元素形如："
    "{\"type\":\"single|multiple|truefalse|short\",\"stem\":\"题干\","
    "\"options\":[\"选项\"]或null,\"answer\":单选为选项索引整数/多选为索引数组/"
    "判断为true或false/简答为参考答案字符串,\"explanation\":\"解析\"}。"
    "不要输出 JSON 以外的任何文字。")


def _gen_user(topic: str, count: int, types: list[str], context: str) -> str:
    return (f"资料：\n{context}\n\n请就主题「{topic}」出 {count} 道题，"
            f"题型限定在 {types} 中。严格输出 JSON 数组。")


def _valid(q: dict, types: list[str]) -> bool:
    if not isinstance(q, dict):
        return False
    t = q.get("type")
    if t not in types or not (q.get("stem") or "").strip():
        return False
    a = q.get("answer")
    opts = q.get("options")
    if t in ("single", "multiple"):
        if not isinstance(opts, list) or len(opts) < 2:
            return False
        if t == "single":
            return isinstance(a, int) and not isinstance(a, bool) and 0 <= a < len(opts)
        return (isinstance(a, list) and len(a) > 0
                and all(isinstance(i, int) and 0 <= i < len(opts) for i in a))
    if t == "truefalse":
        return isinstance(a, bool)
    if t == "short":
        return isinstance(a, str) and bool(a.strip())
    return False


def _parse_questions(raw: str) -> list:
    try:
        data = json.loads(_strip_fence(raw))
    except (ValueError, TypeError):
        raise QuizError("生成结果不是合法 JSON")
    if not isinstance(data, list):
        raise QuizError("生成结果不是 JSON 数组")
    return data
```

`QuizService` 加方法：

```python
    async def generate(self, topic: str, count: int, types: list[str]) -> list[dict]:
        hits = await self._memory.search(topic, self._collection, self._retrieve_k)
        if not hits:
            raise NoKnowledge(topic)
        context = "\n\n".join(h.text for h in hits)
        raw = await self._complete(GEN_SYSTEM, _gen_user(topic, count, types, context))
        valid = [q for q in _parse_questions(raw) if _valid(q, types)]
        if not valid:
            raise QuizError("生成结果无有效题目")
        for q in valid:
            q["source"] = topic
            q["explanation"] = q.get("explanation", "")
            q["id"] = self._store.create(q)
        return valid
```

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/app/test_quiz_generate.py -q`
预期：PASS（4 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/quiz_service.py tests/app/test_quiz_generate.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: QuizService.generate RAG 检索 + 单轮生成 + 校验入库"
```

---

### 任务 8：api/questions.py（题库路由）

**文件：**
- 创建：`app/api/questions.py`
- 测试：并入任务 10 的 `tests/app/test_quiz_api.py`（此任务只建路由，单测在集成任务统一验证）

- [ ] **步骤 1：实现 `app/api/questions.py`**

```python
# app/api/questions.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..quiz_service import NoKnowledge, QuizError


class GenerateBody(BaseModel):
    topic: str
    count: int = 5
    types: list[str] = ["single"]


class IdsBody(BaseModel):
    ids: list[str]


def make_questions_router(quiz_service, question_store, config) -> APIRouter:
    router = APIRouter()

    @router.post("/api/questions/generate")
    async def generate(body: GenerateBody):
        if quiz_service is None:
            raise HTTPException(status_code=503, detail="知识库未启用（未配置 embedding）")
        count = max(1, min(body.count, config.quiz_max_count))
        types = body.types or ["single"]
        try:
            questions = await quiz_service.generate(body.topic, count, types)
        except NoKnowledge:
            raise HTTPException(status_code=422, detail="知识库无相关内容，请先在知识库上传资料")
        except QuizError as e:
            raise HTTPException(status_code=502, detail=f"出题失败：{e}")
        return {"questions": questions}

    @router.get("/api/questions")
    async def list_questions():
        return question_store.list()

    @router.delete("/api/questions/{qid}")
    async def delete_question(qid: str):
        question_store.delete(qid)
        return {"ok": True}

    @router.post("/api/questions/delete")
    async def delete_questions(body: IdsBody):
        question_store.delete_many(body.ids)
        return {"ok": True}

    return router
```

- [ ] **步骤 2：Commit**

```bash
git add app/api/questions.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: /api/questions 出题/列表/删除路由"
```

---

### 任务 9：api/exams.py（考试路由）

**文件：**
- 创建：`app/api/exams.py`
- 测试：并入任务 10 集成测试

- [ ] **步骤 1：实现 `app/api/exams.py`**

```python
# app/api/exams.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class ComposeBody(BaseModel):
    count: int = 5
    types: list[str] | None = None


class Answer(BaseModel):
    question_id: str
    user_answer: object = None


class SubmitBody(BaseModel):
    answers: list[Answer]


class IdsBody(BaseModel):
    ids: list[str]


def _snapshot(q: dict) -> dict:
    return {"type": q["type"], "stem": q["stem"], "options": q["options"],
            "answer": q["answer"], "explanation": q["explanation"]}


def make_exams_router(quiz_service, question_store, exam_store, wrong_store) -> APIRouter:
    router = APIRouter()

    @router.post("/api/exams")
    async def compose(body: ComposeBody):
        picked = question_store.sample(body.count, body.types)
        # 去掉 answer/explanation，防前端偷看
        paper = [{"id": q["id"], "type": q["type"], "stem": q["stem"],
                  "options": q["options"]} for q in picked]
        return {"questions": paper}

    @router.post("/api/exams/submit")
    async def submit(body: SubmitBody):
        if quiz_service is None:
            raise HTTPException(status_code=503, detail="知识库未启用")
        detail, correct = [], 0
        graded = []  # (question, user_answer, result)
        for ans in body.answers:
            q = question_store.get(ans.question_id)
            if q is None:
                detail.append({"question_id": ans.question_id, "missing": True,
                               "correct": False})
                continue
            res = await quiz_service.grade(q, ans.user_answer)
            if res["correct"]:
                correct += 1
            detail.append({
                "question_id": q["id"], "type": q["type"], "stem": q["stem"],
                "user_answer": ans.user_answer, "correct": res["correct"],
                "correct_answer": q["answer"], "explanation": q["explanation"],
                "feedback": res.get("feedback")})
            graded.append((q, ans.user_answer, res))
        total = len(body.answers)
        score = round(correct / total * 100, 1) if total else 0.0
        exam_id = exam_store.create(total, correct, score, detail)
        for q, ua, res in graded:
            if not res["correct"]:
                wrong_store.create(q["id"], exam_id, _snapshot(q), ua)
        return {"exam_id": exam_id, "total": total, "correct": correct,
                "score": score, "detail": detail}

    @router.get("/api/exams")
    async def list_exams():
        return exam_store.list()

    @router.get("/api/wrong-answers")
    async def list_wrong():
        return wrong_store.list()

    @router.post("/api/wrong-answers/delete")
    async def delete_wrong(body: IdsBody):
        wrong_store.delete_many(body.ids)
        return {"ok": True}

    @router.delete("/api/wrong-answers/{wid}")
    async def delete_one_wrong(wid: str):
        wrong_store.delete(wid)
        return {"ok": True}

    return router
```

- [ ] **步骤 2：Commit**

```bash
git add app/api/exams.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: /api/exams 组卷/交卷判分/成绩/错题路由"
```

---

### 任务 10：main.py 装配 + 集成测试

**文件：**
- 修改：`app/main.py`
- 测试：`tests/app/test_quiz_api.py`

- [ ] **步骤 1：编写失败的集成测试**

`tests/app/test_quiz_api.py`：

```python
import sqlite3
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import AppConfig
from app.assembly import Harness
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.questions import QuestionStore
from app.exams import ExamStore
from app.wrong_answers import WrongAnswerStore
from app.quiz_service import QuizService
from harness.tools.base import ToolRegistry
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    original = sqlite3.connect

    def _patched(*args, **kwargs):
        kwargs.setdefault("check_same_thread", False)
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _patched)


GEN_JSON = ('[{"type":"single","stem":"光合作用在哪?","options":["线粒体","叶绿体"],'
            '"answer":1,"explanation":"叶绿体"},'
            '{"type":"short","stem":"简述光合作用","options":null,'
            '"answer":"光能转化为化学能","explanation":"要点"}]')


def _app(make_mock, mock_embedder, with_memory=True, complete=None):
    mstore = MemoryStore(":memory:", dimension=64)
    mem = Memory(mstore, mock_embedder(dimension=64), 1000, 0) if with_memory else None
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock([]), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="s", memory=mem,
                      memory_store=mstore if with_memory else None)
    qs = QuestionStore(":memory:")
    quiz = None
    if mem is not None:
        quiz = QuizService(mem, qs, complete or (lambda s, u: None))
    app = create_app(config=AppConfig(api_key="k"), harness=harness,
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=qs, exam_store=ExamStore(":memory:"),
                     wrong_store=WrongAnswerStore(":memory:"), quiz_service=quiz)
    return TestClient(app), qs


@pytest.mark.asyncio
async def _seed_knowledge(mem):
    await mem.add_texts(["光合作用在叶绿体进行"], "knowledge", {})


def test_generate_503_without_memory(make_mock, mock_embedder):
    client, _ = _app(make_mock, mock_embedder, with_memory=False)
    r = client.post("/api/questions/generate", json={"topic": "x", "count": 1})
    assert r.status_code == 503


def test_generate_422_without_knowledge(make_mock, mock_embedder):
    async def complete(s, u): return GEN_JSON
    client, _ = _app(make_mock, mock_embedder, complete=complete)
    r = client.post("/api/questions/generate", json={"topic": "空", "count": 1, "types": ["single"]})
    assert r.status_code == 422


def test_generate_list_delete_flow(make_mock, mock_embedder):
    async def complete(s, u): return GEN_JSON
    client, qs = _app(make_mock, mock_embedder, complete=complete)
    # 先塞知识（直接用 store 背后的 memory 不方便，这里改走上传接口的等价：直接 add）
    import anyio
    from harness.memory.memory import Memory  # 已在 harness 内
    # 直接对 QuizService 的 memory 播种：通过题库为空 + 生成需要知识，用 knowledge 接口
    # 简化：用 documents 上传不可行（需 embedding），改由测试直接调用 memory
    # —— 见下方 conftest 注入的 mem 播种 helper
    raise NotImplementedError  # 占位：本测试在步骤3被下方正式版本替换
```

> ⚠️ 实现者注意：上面 `test_generate_list_delete_flow` 是**草稿占位**，请用步骤 3 给出的正式版本替换整段测试文件里的该函数。草稿保留是为了说明「知识播种」的坑：`_app` 内部持有 `mem`，测试需要拿到它来 `add_texts`。步骤 3 的正式版把 `mem` 从 `_app` 返回出来解决。

- [ ] **步骤 2：改用可播种知识的正式测试**

用以下**完整文件**覆盖 `tests/app/test_quiz_api.py`（`_app` 额外返回 `mem`）：

```python
import sqlite3
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import AppConfig
from app.assembly import Harness
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.questions import QuestionStore
from app.exams import ExamStore
from app.wrong_answers import WrongAnswerStore
from app.quiz_service import QuizService
from harness.tools.base import ToolRegistry
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    original = sqlite3.connect

    def _patched(*args, **kwargs):
        kwargs.setdefault("check_same_thread", False)
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _patched)


GEN_JSON = ('[{"type":"single","stem":"光合作用在哪?","options":["线粒体","叶绿体"],'
            '"answer":1,"explanation":"叶绿体"},'
            '{"type":"short","stem":"简述光合作用","options":null,'
            '"answer":"光能转化为化学能","explanation":"要点"}]')


def _app(make_mock, mock_embedder, with_memory=True, complete=None):
    mstore = MemoryStore(":memory:", dimension=64)
    mem = Memory(mstore, mock_embedder(dimension=64), 1000, 0) if with_memory else None
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock([]), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="s", memory=mem,
                      memory_store=mstore if with_memory else None)
    qs = QuestionStore(":memory:")
    quiz = QuizService(mem, qs, complete or (lambda s, u: None)) if mem is not None else None
    app = create_app(config=AppConfig(api_key="k"), harness=harness,
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=qs, exam_store=ExamStore(":memory:"),
                     wrong_store=WrongAnswerStore(":memory:"), quiz_service=quiz)
    return TestClient(app), qs, mem


def test_generate_503_without_memory(make_mock, mock_embedder):
    client, _, _ = _app(make_mock, mock_embedder, with_memory=False)
    assert client.post("/api/questions/generate",
                       json={"topic": "x", "count": 1}).status_code == 503


def test_generate_422_without_knowledge(make_mock, mock_embedder):
    async def complete(s, u): return GEN_JSON
    client, _, _ = _app(make_mock, mock_embedder, complete=complete)
    assert client.post("/api/questions/generate",
                       json={"topic": "空", "count": 1, "types": ["single"]}).status_code == 422


@pytest.mark.asyncio
async def test_full_quiz_flow(make_mock, mock_embedder):
    async def complete(s, u):
        # generate 用 GEN_JSON；grade(short) 用打分 JSON。按提示内容区分。
        return GEN_JSON if "出" in u or "题型" in u else '{"score": 90, "feedback": "好"}'
    client, qs, mem = _app(make_mock, mock_embedder, complete=complete)
    await mem.add_texts(["光合作用在叶绿体进行，把光能转化为化学能"], "knowledge", {})

    # 出题
    r = client.post("/api/questions/generate",
                    json={"topic": "光合作用", "count": 2, "types": ["single", "short"]})
    assert r.status_code == 200
    qlist = client.get("/api/questions").json()
    assert len(qlist) == 2

    # 组卷不含答案
    paper = client.post("/api/exams", json={"count": 2}).json()["questions"]
    assert paper and all("answer" not in q and "explanation" not in q for q in paper)

    # 交卷：单选答对(1)、简答走 LLM 判 90 分→对
    single = next(q for q in qlist if q["type"] == "single")
    short = next(q for q in qlist if q["type"] == "short")
    submit = client.post("/api/exams/submit", json={"answers": [
        {"question_id": single["id"], "user_answer": 1},
        {"question_id": short["id"], "user_answer": "光能变化学能"}]}).json()
    assert submit["total"] == 2 and submit["correct"] == 2
    assert client.get("/api/exams").json()[0]["id"] == submit["exam_id"]


@pytest.mark.asyncio
async def test_wrong_answer_survives_question_delete(make_mock, mock_embedder):
    async def complete(s, u):
        return GEN_JSON if "题型" in u or "出" in u else '{"score": 10, "feedback": "错"}'
    client, qs, mem = _app(make_mock, mock_embedder, complete=complete)
    await mem.add_texts(["光合作用内容"], "knowledge", {})
    client.post("/api/questions/generate",
                json={"topic": "光合作用", "count": 2, "types": ["single", "short"]})
    qlist = client.get("/api/questions").json()
    single = next(q for q in qlist if q["type"] == "single")
    # 单选故意答错 → 进错题集
    client.post("/api/exams/submit",
                json={"answers": [{"question_id": single["id"], "user_answer": 0}]})
    wrong = client.get("/api/wrong-answers").json()
    assert len(wrong) == 1 and wrong[0]["snapshot"]["stem"]
    # 删原题后，错题快照仍在
    client.delete(f"/api/questions/{single['id']}")
    assert client.get("/api/questions").json() == []
    still = client.get("/api/wrong-answers").json()
    assert len(still) == 1 and still[0]["snapshot"]["stem"]
    # 批量删错题
    client.post("/api/wrong-answers/delete", json={"ids": [w["id"] for w in still]})
    assert client.get("/api/wrong-answers").json() == []
```

> 注：`complete` 用提示里是否含「题型」区分 generate 与 grade 调用（generate 的 `_gen_user` 含「题型」，grade 的 `_grade_user` 不含）。若实现者调整了提示词用语，请同步这里的判别关键字。

- [ ] **步骤 3：运行验证失败**

运行：`uv run pytest tests/app/test_quiz_api.py -q`
预期：FAIL（`create_app() got an unexpected keyword argument 'question_store'`）

- [ ] **步骤 4：改 `app/main.py`**

在 imports 增：

```python
from .api.questions import make_questions_router
from .api.exams import make_exams_router
from .completion import build_completer
from .questions import QuestionStore
from .exams import ExamStore
from .wrong_answers import WrongAnswerStore
from .quiz_service import QuizService
```

`create_app` 签名与体改为：

```python
def create_app(config=None, harness=None, store=None, doc_store=None,
               question_store=None, exam_store=None, wrong_store=None,
               quiz_service=None) -> FastAPI:
    config = config or AppConfig()
    harness = harness if harness is not None else build_harness(config)
    store = store if store is not None else ConversationStore(config.conversations_db_path)
    doc_store = doc_store if doc_store is not None else DocumentStore(config.documents_db_path)
    question_store = question_store if question_store is not None else QuestionStore(config.questions_db_path)
    exam_store = exam_store if exam_store is not None else ExamStore(config.exams_db_path)
    wrong_store = wrong_store if wrong_store is not None else WrongAnswerStore(config.wrong_answers_db_path)

    has_mem = (getattr(harness, "memory", None) is not None
               and getattr(harness, "memory_store", None) is not None)
    service = KnowledgeService(harness.memory, harness.memory_store, doc_store) if has_mem else None
    if quiz_service is None and has_mem:
        completer = build_completer(harness.client, config.model)
        quiz_service = QuizService(harness.memory, question_store, completer,
                                   retrieve_k=config.quiz_retrieve_k,
                                   short_pass_score=config.short_pass_score)

    app = FastAPI(title="AI 学习助手")
    app.add_middleware(
        CORSMiddleware, allow_origins=config.cors_origins,
        allow_methods=["*"], allow_headers=["*"])
    app.include_router(make_conversations_router(store))
    app.include_router(make_chat_router(harness, store, config))
    app.include_router(make_documents_router(service, doc_store, config))
    app.include_router(make_questions_router(quiz_service, question_store, config))
    app.include_router(make_exams_router(quiz_service, question_store, exam_store, wrong_store))

    if os.path.isdir("web/dist"):
        app.mount("/", StaticFiles(directory="web/dist", html=True), name="static")
    return app
```

- [ ] **步骤 5：运行验证通过**

运行：`uv run pytest tests/app/test_quiz_api.py -q`
预期：PASS（4 passed）

- [ ] **步骤 6：全量后端回归**

运行：`uv run pytest -q`
预期：全绿（App-1/App-2 + harness 不回归）

- [ ] **步骤 7：Commit**

```bash
git add app/main.py tests/app/test_quiz_api.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: main 装配 QuizService + 题库/考试路由 + 集成测试"
```

---

### 任务 11：前端 API 客户端

**文件：**
- 修改：`web/src/api/client.ts`

- [ ] **步骤 1：在 `client.ts` 的 `api` 对象里增三组方法**

先看现有 `client.ts` 里 `api` 对象结构（App-2 已有 `documents`）。在其中追加（保持同样的 `fetch().then(r=>r.json())` 风格）：

```ts
  questions: {
    generate: (topic: string, count: number, types: string[]) =>
      fetch("/api/questions/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic, count, types }),
      }).then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "出题失败");
        return r.json();
      }),
    list: () => fetch("/api/questions").then((r) => r.json()),
    remove: (id: string) =>
      fetch(`/api/questions/${id}`, { method: "DELETE" }).then(() => undefined),
    removeMany: (ids: string[]) =>
      fetch("/api/questions/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      }).then(() => undefined),
  },
  exams: {
    compose: (count: number, types: string[] | null) =>
      fetch("/api/exams", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count, types }),
      }).then((r) => r.json()),
    submit: (answers: { question_id: string; user_answer: unknown }[]) =>
      fetch("/api/exams/submit", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers }),
      }).then((r) => r.json()),
    history: () => fetch("/api/exams").then((r) => r.json()),
  },
  wrong: {
    list: () => fetch("/api/wrong-answers").then((r) => r.json()),
    removeMany: (ids: string[]) =>
      fetch("/api/wrong-answers/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      }).then(() => undefined),
  },
```

- [ ] **步骤 2：类型检查**

运行：`cd web && npx tsc --noEmit`（回到根目录再继续：`cd ..`）
预期：无输出（无类型错误）

- [ ] **步骤 3：Commit**

```bash
git add web/src/api/client.ts
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: 前端 questions/exams/wrong API 客户端"
```

---

### 任务 12：前端题库页 + 导航

**文件：**
- 创建：`web/src/pages/QuestionBankView.tsx`
- 修改：`web/src/App.tsx`（导航加三项 + 路由）
- 测试：`web/src/pages/QuestionBankView.test.tsx`

- [ ] **步骤 1：编写失败的 Vitest**

`web/src/pages/QuestionBankView.test.tsx`：

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import QuestionBankView from "./QuestionBankView";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    questions: {
      list: vi.fn(),
      generate: vi.fn(),
      remove: vi.fn(),
      removeMany: vi.fn(),
    },
  },
}));

describe("QuestionBankView", () => {
  beforeEach(() => vi.clearAllMocks());

  it("渲染已有题目列表", async () => {
    (api.questions.list as any).mockResolvedValue([
      { id: "1", type: "single", stem: "光合作用在哪?", source: "生物" },
    ]);
    render(<MemoryRouter><QuestionBankView /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/光合作用在哪/)).toBeInTheDocument());
  });
});
```

- [ ] **步骤 2：运行验证失败**

运行：`cd web && npx vitest run src/pages/QuestionBankView.test.tsx; cd ..`
预期：FAIL（找不到 `./QuestionBankView`）

- [ ] **步骤 3：实现 `web/src/pages/QuestionBankView.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api } from "../api/client";

const TYPES: { key: string; label: string }[] = [
  { key: "single", label: "单选" },
  { key: "multiple", label: "多选" },
  { key: "truefalse", label: "判断" },
  { key: "short", label: "简答" },
];

interface Question {
  id: string;
  type: string;
  stem: string;
  source: string;
}

export default function QuestionBankView() {
  const [questions, setQuestions] = useState<Question[]>([]);
  const [topic, setTopic] = useState("");
  const [count, setCount] = useState(5);
  const [types, setTypes] = useState<string[]>(["single"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = () => api.questions.list().then(setQuestions);
  useEffect(() => { refresh(); }, []);

  const toggleType = (k: string) =>
    setTypes((ts) => (ts.includes(k) ? ts.filter((t) => t !== k) : [...ts, k]));

  const generate = async () => {
    if (!topic.trim() || types.length === 0) return;
    setBusy(true); setError("");
    try {
      await api.questions.generate(topic.trim(), count, types);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "出题失败");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    await api.questions.remove(id);
    await refresh();
  };

  return (
    <div className="p-6 space-y-4">
      <h2 className="text-xl font-bold">题库</h2>
      <div className="space-y-2 border rounded p-4">
        <input
          className="border rounded px-2 py-1 w-full" placeholder="出题主题（从知识库检索）"
          value={topic} onChange={(e) => setTopic(e.target.value)} />
        <div className="flex items-center gap-3 flex-wrap">
          <label>题数
            <input type="number" min={1} max={20} value={count}
              onChange={(e) => setCount(Number(e.target.value))}
              className="border rounded px-2 py-1 w-16 ml-1" />
          </label>
          {TYPES.map((t) => (
            <label key={t.key} className="flex items-center gap-1">
              <input type="checkbox" checked={types.includes(t.key)}
                onChange={() => toggleType(t.key)} />{t.label}
            </label>
          ))}
          <button onClick={generate} disabled={busy || !topic.trim() || types.length === 0}
            className="bg-blue-600 text-white px-3 py-1 rounded disabled:opacity-50">
            {busy ? "出题中…" : "出题"}
          </button>
        </div>
        {error && <p className="text-red-600 text-sm">{error}</p>}
      </div>

      {questions.length === 0 ? (
        <p className="text-gray-500">暂无题目，先出题吧。</p>
      ) : (
        <ul className="space-y-2">
          {questions.map((q) => (
            <li key={q.id} className="border rounded p-3 flex justify-between items-start">
              <div>
                <span className="text-xs bg-gray-200 rounded px-1 mr-2">{q.type}</span>
                {q.stem}
                {q.source && <span className="text-xs text-gray-400 ml-2">· {q.source}</span>}
              </div>
              <button onClick={() => remove(q.id)}
                className="text-red-600 text-sm ml-3">删除</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **步骤 4：改 `web/src/App.tsx` 导航 + 路由**

在导航数组加三项、`<Routes>` 加三条路由。定位现有 `nav`（App-2 的 `[["/","聊天"],["/knowledge","知识库"]]`）与 `<Routes>`，改为：

```tsx
// 导航项
[["/", "聊天"], ["/knowledge", "知识库"], ["/questions", "题库"],
 ["/exam", "考试"], ["/wrong", "错题集"]]
```

`<Routes>` 内新增（并在文件顶部 import 三个页面）：

```tsx
import QuestionBankView from "./pages/QuestionBankView";
import ExamView from "./pages/ExamView";
import WrongAnswersView from "./pages/WrongAnswersView";
// ...
<Route path="/questions" element={<QuestionBankView />} />
<Route path="/exam" element={<ExamView />} />
<Route path="/wrong" element={<WrongAnswersView />} />
```

> ExamView / WrongAnswersView 在任务 13 创建。为让 App.tsx 先编译通过，本任务可先建两个**最小占位组件**（`export default function ExamView(){return <div/>}`），任务 13 再填实现——或把任务 13 与本任务合并提交。推荐：本任务只加 QuestionBankView 路由 + 占位另两个，任务 13 替换占位。

- [ ] **步骤 5：运行验证通过**

运行：`cd web && npx vitest run src/pages/QuestionBankView.test.tsx && npx tsc --noEmit; cd ..`
预期：Vitest PASS + tsc 无输出

- [ ] **步骤 6：Commit**

```bash
git add web/src/pages/QuestionBankView.tsx web/src/pages/QuestionBankView.test.tsx web/src/App.tsx web/src/pages/ExamView.tsx web/src/pages/WrongAnswersView.tsx
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: 前端题库页 + 三页导航路由"
```

---

### 任务 13：前端考试页 + 错题集页

**文件：**
- 创建/替换：`web/src/pages/ExamView.tsx`、`web/src/pages/WrongAnswersView.tsx`

- [ ] **步骤 1：实现 `web/src/pages/ExamView.tsx`**

```tsx
import { useState } from "react";
import { api } from "../api/client";

interface PaperQ { id: string; type: string; stem: string; options: string[] | null; }
interface DetailItem {
  question_id: string; type?: string; stem?: string; correct: boolean;
  correct_answer?: unknown; explanation?: string; feedback?: string | null;
}
interface Result { total: number; correct: number; score: number; detail: DetailItem[]; }

export default function ExamView() {
  const [count, setCount] = useState(5);
  const [paper, setPaper] = useState<PaperQ[]>([]);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true); setResult(null); setAnswers({});
    const data = await api.exams.compose(count, null);
    setPaper(data.questions);
    setBusy(false);
  };

  const setAns = (id: string, v: unknown) => setAnswers((a) => ({ ...a, [id]: v }));

  const toggleMulti = (id: string, idx: number) =>
    setAnswers((a) => {
      const cur = (a[id] as number[] | undefined) || [];
      return { ...a, [id]: cur.includes(idx) ? cur.filter((i) => i !== idx) : [...cur, idx] };
    });

  const submit = async () => {
    setBusy(true);
    const payload = paper.map((q) => ({ question_id: q.id, user_answer: answers[q.id] ?? null }));
    setResult(await api.exams.submit(payload));
    setBusy(false);
  };

  if (result) {
    return (
      <div className="p-6 space-y-3">
        <h2 className="text-xl font-bold">成绩：{result.correct}/{result.total}（{result.score} 分）</h2>
        <ul className="space-y-2">
          {result.detail.map((d, i) => (
            <li key={i} className={`border rounded p-3 ${d.correct ? "border-green-400" : "border-red-400"}`}>
              <div>{d.correct ? "✅" : "❌"} {d.stem}</div>
              {!d.correct && <div className="text-sm text-gray-600">正确答案：{JSON.stringify(d.correct_answer)}</div>}
              {d.explanation && <div className="text-sm text-gray-500">解析：{d.explanation}</div>}
              {d.feedback && <div className="text-sm text-blue-600">点评：{d.feedback}</div>}
            </li>
          ))}
        </ul>
        <button onClick={() => { setPaper([]); setResult(null); }}
          className="bg-blue-600 text-white px-3 py-1 rounded">再考一次</button>
      </div>
    );
  }

  if (paper.length === 0) {
    return (
      <div className="p-6 space-y-3">
        <h2 className="text-xl font-bold">模拟考试</h2>
        <label>题数
          <input type="number" min={1} max={20} value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            className="border rounded px-2 py-1 w-16 ml-1" />
        </label>
        <button onClick={start} disabled={busy}
          className="bg-blue-600 text-white px-3 py-1 rounded ml-3 disabled:opacity-50">
          {busy ? "组卷中…" : "开始考试"}
        </button>
        <p className="text-gray-500 text-sm">若提示无题，请先到题库出题。</p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4">
      <h2 className="text-xl font-bold">答题（{paper.length} 题）</h2>
      {paper.map((q, qi) => (
        <div key={q.id} className="border rounded p-3 space-y-1">
          <div>{qi + 1}. {q.stem}</div>
          {q.type === "single" && q.options?.map((o, i) => (
            <label key={i} className="block">
              <input type="radio" name={q.id} onChange={() => setAns(q.id, i)} /> {o}
            </label>
          ))}
          {q.type === "multiple" && q.options?.map((o, i) => (
            <label key={i} className="block">
              <input type="checkbox" onChange={() => toggleMulti(q.id, i)} /> {o}
            </label>
          ))}
          {q.type === "truefalse" && (
            <div>
              <label className="mr-3"><input type="radio" name={q.id} onChange={() => setAns(q.id, true)} /> 对</label>
              <label><input type="radio" name={q.id} onChange={() => setAns(q.id, false)} /> 错</label>
            </div>
          )}
          {q.type === "short" && (
            <textarea className="border rounded w-full p-1"
              onChange={(e) => setAns(q.id, e.target.value)} />
          )}
        </div>
      ))}
      <button onClick={submit} disabled={busy}
        className="bg-green-600 text-white px-4 py-1 rounded disabled:opacity-50">
        {busy ? "判分中…" : "交卷"}
      </button>
    </div>
  );
}
```

- [ ] **步骤 2：实现 `web/src/pages/WrongAnswersView.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Wrong {
  id: string;
  user_answer: unknown;
  snapshot: { type: string; stem: string; answer: unknown; explanation: string };
}

export default function WrongAnswersView() {
  const [items, setItems] = useState<Wrong[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const refresh = () => api.wrong.list().then(setItems);
  useEffect(() => { refresh(); }, []);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const removeSelected = async () => {
    if (selected.size === 0) return;
    await api.wrong.removeMany([...selected]);
    setSelected(new Set());
    await refresh();
  };

  return (
    <div className="p-6 space-y-3">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-bold">错题集</h2>
        <button onClick={removeSelected} disabled={selected.size === 0}
          className="bg-red-600 text-white px-3 py-1 rounded disabled:opacity-50">
          批量删除（{selected.size}）
        </button>
      </div>
      {items.length === 0 ? (
        <p className="text-gray-500">暂无错题。</p>
      ) : (
        <ul className="space-y-2">
          {items.map((w) => (
            <li key={w.id} className="border rounded p-3 flex gap-2">
              <input type="checkbox" checked={selected.has(w.id)} onChange={() => toggle(w.id)} />
              <div>
                <div><span className="text-xs bg-gray-200 rounded px-1 mr-2">{w.snapshot.type}</span>{w.snapshot.stem}</div>
                <div className="text-sm text-gray-600">你的作答：{JSON.stringify(w.user_answer)}</div>
                <div className="text-sm text-gray-500">正确答案：{JSON.stringify(w.snapshot.answer)}</div>
                {w.snapshot.explanation && <div className="text-sm text-gray-400">解析：{w.snapshot.explanation}</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **步骤 3：类型检查 + 前端测试全跑**

运行：`cd web && npx tsc --noEmit && npx vitest run; cd ..`
预期：tsc 无输出 + 所有 Vitest 通过

- [ ] **步骤 4：Commit**

```bash
git add web/src/pages/ExamView.tsx web/src/pages/WrongAnswersView.tsx
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: 前端考试页（答题/判分/成绩）+ 错题集页（批量删）"
```

---

### 任务 14：README 更新 + 最终回归

**文件：**
- 修改：`app/README.md`

- [ ] **步骤 1：在 README 增 App-3 说明段**

在 App-2 说明之后追加「题库/考试/错题集」小节：出题依赖知识库（先在知识库上传资料），四题型、判分方式（客观精确匹配、简答 LLM）、手测步骤（出题→组卷→交卷看成绩→错题集）、相关 DB 文件（questions.db/exams.db/wrong_answers.db）。

- [ ] **步骤 2：最终回归**

运行：`uv run pytest -q`
预期：后端全绿（约 220+ passed）

运行：`cd web && npx vitest run && npx tsc --noEmit; cd ..`
预期：前端全绿 + tsc 无输出

运行：`git diff --stat origin/main -- src/harness/`
预期：**空**（harness 零改动）

- [ ] **步骤 3：Commit**

```bash
git add app/README.md
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "docs: App-3 题库/考试运行说明"
```

---

## 自检结论

**规格覆盖度：**
- 出题（generate RAG+单轮+校验）→ 任务 7；题库 CRUD → 任务 2/8；组卷去答案 → 任务 9（`compose`）；交卷判分+成绩+错题入集 → 任务 9/10；简答 LLM 判分 → 任务 6；错题快照解耦 → 任务 4 + 任务 10（`test_wrong_answer_survives_question_delete`）；503/422/502 → 任务 8/10；前端三页 → 任务 12/13；配置 → 任务 1；main 装配 → 任务 10；harness 零改动 → 全程不碰 `src/harness/`，任务 14 校验。验收 1-10 全覆盖。

**占位符扫描：** 任务 10 步骤 1 的草稿测试已在步骤 2 用完整正式版覆盖并显式标注；任务 12 的 ExamView/WrongAnswersView 占位在任务 13 替换。无「TODO/待定」。

**类型一致性：** `QuestionStore`（create/get/list/sample/delete/delete_many）、`ExamStore`（create/list）、`WrongAnswerStore`（create/list/delete/delete_many）、`QuizService`（generate/grade，注入 memory/question_store/complete）、`build_completer(client, model_name)`、`make_questions_router(quiz_service, question_store, config)`、`make_exams_router(quiz_service, question_store, exam_store, wrong_store)`、`create_app(...question_store, exam_store, wrong_store, quiz_service)` 在各任务间签名一致。`answer` 编码（single=int/multiple=list[int]/truefalse=bool/short=str）在 store/grade/generate 校验/前端渲染处一致。

## 执行交接

计划已完成。将用 **subagent-driven-development** 逐任务执行（每任务实现子代理 → 规格审查 → 代码质量审查），全部通过后推送。
