# AI 学习助手 · App-2（文件上传 → 知识库）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 上传 PDF/docx/txt/md → 解析 → `Memory.add_texts`(knowledge) → 文档管理；前端 react-router 壳 + 知识库页。

**架构：** 后端 `app/`（parsing/documents/knowledge/api）复用 harness `Memory`；App-1 `assembly.py` 暴露 memory 引用；前端路由化 + `KnowledgeView`。harness 零改动。

**技术栈：** 沿用 App-1 · 新增 `pypdf`/`python-docx`/`python-multipart`（后端）、`react-router-dom`（前端）。

**规格：** `docs/superpowers/specs/2026-07-08-app-knowledge-base-design.md`

**提交规范：** git 身份已是 sumengnan，默认提交；**任何 commit message 不得出现 Claude/AI/Co-Authored-By 等署名**；**不提交 `web/node_modules`**。

---

## 文件结构与职责

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/parsing.py` | 新增 | `parse_file` + `UnsupportedFormat` |
| `app/documents.py` | 新增 | `DocumentStore`（文档 + chunk_ids 映射） |
| `app/knowledge.py` | 新增 | `KnowledgeService` + `EmptyDocument` |
| `app/api/documents.py` | 新增 | POST/GET/DELETE /api/documents |
| `app/assembly.py` | 改 | Harness 增 memory/memory_store |
| `app/config.py` | 改 | app_max_upload_mb、documents_db_path |
| `app/main.py` | 改 | 挂 documents 路由 + KnowledgeService |
| `web/src/App.tsx` | 改 | react-router 壳 |
| `web/src/pages/{ChatPage,KnowledgeView}.tsx` | 新增 | 聊天页（搬 App-1）+ 知识库页 |
| `web/src/api/client.ts` | 改 | documents API |
| `pyproject.toml` / `web/package.json` | 改 | 依赖 |

---

## 任务 0：依赖与配置

**文件：** 改 `pyproject.toml`、`app/config.py`、测试 `tests/app/test_config.py`

- [ ] **步骤 1：`pyproject.toml`** `dependencies` 追加：
```toml
    "pypdf>=4.2",
    "python-docx>=1.1",
    "python-multipart>=0.0.9",
```
- [ ] **步骤 2：`uv sync`**　运行：`uv sync`。
- [ ] **步骤 3：写失败测试**（`tests/app/test_config.py` 追加）
```python
def test_app2_config_defaults():
    cfg = AppConfig(api_key="k")
    assert cfg.app_max_upload_mb == 20
    assert cfg.documents_db_path == "documents.db"
```
运行：预期 FAIL。
- [ ] **步骤 4：改 `app/config.py`** 追加字段：
```python
    app_max_upload_mb: int = 20
    documents_db_path: str = "documents.db"
```
- [ ] **步骤 5：跑通并 commit**
```bash
git add pyproject.toml uv.lock app/config.py tests/app/test_config.py
git commit -m "chore: App-2 依赖与配置项"
```

---

## 任务 1：assembly 暴露 memory

**文件：** 改 `app/assembly.py`、测试 `tests/app/test_assembly.py`

- [ ] **步骤 1：写失败测试**（`tests/app/test_assembly.py` 追加）
```python
def test_build_harness_exposes_memory():
    h = build_harness(_cfg())        # _cfg 已设 api_key + :memory: dbs
    assert h.memory is not None
    assert h.memory_store is not None
```
运行：预期 FAIL（Harness 无 memory 字段）。

- [ ] **步骤 2：改 `app/assembly.py`**
- `Harness` dataclass 末尾追加两个带默认值的字段：
```python
    memory: object | None = None
    memory_store: object | None = None
```
- `build_harness` 里：在函数开头加 `memory = None; memory_store = None`；在记忆装配块内、构造 `mem` 后加 `memory = mem; memory_store = mem_store`（把 `MemoryStore(...)` 先赋给局部变量 `mem_store` 再传给 `Memory`，以便引用）；`return Harness(...)` 时补 `memory=memory, memory_store=memory_store`。
  例如记忆块改为：
```python
        mem_store = MemoryStore(config.memory_db_path, config.embedding_dimension)
        mem = Memory(mem_store, embedder, config.chunk_size, config.chunk_overlap)
        memory = mem
        memory_store = mem_store
        reg.register(SearchMemoryTool(mem, default_k=config.search_top_k))
        ...
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/app/test_assembly.py -v`　预期全 pass（含既有门控测试）。
```bash
git add app/assembly.py tests/app/test_assembly.py
git commit -m "feat: build_harness 暴露 memory/memory_store"
```

---

## 任务 2：文件解析 parsing.py

**文件：** 创建 `app/parsing.py`、测试 `tests/app/test_parsing.py`

- [ ] **步骤 1：写失败测试**
```python
# tests/app/test_parsing.py
import io
import pytest
from app.parsing import parse_file, UnsupportedFormat


def test_parse_txt_and_md():
    assert parse_file("a.txt", "你好\n世界".encode()) == "你好\n世界"
    assert "标题" in parse_file("b.md", "# 标题".encode())


def test_parse_docx_roundtrip():
    import docx
    d = docx.Document()
    d.add_paragraph("第一段内容")
    d.add_paragraph("第二段内容")
    buf = io.BytesIO(); d.save(buf)
    text = parse_file("x.docx", buf.getvalue())
    assert "第一段内容" in text and "第二段内容" in text


def test_unsupported_format_raises():
    with pytest.raises(UnsupportedFormat):
        parse_file("x.pptx", b"data")
```
运行：预期 FAIL。

- [ ] **步骤 2：实现 `app/parsing.py`**
```python
# app/parsing.py
from __future__ import annotations

import io


class UnsupportedFormat(Exception):
    pass


def parse_file(filename: str, data: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("txt", "md"):
        return data.decode("utf-8", errors="replace")
    if ext == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if ext == "docx":
        import docx
        document = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs)
    raise UnsupportedFormat(ext or filename)
```
> PDF 解析未做自动化单测（手工构造可靠 PDF 需额外依赖）；由手动 E2E（任务 8）覆盖。

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/app/test_parsing.py -v`　预期：3 passed。
```bash
git add app/parsing.py tests/app/test_parsing.py
git commit -m "feat: parse_file 文件解析（PDF/docx/txt/md）"
```

---

## 任务 3：DocumentStore

**文件：** 创建 `app/documents.py`、测试 `tests/app/test_documents.py`

- [ ] **步骤 1：写失败测试**
```python
# tests/app/test_documents.py
from app.documents import DocumentStore


def test_create_list_chunkids_delete():
    s = DocumentStore(":memory:")
    s.create("d1", "bio.txt", 123, [1, 2, 3])
    docs = s.list()
    assert docs[0]["id"] == "d1" and docs[0]["num_chunks"] == 3
    assert s.chunk_ids("d1") == [1, 2, 3]
    assert s.exists("d1") is True
    s.delete("d1")
    assert s.exists("d1") is False and s.list() == []
```
运行：预期 FAIL。

- [ ] **步骤 2：实现 `app/documents.py`**
```python
# app/documents.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocumentStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS documents("
            "id TEXT PRIMARY KEY, filename TEXT, size INTEGER, num_chunks INTEGER, "
            "chunk_ids TEXT, uploaded_at TEXT)")
        self._conn.commit()

    def create(self, doc_id: str, filename: str, size: int, chunk_ids: list[int]) -> None:
        self._conn.execute(
            "INSERT INTO documents(id, filename, size, num_chunks, chunk_ids, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, filename, size, len(chunk_ids), json.dumps(chunk_ids), _now()))
        self._conn.commit()

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, filename, size, num_chunks, uploaded_at FROM documents "
            "ORDER BY uploaded_at DESC").fetchall()
        return [{"id": r[0], "filename": r[1], "size": r[2],
                 "num_chunks": r[3], "uploaded_at": r[4]} for r in rows]

    def exists(self, doc_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone() is not None

    def chunk_ids(self, doc_id: str) -> list[int]:
        row = self._conn.execute(
            "SELECT chunk_ids FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def delete(self, doc_id: str) -> None:
        self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        self._conn.commit()
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/app/test_documents.py -v`　预期：1 passed。
```bash
git add app/documents.py tests/app/test_documents.py
git commit -m "feat: DocumentStore 文档登记与 chunk 映射"
```

---

## 任务 4：KnowledgeService

**文件：** 创建 `app/knowledge.py`、测试 `tests/app/test_knowledge.py`

- [ ] **步骤 1：写失败测试**
```python
# tests/app/test_knowledge.py
from app.knowledge import KnowledgeService, EmptyDocument
from app.documents import DocumentStore
from harness.memory.memory import Memory
from harness.memory.store import MemoryStore
import pytest


def _service(mock_embedder):
    store = MemoryStore(":memory:", dimension=64)
    mem = Memory(store, mock_embedder(dimension=64), chunk_size=1000, overlap=0)
    return KnowledgeService(mem, store, DocumentStore(":memory:")), mem


async def test_ingest_registers_and_searchable(mock_embedder):
    svc, mem = _service(mock_embedder)
    doc = await svc.ingest("bio.txt", "光合作用把二氧化碳和水转化为氧气".encode())
    assert doc["num_chunks"] >= 1 and doc["filename"] == "bio.txt"
    hits = await mem.search("光合作用", "knowledge", 3)
    assert any("光合作用" in h.text for h in hits)


async def test_delete_removes_chunks_and_doc(mock_embedder):
    svc, mem = _service(mock_embedder)
    doc = await svc.ingest("a.txt", "独特内容ABC".encode())
    svc.delete(doc["id"])
    assert await mem.search("独特内容", "knowledge", 3) == []
    assert svc._doc_store.exists(doc["id"]) is False


async def test_empty_document_raises(mock_embedder):
    svc, _ = _service(mock_embedder)
    with pytest.raises(EmptyDocument):
        await svc.ingest("empty.txt", "   ".encode())
```
运行：预期 FAIL。

- [ ] **步骤 2：实现 `app/knowledge.py`**
```python
# app/knowledge.py
from __future__ import annotations

from uuid import uuid4

from .parsing import parse_file


class EmptyDocument(Exception):
    pass


class KnowledgeService:
    def __init__(self, memory, memory_store, doc_store, collection: str = "knowledge") -> None:
        self._memory = memory
        self._memory_store = memory_store
        self._doc_store = doc_store
        self._collection = collection

    async def ingest(self, filename: str, data: bytes) -> dict:
        text = parse_file(filename, data)          # UnsupportedFormat 冒泡
        if not text.strip():
            raise EmptyDocument(filename)
        doc_id = uuid4().hex
        chunk_ids = await self._memory.add_texts(
            [text], self._collection, {"source": filename, "doc_id": doc_id})
        self._doc_store.create(doc_id, filename, len(data), chunk_ids)
        return {"id": doc_id, "filename": filename, "num_chunks": len(chunk_ids)}

    def delete(self, doc_id: str) -> None:
        self._memory_store.delete(self._doc_store.chunk_ids(doc_id))
        self._doc_store.delete(doc_id)
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/app/test_knowledge.py -v`　预期：3 passed。
```bash
git add app/knowledge.py tests/app/test_knowledge.py
git commit -m "feat: KnowledgeService 摄入与删除"
```

---

## 任务 5：documents API + main 装配

**文件：** 创建 `app/api/documents.py`、改 `app/main.py`、测试 `tests/app/test_api.py`（追加）

- [ ] **步骤 1：写失败测试**（`tests/app/test_api.py` 追加；构造含 Memory 的假 harness）
```python
def _client_with_kb(make_mock, mock_embedder):
    from harness.memory.memory import Memory
    from harness.memory.store import MemoryStore
    from app.documents import DocumentStore
    mstore = MemoryStore(":memory:", dimension=64)
    mem = Memory(mstore, mock_embedder(dimension=64), 1000, 0)
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=make_mock([]), registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s",
                      memory=mem, memory_store=mstore)
    store = ConversationStore(":memory:")
    doc_store = DocumentStore(":memory:")
    app = create_app(config=AppConfig(api_key="k"), harness=harness, store=store, doc_store=doc_store)
    return TestClient(app)


def test_upload_list_delete(make_mock, mock_embedder):
    client = _client_with_kb(make_mock, mock_embedder)
    r = client.post("/api/documents", files={"file": ("bio.txt", "光合作用内容".encode(), "text/plain")})
    assert r.status_code == 200
    doc_id = r.json()["id"]
    assert any(d["id"] == doc_id for d in client.get("/api/documents").json())
    assert client.delete(f"/api/documents/{doc_id}").status_code == 200
    assert client.get("/api/documents").json() == []


def test_upload_unsupported_400(make_mock, mock_embedder):
    client = _client_with_kb(make_mock, mock_embedder)
    r = client.post("/api/documents", files={"file": ("x.pptx", b"data", "application/octet-stream")})
    assert r.status_code == 400


def test_upload_503_without_memory(make_mock):
    # 默认 _client（无 memory 的 harness）→ 上传 503
    client, _ = _client(make_mock, [])
    r = client.post("/api/documents", files={"file": ("a.txt", b"hi", "text/plain")})
    assert r.status_code == 503
```
运行：预期 FAIL。

- [ ] **步骤 2：实现 `app/api/documents.py`**
```python
# app/api/documents.py
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..knowledge import EmptyDocument
from ..parsing import UnsupportedFormat


def make_documents_router(service, doc_store, config) -> APIRouter:
    router = APIRouter()

    @router.post("/api/documents")
    async def upload(file: UploadFile = File(...)):
        if service is None:
            raise HTTPException(status_code=503, detail="知识库未启用（未配置 embedding）")
        data = await file.read()
        if len(data) > config.app_max_upload_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"文件超过 {config.app_max_upload_mb}MB")
        try:
            return await service.ingest(file.filename, data)
        except UnsupportedFormat as e:
            raise HTTPException(status_code=400, detail=f"不支持的格式：{e}")
        except EmptyDocument:
            raise HTTPException(status_code=400, detail="文档为空或无法提取文本")

    @router.get("/api/documents")
    async def list_documents():
        return doc_store.list()

    @router.delete("/api/documents/{doc_id}")
    async def delete_document(doc_id: str):
        if service is None:
            raise HTTPException(status_code=503, detail="知识库未启用")
        if not doc_store.exists(doc_id):
            raise HTTPException(status_code=404, detail="文档不存在")
        service.delete(doc_id)
        return {"ok": True}

    return router
```

- [ ] **步骤 3：改 `app/main.py`**：`create_app` 增 `doc_store` 参数、构造 `KnowledgeService`、挂路由。
```python
from .documents import DocumentStore
from .knowledge import KnowledgeService
from .api.documents import make_documents_router
# ...
def create_app(config=None, harness=None, store=None, doc_store=None):
    config = config or AppConfig()
    harness = harness if harness is not None else build_harness(config)
    store = store if store is not None else ConversationStore(config.conversations_db_path)
    doc_store = doc_store if doc_store is not None else DocumentStore(config.documents_db_path)

    service = None
    if getattr(harness, "memory", None) is not None and getattr(harness, "memory_store", None) is not None:
        service = KnowledgeService(harness.memory, harness.memory_store, doc_store)

    app = FastAPI(title="AI 学习助手")
    app.add_middleware(CORSMiddleware, allow_origins=config.cors_origins,
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(make_conversations_router(store))
    app.include_router(make_chat_router(harness, store, config))
    app.include_router(make_documents_router(service, doc_store, config))
    if os.path.isdir("web/dist"):
        app.mount("/", StaticFiles(directory="web/dist", html=True), name="static")
    return app
```

- [ ] **步骤 4：更新既有 `_client` 辅助（避免落 documents.db 文件）**

`tests/app/test_api.py` 里 App-1 的 `_client(make_mock, turns)` 现在调 `create_app(config, harness, store)`，未传 `doc_store` → 会建真实 `DocumentStore("documents.db")` 落文件。改为传 `doc_store=DocumentStore(":memory:")`（import `from app.documents import DocumentStore`），保持测试不落盘。

- [ ] **步骤 5：跑通并 commit**

运行：`uv run pytest tests/app/ -v`　预期全 pass；再 `uv run pytest -q` 确认无回归。
```bash
git add app/api/documents.py app/main.py tests/app/test_api.py
git commit -m "feat: documents API（上传/列表/删除）+ main 装配"
```

---

## 任务 6：前端 react-router 壳 + ChatPage

**文件：** 改 `web/package.json`、`web/src/App.tsx`、创建 `web/src/pages/ChatPage.tsx`

- [ ] **步骤 1：`web/package.json`** `dependencies` 加 `"react-router-dom": "^6.26.0"`，然后 `cd web && npm install`。

- [ ] **步骤 2：创建 `web/src/pages/ChatPage.tsx`**（把 App-1 `App.tsx` 的聊天逻辑整体搬来）
```tsx
// web/src/pages/ChatPage.tsx
import { useEffect, useState } from "react";
import type { Conversation, ChatMessage } from "../types";
import { api } from "../api/client";
import { ConversationList } from "../components/ConversationList";
import { ChatView } from "../components/ChatView";

export function ChatPage() {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [initial, setInitial] = useState<ChatMessage[]>([]);
  const refresh = () => api.list().then(setConvs);
  useEffect(() => { refresh(); }, []);
  async function select(id: string) {
    setActiveId(id);
    const msgs = await api.messages(id);
    setInitial(msgs.map((m) => ({ role: m.role as "user" | "assistant", content: m.content })));
  }
  async function newConv() { const { id } = await api.create(); await refresh(); await select(id); }
  async function del(id: string) {
    await api.remove(id); await refresh();
    if (id === activeId) { setActiveId(null); setInitial([]); }
  }
  return (
    <div className="flex h-full">
      <ConversationList items={convs} activeId={activeId} onSelect={select} onNew={newConv} onDelete={del} />
      <div className="flex-1">
        {activeId
          ? <ChatView key={activeId} conversationId={activeId} initial={initial} />
          : <div className="h-full flex items-center justify-center text-gray-400">新建或选择一个对话开始</div>}
      </div>
    </div>
  );
}
```

- [ ] **步骤 3：改 `web/src/App.tsx`**（改成路由壳）
```tsx
// web/src/App.tsx
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import { ChatPage } from "./pages/ChatPage";
import { KnowledgeView } from "./pages/KnowledgeView";

const NAV: [string, string][] = [["/", "聊天"], ["/knowledge", "知识库"]];

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-full">
        <nav className="w-20 bg-gray-800 text-white flex flex-col shrink-0">
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"}
              className={({ isActive }) =>
                `px-2 py-3 text-center text-sm ${isActive ? "bg-gray-600" : "hover:bg-gray-700"}`}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="flex-1 min-w-0">
          <Routes>
            <Route path="/" element={<ChatPage />} />
            <Route path="/knowledge" element={<KnowledgeView />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  );
}
```

- [ ] **步骤 4：类型检查**　运行：`cd web && npx tsc --noEmit`（此时 KnowledgeView 还没建会报错——先建任务 7 再一起 tsc/commit；或本步骤仅创建 ChatPage/App，任务 7 建 KnowledgeView 后统一 commit）。**建议任务 6、7 连续做，一起 commit。**

---

## 任务 7：前端知识库页 + documents API + Vitest

**文件：** 改 `web/src/api/client.ts`、创建 `web/src/pages/KnowledgeView.tsx`、`web/src/pages/KnowledgeView.test.tsx`

- [ ] **步骤 1：改 `web/src/api/client.ts`**——给导出的 `api` 对象加 `documents` 子对象：
```ts
// 在现有 api 对象里追加 documents（与 list/create/messages/remove 并列）
  documents: {
    list: (): Promise<{ id: string; filename: string; num_chunks: number; uploaded_at: string }[]> =>
      fetch("/api/documents").then((r) => r.json()),
    upload: (file: File) => {
      const fd = new FormData(); fd.append("file", file);
      return fetch("/api/documents", { method: "POST", body: fd }).then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `上传失败：${r.status}`);
        return r.json();
      });
    },
    remove: (id: string): Promise<void> =>
      fetch(`/api/documents/${id}`, { method: "DELETE" }).then(() => undefined),
  },
```

- [ ] **步骤 2：创建 `web/src/pages/KnowledgeView.tsx`**
```tsx
// web/src/pages/KnowledgeView.tsx
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

type Doc = { id: string; filename: string; num_chunks: number; uploaded_at: string };

export function KnowledgeView() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const refresh = () => api.documents.list().then(setDocs);
  useEffect(() => { refresh(); }, []);

  async function upload(file: File) {
    setBusy(true); setError(null);
    try { await api.documents.upload(file); await refresh(); }
    catch (e: any) { setError(String(e?.message || e)); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }
  async function remove(id: string) { await api.documents.remove(id); await refresh(); }

  return (
    <div className="p-6 max-w-2xl">
      <h1 className="text-xl font-bold mb-4">知识库</h1>
      <div className="mb-4">
        <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md" disabled={busy}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
        {busy && <span className="ml-2 text-gray-500">上传中…</span>}
        {error && <div className="text-red-600 mt-1">{error}</div>}
      </div>
      {docs.length === 0 ? (
        <div className="text-gray-400">还没有上传文档</div>
      ) : (
        <ul className="divide-y border rounded">
          {docs.map((d) => (
            <li key={d.id} className="flex justify-between items-center px-3 py-2">
              <span>{d.filename} <span className="text-xs text-gray-400">· {d.num_chunks} 块</span></span>
              <button className="text-red-500" onClick={() => remove(d.id)}>删除</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **步骤 3：Vitest `web/src/pages/KnowledgeView.test.tsx`**
```tsx
// web/src/pages/KnowledgeView.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { KnowledgeView } from "./KnowledgeView";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { documents: { list: vi.fn(), upload: vi.fn(), remove: vi.fn() } },
}));

describe("KnowledgeView", () => {
  it("渲染文档列表", async () => {
    (api.documents.list as any).mockResolvedValue([
      { id: "1", filename: "bio.txt", num_chunks: 3, uploaded_at: "" },
    ]);
    render(<KnowledgeView />);
    await waitFor(() => expect(screen.getByText(/bio\.txt/)).toBeTruthy());
    expect(screen.getByText(/3 块/)).toBeTruthy();
  });
});
```

- [ ] **步骤 4：前端测试 + 类型检查，commit**

运行：`cd web && npm run test && npx tsc --noEmit`　预期：Vitest 全过（sse + ChatView + KnowledgeView）、tsc 无错。
```bash
git add web/package.json web/package-lock.json web/src/App.tsx web/src/pages/ChatPage.tsx web/src/pages/KnowledgeView.tsx web/src/pages/KnowledgeView.test.tsx web/src/api/client.ts
git commit -m "feat: 前端 react-router 壳 + 知识库页"
```

---

## 任务 8：全量测试 + 手动 E2E + README

- [ ] **步骤 1：后端全量**　运行：`uv run pytest -q`　预期全绿（harness + App-1 + App-2；既有 3 skip 不变）。

- [ ] **步骤 2：手动 E2E**（需 `.env` 配聊天 + embedding 端点）
```bash
uv run python -m app          # 或 uv run uvicorn --factory app.main:create_app --reload
cd web && npm run dev
```
验收：知识库页上传一个 txt/pdf → 列表出现（含块数）→ 回聊天问该文档内容 → agent 用 search_memory 召回并作答 → 回知识库删除该文档 → 再问已召回不到。

- [ ] **步骤 3：更新 `app/README.md`**（补知识库功能、`.env` 需 embedding key、支持格式、上传大小上限）。

- [ ] **步骤 4：commit**
```bash
git add app/README.md
git commit -m "docs: App-2 知识库运行说明"
```

---

## 完成标准（对照规格验收）

- [ ] 上传 txt/md/docx → 解析入库 → `/api/documents` 列出（任务 2/4/5）
- [ ] 删文档 → chunk 从 sqlite-vec 删、search 不再召回、记录消失（任务 4/5）
- [ ] 入库后 search_memory 能召回（任务 4）
- [ ] 上传错误码 503/400（任务 5）
- [ ] 前端知识库页上传/列表/删除、路由聊天页不受影响、Vitest 过（任务 6/7）
- [ ] harness 零改动；harness+App-1 测试无回归（任务 1/8）
```
