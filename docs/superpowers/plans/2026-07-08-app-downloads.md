> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# App-4 下载管理 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** agent 用 `save_download` 工具把聊天产出（文本/base64）存进持久下载区；下载管理页列表 / 下载 / 删除，图片缩略预览。

**架构：** flat `app/*.py` + 一 store 一 api。`DownloadStore`（文件按 id 落磁盘 + SQLite 登记）+ app 层 `SaveDownloadTool`（继承 harness `Tool`，`build_harness` 注册）+ `/api/downloads`（`FileResponse` 下载）+ React 下载页。**harness 零改动**。

**技术栈：** Python 3.11+ · FastAPI（`FileResponse`）· pydantic v2 · pytest · React + react-router + Vitest。标准库 `base64`/`mimetypes`/`sqlite3`。

**规格：** `docs/superpowers/specs/2026-07-08-app-downloads-design.md`

**关键约定：**
- 提交署名必须 `sumengnan`（`git -c user.name=sumengnan -c user.email=2499165351@qq.com commit`），提交信息**禁止** Claude/AI/Co-Authored/Generated/anthropic。
- harness（`src/harness/`）零改动。
- 测试用 `tmp_path`（磁盘目录）+ `:memory:`（登记），不打网络。
- 文件按 `id` 落盘，`filename` 仅元数据 → 无路径穿越。

---

### 任务 1：配置项

**文件：** 修改 `app/config.py`

- [ ] **步骤 1：在 `AppConfig` 末尾（`short_pass_score` 之后）追加**

```python
    downloads_dir: str = "downloads"
    downloads_db_path: str = "downloads.db"
    download_max_mb: int = 25
```

- [ ] **步骤 2：Commit**

```bash
git add app/config.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "chore: App-4 下载管理配置项"
```

---

### 任务 2：DownloadStore

**文件：** 创建 `app/downloads.py`；测试 `tests/app/test_downloads.py`

- [ ] **步骤 1：编写失败的测试**

`tests/app/test_downloads.py`：

```python
import os
from app.downloads import DownloadStore


def _store(tmp_path):
    return DownloadStore(str(tmp_path / "files"), ":memory:")


def test_create_writes_file_and_registers(tmp_path):
    s = _store(tmp_path)
    rec = s.create("笔记.md", b"# hi", "text/markdown")
    assert rec["filename"] == "笔记.md" and rec["size"] == 4
    assert rec["content_type"] == "text/markdown"
    assert os.path.exists(s.path(rec["id"]))                 # 落盘
    with open(s.path(rec["id"]), "rb") as f:
        assert f.read() == b"# hi"


def test_list_newest_first(tmp_path):
    s = _store(tmp_path)
    a = s.create("a.txt", b"a", "text/plain")["id"]
    b = s.create("b.txt", b"b", "text/plain")["id"]
    assert [r["id"] for r in s.list()] == [b, a]             # 倒序


def test_get_includes_path_and_none_missing(tmp_path):
    s = _store(tmp_path)
    rid = s.create("x.txt", b"x", "text/plain")["id"]
    got = s.get(rid)
    assert got["path"] == s.path(rid) and got["filename"] == "x.txt"
    assert s.get("nope") is None


def test_delete_removes_file_and_row(tmp_path):
    s = _store(tmp_path)
    rid = s.create("x.txt", b"x", "text/plain")["id"]
    p = s.path(rid)
    assert s.delete(rid) is True
    assert not os.path.exists(p)                             # 文件删了
    assert s.get(rid) is None                                # 登记删了
    assert s.delete(rid) is False                            # 再删不存在


def test_same_name_no_overwrite(tmp_path):
    s = _store(tmp_path)
    a = s.create("同名.txt", b"AAAA", "text/plain")
    b = s.create("同名.txt", b"BB", "text/plain")
    assert a["id"] != b["id"]                                # 各自独立 id
    with open(s.path(a["id"]), "rb") as f: assert f.read() == b"AAAA"
    with open(s.path(b["id"]), "rb") as f: assert f.read() == b"BB"
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/app/test_downloads.py -q`
预期：FAIL（`ModuleNotFoundError: app.downloads`）

- [ ] **步骤 3：实现 `app/downloads.py`**

```python
# app/downloads.py
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DownloadStore:
    def __init__(self, files_dir: str, db_path: str) -> None:
        self._dir = files_dir
        os.makedirs(files_dir, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS downloads(
                 id TEXT PRIMARY KEY, filename TEXT, size INTEGER,
                 content_type TEXT, created_at TEXT, seq INTEGER)""")
        self._db.commit()
        self._seq = self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM downloads").fetchone()[0]

    def create(self, filename: str, data: bytes, content_type: str) -> dict:
        did = uuid4().hex
        with open(os.path.join(self._dir, did), "wb") as f:
            f.write(data)
        self._seq += 1
        self._db.execute(
            "INSERT INTO downloads VALUES (?,?,?,?,?,?)",
            (did, filename, len(data), content_type, _now(), self._seq))
        self._db.commit()
        return {"id": did, "filename": filename, "size": len(data),
                "content_type": content_type}

    def _row(self, r) -> dict:
        return {"id": r[0], "filename": r[1], "size": r[2],
                "content_type": r[3], "created_at": r[4]}

    def list(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id,filename,size,content_type,created_at "
            "FROM downloads ORDER BY seq DESC").fetchall()
        return [self._row(r) for r in rows]

    def get(self, did: str) -> dict | None:
        r = self._db.execute(
            "SELECT id,filename,size,content_type,created_at "
            "FROM downloads WHERE id=?", (did,)).fetchone()
        if not r:
            return None
        d = self._row(r)
        d["path"] = self.path(did)
        return d

    def path(self, did: str) -> str:
        return os.path.join(self._dir, did)

    def delete(self, did: str) -> bool:
        r = self._db.execute("SELECT id FROM downloads WHERE id=?", (did,)).fetchone()
        if not r:
            return False
        p = self.path(did)
        if os.path.exists(p):
            os.remove(p)
        self._db.execute("DELETE FROM downloads WHERE id=?", (did,))
        self._db.commit()
        return True
```

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/app/test_downloads.py -q`
预期：PASS（5 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/downloads.py tests/app/test_downloads.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: DownloadStore 磁盘文件 + SQLite 登记"
```

---

### 任务 3：SaveDownloadTool（app 层工具）

**文件：** 创建 `app/tools/__init__.py`（空）、`app/tools/save_download.py`；测试 `tests/app/test_save_download.py`

- [ ] **步骤 1：编写失败的测试**

`tests/app/test_save_download.py`：

```python
import base64
import pytest
from app.downloads import DownloadStore
from app.tools.save_download import SaveDownloadTool


def _tool(tmp_path, max_mb=25):
    store = DownloadStore(str(tmp_path / "f"), ":memory:")
    return SaveDownloadTool(store, max_mb * 1024 * 1024), store


@pytest.mark.asyncio
async def test_save_text(tmp_path):
    tool, store = _tool(tmp_path)
    out = await tool.run(tool.Params(filename="note.md", content="# 标题"))
    assert "已保存" in out
    lst = store.list()
    assert len(lst) == 1 and lst[0]["filename"] == "note.md"
    assert lst[0]["content_type"] == "text/markdown"


@pytest.mark.asyncio
async def test_save_base64_image(tmp_path):
    tool, store = _tool(tmp_path)
    b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n fake png").decode()
    out = await tool.run(tool.Params(filename="chart.png", content=b64, encoding="base64"))
    assert "已保存" in out
    assert store.list()[0]["content_type"] == "image/png"      # mimetypes 识别


@pytest.mark.asyncio
async def test_bad_base64_returns_error_no_store(tmp_path):
    tool, store = _tool(tmp_path)
    out = await tool.run(tool.Params(filename="x.png", content="不是base64!!!", encoding="base64"))
    assert "失败" in out and store.list() == []                 # 未落库


@pytest.mark.asyncio
async def test_oversize_returns_error_no_store(tmp_path):
    tool, store = _tool(tmp_path, max_mb=0)                     # 0MB 上限 → 任何内容都超
    out = await tool.run(tool.Params(filename="big.txt", content="hello"))
    assert "失败" in out and "上限" in out and store.list() == []
```

> 说明：`test_oversize` 用 `max_mb=0` 使上限为 0 字节，`"hello"`(5 字节) 即超限，稳定触发拒绝路径。

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/app/test_save_download.py -q`
预期：FAIL（`ModuleNotFoundError: app.tools.save_download`）

- [ ] **步骤 3：实现工具**

创建空文件 `app/tools/__init__.py`（无内容）。

`app/tools/save_download.py`：

```python
# app/tools/save_download.py
from __future__ import annotations

import base64
import binascii
import mimetypes

from pydantic import BaseModel

from harness.tools.base import Tool


class SaveDownloadTool(Tool):
    name = "save_download"
    description = (
        "把整理好的内容保存为可下载文件（笔记/导出/图表）。"
        "content 为文本内容；若要保存图片等二进制，先把它 base64 编码并令 encoding=base64。")

    class Params(BaseModel):
        filename: str
        content: str
        encoding: str = "text"        # "text" | "base64"

    def __init__(self, download_store, max_bytes: int) -> None:
        self._store = download_store
        self._max = max_bytes

    async def run(self, params: "SaveDownloadTool.Params") -> str:
        if params.encoding == "base64":
            try:
                data = base64.b64decode(params.content, validate=True)
            except (binascii.Error, ValueError):
                return "保存失败：内容不是合法 base64。"
        else:
            data = params.content.encode("utf-8")
        if len(data) > self._max:
            return f"保存失败：超过 {self._max // (1024 * 1024)}MB 上限。"
        content_type = mimetypes.guess_type(params.filename)[0] or "application/octet-stream"
        rec = self._store.create(params.filename, data, content_type)
        return f"已保存到下载区：{rec['filename']}（{rec['size']} 字节）。"
```

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/app/test_save_download.py -q`
预期：PASS（4 passed）

- [ ] **步骤 5：Commit**

```bash
git add app/tools/__init__.py app/tools/save_download.py tests/app/test_save_download.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: SaveDownloadTool app 层工具（text/base64 存下载区）"
```

---

### 任务 4：API 路由 + 装配 + 集成测试

**文件：** 创建 `app/api/downloads.py`；修改 `app/assembly.py`、`app/main.py`、`tests/app/test_assembly.py`；测试 `tests/app/test_downloads_api.py`

- [ ] **步骤 1：实现 `app/api/downloads.py`**

```python
# app/api/downloads.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


def make_downloads_router(download_store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/downloads")
    async def list_downloads():
        return download_store.list()

    @router.get("/api/downloads/{did}")
    async def get_download(did: str):
        rec = download_store.get(did)
        if rec is None:
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(rec["path"], media_type=rec["content_type"],
                            filename=rec["filename"])

    @router.delete("/api/downloads/{did}")
    async def delete_download(did: str):
        if not download_store.delete(did):
            raise HTTPException(status_code=404, detail="文件不存在")
        return {"ok": True}

    return router
```

- [ ] **步骤 2：改 `app/assembly.py`**

`Harness` dataclass 增字段（在 `memory_store` 之后）：

```python
    download_store: object | None = None
```

`build_harness` 里，在 `traj = TrajectoryStore(...)` **之前**加装配（工具始终注册，是应用核心能力）：

```python
    from .downloads import DownloadStore
    from .tools.save_download import SaveDownloadTool
    dstore = DownloadStore(config.downloads_dir, config.downloads_db_path)
    _reg(SaveDownloadTool(dstore, config.download_max_mb * 1024 * 1024))
```

并把 `return Harness(...)` 改为传入 `download_store=dstore`：

```python
    return Harness(
        client=client, registry=reg,
        checkpoint_store=CheckpointStore(config.persistence_db_path),
        trajectory_store=traj, sink=TrajectorySink(traj),
        system_prompt=config.app_system_prompt,
        memory=memory, memory_store=memory_store, download_store=dstore)
```

- [ ] **步骤 3：改 `app/main.py`**

import 增 `from .api.downloads import make_downloads_router`。在 documents 路由挂载之后加（`build_harness` 保证非空；外部注入的 harness 可能无此字段，故用 getattr 守卫）：

```python
    dstore = getattr(harness, "download_store", None)
    if dstore is not None:
        app.include_router(make_downloads_router(dstore))
```

- [ ] **步骤 4：改 `tests/app/test_assembly.py` 的 `_cfg`，避免 build_harness 在 cwd 落下 downloads 目录/库**

把文件顶部与 `_cfg` 改为（加 `tempfile` 临时目录 + `:memory:` 登记）：

```python
import tempfile
from app.config import AppConfig
from app.assembly import build_harness, Harness

_DL_DIR = tempfile.mkdtemp()


def _cfg(**kw):
    return AppConfig(api_key="k", persistence_db_path=":memory:", memory_db_path=":memory:",
                     downloads_dir=_DL_DIR, downloads_db_path=":memory:", **kw)
```

并在文件末尾追加一条断言 `save_download` 注册 + `download_store` 暴露的测试：

```python
def test_build_harness_registers_save_download():
    h = build_harness(_cfg())
    assert h.registry.get("save_download") is not None
    assert h.download_store is not None
```

- [ ] **步骤 5：编写集成测试 `tests/app/test_downloads_api.py`**

```python
import sqlite3
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import AppConfig
from app.assembly import Harness
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.downloads import DownloadStore
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


def _cfg():
    return AppConfig(api_key="k", questions_db_path=":memory:",
                     exams_db_path=":memory:", wrong_answers_db_path=":memory:")


def _client(tmp_path):
    dstore = DownloadStore(str(tmp_path / "dl"), ":memory:")
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=None, registry=ToolRegistry(),
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj),
                      system_prompt="s", download_store=dstore)
    app = create_app(config=_cfg(), harness=harness,
                     store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"),
                     question_store=None, exam_store=None, wrong_store=None, quiz_service=None)
    # question/exam/wrong 传 None → create_app 内部用默认 :memory:（见 _cfg）
    return TestClient(app), dstore


def test_list_and_download(tmp_path):
    client, dstore = _client(tmp_path)
    rec = dstore.create("hello.txt", b"hello world", "text/plain")
    listing = client.get("/api/downloads").json()
    assert any(d["id"] == rec["id"] for d in listing)
    r = client.get(f"/api/downloads/{rec['id']}")
    assert r.status_code == 200 and r.content == b"hello world"
    assert r.headers["content-type"].startswith("text/plain")


def test_download_404(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/downloads/nope").status_code == 404


def test_delete_removes_file_and_row(tmp_path):
    import os
    client, dstore = _client(tmp_path)
    rec = dstore.create("x.txt", b"x", "text/plain")
    path = dstore.path(rec["id"])
    assert client.delete(f"/api/downloads/{rec['id']}").status_code == 200
    assert not os.path.exists(path)
    assert client.get(f"/api/downloads/{rec['id']}").status_code == 404
    assert client.delete(f"/api/downloads/{rec['id']}").status_code == 404
```

> 注：`create_app` 当前签名为
> `create_app(config, harness, store, doc_store, question_store, exam_store, wrong_store, quiz_service)`
> （App-3 已扩展）。本测试给 quiz 相关参数传 `None`，配合 `_cfg()` 的 `:memory:` 让 create_app 内部构造内存 store，不落库文件。若签名与此不符，先 Read `app/main.py` 核对再调整调用。

- [ ] **步骤 6：运行验证**

运行：`uv run pytest tests/app/test_downloads_api.py tests/app/test_assembly.py -q`
预期：PASS（含 3 个 downloads_api + assembly 全绿）

- [ ] **步骤 7：全量后端回归**

运行：`uv run pytest -q`
预期：全绿（App-1/2/3 + harness 不回归）

运行：`ls *.db downloads 2>/dev/null || echo "无 stray 落盘"`
预期：cwd 无 stray `.db` 或 `downloads/` 目录（测试都用 tmp/:memory:）

- [ ] **步骤 8：Commit**

```bash
git add app/api/downloads.py app/assembly.py app/main.py tests/app/test_assembly.py tests/app/test_downloads_api.py
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: /api/downloads 路由 + build_harness 注册工具 + 集成测试"
```

---

### 任务 5：前端下载页

**文件：** 修改 `web/src/api/client.ts`、`web/src/App.tsx`；创建 `web/src/pages/DownloadsView.tsx`、`web/src/pages/DownloadsView.test.tsx`

- [ ] **步骤 1：在 `client.ts` 的 `api` 对象追加 `downloads`**

```ts
  downloads: {
    list: () => fetch("/api/downloads").then((r) => r.json()),
    remove: (id: string) =>
      fetch(`/api/downloads/${id}`, { method: "DELETE" }).then(() => undefined),
  },
```

- [ ] **步骤 2：编写失败的 Vitest**

`web/src/pages/DownloadsView.test.tsx`：

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import DownloadsView from "./DownloadsView";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { downloads: { list: vi.fn(), remove: vi.fn() } },
}));

describe("DownloadsView", () => {
  beforeEach(() => vi.clearAllMocks());

  it("渲染下载列表", async () => {
    (api.downloads.list as any).mockResolvedValue([
      { id: "1", filename: "笔记.md", size: 12, content_type: "text/markdown", created_at: "2026-07-08" },
    ]);
    render(<DownloadsView />);
    await waitFor(() => expect(screen.getByText(/笔记\.md/)).toBeTruthy());
  });
});
```

> 注：本仓库未装 jest-dom，断言用 `.toBeTruthy()`（与 `KnowledgeView.test.tsx` 一致），勿用 `.toBeInTheDocument()`。DownloadsView 不含 `<Link>`，无需 `MemoryRouter` 包裹。

- [ ] **步骤 3：运行验证失败**

运行：`cd web && npx vitest run src/pages/DownloadsView.test.tsx; cd ..`
预期：FAIL（找不到 `./DownloadsView`）

- [ ] **步骤 4：实现 `web/src/pages/DownloadsView.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Download {
  id: string;
  filename: string;
  size: number;
  content_type: string;
  created_at: string;
}

export default function DownloadsView() {
  const [items, setItems] = useState<Download[]>([]);

  const refresh = () => api.downloads.list().then(setItems);
  useEffect(() => { refresh(); }, []);

  const remove = async (id: string) => {
    await api.downloads.remove(id);
    await refresh();
  };

  return (
    <div className="p-6 space-y-3">
      <h2 className="text-xl font-bold">下载管理</h2>
      {items.length === 0 ? (
        <p className="text-gray-500">暂无文件。聊天中让助手用 save_download 保存内容后会出现在这里。</p>
      ) : (
        <ul className="space-y-2">
          {items.map((d) => (
            <li key={d.id} className="border rounded p-3 flex justify-between items-center gap-3">
              <div className="flex items-center gap-3 min-w-0">
                {d.content_type.startsWith("image/") && (
                  <img src={`/api/downloads/${d.id}`} alt={d.filename}
                    className="w-12 h-12 object-cover rounded border" />
                )}
                <div className="min-w-0">
                  <div className="truncate">{d.filename}</div>
                  <div className="text-xs text-gray-400">
                    {d.content_type} · {d.size} 字节 · {d.created_at.slice(0, 10)}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <a href={`/api/downloads/${d.id}`} download={d.filename}
                  className="text-blue-600 text-sm">下载</a>
                <button onClick={() => remove(d.id)} className="text-red-600 text-sm">删除</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **步骤 5：改 `web/src/App.tsx` 导航 + 路由**

顶部 import：`import DownloadsView from "./pages/DownloadsView";`
导航数组末尾追加一项 `["/downloads", "下载"]`（保留现有「聊天/知识库/题库/考试/错题集」）。
`<Routes>` 内追加：`<Route path="/downloads" element={<DownloadsView />} />`。

- [ ] **步骤 6：运行验证通过**

运行：`cd web && npx vitest run && npx tsc --noEmit; cd ..`
预期：全部 Vitest PASS + tsc 无输出

- [ ] **步骤 7：Commit**

```bash
git add web/src/api/client.ts web/src/App.tsx web/src/pages/DownloadsView.tsx web/src/pages/DownloadsView.test.tsx
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "feat: 前端下载管理页（列表/图片预览/下载/删除）"
```

---

### 任务 6：README + 最终回归

**文件：** 修改 `app/README.md`

- [ ] **步骤 1：在 README 增 App-4 段**

在 App-3 说明之后加「下载管理」小节：agent 用 `save_download(filename, content, encoding)` 把笔记/导出/图表（文本或 base64）存进下载区；下载页列表 / 图片缩略预览 / 下载 / 删除；`download_max_mb` 上限；相关：`downloads/` 目录 + `downloads.db`。

- [ ] **步骤 2：最终回归**

运行：`uv run pytest -q`
预期：后端全绿（约 245+ passed）

运行：`cd web && npx vitest run && npx tsc --noEmit; cd ..`
预期：前端全绿 + tsc 无输出

运行：`git diff --stat origin/main -- src/harness/`
预期：**空**（harness 零改动）

运行：`ls *.db downloads 2>/dev/null || echo "无 stray 落盘"`
预期：cwd 无 stray 文件

- [ ] **步骤 3：Commit**

```bash
git add app/README.md
git -c user.name=sumengnan -c user.email=2499165351@qq.com commit -m "docs: App-4 下载管理运行说明"
```

---

## 自检结论

**规格覆盖度：** DownloadStore（磁盘+登记）→任务 2；SaveDownloadTool（text/base64/超限/非法）→任务 3；/api/downloads（列表/下载/删除/404）→任务 4；装配注册工具 + Harness 暴露 store→任务 4；main 挂载→任务 4；前端页+导航→任务 5；配置→任务 1；harness 零改动→全程不碰 src/harness/，任务 6 校验。验收 1-9 全覆盖（1→任务3+4 list、2→test_save_base64_image、3→test_bad_base64、4→test_oversize、5→test_list_and_download、6→test_delete_removes_file_and_row、7→test_same_name_no_overwrite、8→前端 + DownloadsView.test、9→任务6 回归+harness diff）。

**占位符扫描：** 无 TODO/待定。所有步骤含完整代码或精确改动说明。

**类型一致性：** `DownloadStore`（create/list/get/path/delete，create 返回含 id/filename/size/content_type，get 额外含 path）在 store/tool/api/测试间一致；`SaveDownloadTool(download_store, max_bytes)`、`make_downloads_router(download_store)`、`Harness.download_store`、`build_harness` 注册与 `main.py` getattr 挂载一致；前端 `Download` 接口字段与后端 list 输出一致（id/filename/size/content_type/created_at）。

## 执行交接

计划已完成。将用 **subagent-driven-development** 逐任务执行（实现→规格审查→代码质量审查），全部通过后推送。
