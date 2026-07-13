# 题库页面优化设计

日期：2026-07-13
状态：已确认，待实现

## 背景与目标

现有「题库」页（`web/src/pages/QuestionBankView.tsx`）功能单薄：顶部是一块「从知识库检索出题」的表单，下方是只显示题型+题干+来源的朴素列表，仅支持单条删除。没有答案展示、没有详情、没有筛选、没有批量操作、无法上传导入。

本次优化目标：移除页面内的「出题」入口，把题库页做成一个可用的题目管理界面——列表更丰富、可查询、可批量删除、有详情、可上传文件智能导入题目，并在所有新增路径上防止重复入库。

已与用户确认的关键决策：
1. **移除范围**：只删「题库页顶部出题表单 + `POST /api/questions/generate` REST 端点」；**保留**聊天里的 `generate_questions` 工具与 `QuizService` 类（`grade`/`_valid` 仍被入库校验和模拟考试判分依赖）。
2. **上传用途**：上传含「题目+答案」的文件，经 **LLM 智能解析**抽取成结构化题目入库（不是向量出题，也不是死板 CSV 导入）。
3. **去重标准**：按 **题型 + 题干**（题干去首尾空白后比较）判重。
4. **填空题型**：不新增；筛选只用现有 4 种（单选/多选/判断/简答）。
5. **详情形态**：右侧滑出抽屉，不建独立路由页。

## 现状与可复用件

- 题型枚举全仓统一 4 种：`single/multiple/truefalse/short`（`app/tools/exam_tools.py` `ALL_TYPES`）。**无 `fill`（填空）**。
- 数据层 `app/questions.py` `QuestionStore`：`create/get/list/sample/delete/delete_many`。`list` 无筛选/分页；`create` 无去重。表 `questions(id,user_id,type,stem,options,answer,explanation,source,created_at)`（`app/db.py` `_SCHEMA`）。
- `source` 是自由文本（出题时=主题名，`add_questions`=「聊天整理」，缺省空串）。
- 批量删除后端 `POST /api/questions/delete` 与前端 `api.questions.removeMany` **已存在**，页面未用。
- 文件解析 `app/parsing.py` `parse_file(filename,data)` 支持 txt/md/pdf/docx→文本，复用。
- 分页范式：`DocumentStore.list(user_id,limit,offset)`+`count` / `GET /api/documents?page&size`→`{items,total}`（`app/api/documents.py`）。
- 前端可复用模式：`KnowledgeView.tsx`（上传按钮、防抖搜索、`Pagination`、CSS `-webkit-line-clamp` 截断、详情抽屉 `KnowledgeDetailDrawer`）；`WrongAnswersView.tsx`（`Set<string>` 勾选 + 头部「批量删除（N）」）。

## 详细设计

### A. 移除「出题」

- **后端**：删 `app/api/questions.py` 的 `POST /api/questions/generate` 端点与 `GenerateBody`；`make_questions_router` 去掉 `quiz_service` 形参；`app/main.py` 调用处不再传 `quiz_service`（`quiz_service` 仍构造，供聊天 `generate_questions` 工具用）。
- **前端**：删 `QuestionBankView.tsx` 顶部出题 `Card` 及 `topic/count/types/generate` 相关 state；删 `api.questions.generate`。
- **不动**：`app/tools/exam_tools.py` 的 `GenerateQuestionsTool`、`app/api/chat.py` 中其注册、`QuizService`。

### B. 列表重构（显示答案 + 截断 + 筛选 + 分页 + 批量删除）

**前端**：列表由 `List/ListItem` 改为卡片列（对齐知识库/错题集）。每卡显示：
- 题型 Chip、题干、**答案**、来源、创建时间。
- 题干/答案过长用 CSS `-webkit-line-clamp` 截断显示 `…`，完整内容进详情抽屉。
- 答案按题型人性化渲染：单选→对应选项文本；多选→多个选项文本；判断→「正确/错误」；简答→参考答案字符串。
- 顶部工具条：题名关键词输入（300ms 防抖）、题型下拉（4 种）、来源下拉（后端返回去重后的 source 列表）。三者可组合，任一变化回到第 1 页。
- 每卡 `Checkbox` + 头部「批量删除（N）」（`Set<string>`，复用 `WrongAnswersView`），调用 `api.questions.removeMany`；保留单条删除。
- 底部 MUI `Pagination`。

**后端**：
- `QuestionStore.list(user_id, *, type=None, source=None, q=None, limit=None, offset=None)`：动态 `WHERE user_id=?` + 可选 `type=?` / `source=?` / `stem LIKE ?`；`ORDER BY created_at DESC`；有 limit 时分页。配套 `count(user_id, *, type, source, q)`（同筛选条件）。
- `GET /api/questions?page&size&type&source&q` → `{items,total}`（size clamp，如 1..100）。
- 新增 `GET /api/questions/sources` → `question_store.sources(user_id)`（`SELECT DISTINCT source ... WHERE source<>'' ORDER BY source`）。

### C. 详情抽屉

- 新增 `web/src/pages/QuestionDetailDrawer.tsx`（仿 `KnowledgeDetailDrawer`）：右侧 `Drawer`，展示完整题干、全部选项（正确项高亮）、正确答案、解析、来源、创建时间。
- 点卡片打开（`previewQuestion` 状态持有该题对象）。列表项已含完整字段，抽屉**直接用内存对象渲染，不新增 GET 详情接口**。

### D. 上传智能解析入库

- 新增 `POST /api/questions/import`（`UploadFile` multipart）：
  1. 按 `config.app_max_upload_mb` 拦截大小；
  2. `parse_file(filename, data)` → 文本（`UnsupportedFormat`/`ParseError`→400）；
  3. 调 `QuestionImporter.import_text(user_id, filename, text)`；
  4. 返回 `{imported, skipped_invalid, skipped_duplicate}`。
- 新模块 `app/question_import.py`：`QuestionImporter(complete, question_store)`（依赖 completer + store，**不依赖 embedding**）。
  - `EXTRACT_SYSTEM` 提示：从文本中抽取所有题目，识别题干/选项/正确答案/解析、判定题型（限 4 种），严格只输出 JSON 数组。
  - `import_text`：`complete(EXTRACT_SYSTEM, user)` → `_parse_questions`（复用）→ `_valid`（复用）过滤非法 → 去重（见 E，含批内去重）→ 逐题 `create_deduped`，`source=filename`。返回三项计数。
- **前端**：`QuestionBankView` 头部加「导入题库」按钮（复用 KnowledgeView 隐藏 `input[type=file] accept=".txt,.md,.pdf,.docx"` + FormData），上传后 toast 显示导入/跳过数并刷新列表。`api.questions.import(file)`。

### E. 去重（横切所有新增路径）

- 判重键：`(type, TRIM(stem))`，同一 `user_id` 内。
- `QuestionStore.create_deduped(user_id, q) -> str | None`：先 `SELECT id FROM questions WHERE user_id=? AND type=? AND TRIM(stem)=TRIM(?)`，命中返回 None（不插入），否则插入并返回新 id。
- 改用方：`QuestionImporter`（并做批内去重，避免同一文件内重复）、聊天工具 `AddQuestionsTool`（`app/tools/exam_tools.py`）。`QuizService.generate` 与本次无关，不改（保留原 `create`）。

### F. 变更文件汇总

- `app/questions.py`：`list`/`count` 加筛选分页；新增 `create_deduped`、`sources`。
- `app/api/questions.py`：删 `generate`+`GenerateBody`；`GET /api/questions` 改分页筛选返回 `{items,total}`；新增 `POST /api/questions/import`、`GET /api/questions/sources`；`make_questions_router` 去 `quiz_service`。
- `app/question_import.py`（新）；`app/parsing.py`（复用，不改）。
- `app/main.py`：`make_questions_router` 调用去 `quiz_service`；构造 `QuestionImporter`（用 `build_completer`）并注入路由。
- 前端：`web/src/api/client.ts`（`api.questions` 改造）；`web/src/pages/QuestionBankView.tsx`（重写）；`web/src/pages/QuestionDetailDrawer.tsx`（新）。

## 数据流

上传导入：前端选文件 → `POST /api/questions/import` → `parse_file` 得文本 → `QuestionImporter` LLM 抽取 JSON → `_valid` 过滤 → `(type,stem)` 去重（批内 + 对库）→ `create_deduped` 落库（source=文件名）→ 返回计数 → 前端 toast + 刷新。

列表浏览：前端筛选/翻页 → `GET /api/questions?page&size&type&source&q` → `{items,total}` → 卡片渲染（答案截断）→ 点卡片 → 抽屉展示完整内容。

## 错误处理

- 上传：超限/不支持格式/解析失败/空文件 → 4xx，前端 Alert/toast。
- LLM 抽取结果非合法 JSON 或无有效题 → `imported=0`，前端提示「未识别到有效题目」。
- 去重与非法题静默跳过并计数，回传前端汇总，不报错。

## 测试

后端（pytest，`:memory:` store / stub completer）：
- `create_deduped`：同 `(type,stem)` 二次插入返回 None 不入库；题干首尾空白等价；跨用户不误判；不同题型同题干可共存。
- `list`/`count`：按 type/source/q 筛选、分页 offset/limit、`DESC` 排序、用户隔离。
- `sources`：去重、排除空串、用户隔离。
- `QuestionImporter.import_text`：stub completer 返回假 JSON → 抽取入库、非法跳过、批内+对库去重、`source=文件名`、计数正确。
- `POST /api/questions/import` 端点：正常/超限/不支持格式/空文件。
- 回归：`/api/questions/generate` 已删（404/405）；`GET /api/questions` 分页结构。

前端（vitest；注意先 tsc 构建避免过期 .js）：
- `QuestionBankView.test.tsx` 更新：答案渲染与截断、题型/来源/题名筛选触发请求、分页、勾选批量删除、上传调用与结果提示、点卡开详情抽屉。

## YAGNI 取舍

- 详情用抽屉，不建 `/questions/:id` 路由。
- 不新增 GET 单题详情接口（列表已含全字段，抽屉用内存对象）。
- 不新增填空题型。
- 导入同步处理（单次 LLM 调用 + busy spinner），不引入后台任务。
