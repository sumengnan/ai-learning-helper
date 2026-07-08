# AI 学习助手 · App-4（下载管理）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用，应用层第四个（收官）子项目
- **前置**：harness ①-③d + App-1 聊天 + App-2 知识库 + App-3 题库/考试/错题集已完成并在 `main`

---

## 0. 背景与范围

最初需求清单第 5 项「下载管理（允许列表和删除）」。做法：给 agent 一个 **app 层 `save_download` 工具**，聊天中它把整理好的笔记/导出文件/图表（文本或 base64）存进**持久下载区**（磁盘目录 + SQLite 登记）；下载管理页列出、下载、删除。图片类型顺带缩略预览，轻量满足最初「图库」诉求。

**技术选型**：沿用 App-1/2/3（FastAPI + React + react-router）；工具继承 harness `Tool` 基类但定义在 `app/`，**harness 零改动**；文件落磁盘、元数据落 SQLite。

设计通则：严格 YAGNI；文件按 `id` 落盘（原名仅作元数据 → 无路径穿越）；harness 零改动；测试用 `tmp_path` 临时目录 + `:memory:` 登记，不打网络。

---

## 1. 范围与验收

### IN
- **`DownloadStore`**：磁盘文件（按 id 落盘）+ SQLite 登记（filename/size/content_type/created_at）；create/list/get/path/delete。
- **`SaveDownloadTool`**（app 层工具）：`save_download(filename, content, encoding)`，text/base64 解码 → 超限拒绝 → 入库。
- **`/api/downloads`**：列表 / 下载（FileResponse，图片可预览）/ 删除。
- **装配小改**：`Harness` 增 `download_store` 字段；`build_harness` 注册 `save_download` 工具并暴露 store；`main.py` 挂路由。
- **前端**：`DownloadsView`（列表 + 图片缩略预览 + 下载 + 删除）；导航加「下载」。

### OUT（不做）
沙箱产物自动抓取、云存储、断点续传、在线编辑、完整相册图库（仅列表内缩略预览）、从知识库/题库/错题集导出（可后续再加）、鉴权/配额。

### 验收标准
1. agent 调 `save_download("笔记.md", "# 标题", "text")` → 文件落盘、登记入库、`GET /api/downloads` 列出（含文件名/大小/类型）。
2. `save_download` 传 base64 图片 → 解码入库，`content_type` 识别为 `image/*`。
3. base64 非法 → 工具返回错误提示串（不抛异常、不落库）。
4. 超 `download_max_mb` → 工具拒绝并返回提示（不落库）。
5. `GET /api/downloads/{id}` 返回文件本体（`Content-Type` 正确）；id 不存在→404。
6. `DELETE /api/downloads/{id}` → 磁盘文件与登记均删除；再 `GET` →404；删不存在→404。
7. 同名文件多次保存 → 各自独立 id、各自落盘，互不覆盖。
8. 前端下载页列出文件、图片显缩略图、可下载、可删除；聊天/知识库/题库/考试/错题集页不受影响。
9. harness 零改动；harness + App-1/2/3 测试不回归。

---

## 2. 架构与模块

**设计取向**：`DownloadStore` 是唯一同时管磁盘与登记的单元；工具与 API 都只经它读写。文件名从不参与磁盘路径拼接（按 `id` 落盘），杜绝路径穿越。

```
app/
├── downloads.py             [新] DownloadStore
├── tools/
│   ├── __init__.py          [新] （空，标记包）
│   └── save_download.py     [新] SaveDownloadTool（继承 harness Tool）
├── assembly.py              [改] Harness 增 download_store；build_harness 注册工具 + 暴露 store
├── config.py                [改] downloads_dir / downloads_db_path / download_max_mb
├── main.py                  [改] 挂 /api/downloads 路由
└── api/downloads.py         [新] make_downloads_router(download_store)
web/src/
├── App.tsx                  [改] 导航加「下载 /downloads」
├── pages/DownloadsView.tsx  [新] 列表 + 缩略预览 + 下载 + 删除
└── api/client.ts            [改] downloads API
```

**依赖新增**：无（FastAPI 自带 `FileResponse`；`mimetypes`/`base64` 标准库）。harness 不加依赖不改代码。

**数据流**：
```
保存：save_download(filename, content, encoding)
 → 解码 bytes（超限/非法则返回错误串）→ DownloadStore.create(filename, data, content_type)
   → 写 downloads/{id} + INSERT 登记 → 返回 {id, filename, size}
下载：GET /api/downloads/{id} → DownloadStore.get → FileResponse(path, media_type, filename)
删除：DELETE /api/downloads/{id} → DownloadStore.delete(id) → os.remove(path) + DELETE 登记
```

---

## 3. 后端

### 3.1 `downloads.py::DownloadStore`
```sql
downloads(id TEXT PRIMARY KEY, filename TEXT, size INTEGER,
          content_type TEXT, created_at TEXT)
```
```python
class DownloadStore:
    def __init__(self, files_dir: str, db_path: str): ...   # mkdir files_dir；建表
    def create(self, filename: str, data: bytes, content_type: str) -> dict:
        # id=uuid4().hex；写 files_dir/{id}；INSERT；返回 {id, filename, size, content_type, created_at}
    def list(self) -> list[dict]:                            # 按时间倒序（用 seq 稳定）
    def get(self, did: str) -> dict | None:                  # 含 path
    def path(self, did: str) -> str:                         # files_dir/{did}
    def delete(self, did: str) -> bool:                      # 删登记+文件；返回是否存在
```
- `list`/登记倒序用自增 `seq` 列（同 App-3 ExamStore，避免同秒时间戳排序不稳）。
- `delete`：先查存在性（返回 bool 供 API 判 404）；删文件用 `os.path.exists` 守卫，缺文件不报错。

### 3.2 `tools/save_download.py::SaveDownloadTool`
```python
from harness.tools.base import Tool
from pydantic import BaseModel

class SaveDownloadTool(Tool):
    name = "save_download"
    description = "把整理好的内容保存为可下载文件（笔记/导出/图表）。content 为文本；若是图片等二进制，encoding 传 base64。"

    class Params(BaseModel):
        filename: str
        content: str
        encoding: str = "text"        # "text" | "base64"

    def __init__(self, download_store, max_bytes: int): ...

    async def run(self, params) -> str:
        # encoding=="base64": 尝试 base64.b64decode(validate=True)，失败返回「保存失败：内容不是合法 base64」
        # 否则 content.encode("utf-8")
        # len(data) > max_bytes → 返回「保存失败：超过 N MB 上限」
        # content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        # rec = download_store.create(filename, data, content_type)
        # 返回「已保存到下载区：{filename}（{size} 字节）」
```
- run **不抛异常**：解码/超限失败都返回给 agent 的错误串（工具协议是返回 str）。

### 3.3 `api/downloads.py::make_downloads_router(download_store)`
- `GET /api/downloads` → `download_store.list()`。
- `GET /api/downloads/{did}` → `rec = get(did)`；None→404；否则 `FileResponse(rec["path"], media_type=rec["content_type"], filename=rec["filename"])`。
  - 用 `FileResponse` 的 `filename` 参数（Starlette 负责 Content-Disposition 与文件名编码）；图片子资源 `<img>` 仍能预览。
- `DELETE /api/downloads/{did}` → `delete(did)` 返回 False→404；否则 `{"ok": True}`。

### 3.4 `assembly.py` 改
- `Harness` dataclass 增 `download_store: object | None = None`。
- `build_harness`：`from app.downloads import DownloadStore` 相对导入（`from .downloads import DownloadStore`）+ `from .tools.save_download import SaveDownloadTool`；建 `dstore = DownloadStore(config.downloads_dir, config.downloads_db_path)`；`_reg(SaveDownloadTool(dstore, config.download_max_mb * 1024 * 1024))`（**始终注册**，是应用核心能力）；`return Harness(..., download_store=dstore)`。

### 3.5 `main.py` 改
- import `make_downloads_router`；`app.include_router(make_downloads_router(harness.download_store))`（`build_harness` 已保证 `download_store` 非空；若外部注入的 harness 没有该字段，用 `getattr(harness, "download_store", None)`，为 None 时不挂路由或挂一个返回空列表的——**取 None 时跳过挂载**，与 memory 处理一致）。

### 3.6 `config.py` 改
`downloads_dir: str = "downloads"`、`downloads_db_path: str = "downloads.db"`、`download_max_mb: int = 25`。

---

## 4. 前端

- **`App.tsx`**：导航加 `下载 /downloads`（`NavLink`）；`<Routes>` 加 `/downloads`→`DownloadsView`。
- **`pages/DownloadsView.tsx`**：加载 `GET /api/downloads` 列表；每项显示文件名 · 大小 · 类型 · 时间；`content_type` 以 `image/` 开头时用 `<img src="/api/downloads/{id}">` 显示缩略图；「下载」用 `<a href="/api/downloads/{id}" download={filename}>`；「删除」调 DELETE 后刷新。空态提示「聊天中让助手用 save_download 保存文件后会出现在这里」。
- **`api/client.ts`** 增：
```ts
downloads: {
  list: () => fetch("/api/downloads").then(r => r.json()),
  remove: (id: string) => fetch(`/api/downloads/${id}`, { method: "DELETE" }).then(() => undefined),
  // 下载/预览直接用 URL /api/downloads/{id}，不经此对象
}
```

---

## 5. 配置 / 测试 / 依赖

### 配置
`downloads_dir`（默认 `downloads`）、`downloads_db_path`（默认 `downloads.db`）、`download_max_mb: int = 25`。

### 测试（后端 pytest，`tmp_path` + `:memory:`，不打网络）
- `DownloadStore`：create 落盘（`os.path.exists`）+ 登记往返；list 倒序；get 含 path；delete 删文件+登记并返回 bool；同名两次→两 id 两文件不覆盖。
- `SaveDownloadTool`：text 入库、base64 图片入库（content_type=image/*）、base64 非法→错误串不落库、超限→错误串不落库。
- `/api/downloads`（TestClient + 注入含 `download_store` 的假 harness）：list；下载 200 + Content-Type + 404；delete 删文件+登记 + 404。
- 前端：Vitest 覆盖 `DownloadsView` 渲染或删除（1 用例）。

### 依赖
无新增。

---

## 6. 后续衔接（非本次范围）
- 从知识库/题库/错题集一键导出到下载区；完整图库相册视图；沙箱执行产物自动收集；分类/搜索/标签。
