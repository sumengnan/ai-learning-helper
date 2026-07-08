# AI 学习助手 · App-2（文件上传 → 知识库）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用，应用层第二个子项目
- **前置**：harness ①-③d + App-1 聊天脊柱已完成并在 `main`

---

## 0. 背景与范围

在 App-1（FastAPI 后端 + React 前端 + harness 装配）之上加"文件上传 → 知识库"：上传 PDF/docx/txt/md → 解析 → 分块 embedding 入 harness `Memory` 的 `knowledge` collection → agent 聊天时 `search_memory` 可检索；知识库管理页查看/删除文档。

**技术选型**：沿用 App-1（FastAPI + React + react-router）；解析用 `pypdf`/`python-docx`；复用 harness `Memory`（App-1 已装配），**harness 零改动**。

设计通则：严格 YAGNI；复用 harness Memory（不重造向量逻辑）；harness 零改动（App-2 仅在 App-1 `assembly.py` 暴露 memory 引用）；测试用 mock embedder + `:memory:`，不打网络。

---

## 1. 范围与验收

### IN
- **后端**：`parse_file`（PDF/docx/txt/md → 文本）；`DocumentStore`（SQLite 文档登记 + 文档→chunk_ids 映射）；`KnowledgeService`（parse → `Memory.add_texts`(knowledge) → 登记）；`/api/documents` 上传/列表/删除。
- **App-1 小改**：`build_harness` 暴露 `memory`/`memory_store`（供上传入库、删 chunk）。
- **前端**：`App.tsx` 重构成 react-router 壳（左导航：聊天/知识库）；`ChatPage`（搬 App-1 聊天）；`KnowledgeView`（上传 + 列表 + 删除）。

### OUT（后续）
题库/错题/模拟考试（App-3）、下载/图库（App-4）、rerank、后台异步解析、pptx/xlsx/html/epub。

### 验收标准
1. 上传 txt/md → 解析入库 → `GET /api/documents` 列出（含文件名/块数）。
2. 上传 docx（测试内 python-docx 现造）→ 正文提取入库。
3. 删文档 → 其 chunk 从 sqlite-vec 删除（`search` 不再召回）、文档记录消失。
4. 入库后 `search_memory` 能召回该文档内容（mock embedder + `:memory:`）。
5. `/api/documents` 上传/列表/删除全通（TestClient + 假文件）。
6. 前端知识库页上传/列表/删除可用；聊天页经 `/` 路由不受影响。
7. memory 未装配（无 embedding）时上传返回 **503**、不崩。
8. harness 零改动；harness + App-1 测试不回归。

---

## 2. 架构与模块

**设计取向**：上传即 `memory.add_texts([文本], "knowledge", {"source": 文件名, "doc_id": id})`，返回的 chunk_ids 存进 `DocumentStore`；删除即 `memory_store.delete(chunk_ids)` + 删文档记录。**harness 零改动**。

```
app/                              [改/增]
├── assembly.py        [改] Harness 增 memory / memory_store 字段（build_harness 装配时赋值）
├── parsing.py         [新增] parse_file(filename, data) → text
├── documents.py       [新增] DocumentStore(SQLite)
├── knowledge.py       [新增] KnowledgeService（ingest / delete）
├── config.py          [改] app_max_upload_mb
├── main.py            [改] 挂 documents 路由（注入 KnowledgeService/DocumentStore）
└── api/documents.py   [新增] POST/GET/DELETE /api/documents
web/                              [改/增]
├── package.json       [改] react-router-dom
├── src/App.tsx        [改] BrowserRouter 壳（左导航 + Routes）
├── src/pages/ChatPage.tsx        [新增] App-1 对话侧栏 + ChatView
├── src/pages/KnowledgeView.tsx   [新增] 上传 + 列表 + 删除
└── src/api/client.ts  [改] documents API（FormData）
```

**依赖新增**：后端 `pypdf`、`python-docx`、`python-multipart`（FastAPI 上传必需）；前端 `react-router-dom`。harness 不加依赖不改代码。

**数据流**：
```
上传：POST /api/documents (multipart)
 → parse_file → memory.add_texts([文本],"knowledge",{source,doc_id}) → chunk_ids
 → DocumentStore.create(doc_id, 文件名, 大小, chunk_ids) → 文档信息
删除：DELETE /api/documents/{id}
 → DocumentStore.chunk_ids(id) → memory_store.delete(chunk_ids) → DocumentStore.delete(id)
```

---

## 3. 后端

**`parsing.py`**：
```python
class UnsupportedFormat(Exception): ...

def parse_file(filename: str, data: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("txt", "md"):
        return data.decode("utf-8", errors="replace")
    if ext == "pdf":
        # pypdf：逐页 extract_text 拼接
        ...
    if ext == "docx":
        # python-docx：Document(BytesIO).paragraphs 拼接
        ...
    raise UnsupportedFormat(ext or filename)
```

**`documents.py::DocumentStore`（SQLite）**：
```sql
documents(id TEXT PRIMARY KEY, filename TEXT, size INTEGER, num_chunks INTEGER,
          chunk_ids TEXT, uploaded_at TEXT)   -- chunk_ids = JSON 数组
```
`create(doc_id, filename, size, chunk_ids)`、`list() -> [{id,filename,size,num_chunks,uploaded_at}]`、`chunk_ids(doc_id) -> list[int]`、`exists(doc_id) -> bool`、`delete(doc_id)`。

**`knowledge.py::KnowledgeService`**：
```python
class EmptyDocument(Exception): ...

class KnowledgeService:
    def __init__(self, memory, memory_store, doc_store, collection="knowledge"): ...
    async def ingest(self, filename: str, data: bytes) -> dict:
        text = parse_file(filename, data)                 # UnsupportedFormat 冒泡
        if not text.strip(): raise EmptyDocument(filename)
        doc_id = uuid4().hex
        chunk_ids = await self._memory.add_texts(
            [text], self._collection, {"source": filename, "doc_id": doc_id})
        self._doc_store.create(doc_id, filename, len(data), chunk_ids)
        return {"id": doc_id, "filename": filename, "num_chunks": len(chunk_ids)}
    def delete(self, doc_id: str) -> None:
        self._memory_store.delete(self._doc_store.chunk_ids(doc_id))
        self._doc_store.delete(doc_id)
```

**`api/documents.py::make_documents_router(service, doc_store, config)`**：
- `POST /api/documents`（`UploadFile`）：`service is None`（memory 未装配）→ **503**；超 `app_max_upload_mb` → **413**；`UnsupportedFormat` → **400**；`EmptyDocument` → **400**；成功 → 文档信息。
- `GET /api/documents` → `doc_store.list()`。
- `DELETE /api/documents/{id}`：不存在 → 404；否则 `service.delete(id)` → `{"ok": True}`。

**`assembly.py` 改**：`Harness` dataclass 增 `memory` / `memory_store` 字段；`build_harness` 在建 Memory 时把 `mem` 和其 `MemoryStore` 存进去（未装配时为 `None`）。`main.py` 据此构造 `KnowledgeService`（memory 为 None 时 service 为 None → 上传 503）。

---

## 4. 前端

- **`App.tsx`**：`BrowserRouter` + 左侧主导航（聊天 / 知识库，`NavLink`）+ `<Routes>`：`/`→`ChatPage`、`/knowledge`→`KnowledgeView`。
- **`pages/ChatPage.tsx`**：把 App-1 `App.tsx` 里的对话侧栏 + `ChatView`（含 conversation 列表状态/选择/新建/删除）整体搬进来，逻辑不变。
- **`pages/KnowledgeView.tsx`**：`<input type="file">` + 上传按钮（`FormData` POST，上传中态/错误提示）+ 文档列表（文件名 · 块数 · 时间 + 删除）+ 空态。上传/删除后刷新列表。
- **`api/client.ts`** 增：
```ts
documents: {
  list: () => fetch("/api/documents").then(r => r.json()),
  upload: (file: File) => { const fd = new FormData(); fd.append("file", file);
    return fetch("/api/documents", { method: "POST", body: fd }).then(r => { if(!r.ok) throw new Error(...); return r.json(); }); },
  remove: (id: string) => fetch(`/api/documents/${id}`, { method: "DELETE" }).then(() => undefined),
}
```

---

## 5. 配置 / 测试 / 依赖

### 配置
`app_max_upload_mb: int = 20`。

### 测试（后端 pytest，mock embedder + `:memory:`，不打网络）
- `parse_file`：`txt/md` 直读；**docx 测试内用 `python-docx` 现造**（写段落 → `BytesIO` → 解析）；未知扩展抛 `UnsupportedFormat`。（PDF：用最小 PDF 字节 fixture 或标记手动，不阻塞。）
- `DocumentStore` CRUD + `chunk_ids` JSON 往返。
- `KnowledgeService`：真 `Memory(:memory:)` + mock embedder → `ingest` 登记文档 + `search` 召回；`delete` → chunk 删、`search` 不再召回、文档消失。
- `/api/documents`（TestClient + 假文件 + 注入含 Memory 的假 harness）：上传 → 200 + 文档；`list`；`delete` → 块删；service=None → 503；未知扩展 → 400。
- 前端：Vitest 覆盖 `documents` API 客户端或 `KnowledgeView` 渲染（1 个用例）。

### 依赖
后端 `pypdf`、`python-docx`、`python-multipart`；前端 `react-router-dom`。

---

## 6. 后续衔接（非本次范围）

- **App-3**：出题 agent + 题库/错题/模拟考试；**App-4**：下载/图库。
- 后台异步解析大文件、更多格式（pptx/xlsx/html）、rerank、按文档过滤检索。
- 文档预览、重新索引、chunk 级查看。
