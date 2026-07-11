# 抓取内容清洗与入库（去重·切片·保留最新）设计规格

日期：2026-07-11
状态：草案，待批准

## 0. 背景与范围

`http_request` 现已在抓取时对 HTML 响应返回**解析后的标题 + 正文**（见
`src/harness/tools/builtins/http_tool.py` 的 `render_http_result`，复用
`src/harness/browser/extract.py:extract_title_and_text`）。本规格回答其**后续**问题：
抓到的内容如何**清洗 → 提取标题正文 → （可选）翻译英文 → 存入向量库时去重、切片、保留最新**。

本文档只做设计，不含实现代码。所引用的能力多为「已存在但未打通」——重点是**复用**，
而非新造轮子。

## 1. 目标

把抓取到的网页/上传的文档规范化后可靠入库，做到：

- 正文干净（无导航/广告/页脚样板），带标题与来源 URL；
- 可选把英文正文翻译成中文再入库（默认关闭）；
- **同一来源重复抓取不产生重复片段**（内容去重）；
- **同一来源内容更新时保留最新版本**、旧版本从检索中隐去（版本化 supersede）；
- 切片策略沿用现状即可满足，给出可选增强。

## 2. 清洗与标题/正文提取

统一产物：`{title, text, url}`。

| 来源 | 取正文 | 取标题 |
|---|---|---|
| `http_request`（静态 HTML） | `extract_main_text`（trafilatura 去样板） | `extract_title_and_text` → trafilatura 元数据，回退 `<title>` |
| `browse`（JS 渲染） | 同上，作用于 `page.html` | 浏览器 `page.title` |
| 文件上传 | `app/parsing.py:parse_file`（txt/md/pdf/docx） | 文件名 |

**改动点（实现阶段）**：`parse_file`（`app/parsing.py:15`）当前仅 txt/md/pdf/docx，
新增 `html/htm` 分支，走 `extract_title_and_text` 取正文（与抓取路径同一套清洗，
避免把 HTML 原样当纯文本入库）。

## 3. 翻译（可选开关，默认关闭）

- **触发**：两层开关——全局 config flag（如 `ingest_translate_to_zh: bool = False`）
  + 单次入库参数覆盖。默认关闭，零额外 LLM 成本。
- **实现**：复用已有 LLM `Completer` 抽象（`app/summarizer.py`、
  `src/harness/memory/writer.py` 里的 `self._complete(system, user)`），
  新增一个 `translate(text) -> str` 薄封装，system 提示「把英文正文翻译成简体中文，
  保留段落结构，不加解释」。**best-effort**：翻译失败回退存原文（与 writer 的异常安全一致）。
- **语言检测**：先判断是否需要翻译，避免对中文正文空跑 LLM。
  - v1 用简单启发式：统计 ASCII 字母 vs CJK 字符占比，英文占比高于阈值才翻译；
  - 可选增强：不确定时用一次轻量 LLM 判定。
- **存储策略**：存中文正文，metadata 记 `lang="zh"`、`translated=true`、`orig_lang`；
  是否同时保留英文原文（双存）留作配置项。翻译在**切片之前**做（对整篇正文翻译再切，
  避免跨片语义割裂）。
- **成本/延迟**：每篇多一次（长文可能多次）LLM 调用；默认关闭即无影响。长文需分段翻译时，
  注意 token 上限与顺序拼接。

## 4. 入库入口（两者都要）

### (a) 新增「保存抓取页面到知识库」动作/工具
当前**无此路径**——`http_request`/`browse` 只把文本返回给模型，不落库。新增一个入口，
入参为已清洗的 `{title, text, url}`（而非文件字节），复用
`KnowledgeService.ingest`（`app/knowledge.py:28`）的下游（切片/embedding/DocumentStore）。

- 形态二选一（实现阶段定）：①新增工具 `save_to_knowledge`，让模型在抓取后主动存；
  ②在知识库 UI 加「粘贴 URL 抓取入库」动作。
- `ingest` 需从「只接受 `filename+bytes`」泛化为「也接受 `{title,text,url,source_type}`」，
  `source` 记 URL、`doc_id` 照旧、excerpt 取正文首段。

### (b) 改进现有文件上传路径
`ingest` 现直接 `self._memory.add_texts([...])` **裸切片、无去重、无版本**。按 §5/§6 接入
去重与保留最新，使上传同名/同内容文档不再堆叠重复片段。

两个入口最终**汇聚到同一条规范化入库管线**（清洗产物 → 可选翻译 → 切片 → 去重/版本 → upsert），
只是数据来源不同。

## 5. 切片（chunk）

- 现状：`src/harness/memory/chunker.py` 定长滑窗（默认 `chunk_size=1000, overlap=200`
  字符，`memory.py`），末块冗余丢弃。够用，**v1 保留现状**。
- 可选增强：token 感知切片（`tiktoken` 已在依赖 `pyproject.toml`），按 token 而非字符切，
  更贴合 embedding 模型上下文；以及按段落/标题边界对齐，减少语义割裂。列为后续增强，不阻塞 v1。

## 6. 去重 + 保留最新（关键，复用已有机制）

**机制已存在，但目前只接了对话记忆，未接知识入库。** 现有原语（均已就绪）：

- `MemoryWriter`（`src/harness/memory/writer.py:110`）：提炼 → 找候选 → **reconcile 决定
  ADD/NOOP/REPLACE** → apply；REPLACE 时 `version = max(旧.version)+1` 并
  `backend.set_superseded(supersede_ids)`（`writer.py:169-173`）。
- backend：`set_superseded` / `version` / `entity_key` / `list_by_entity`
  （`sqlite_backend.py`）；**superseded 行不参与检索**。

`KnowledgeService.ingest` 现绕过这套、直接 `add_texts`，所以无去重无版本。方案：让入库经这套机制。

- **内容去重（跳过重复抓取）**：对规范化正文算 content-hash（如 sha256）。入库前查该
  `(owner, kind, entity_key)` 现有记录的 hash，命中相同 → **NOOP**（不重复写）。
- **保留最新 / 版本化**：以**规范化 URL**（去 fragment、排序 query、去尾斜杠）作 `entity_key`
  （上传文档则用稳定来源标识如文件名/doc 源）。重新抓取同一 URL 且内容变化 →
  写新版本（`version+1`）并 `set_superseded` 旧版本的全部 chunk → 检索只见最新。
- content-hash 与 `entity_key` 建议写入 metadata，便于查证与幂等。

**两种接法（实现阶段取舍）**：

1. **走 `MemoryWriter`（或其变体）**：直接复用 reconcile/supersede，但 writer 面向「LLM 提炼事实」，
   知识片段是「原文切片」，语义不完全一致——可能需要一个「文档版」writer：跳过 LLM 提炼，
   以 URL+hash 做确定性 reconcile（不靠 LLM 判等），再复用 `_apply` 的 version/supersede 逻辑。
2. **在 `ingest` 内加轻量去重步骤**：直接调 backend 的 `list_by_entity`/`set_superseded`/带
   `version` 的 upsert，不引入 writer。primitives 都在，改动更局部。

**倾向**：方案 2（确定性、无额外 LLM 成本、改动局部），把 §6 的 hash/URL 规范化/版本化
封装成 `KnowledgeService` 内的一个 `_reconcile_and_upsert` 步骤，两个入口共用。

## 7. 端到端管线（汇总）

```
抓取/上传
  → 清洗提取 {title, text, url}          (§2, extract_title_and_text / parse_file)
  → [可选] 英文→中文翻译                  (§3, 默认关闭，Completer)
  → content-hash + 规范化 entity_key      (§6)
  → 命中相同 hash? → NOOP（跳过）
  → 切片                                  (§5, chunker)
  → 内容变化? → 新 version + set_superseded 旧 chunk
  → embed + upsert（带 entity_key/version/metadata）
  → DocumentStore 记 doc→chunk_ids
```

## 8. 测试计划（TDD 大纲，供后续实现参照，本次不写代码）

- **清洗**：`parse_file` 新增 html 分支——HTML 入 → 干净正文、无标签墙。
- **翻译**：开关关闭→原文入库、零 LLM 调用；开启+英文→译文入库、metadata 标 `lang`；
  中文正文→语言检测判定不翻译；翻译失败→回退原文（不抛异常）。
- **去重**：同一 URL 同内容二次入库→NOOP，片段数不增。
- **保留最新**：同一 URL 内容变化二次入库→旧 chunk 被 `set_superseded`，检索只返回新版本；
  `version` 递增。
- **切片**：沿用 `tests/test_chunker.py` 现有覆盖；token 感知切片若实现另加。
- **入口 (a)**：`{title,text,url}` 入库路径生成正确 `source=URL` 的片段。
- **入口 (b)**：上传路径接入去重后，同文件重复上传不堆叠。

## 9. v1 范围限制

- 切片保持字符定长滑窗，token 感知/边界对齐留作增强。
- 去重用确定性 hash+URL，不做「近似重复」（模糊语义去重）。
- 翻译默认关闭，仅英文→中文，其他语种不在 v1。
- 「保留最新」以 `entity_key` 为粒度做整源替换，不做片段级差量 diff。
