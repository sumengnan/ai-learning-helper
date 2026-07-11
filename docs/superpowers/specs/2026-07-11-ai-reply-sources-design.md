# AI 回复来源标注 设计规格

日期：2026-07-11
状态：已批准，待实现

## 1. 目标

每条 AI 助手回复展示它**实际取用过的信息来源**，形式为：

- **正文内联 `[n]` 角标**（可点击，定位到来源清单第 n 条）
- **末尾「📚 参考来源」编号清单**（与内联同一套编号）

来源覆盖所有「取用外部/存储信息」的工具；纯计算/动作类工具（计算器、更新计划、保存下载、保存错题、记录经验、写文件等）不计入。

## 2. 来源分类

| 类型 key | 触发工具 | 清单标签 | 点击行为 |
|---|---|---|---|
| `knowledge` 知识库 | `search_memory` | 命中文件名（去重） | 跳 `/knowledge` |
| `web` 网络 | `browse` / `http_request` | 网页标题或域名 | 新标签打开 URL |
| `question` 题库 | `sample_questions` | 「抽取 N 道题」 | 跳 `/questions` |
| `attachment` 附件 | `read_attachment` | 文件名 | 就地展开原文 |
| `memory` 对话记忆 | `recall_episodes` | 「历史经验片段」 | 就地展开原文 |
| `code` 沙箱执行 | `run_python` / `run_node` / `run_java` | 「Python/Node/Java 代码执行」 | 就地展开结果 |
| `mcp` MCP | `mcp__<server>__<tool>` | 「server · tool」 | 就地展开结果 |

空结果（如知识库无命中、题库为空）不计入来源。

## 3. 编号一致机制（关键）

末尾清单必须是**事实**（不能让模型编造来源），内联 `[n]` 又只有模型知道配哪句。方案：

- **后端权威编号 + 接地注入**：在**每轮请求**内为工具注册表包一层 `SourceTagger`，共享一个请求级 `SourceSink`。凡「产生来源」的工具成功返回时：
  1. 由该工具的 builder 从 `(args, result)` 提炼出一条来源描述（含 `type/label/url…`），记入 sink 并分配顺序号 `n`；
  2. 在返回给模型的结果文本末尾追加系统标注 `〔本结果对应参考来源 [n]：<label>，正文引用此来源请写 [n]〕`。
- 系统提示追加 `SOURCE_GUIDE`，说明「引用资料时用对应 `[n]`」。
- 于是模型看到的编号 = sink 记录的编号 = 末尾清单编号，三者天然对齐；模型未引用的来源仍出现在清单里（只是无内联角标）。工具报错时不记来源（异常先于记录抛出）。

**交付门重试对齐**：sink 每次尝试（每次 loop 运行）前 `reset()`；交付某次尝试的答案时快照该次 sink，作为该条消息的权威来源，避免把失败尝试的来源混入。

**v1 范围限制**：只捕获主 agent 的工具调用；子 agent（dispatch/researcher）内部的检索不单独列源（其结论经 dispatch 结果并入正文）。知识库来源只到文件名级（`search_memory` 结果不含 chunk id），故点击跳 `/knowledge` 列表页而非片段抽屉——精确到片段留作后续增强。

## 4. 数据流与落库

- 后端新增 `app/sources.py`：`build_source(tool_name, args, result)` 提炼器（按工具名注册 builder）+ `SourceSink` + `wrap_tool`。
- `chat.py`：`_build_registry` 返回 `(reg, sink)`；`gen` 每次尝试前 `sink.reset()`，交付时快照 `delivered_sources`；`finish_turn(..., sources=delivered_sources)` 落库；同时 `emit Progress(scope="sources", text=json)` 让在途客户端即时显示。
- 存储：`conversation_messages` 加 `sources TEXT` 列（schema + 迁移）；`finish_turn` 写入；`ui_messages` 读出。
- 提炼器**绝不能弄坏回复**：整体 try/except；未知工具忽略；失败则回复照常无来源块。去重（同 URL / 同文件名合并）、超长标签截断。

## 5. 前端

- `types.ts`：`ChatMessage.sources?: SourceItem[]`；`SourceItem = { index; type; label; url?; detail? }`。
- 新增 `<SourceList>`：渲染在助手正文下方，每类型带图标/颜色，条目 `id="cite-{index}"`；`web`→`<a target=_blank>`、`knowledge`→跳 `/knowledge`、`question`→跳 `/questions`、其余→就地展开 `detail`/原文。
- `Markdown`：新增可选 `onCitationClick`；重写 `a` 渲染器，`href="#cite-n"` 的渲染成可点上标并回调（`preventDefault` + `scrollIntoView`，不改 URL hash）。
- `ChatView`：助手且有 sources 时，先 `linkifyCitations(content, len)` 把 `[n]`（不后接 `(`、n 在范围内）替换成 `[n](#cite-n)` 再交给 Markdown；SSE 收到 `Progress{scope:"sources"}` 解析设 `a.sources`（不入 progress 列）；重连时 `a.sources = fin.sources`。
- 原「工具调用」`AgentProgress` 原始块保留（受 `showTools` 控制），「参考来源」为面向用户的常显主块。

## 6. 测试（TDD）

- 后端 `tests/test_sources.py`：各类工具→正确描述；被排除工具/空结果→None；mcp 前缀识别；去重；`SourceSink` 编号与快照；`wrap_tool` 追加标注且异常不记源；提炼器异常被吞不冒泡。
- 前端 `SourceList.test.tsx`：各类型图标/文案；`web` 有正确 `href`；空→不渲染；`linkifyCitations` 正确替换、越界/后接 `(` 不替换；内联上标点击回调。
- 集成（可选）：`finish_turn` 持久化 sources、`ui_messages` 回读仍在。
