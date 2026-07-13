# 题库页面优化 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把题库页做成可用的题目管理界面——移除页内出题入口，列表显示答案且可截断、可按题名/题型/来源查询、分页、批量删除、详情抽屉、上传文件智能解析导入，并在所有新增路径按「题型+题干」去重。

**架构：** 后端 `QuestionStore` 加去重/筛选分页/来源；新增 `QuestionImporter`（LLM 从上传文本抽题，不依赖 embedding）；`questions` 路由删 generate、加 import/sources、改分页 list。前端重写 `QuestionBankView` 并新增 `QuestionDetailDrawer`，复用知识库/错题集的上传·分页·截断·勾选批删模式。

**技术栈：** FastAPI + 原生 sqlite3（无 ORM）；React + MUI + Vite；pytest / vitest（TDD）。

规格：`docs/superpowers/specs/2026-07-13-question-bank-optimization-design.md`

---

## 文件结构

- `app/questions.py`（改）：`QuestionStore` 加 `create_deduped`、`list` 筛选分页、`count`、`sources`。
- `app/question_import.py`（新）：`QuestionImporter(complete, question_store)` — 文本→LLM 抽题→去重入库。
- `app/api/questions.py`（改）：删 `generate`/`GenerateBody`；`GET /api/questions` 改分页筛选；加 `POST /api/questions/import`、`GET /api/questions/sources`；`make_questions_router` 去 `quiz_service`、加 `question_importer`。
- `app/main.py`（改）：构造 `QuestionImporter`，更新 `make_questions_router` 调用。
- `app/tools/exam_tools.py`（改）：`AddQuestionsTool` 改用 `create_deduped`。
- `web/src/api/client.ts`（改）：`api.questions` 改造 + `Question` 类型。
- `web/src/pages/QuestionDetailDrawer.tsx`（新）：题目详情抽屉。
- `web/src/pages/QuestionBankView.tsx`（改）：整体重写。
- 测试：`tests/app/test_questions.py`、`tests/app/test_question_import.py`（新）、`tests/app/test_quiz_api.py`、`tests/app/test_exam_tools.py`、`web/src/pages/QuestionBankView.test.tsx`。

验证命令：后端 `uv run pytest tests/app -q`；前端 `cd web && npx tsc -b && npx vitest run`（先 tsc 再 vitest，避免测到过期 .js）。

---

## 任务 1：QuestionStore.create_deduped（按题型+题干去重）

**文件：**
- 修改：`app/questions.py`
- 测试：`tests/app/test_questions.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/app/test_questions.py` 末尾追加：

```python
def test_create_deduped_skips_same_type_and_stem():
    s = QuestionStore(":memory:")
    a = s.create_deduped("u1", _q(stem="1+1=?"))
    dup = s.create_deduped("u1", _q(stem="  1+1=?  "))   # 首尾空白等价
    assert a is not None and dup is None
    assert len(s.list("u1")) == 1


def test_create_deduped_allows_diff_type_or_user():
    s = QuestionStore(":memory:")
    s.create_deduped("u1", _q(type="single", stem="同题干"))
    diff_type = s.create_deduped("u1", _q(type="short", options=None,
                                          answer="x", stem="同题干"))
    other_user = s.create_deduped("u2", _q(type="single", stem="同题干"))
    assert diff_type is not None and other_user is not None
    assert len(s.list("u1")) == 2 and len(s.list("u2")) == 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/app/test_questions.py::test_create_deduped_skips_same_type_and_stem -q`
预期：FAIL，`AttributeError: 'QuestionStore' object has no attribute 'create_deduped'`

- [ ] **步骤 3：编写最少实现代码**

在 `app/questions.py` 的 `create` 方法后添加：

```python
    def create_deduped(self, user_id: str, q: dict) -> str | None:
        """按 (type, TRIM(stem)) 判重：已存在同题型同题干则返回 None 不插入，否则新建返回 id。"""
        row = self._db.execute(
            "SELECT id FROM questions WHERE user_id=? AND type=? AND TRIM(stem)=TRIM(?)",
            (user_id, q["type"], q["stem"])).fetchone()
        if row is not None:
            return None
        return self.create(user_id, q)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/app/test_questions.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add app/questions.py tests/app/test_questions.py
git commit -m "feat: QuestionStore.create_deduped 按题型+题干去重"
```

---

## 任务 2：QuestionStore 列表筛选/分页 + count + sources

**文件：**
- 修改：`app/questions.py`
- 测试：`tests/app/test_questions.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/app/test_questions.py` 末尾追加：

```python
def test_list_filter_and_pagination():
    s = QuestionStore(":memory:")
    for i in range(3):
        s.create("u1", _q(type="single", stem=f"单选{i}", options=["1", "2"], answer=1))
    s.create("u1", _q(type="truefalse", stem="判断题", options=None, answer=True))
    s.create("u1", _q(type="single", stem="含关键词KW", options=["1", "2"], answer=0))
    assert s.count("u1") == 5
    assert s.count("u1", type="single") == 4
    assert {q["stem"] for q in s.list("u1", type="truefalse")} == {"判断题"}
    assert s.count("u1", q="KW") == 1 and s.list("u1", q="KW")[0]["stem"] == "含关键词KW"
    page1 = s.list("u1", limit=2, offset=0)
    page2 = s.list("u1", limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert {x["id"] for x in page1}.isdisjoint({x["id"] for x in page2})   # 不重叠


def test_sources_distinct_nonempty_isolated():
    s = QuestionStore(":memory:")
    s.create("u1", _q(stem="a")); s.create("u1", _q(stem="b"))          # source="算术"
    s.create("u1", {"type": "single", "stem": "c", "options": ["1", "2"],
                    "answer": 0, "source": ""})                          # 空 source 排除
    s.create("u2", {"type": "single", "stem": "d", "options": ["1", "2"],
                    "answer": 0, "source": "他人"})                      # 他用户不出现
    assert s.sources("u1") == ["算术"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/app/test_questions.py::test_sources_distinct_nonempty_isolated -q`
预期：FAIL，`AttributeError: ... 'sources'`（及 list 不接受 type/limit 关键字）

- [ ] **步骤 3：编写最少实现代码**

在 `app/questions.py` 中，用下面的实现**替换**现有 `list` 方法，并在其后新增 `count`/`sources`：

```python
    def _filter(self, user_id: str, type, source, q):
        clauses = ["user_id=?"]
        params: list = [user_id]
        if type:
            clauses.append("type=?"); params.append(type)
        if source:
            clauses.append("source=?"); params.append(source)
        if q:
            clauses.append("stem LIKE ?"); params.append(f"%{q}%")
        return " AND ".join(clauses), params

    def list(self, user_id: str, *, type: str | None = None, source: str | None = None,
             q: str | None = None, limit: int | None = None, offset: int = 0) -> list[dict]:
        where, params = self._filter(user_id, type, source, q)
        sql = f"SELECT {self._COLS} FROM questions WHERE {where} ORDER BY created_at DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"; params += [limit, offset]
        return [self._row(r) for r in self._db.execute(sql, params).fetchall()]

    def count(self, user_id: str, *, type: str | None = None, source: str | None = None,
              q: str | None = None) -> int:
        where, params = self._filter(user_id, type, source, q)
        return self._db.execute(
            f"SELECT COUNT(*) FROM questions WHERE {where}", params).fetchone()[0]

    def sources(self, user_id: str) -> list[str]:
        rows = self._db.execute(
            "SELECT DISTINCT source FROM questions WHERE user_id=? AND source<>'' "
            "ORDER BY source", (user_id,)).fetchall()
        return [r[0] for r in rows]
```

说明：`user_id` 仍是位置参数，聊天工具 `ListQuestionsTool` 的 `list(self._uid)` 调用不受影响。排序由原 `created_at` 改为 `created_at DESC`（与知识库/错题集一致）。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/app/test_questions.py -q`
预期：PASS（含既有 `test_list_and_delete`）

- [ ] **步骤 5：Commit**

```bash
git add app/questions.py tests/app/test_questions.py
git commit -m "feat: QuestionStore 列表筛选/分页 + count + sources"
```

---

## 任务 3：QuestionImporter（上传文本 LLM 智能解析入库）

**文件：**
- 创建：`app/question_import.py`
- 测试：`tests/app/test_question_import.py`

- [ ] **步骤 1：编写失败的测试**

创建 `tests/app/test_question_import.py`：

```python
import pytest

from app.question_import import QuestionImporter
from app.questions import QuestionStore

GOOD = ('[{"type":"single","stem":"1+1=?","options":["1","2"],"answer":1,'
        '"explanation":"二"},'
        '{"type":"single","stem":"1+1=?","options":["1","2"],"answer":1},'   # 批内重复
        '{"type":"single","stem":"越界","options":["1","2"],"answer":9},'    # 非法
        '{"type":"truefalse","stem":"天是蓝的","options":null,"answer":true}]')


def _importer(raw):
    async def complete(system, user):
        return raw
    return QuestionImporter(complete, QuestionStore(":memory:"))


@pytest.mark.asyncio
async def test_import_extracts_dedups_and_counts():
    imp = _importer(GOOD)
    res = await imp.import_text("u1", "题库.txt", "任意文本")
    assert res == {"imported": 2, "skipped_invalid": 1, "skipped_duplicate": 1}
    saved = imp._store.list("u1")
    assert {q["source"] for q in saved} == {"题库.txt"}     # source=文件名


@pytest.mark.asyncio
async def test_import_dedups_against_existing_bank():
    imp = _importer(GOOD)
    imp._store.create("u1", {"type": "single", "stem": "1+1=?",
                             "options": ["1", "2"], "answer": 1})
    res = await imp.import_text("u1", "f.txt", "x")
    assert res["imported"] == 1 and res["skipped_duplicate"] == 2   # 单选1+1对库重复


@pytest.mark.asyncio
async def test_import_bad_json_returns_zero():
    res = await _importer("这不是JSON").import_text("u1", "f.txt", "x")
    assert res == {"imported": 0, "skipped_invalid": 0, "skipped_duplicate": 0}
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/app/test_question_import.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'app.question_import'`

- [ ] **步骤 3：编写最少实现代码**

创建 `app/question_import.py`：

```python
# app/question_import.py
from __future__ import annotations

from .parsing import parse_file  # noqa: F401  (端点侧解析用；此模块只处理文本)
from .quiz_service import QuizError, _parse_questions, _valid

ALL_TYPES = ["single", "multiple", "truefalse", "short"]

EXTRACT_SYSTEM = (
    "你是题库整理助手。从用户提供的文本中抽取所有题目，识别题干、选项、正确答案、"
    "解析，并判定题型。严格只输出一个 JSON 数组，每个元素形如："
    "{\"type\":\"single|multiple|truefalse|short\",\"stem\":\"题干\","
    "\"options\":[\"选项\"]或null,\"answer\":单选为选项索引整数/多选为索引数组/"
    "判断为true或false/简答为参考答案字符串,\"explanation\":\"解析\"}。"
    "不要输出 JSON 以外的任何文字。")


def _extract_user(text: str) -> str:
    return f"从下面文本中抽取题目，严格输出 JSON 数组：\n\n{text}"


class QuestionImporter:
    """上传文本 → LLM 抽取结构化题目 → 按(题型+题干)去重 → 入库。不依赖 embedding。"""

    def __init__(self, complete, question_store) -> None:
        self._complete = complete
        self._store = question_store

    async def import_text(self, user_id: str, filename: str, text: str) -> dict:
        raw = await self._complete(EXTRACT_SYSTEM, _extract_user(text))
        try:
            parsed = _parse_questions(raw)
        except QuizError:
            parsed = []
        imported = skipped_invalid = skipped_duplicate = 0
        seen: set[tuple] = set()
        for q in parsed:
            if not _valid(q, ALL_TYPES):
                skipped_invalid += 1
                continue
            key = (q["type"], (q.get("stem") or "").strip())
            if key in seen:                       # 批内去重
                skipped_duplicate += 1
                continue
            seen.add(key)
            q["source"] = filename
            q["explanation"] = q.get("explanation", "")
            if self._store.create_deduped(user_id, q) is None:   # 对库去重
                skipped_duplicate += 1
            else:
                imported += 1
        return {"imported": imported, "skipped_invalid": skipped_invalid,
                "skipped_duplicate": skipped_duplicate}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/app/test_question_import.py -q`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add app/question_import.py tests/app/test_question_import.py
git commit -m "feat: QuestionImporter 上传文本智能解析入库并去重"
```

---

## 任务 4：questions 路由改造（删 generate，加 import/sources，分页 list）+ 装配

**文件：**
- 修改：`app/api/questions.py`、`app/main.py`
- 测试：`tests/app/test_quiz_api.py`

- [ ] **步骤 1：改写测试（删 generate 覆盖，加 import/list/sources 覆盖）**

用下面内容**整体替换** `tests/app/test_quiz_api.py`（去掉依赖 memory/quiz 的 generate 测试，改测新端点；用注入的 `question_importer` 桩）：

```python
import io
import sqlite3
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import AppConfig
from app.assembly import Harness
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.questions import QuestionStore
from app.wrong_answers import WrongAnswerStore
from harness.tools.base import ToolRegistry
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    original = sqlite3.connect

    def _patched(*args, **kwargs):
        kwargs.setdefault("check_same_thread", False)
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _patched)


class _StubImporter:
    """按预设返回；记录收到的文件名。"""
    def __init__(self, result):
        self._result = result
        self.seen_filename = None

    async def import_text(self, user_id, filename, text):
        self.seen_filename = filename
        return self._result


def _app(question_importer=None):
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=None, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s")
    qs = QuestionStore(":memory:")
    app = create_app(config=AppConfig(api_key="k", app_db_path=":memory:"), harness=harness,
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=qs, wrong_store=WrongAnswerStore(":memory:"),
                     question_importer=question_importer)
    return TestClient(app), qs


def _auth(client, username="u"):
    r = client.post("/api/auth/register", json={"username": username, "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_generate_endpoint_removed():
    client, _ = _app()
    h = _auth(client)
    r = client.post("/api/questions/generate", json={"topic": "x", "count": 1}, headers=h)
    assert r.status_code in (404, 405)          # 端点已删


def test_list_paginated_and_filtered():
    client, qs = _app()
    h = _auth(client)
    uid = client.app.state.auth.verify_token(h["Authorization"][7:])[0]
    for i in range(3):
        qs.create(uid, {"type": "single", "stem": f"单选{i}", "options": ["1", "2"],
                        "answer": 1, "source": "算术"})
    qs.create(uid, {"type": "truefalse", "stem": "判断", "options": None, "answer": True,
                    "source": "常识"})
    body = client.get("/api/questions?page=1&size=2", headers=h).json()
    assert body["total"] == 4 and len(body["items"]) == 2
    only = client.get("/api/questions?type=truefalse", headers=h).json()
    assert only["total"] == 1 and only["items"][0]["stem"] == "判断"
    srcs = client.get("/api/questions/sources", headers=h).json()
    assert set(srcs) == {"算术", "常识"}


def test_import_endpoint_calls_importer():
    stub = _StubImporter({"imported": 3, "skipped_invalid": 0, "skipped_duplicate": 1})
    client, _ = _app(question_importer=stub)
    h = _auth(client)
    r = client.post("/api/questions/import", headers=h,
                    files={"file": ("题库.txt", io.BytesIO("单选题".encode()), "text/plain")})
    assert r.status_code == 200
    assert r.json()["imported"] == 3 and stub.seen_filename == "题库.txt"


def test_import_empty_file_400():
    stub = _StubImporter({"imported": 0, "skipped_invalid": 0, "skipped_duplicate": 0})
    client, _ = _app(question_importer=stub)
    h = _auth(client)
    r = client.post("/api/questions/import", headers=h,
                    files={"file": ("empty.txt", io.BytesIO(b"   "), "text/plain")})
    assert r.status_code == 400
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/app/test_quiz_api.py -q`
预期：FAIL（`create_app` 尚不接受 `question_importer`；`/api/questions/import` 不存在）

- [ ] **步骤 3：改写路由**

用下面内容**整体替换** `app/api/questions.py`：

```python
# app/api/questions.py
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..auth import current_user
from ..parsing import ParseError, UnsupportedFormat, parse_file


class IdsBody(BaseModel):
    ids: list[str]


def make_questions_router(question_store, config, question_importer=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/questions")
    async def list_questions(page: int = 1, size: int = 20, type: str = "",
                             source: str = "", q: str = "",
                             user_id: str = Depends(current_user)):
        size = max(1, min(100, size))
        page = max(1, page)
        kw = dict(type=type or None, source=source or None, q=q.strip() or None)
        items = question_store.list(user_id, limit=size, offset=(page - 1) * size, **kw)
        return {"items": items, "total": question_store.count(user_id, **kw)}

    @router.get("/api/questions/sources")
    async def question_sources(user_id: str = Depends(current_user)):
        return question_store.sources(user_id)

    @router.post("/api/questions/import")
    async def import_questions(file: UploadFile = File(...),
                               user_id: str = Depends(current_user)):
        if question_importer is None:
            raise HTTPException(status_code=503, detail="导入未启用")
        limit = config.app_max_upload_mb * 1024 * 1024
        if file.size is not None and file.size > limit:
            raise HTTPException(status_code=413, detail=f"文件超过 {config.app_max_upload_mb}MB")
        data = await file.read()
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"文件超过 {config.app_max_upload_mb}MB")
        try:
            text = parse_file(file.filename, data)
        except UnsupportedFormat as e:
            raise HTTPException(status_code=400, detail=f"不支持的格式：{e}")
        except ParseError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not text.strip():
            raise HTTPException(status_code=400, detail="文件为空或无法提取文本")
        return await question_importer.import_text(user_id, file.filename, text)

    @router.delete("/api/questions/{qid}")
    async def delete_question(qid: str, user_id: str = Depends(current_user)):
        question_store.delete(user_id, qid)
        return {"ok": True}

    @router.post("/api/questions/delete")
    async def delete_questions(body: IdsBody, user_id: str = Depends(current_user)):
        question_store.delete_many(user_id, body.ids)
        return {"ok": True}

    return router
```

- [ ] **步骤 4：装配 QuestionImporter 并更新 create_app**

在 `app/main.py` 顶部 import 区加：

```python
from .question_import import QuestionImporter
```

在 `create_app` 签名（`app/main.py:37-40`）里加参数 `question_importer=None`（放在 `quiz_service=None,` 之后同一区域）。

在 `quiz_service` 装配之后（`app/main.py:68` 之后）新增（`build_completer` 已在本文件导入并使用）：

```python
    if question_importer is None:
        question_importer = QuestionImporter(
            build_completer(harness.client, config.model), question_store)
```

把路由装配行（`app/main.py:118`）：

```python
    app.include_router(make_questions_router(quiz_service, question_store, config))
```

改为：

```python
    app.include_router(make_questions_router(question_store, config, question_importer))
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run pytest tests/app/test_quiz_api.py tests/app/test_api.py -q`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add app/api/questions.py app/main.py tests/app/test_quiz_api.py
git commit -m "feat: 题库路由删 generate、加 import/sources、改分页 list"
```

---

## 任务 5：AddQuestionsTool 改用 create_deduped

**文件：**
- 修改：`app/tools/exam_tools.py`
- 测试：`tests/app/test_exam_tools.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/app/test_exam_tools.py` 末尾追加：

```python
async def test_add_questions_dedups_against_bank():
    qs = QuestionStore(":memory:")
    qs.create("u1", {"type": "single", "stem": "重复题", "options": ["A", "B"], "answer": 0})
    tool = AddQuestionsTool(qs, "u1")
    dup = {"type": "single", "stem": "重复题", "options": ["A", "B"], "answer": 0}
    fresh = {"type": "single", "stem": "新题", "options": ["A", "B"], "answer": 1}
    out = await tool.run(tool.Params(questions=[dup, fresh]))
    assert "1" in out and len(qs.list("u1")) == 2      # 只新增「新题」
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/app/test_exam_tools.py::test_add_questions_dedups_against_bank -q`
预期：FAIL（当前 `AddQuestionsTool` 用 `create`，重复题也会入库 → list 长度 3）

- [ ] **步骤 3：编写最少实现代码**

在 `app/tools/exam_tools.py` 的 `AddQuestionsTool.run` 中，把入库循环改为按去重计数（替换现有 `for q in valid:` 块与返回语句）：

```python
    async def run(self, params: "AddQuestionsTool.Params") -> str:
        valid = [q for q in params.questions if _valid(q, ALL_TYPES)]
        added = 0
        for q in valid:
            q.setdefault("source", "聊天整理")
            q["explanation"] = q.get("explanation", "")
            if self._store.create_deduped(self._uid, q) is not None:
                added += 1
        skipped = len(params.questions) - added
        if added == 0:
            return f"没有新题入库（跳过 {skipped} 道：无效或与题库重复）。"
        return f"已入库 {added} 道，跳过 {skipped} 道（无效或重复）。"
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/app/test_exam_tools.py -q`
预期：PASS（既有 `test_add_questions_saves_valid_and_skips_invalid` 仍通过——两题不同题干，added=1）

- [ ] **步骤 5：Commit**

```bash
git add app/tools/exam_tools.py tests/app/test_exam_tools.py
git commit -m "feat: add_questions 工具入库改用 create_deduped 去重"
```

---

## 任务 6：前端 API 客户端 api.questions 改造

**文件：**
- 修改：`web/src/api/client.ts`

- [ ] **步骤 1：替换 api.questions 定义**

把 `web/src/api/client.ts` 中现有 `questions: { ... }` 块（含 `generate`）**整体替换**为：

```ts
  questions: {
    list: (params: { page?: number; size?: number; type?: string; source?: string; q?: string } = {}):
      Promise<{ items: Question[]; total: number }> => {
      const sp = new URLSearchParams();
      sp.set("page", String(params.page ?? 1));
      sp.set("size", String(params.size ?? 20));
      if (params.type) sp.set("type", params.type);
      if (params.source) sp.set("source", params.source);
      if (params.q) sp.set("q", params.q);
      return authFetch(`/api/questions?${sp.toString()}`).then((r) => r.json());
    },
    sources: (): Promise<string[]> =>
      authFetch("/api/questions/sources").then((r) => r.json()),
    import: (file: File): Promise<{ imported: number; skipped_invalid: number; skipped_duplicate: number }> => {
      const fd = new FormData(); fd.append("file", file);
      return authFetch("/api/questions/import", { method: "POST", body: fd }).then(async (r) => {
        if (!r.ok) throw new Error(await detail(r, `导入失败：${r.status}`));
        return r.json();
      });
    },
    remove: (id: string): Promise<void> =>
      authFetch(`/api/questions/${id}`, { method: "DELETE" }).then(() => undefined),
    removeMany: (ids: string[]): Promise<void> =>
      authFetch("/api/questions/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      }).then(() => undefined),
  },
```

- [ ] **步骤 2：新增 Question 类型**

在 `web/src/api/client.ts` 中 `VersionInfo` 附近（导出区）加：

```ts
export interface Question {
  id: string;
  type: string;
  stem: string;
  options: string[] | null;
  answer: unknown;
  explanation: string;
  source: string;
  created_at: string;
}
```

- [ ] **步骤 3：类型检查**

运行：`cd web && npx tsc -b`
预期：PASS（会因 `QuestionBankView.tsx` 仍用旧 `generate`/`list` 报错——留待任务 8 修复；本步只确认 client.ts 本身无语法/类型错。若 tsc 因下游文件报错，先记下，任务 8 会消除。）

- [ ] **步骤 4：Commit**

```bash
git add web/src/api/client.ts
git commit -m "feat: 前端 api.questions 改分页 list + import + sources"
```

---

## 任务 7：题目详情抽屉 QuestionDetailDrawer

**文件：**
- 创建：`web/src/pages/QuestionDetailDrawer.tsx`

- [ ] **步骤 1：创建组件**

创建 `web/src/pages/QuestionDetailDrawer.tsx`：

```tsx
import {
  Drawer, Box, Typography, Chip, Stack, IconButton, Divider,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import type { Question } from "../api/client";

const TYPE_LABEL: Record<string, string> = {
  single: "单选", multiple: "多选", truefalse: "判断", short: "简答",
};

// 正确答案是否包含某选项下标（单选=相等，多选=数组含）
function isCorrect(q: Question, idx: number): boolean {
  if (q.type === "single") return q.answer === idx;
  if (q.type === "multiple") return Array.isArray(q.answer) && (q.answer as number[]).includes(idx);
  return false;
}

function answerText(q: Question): string {
  if (q.type === "truefalse") return q.answer ? "正确" : "错误";
  if (q.type === "single" && q.options && typeof q.answer === "number")
    return q.options[q.answer] ?? String(q.answer);
  if (q.type === "multiple" && q.options && Array.isArray(q.answer))
    return (q.answer as number[]).map((i) => q.options![i] ?? i).join("、");
  return String(q.answer ?? "");
}

// 题目详情抽屉：右侧滑出，直接渲染内存中的题目对象（列表已含全字段，无需再请求）。
export function QuestionDetailDrawer({ question, onClose }: {
  question: Question | null; onClose: () => void;
}) {
  return (
    <Drawer anchor="right" open={question !== null} onClose={onClose}
      slotProps={{ paper: { sx: { width: { xs: "100%", sm: 560 }, maxWidth: "100%" } } }}>
      {question && (
        <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 1.5, height: "100%" }}>
          <Stack direction="row" spacing={1}
            sx={{ alignItems: "center", justifyContent: "space-between" }}>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>题目详情</Typography>
            <IconButton size="small" aria-label="关闭" onClick={onClose}>
              <CloseIcon />
            </IconButton>
          </Stack>

          <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
            <Chip size="small" color="primary" label={TYPE_LABEL[question.type] ?? question.type} />
            {question.source && (
              <Typography variant="caption" color="text.secondary">来源：{question.source}</Typography>
            )}
          </Stack>
          <Divider />

          <Box sx={{ overflowY: "auto", flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
            <Typography variant="body1" sx={{ whiteSpace: "pre-wrap", fontWeight: 600 }}>
              {question.stem}
            </Typography>

            {question.options && question.options.length > 0 && (
              <Stack spacing={0.75}>
                {question.options.map((opt, i) => (
                  <Stack key={i} direction="row" spacing={1} sx={{ alignItems: "center" }}>
                    {isCorrect(question, i)
                      ? <CheckCircleIcon color="success" fontSize="small" />
                      : <Box sx={{ width: 20 }} />}
                    <Typography variant="body2"
                      sx={{ fontWeight: isCorrect(question, i) ? 700 : 400 }}>
                      {String.fromCharCode(65 + i)}. {opt}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            )}

            <Box>
              <Typography variant="subtitle2" color="text.secondary">正确答案</Typography>
              <Typography variant="body1" sx={{ whiteSpace: "pre-wrap" }}>{answerText(question)}</Typography>
            </Box>

            {question.explanation && (
              <Box>
                <Typography variant="subtitle2" color="text.secondary">解析</Typography>
                <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{question.explanation}</Typography>
              </Box>
            )}
          </Box>
        </Box>
      )}
    </Drawer>
  );
}
```

- [ ] **步骤 2：类型检查**

运行：`cd web && npx tsc -b`
预期：`QuestionDetailDrawer.tsx` 本身无错（下游 `QuestionBankView.tsx` 报错留待任务 8）。

- [ ] **步骤 3：Commit**

```bash
git add web/src/pages/QuestionDetailDrawer.tsx
git commit -m "feat: 题目详情抽屉 QuestionDetailDrawer"
```

---

## 任务 8：重写 QuestionBankView（列表/筛选/分页/批删/上传/详情）

**文件：**
- 修改：`web/src/pages/QuestionBankView.tsx`
- 测试：`web/src/pages/QuestionBankView.test.tsx`

- [ ] **步骤 1：改写测试**

用下面内容**整体替换** `web/src/pages/QuestionBankView.test.tsx`：

```tsx
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import QuestionBankView from "./QuestionBankView";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    questions: {
      list: vi.fn(),
      sources: vi.fn(),
      import: vi.fn(),
      remove: vi.fn(),
      removeMany: vi.fn(),
    },
  },
}));

const Q = {
  id: "1", type: "single", stem: "光合作用在哪?",
  options: ["线粒体", "叶绿体"], answer: 1, explanation: "叶绿体", source: "生物",
  created_at: "2026-07-13T00:00:00Z",
};

describe("QuestionBankView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.questions.list as any).mockResolvedValue({ items: [Q], total: 1 });
    (api.questions.sources as any).mockResolvedValue(["生物"]);
  });

  it("渲染题目并显示人性化答案", async () => {
    render(<MemoryRouter><QuestionBankView /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/光合作用在哪/)).toBeTruthy());
    expect(screen.getByText(/叶绿体/)).toBeTruthy();   // 答案按选项文本显示
  });

  it("点题目卡片打开详情抽屉", async () => {
    render(<MemoryRouter><QuestionBankView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    fireEvent.click(screen.getByText(/光合作用在哪/));
    await waitFor(() => expect(screen.getByText("题目详情")).toBeTruthy());
  });

  it("勾选后批量删除调用 removeMany", async () => {
    (api.questions.removeMany as any).mockResolvedValue(undefined);
    render(<MemoryRouter><QuestionBankView /></MemoryRouter>);
    await waitFor(() => screen.getByText(/光合作用在哪/));
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByText(/批量删除/));
    await waitFor(() => expect(api.questions.removeMany).toHaveBeenCalledWith(["1"]));
  });
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd web && npx vitest run src/pages/QuestionBankView.test.tsx`
预期：FAIL（旧组件无详情/批删/答案渲染）

- [ ] **步骤 3：重写组件**

用下面内容**整体替换** `web/src/pages/QuestionBankView.tsx`：

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Box, Typography, Button, Card, CardContent, TextField, InputAdornment,
  IconButton, Checkbox, Chip, Stack, Pagination, Alert, MenuItem, Select,
  CircularProgress, FormControl, InputLabel,
} from "@mui/material";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import DeleteIcon from "@mui/icons-material/Delete";
import DeleteSweepIcon from "@mui/icons-material/DeleteSweep";
import SearchIcon from "@mui/icons-material/Search";
import { AnimatePresence, motion } from "framer-motion";
import { api, type Question } from "../api/client";
import { listItemVariants } from "../components/motion";
import { QuestionDetailDrawer } from "./QuestionDetailDrawer";

const PAGE_SIZE = 10;
const TYPES = [
  { key: "single", label: "单选" },
  { key: "multiple", label: "多选" },
  { key: "truefalse", label: "判断" },
  { key: "short", label: "简答" },
];
const typeLabel = (t: string) => TYPES.find((x) => x.key === t)?.label ?? t;

function answerText(q: Question): string {
  if (q.type === "truefalse") return q.answer ? "正确" : "错误";
  if (q.type === "single" && q.options && typeof q.answer === "number")
    return q.options[q.answer] ?? String(q.answer);
  if (q.type === "multiple" && q.options && Array.isArray(q.answer))
    return (q.answer as number[]).map((i) => q.options![i] ?? i).join("、");
  return String(q.answer ?? "");
}

// 单行截断（题干/答案过长显示 …）
const clampSx = {
  display: "-webkit-box", WebkitLineClamp: 1, WebkitBoxOrient: "vertical" as const,
  overflow: "hidden", wordBreak: "break-all" as const,
};

export default function QuestionBankView() {
  const [items, setItems] = useState<Question[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [source, setSource] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<Question | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback((p: number, filters: { q: string; type: string; source: string }) => {
    return api.questions.list({ page: p, size: PAGE_SIZE, q: filters.q, type: filters.type, source: filters.source })
      .then((r) => { setItems(r.items); setTotal(r.total); });
  }, []);

  const refreshSources = useCallback(() => api.questions.sources().then(setSources), []);
  useEffect(() => { refreshSources(); }, [refreshSources]);

  // 筛选变化（含防抖搜索）→ 回第 1 页并加载
  useEffect(() => {
    const t = setTimeout(() => {
      setPage(1);
      setError(null);
      load(1, { q: q.trim(), type, source }).catch((e: any) => setError(String(e?.message || e)));
    }, 300);
    return () => clearTimeout(t);
  }, [q, type, source, load]);

  // 翻页
  useEffect(() => {
    load(page, { q: q.trim(), type, source }).catch((e: any) => setError(String(e?.message || e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const toggle = (id: string) => setSelected((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n;
  });

  async function reload() {
    setSelected(new Set());
    await Promise.all([load(page, { q: q.trim(), type, source }), refreshSources()]);
  }

  async function removeOne(id: string) { await api.questions.remove(id); await reload(); }

  async function removeSelected() {
    if (selected.size === 0) return;
    await api.questions.removeMany([...selected]);
    await reload();
  }

  async function upload(file: File) {
    setBusy(true); setError(null); setNotice(null);
    try {
      const r = await api.questions.import(file);
      setNotice(`导入完成：新增 ${r.imported} 道，跳过重复 ${r.skipped_duplicate} 道，无效 ${r.skipped_invalid} 道。`);
      setPage(1);
      await reload();
    } catch (e: any) { setError(String(e?.message || e)); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2, maxWidth: 880, mx: "auto" }}>
      {/* 头部：标题 + 计数 + 导入 */}
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>题库</Typography>
          <Typography color="text.secondary" variant="body2">共 {total} 道题目</Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <Button variant="outlined" color="error" startIcon={<DeleteSweepIcon />}
            onClick={removeSelected} disabled={selected.size === 0}>
            批量删除（{selected.size}）
          </Button>
          <Button component="label" variant="contained" startIcon={<UploadFileIcon />} disabled={busy}>
            导入题库
            <input ref={fileRef} hidden type="file" accept=".txt,.md,.pdf,.docx"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
          </Button>
        </Stack>
      </Box>

      {/* 筛选工具条：题名 + 题型 + 来源 */}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <TextField fullWidth size="small" placeholder="搜索题名…"
          value={q} onChange={(e) => setQ(e.target.value)}
          slotProps={{ input: {
            startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>,
            endAdornment: busy ? <CircularProgress size={18} /> : undefined,
          } }} />
        <FormControl size="small" sx={{ minWidth: 120 }}>
          <InputLabel>题型</InputLabel>
          <Select label="题型" value={type} onChange={(e) => setType(e.target.value)}>
            <MenuItem value="">全部题型</MenuItem>
            {TYPES.map((t) => <MenuItem key={t.key} value={t.key}>{t.label}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 140 }}>
          <InputLabel>来源</InputLabel>
          <Select label="来源" value={source} onChange={(e) => setSource(e.target.value)}>
            <MenuItem value="">全部来源</MenuItem>
            {sources.map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </Select>
        </FormControl>
      </Stack>

      {notice && <Alert severity="success" onClose={() => setNotice(null)}>{notice}</Alert>}
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

      {items.length === 0 ? (
        <Typography color="text.secondary" sx={{ py: 4, textAlign: "center" }}>暂无题目</Typography>
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <AnimatePresence initial={false}>
            {items.map((item) => (
              <motion.div key={item.id} layout variants={listItemVariants}
                initial="initial" animate="animate" exit="exit">
                <Card variant="outlined"
                  sx={{ "&:hover": { borderColor: "primary.main", boxShadow: 2 } }}>
                  <CardContent sx={{ display: "flex", gap: 1, "&:last-child": { pb: 2 } }}>
                    <Checkbox sx={{ p: 0, mt: 0.25 }} checked={selected.has(item.id)}
                      onChange={() => toggle(item.id)} />
                    <Box sx={{ minWidth: 0, flex: 1, cursor: "pointer" }} onClick={() => setPreview(item)}>
                      <Stack direction="row" spacing={1} sx={{ alignItems: "center", minWidth: 0 }}>
                        <Chip size="small" label={typeLabel(item.type)} />
                        <Typography variant="body2" sx={{ fontWeight: 600, ...clampSx }}>{item.stem}</Typography>
                      </Stack>
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, ...clampSx }}>
                        答案：{answerText(item)}
                      </Typography>
                      {item.source && (
                        <Typography variant="caption" color="text.secondary">· {item.source}</Typography>
                      )}
                    </Box>
                    <IconButton size="small" color="error" aria-label="删除题目"
                      onClick={() => removeOne(item.id)}>
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </CardContent>
                </Card>
              </motion.div>
            ))}
          </AnimatePresence>
        </Box>
      )}

      {pageCount > 1 && (
        <Box sx={{ display: "flex", justifyContent: "center", mt: 1 }}>
          <Pagination color="primary" count={pageCount} page={page} onChange={(_, p) => setPage(p)} />
        </Box>
      )}

      <QuestionDetailDrawer question={preview} onClose={() => setPreview(null)} />
    </Box>
  );
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`cd web && npx tsc -b && npx vitest run src/pages/QuestionBankView.test.tsx`
预期：tsc PASS（下游错误已消除）；vitest 3 例 PASS

- [ ] **步骤 5：Commit**

```bash
git add web/src/pages/QuestionBankView.tsx web/src/pages/QuestionBankView.test.tsx
git commit -m "feat: 重写题库页——答案截断/筛选/分页/批量删除/上传导入/详情抽屉"
```

---

## 任务 9：全量验证与收尾

- [ ] **步骤 1：后端全量测试**

运行：`uv run pytest tests/app -q`
预期：全部 PASS（含 test_questions / test_question_import / test_quiz_api / test_exam_tools）。若 `test_quiz_generate.py`、`test_quiz_grade.py` 失败，检查是否误删 `QuizService`（不应删；它们测的是 service 而非端点）。

- [ ] **步骤 2：前端全量测试**

运行：`cd web && npx tsc -b && npx vitest run`
预期：全部 PASS。

- [ ] **步骤 3：端到端手测（可选）**

启动 `uv run python -m app` + 前端，验证：导入 txt/pdf 题库文件→toast 显示新增/跳过数、题库列表出现且答案截断、题名/题型/来源筛选、翻页、勾选批量删除、点卡片开详情抽屉、页面顶部已无「出题」表单。

- [ ] **步骤 4：合并收尾**

用 finishing-a-development-branch 技能决定合并/PR/清理。

---

## 自检结论

- **规格覆盖**：#1 移除出题=任务4+8；#2 详情页=任务7+8；#3 列表显示答案+截断=任务8；#4 上传智能解析=任务3+4+8；#5 去重=任务1+3+5；#6 查询条件=任务2+4+8；#7 批量删除=任务8（复用既有 removeMany）。全覆盖。
- **占位符**：无 TODO/待定；每个代码步骤含完整代码。
- **类型一致**：后端 `create_deduped`/`list(*,type,source,q,limit,offset)`/`count`/`sources`、`QuestionImporter.import_text`、路由 `make_questions_router(question_store, config, question_importer)` 跨任务一致；前端 `Question` 类型、`answerText`、`QuestionDetailDrawer({question,onClose})` 一致。
