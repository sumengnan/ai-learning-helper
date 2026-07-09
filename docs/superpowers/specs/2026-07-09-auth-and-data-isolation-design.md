# 用户认证 + 数据隔离 — 设计规格

> 状态：已批准，待编写实现计划
> 日期：2026-07-09
> 所属：这是"账号体系 + 界面改造 + 聊天内考试"三个子项目中的**第 ①️ 个（地基）**。
> 后续子项目：② 界面壳层改造（顶部栏/改名/空态引导）、③ 聊天驱动的模拟考试 + 输入区开关。二者均依赖本子项目。

## 1. 背景与目标

当前前后端**完全没有用户概念**：FastAPI + SQLite 的各 store 全局单例、方法不带 `user_id`；前端无登录/鉴权。本子项目为整个应用打地基：

- 引入真实的后端认证（注册 / 登录），未登录不放行任何业务 API。
- 所有业务数据（对话、知识库、题库、考试、错题）**按用户隔离**。
- 前端提供登录页、注册页与路由守卫。

**非目标（YAGNI）**：邮箱 / 找回密码、角色权限、第三方登录、"记住我"、后端登出端点（无状态 token 由前端丢弃实现）。

## 2. 关键决策（已确认）

| 决策点 | 选择 |
| --- | --- |
| 认证深度 | 后端真实认证 + **按用户隔离**（完整多租户） |
| 会话机制 | **无状态签名 token**（hmac 签名，标准库实现，零新依赖） |
| Token 有效期 | 默认 **24 小时**；剩余不足 **1 小时**时自动续期 |
| 存量数据 | **直接清空重建**，新表结构直接带 `user_id` |
| 账号形态 | 用户名（字符串），无需邮箱 |
| 密码哈希 | 标准库 `hashlib.pbkdf2_hmac`（零新依赖） |

约束：`requires-python >=3.11`，**不引入新第三方依赖**（无 passlib/bcrypt/pyjwt），全部用标准库 `hashlib` / `hmac` / `secrets` / `base64`。

## 3. 架构总览

```
前端                                   后端
────────────────────────────          ──────────────────────────────
AuthProvider (token+user, localStorage)
  ├─ LoginPage / RegisterPage  ──────► POST /api/auth/register
  │                                    POST /api/auth/login
  ├─ RequireAuth 路由守卫              GET  /api/auth/me
  └─ authFetch(自动加 Authorization    
      头 + 处理 401 + 吸收             app/auth.py
      X-Refresh-Token)                  ├─ UserStore (users 表)
        │                               ├─ 密码哈希 (pbkdf2)
        ▼                               ├─ token 签名/校验/续期
   业务 API (/api/conversations ...)    └─ current_user 依赖 (FastAPI Depends)
                                              │ 注入 user_id
                                              ▼
                                        各 store 方法 WHERE user_id=?
                                        知识库向量: collection=f"knowledge:{user_id}"
```

## 4. 后端设计

### 4.1 新增 `app/auth.py`

**`UserStore`**（`users.db`，独立 sqlite 文件）
```
users(
  id           TEXT PRIMARY KEY,   -- uuid4().hex
  username     TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,     -- pbkdf2_hmac("sha256", pwd, salt, 200_000) 的 hex
  salt         TEXT NOT NULL,      -- secrets.token_hex(16)
  created_at   TEXT NOT NULL
)
```
方法：`create(username, password) -> user_id`（重名抛领域异常）、`verify(username, password) -> user_id | None`、`get(user_id) -> dict | None`、`exists_username(username) -> bool`。

**密码哈希**
- 注册：`salt = secrets.token_hex(16)`；`password_hash = pbkdf2_hmac("sha256", pwd.encode(), bytes.fromhex(salt), 200_000).hex()`。
- 校验：同法重算，用 `hmac.compare_digest` 常量时间比对。

**Token（无状态签名，类 JWT）**
- 结构：`base64url(payload_json) + "." + base64url(hmac_sha256(secret, payload_b64))`。
- `payload = {"uid": user_id, "iat": <签发 unix 秒>, "exp": iat + 86400}`。
- 签发 `issue_token(user_id)`；校验 `verify_token(token) -> user_id | None`：拆分→重算 hmac 常量时间比对→检查 `exp` 未过期。
- **secret**：`AUTH_SECRET` 环境变量；未设置时用固定开发默认值并在启动 `logging.warning` 告警（生产必须设）。
- **自动续期**：`verify_token` 额外返回"剩余秒数"；当剩余 `< 3600` 时，`current_user` 依赖签发新 token 并写入响应头 `X-Refresh-Token`。前端 `authFetch` 读到该头即替换 localStorage 中的 token。（无状态方案下的滑动续期。）

**`current_user` 依赖**
- 从 `Authorization: Bearer <token>` 取 token → `verify_token`。
- 失败（缺失/坏签名/过期）→ `HTTPException(401)`。
- 成功 → 返回 user_id；若剩余 < 1h，通过 `Response` 对象设置 `X-Refresh-Token`。

### 4.2 新增 `app/api/auth.py`

| 方法 | 路径 | 请求 | 响应 | 错误 |
| --- | --- | --- | --- | --- |
| POST | `/api/auth/register` | `{username, password}` | `{token, user:{id,username}}` | 400「账号已存在」/ 422 空字段 |
| POST | `/api/auth/login` | `{username, password}` | `{token, user:{id,username}}` | 401「账号或密码错误」 |
| GET | `/api/auth/me` | — (需 token) | `{id, username}` | 401 |

注册成功即视为登录，直接返回 token（前端无需再登录一次）。

### 4.3 业务路由改造

所有现有 `/api/*` 业务路由（conversations / chat / documents / questions / exams / wrong-answers / downloads）在处理函数签名加：
```python
user_id: str = Depends(current_user)
```
并把 `user_id` 透传给对应 store 方法。SSE 的 chat 路由同样受保护。

### 4.4 各 store / service 改造

每个业务表加 `user_id TEXT NOT NULL` 列并建索引 `(user_id, ...)`；每个读写方法在签名首位加 `user_id`，在 `WHERE` / `INSERT` 中使用。

| 单元 | 受影响方法 |
| --- | --- |
| `ConversationStore` | create / list / exists / messages / append / delete |
| `DocumentStore` | create / list / exists / chunk_ids / delete |
| `QuestionStore` | create / get / list / sample / delete / delete_many |
| `ExamStore` | create / list |
| `WrongAnswerStore` | create / list / delete / delete_many |
| `KnowledgeService` | ingest / delete / search（collection 命名空间化，见下） |
| `QuizService` | 检索走用户 collection |

**知识库向量隔离**：`harness.memory.search(collection, ...)` 按 collection 命名空间检索，**无需改动 harness 核心**。将固定的 `"knowledge"` 改为 `f"knowledge:{user_id}"`，`KnowledgeService` 与 `QuizService` 透传 `user_id` 即可。删除文档时按同一 collection 操作。

> 这是本子项目的工作量主体：机械但覆盖面广（每个 store 方法 + 每个路由注入）。边界清晰，逐单元改造 + 单元测试。

### 4.5 配置 / 清空重建

- `AppConfig` 新增：`auth_secret`（env `AUTH_SECRET`）、`users_db_path`。
- `create_app` 构建并注册 `UserStore` 与 auth 路由；`current_user` 依赖能访问到 `UserStore`（通过闭包/依赖工厂注入）。
- **清空重建**：删除现有 `conversations` / `documents` / `questions` / `exams` / `wrong_answers` 及向量库对应的 db 文件（实现时按 `AppConfig` 中的实际路径列出并删除），让新 schema（带 `user_id`）从零建立。

## 5. 前端设计

### 5.1 `AuthProvider`（新增 context）
- 状态 `{ user: {id,username} | null, token: string | null }`，token 持久化到 `localStorage("auth_token")`，启动时读取本地 token 即信任（不额外校验，保持简单）。
- 方法 `login(username, password)` / `register(username, password)` / `logout()`。
- `logout()`：清 localStorage + 重置状态 + 跳 `/login`。

### 5.2 路由结构
```
<AuthProvider>
  <Routes>
    /login     → <LoginPage>       (不套 AppShell)
    /register  → <RegisterPage>    (不套 AppShell)
    /*         → <RequireAuth><AppShell>…现有页面…</AppShell></RequireAuth>
  </Routes>
</AuthProvider>
```
`RequireAuth`：无 token → `<Navigate to="/login">`。

### 5.3 页面
- `LoginPage`：账号、密码、登录按钮、「去注册」链接。提交失败显示后端错误文案。
- `RegisterPage`：账号、密码、确认密码、注册按钮、「去登录」链接。**前端校验两次密码一致**（不一致内联报错，不发请求）。成功后自动登录并跳主页。
- 用 MUI 组件，风格与现有页面一致。

### 5.4 `api/client.ts` 改造
- 抽 `authFetch(input, init)` 包装：自动注入 `Authorization: Bearer <token>`；响应含 `X-Refresh-Token` 头则更新 localStorage；`401` → 清 token 并跳 `/login`。
- 现有 `api.*` 与 `streamChat` 全部改走 `authFetch`（SSE 的 `fetch` 也要带头与 401 处理）。

## 6. 错误处理

| 场景 | 表现 |
| --- | --- |
| 注册重名 | 后端 400「账号已存在」→ 前端表单内联提示 |
| 登录失败 | 后端 401「账号或密码错误」→ 前端表单内联提示 |
| 两次密码不一致 | 前端拦截，不发请求 |
| 空字段 | 前端 + 后端双重校验 |
| 缺失/坏/过期 token | 后端 401 → 前端清 token 跳登录 |

密码规则（最简）：非空、两次一致，长度 **≥ 6** 位。

## 7. 测试（TDD 先行）

**后端**（pytest + FastAPI `TestClient`）
- 注册 happy path 返回 token；重名 → 400；空字段 → 422。
- 登录成功 / 密码错误 → 401。
- token 签名校验：伪造/篡改 → 401；过期 → 401。
- 自动续期：剩余 < 1h 的 token 请求返回 `X-Refresh-Token`。
- 受保护路由未带 token → 401。
- **数据隔离**：用户 A 建对话，用户 B 的 `list` 看不到；错题、题库、知识库同样隔离。

**前端**（vitest + testing-library）
- 注册表单两次密码不一致时拦截并提示。
- `RequireAuth` 未登录重定向 `/login`。
- `authFetch` 注入 `Authorization` 头；遇 401 清 token 跳登录；遇 `X-Refresh-Token` 更新本地 token。

## 8. 实现顺序建议（供实现计划参考）

1. 后端 `app/auth.py`（UserStore + 哈希 + token）+ 单测。
2. `app/api/auth.py` 路由 + `AppConfig` + `create_app` 接线 + 单测。
3. 逐个 store 加 `user_id`（含知识库 collection 命名空间）+ 单测。
4. 业务路由注入 `current_user` + 隔离测试。
5. 清空重建 db。
6. 前端 `AuthProvider` + `authFetch` + 路由守卫 + 登录/注册页 + 前端测试。

## 9. 影响面小结

- **新增**：`app/auth.py`、`app/api/auth.py`、前端 `AuthProvider` / `LoginPage` / `RegisterPage` / `RequireAuth`。
- **改动**：全部 5 个业务 store + 相关 service、全部业务路由、`app/main.py`、`AppConfig`、前端 `App.tsx` / `api/client.ts`。
- **删除数据**：现有业务 db 文件（清空重建）。
- **零新第三方依赖**。
