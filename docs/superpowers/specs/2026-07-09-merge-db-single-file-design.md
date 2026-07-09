# 合并应用领域数据库为单文件 + 统一迁移入口 — 设计规格

日期：2026-07-09
状态：已批准设计，待编写实现计划

## 背景与目标

当前 app 层把每个领域拆成独立的 SQLite 文件（`users.db` / `conversations.db` /
`documents.db` / `questions.db` / `wrong_answers.db` / `downloads.db`），每个
Store 各自 `sqlite3.connect(path)`、各自 `CREATE TABLE`、各自补列。这带来的实际
痛点是：**一次 schema 变更要改多个文件**（例如新增 `user_id` 列时的 500，需要在
5 个 Store 里各补一遍）。

本设计将这 **6 个应用领域库合并为一个可配置路径的 `app.db`**，并提供**统一的
迁移入口**，让以后的表结构变更集中在一处。

### 成功标准

- 6 个领域的表存放于同一个 SQLite 文件，路径可通过配置/环境变量指定。
- 存在唯一的 `migrate()` 入口，集中所有建表与列迁移 DDL；新增字段只改这一处。
- 生产运行时，5 个应用 Store 共享同一个数据库连接。
- app 层与 harness 装配层保持解耦：不把 app 的共享连接穿透进 `build_harness`。
- 现有测试基本零改动即可通过。

## 范围

### 纳入（合并进 `app.db`）

`users` / `conversations` / `conversation_messages` / `documents` / `questions` /
`wrong_answers` / `downloads` 七张表（分属 6 个 Store）。

### 排除（不动）

- harness 的 `memory`（sqlite-vec 向量库，依赖扩展，性质特殊）。
- harness 的 `persistence`（`trajectory` + `checkpoint`，本就已共享一个文件）。
- 不做数据迁移（见下）。
- 不新增跨领域外键（当前无此需求，YAGNI）。
- `exams_db_path`：本就无代码使用，随本次一并删除配置项。

## 设计

### 1. 新增 `app/db.py` —— 单一数据层入口

- `open_db(path) -> sqlite3.Connection`
  - `sqlite3.connect(path, check_same_thread=False)`；
  - `PRAGMA journal_mode=WAL`（允许同文件多连接并发读写，供 DownloadStore 的第二条
    连接场景）；
  - 返回连接。
- `migrate(conn) -> None`
  - 集中所有 6 个领域的建表 DDL（`CREATE TABLE IF NOT EXISTS`），字段自建表起即
    包含 `user_id`；
  - 幂等：可安全重复调用；
  - 保留 `ensure_columns(conn, table, cols)` 辅助函数（由现有 `app/_migrations.py`
    并入本模块）以备将来补列；
  - 用 `PRAGMA user_version` 打版本戳，为未来有序迁移留钩子。

这就是"统一迁移入口"：以后加字段/改表只改 `migrate()`。

### 2. Store 双构造（6 个 Store）

签名统一为 `__init__(self, db_path: str | None = None, *, conn: Connection | None = None)`
（`DownloadStore` 为 `__init__(self, files_dir, db_path=None, *, conn=None)`）：

- 传 `conn` → 直接复用该共享连接，**不再自建表**（由 `migrate()` 负责）；
- 传 `db_path` → 内部 `open_db(db_path)` 并 `migrate()` 后使用（供独立运行 / 测试）。

要点：

- 各 Store 的查询方法与连接属性名（现状 `self._conn` 或 `self._db`）保持不变，只改
  `__init__` 中连接的来源；`WrongAnswerStore` / `DownloadStore` 的 `self._seq`
  记账逻辑（`SELECT MAX(seq)`）照旧，在 `migrate()` 建表之后执行。
- **保留位置参数 `db_path`**，因此现有测试里的 `Store(tmp_path)` 位置调用无需改动；
  生产侧改用关键字 `conn=`。

### 3. `app/config.py`

- 新增 `app_db_path: str = "app.db"`（受 `env_prefix="HARNESS_"` 约束，可用
  `HARNESS_APP_DB_PATH` 覆盖，满足"允许配置 db 文件路径"）。
- 删除已无用的路径项：`users_db_path` / `conversations_db_path` /
  `documents_db_path` / `questions_db_path` / `exams_db_path` /
  `wrong_answers_db_path` / `downloads_db_path`。
- 保留 `downloads_dir`（下载文件落盘目录，非数据库）、`persistence_db_path`、
  `memory_db_path`。

### 4. 装配 —— 保持 app / harness 解耦

- `main.py`（app 层）：启动时 `conn = open_db(config.app_db_path); migrate(conn)`
  一次，把这**一个共享 `conn`** 以 `conn=` 传给
  `ConversationStore` / `DocumentStore` / `QuestionStore` / `WrongAnswerStore` /
  `UserStore`。
- `assembly.py`（harness 装配层）：`DownloadStore` 仍在此创建（它需接到
  `save_download` 工具，归属合理），但**只从 config 取路径**：
  `DownloadStore(config.downloads_dir, db_path=config.app_db_path)`。它凭双构造
  **自己开一条到 `app.db` 的连接**，装配层完全不接触也不依赖 app 层的共享连接。
- 结果：`app.db` 上存在两条连接（5 Store 的共享连接 + DownloadStore 自己的一条），
  已开 WAL，功能无碍。这点"非纯单连接"是为换取 app 层与 harness 装配层不耦合的
  刻意取舍。

### 5. 数据处理 —— 空库重来

- 不迁移旧数据。首次运行生成全新的空 `app.db`。
- 旧的 6 个 `.db` 属 gitignore 的开发文件，本次不自动删除；文档提示用户可自行清理。

## 错误处理

- `migrate()` 幂等，重复启动安全。
- WAL 模式确保同文件多连接的读写正确性。
- 表名跨领域互不冲突（已核对：7 张表命名唯一），合并到单文件无碰撞。

## 测试策略

- 现有 `tests/app/` 下约 15 个测试文件通过位置参数 `db_path` 构造 Store，双构造
  向后兼容，**预期零改动通过**。
- 每个 Store 在 `db_path` 分支会调用 `migrate()`，故独立构造时该临时文件含完整 schema
  （多出的其它领域空表无害）。
- 验证命令：后端 `pytest`（重点 `tests/app/`）+ 现有前端测试保持通过。

## 影响文件一览

- 新增：`app/db.py`（含 `open_db` / `migrate` / `ensure_columns`）。
- 删除或并入：`app/_migrations.py`（`ensure_columns` 迁入 `app/db.py`）。
- 修改：`app/conversations.py`、`app/documents.py`、`app/questions.py`、
  `app/wrong_answers.py`、`app/downloads.py`、`app/auth.py`(UserStore)、
  `app/config.py`、`app/main.py`、`app/assembly.py`。

## 明确不做（YAGNI）

- 不触碰 harness memory（sqlite-vec）与 persistence。
- 不做旧数据迁移。
- 不加跨领域外键。
- 不把 DownloadStore 迁出 harness 装配（属无关重构）。
