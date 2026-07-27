> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# Skill 渐进式披露（SkillRegistry） — 设计规格

> 状态：已实现（提交 `bedc144`，全量 308 passed / 3 skipped）
> 日期：2026-07-09
> 所属：harness 内核能力扩展。为主循环引入「按需加载技能」，对齐 Anthropic Agent Skills 的渐进式披露机制。

## 1. 背景与目标

harness 此前没有「技能」概念：唯一的能力扩展手段是 `AgentSpec`（`orchestration/spec.py`），但它只能通过 `dispatch` 派给子 agent 使用，**主循环无法热加载**。当专家单元数量增长时，把所有说明塞进 `system_prompt` 会持续占用上下文。

本特性为主循环引入渐进式披露：

- **元数据索引常驻**系统提示（便宜：仅技能名 + 一句话描述）。
- 模型判断需要时用 `load_skill` **按需加载** `SKILL.md` 正文，用完可 `unload_skill` 释放。
- 技能可携带脚本 / 资源文件，用 `read_skill_resource` 按需读取。

**非目标（YAGNI）**：主循环之外的自动检索注入（RAG-over-skills）、运行时动态增删工具、LRU 自动驱逐、dispatch 子 agent 接入技能（留作扩展点）。

## 2. 关键决策（已确认）

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 加载触发 | **模型显式调 `load_skill` 工具** | 可解释、可审计、模型自主；对齐 Agent Skills 真实机制 |
| skill 范围 | **完整 Agent Skills**（指令 + 脚本 + 资源） | 复用已有沙箱跑脚本，**不动 `ToolRegistry`** |
| 资源交付 | **专用 `read_skill_resource` 工具** | 读资源与沙箱解耦；无沙箱时技能降级为纯指令仍可用 |
| 生命周期 | **追加式 + 可选 `unload_skill`** | 机制简单、模型可控；不像 LRU 那样偷偷改变脚下上下文 |
| 接入范围 | **v1 仅主循环** | 子 agent 已有 `AgentSpec` 定制 prompt，需求不急 |

**核心洞察**：主循环每请求新建 `ContextManager` 与 `AgentLoop`（`app/api/chat.py`），而工具与 `SkillRegistry` 是共享单例；模型的 `load_skill`/`unload_skill` 调用会作为 assistant 的 `tool_calls` 落入 `state.messages`。因此**「当前加载了哪些技能」可以纯函数式地从消息历史推导**，无需任何共享可变状态 —— 从而做到并发安全，且**零改** `AgentLoop`/`RunState`/`ToolRegistry`/`ToolExecutor`。

## 3. 架构总览

```
技能目录                         主循环（每请求）
────────────────────           ──────────────────────────────────
skills/<name>/                 ConversationContextManager (system + history)
  SKILL.md   (frontmatter        │  被装饰
   name/description + 正文)       ▼
  scripts/…  (可选)             SkillContextManager.build(state)   ← 纯函数注入点
  refs/…     (可选)               ├─ 折叠 index_text() 进单个 system 消息（常驻）
        │ 启动扫描一次、只读        ├─ resolve_loaded_skills(state) 扫 tool_calls
        ▼                          │     load 入序 / unload 移除 / 未知忽略
  SkillRegistry (进程内单例)       └─ 把已加载技能正文追加进同一 system 消息
   ├─ index_text()                        │
   ├─ body(name)                          ▼
   └─ read_resource(name,path)     发给模型 → 模型调用：
        ▲                            load_skill(name)       ← 无状态，仅校验+确认
        └──────────────────────────  unload_skill(name)     ← 无状态，仅校验+确认
                                     read_skill_resource(skill,path) → registry.read_resource
```

## 4. 组件设计

### 4.1 `src/harness/skills/registry.py`

- `parse_frontmatter(text)`：零依赖解析 `---` 头部的扁平 `key: value`（**不引入 PyYAML**，元数据只需 `name`/`description`）+ 正文；无合法 frontmatter 返回 `({}, 原文)`。
- `Skill`（frozen dataclass）：`name` / `description` / `body` / `dir`（绝对路径，作为 `read_resource` 的沙盒根）。
- `SkillRegistry(skills_dir, resource_max_chars=8000)`：**启动扫描一次、之后只读、并发安全**。
  - `_scan()`：遍历子目录，缺 `SKILL.md` / 缺 `name` 或 `description` / 技能名重复 → **跳过并记 `warnings`，不崩启动**。
  - `index_text()`：常驻系统提示的元数据索引（名 + 描述 + 三工具用法）；空目录返回 `""`。
  - `read_resource(name, rel_path)`：`abspath(join(dir, rel_path))` 后校验必须在 `dir` 之内 —— **路径穿越防护**（拒绝 `..` 与绝对路径）；不存在抛 `ValueError`；超 `resource_max_chars` 截断。

### 4.2 `src/harness/skills/context.py`

- `resolve_loaded_skills(state, registry) -> list[str]`：**纯函数**。遍历 `state.messages` 的 `tool_calls`，`load_skill` 入序去重、`unload_skill` 移除、registry 中不存在的忽略。加载状态的唯一真相是消息历史本身。
- `SkillContextManager(inner, registry)`：**装饰器**，包裹任意 inner ContextManager（`ContextManager` 或 `ConversationContextManager` 都不改）。
  - 元数据索引与已加载技能正文**折叠进单个 system 消息**，规避多 system 消息的 provider 兼容性问题。
  - registry 为空（`index_text()` 为空）时**完全透明**，等价于直接用 inner。
  - `build()` 是纯函数，不改 `inner`、不改 `state`。

### 4.3 `src/harness/skills/tools.py`

三个工具均**无状态**（只持有 registry 引用），可作共享单例：

| 工具 | 参数 | 行为 |
| --- | --- | --- |
| `load_skill` | `name` | name 不存在 → `ValueError(可用列表)`；否则返回确认文本（真正注入由 ContextManager 完成） |
| `unload_skill` | `name` | 同上校验，返回确认 |
| `read_skill_resource` | `skill, path` | 调 `registry.read_resource`；越界 / 不存在 → `ValueError` |

工具抛出的 `ValueError` 由 `ToolExecutor` 统一兜成 `is_error` 结果回填模型自纠正（`tools/base.py`）。

## 5. 配置与装配

- `src/harness/config.py`：`skills_dir="skills"`、`skill_resource_max_chars=8000`。
- `app/config.py`：`enable_skills: bool = False`（与 `enable_browser` 等同格式的开关）。
- `app/assembly.py`：`enable_skills` 时建 `SkillRegistry`；**目录非空才注册三工具**并把 registry 挂到 `Harness.skill_registry`（空目录则置 `None`，不必包装 context）。
- `app/api/chat.py`：有 `skill_registry` 时把每请求的 `ctx` 包一层 `SkillContextManager`（与用户级 `_build_registry` / `EXAM_GUIDE` 并存）。

## 6. 错误处理

- 技能不存在 / 资源路径越界 / 资源不存在 → 工具返回 `is_error`，模型自纠正。
- `SKILL.md` 畸形（缺 frontmatter / 字段）→ 扫描期跳过 + 记 `warnings`，不影响启动。
- 无沙箱时：`read_skill_resource` 仍可读文档类资源；脚本无法执行，技能**降级为纯指令**。

## 7. 测试（TDD 先行）

- `tests/test_skill_registry.py`：frontmatter 解析、扫描、畸形跳过、`index_text`、`read_resource` 正常 / 路径穿越拒绝 / 截断。
- `tests/test_skill_context.py`：`resolve_loaded_skills`（空 / load-unload / 未知忽略 / 去重保序）、装饰器注入索引与正文、单一 system 消息、空 registry 透明、不改 state。
- `tests/test_skill_tools.py`：三工具的成功与错误路径（含越界）。
- `tests/app/test_assembly.py`：开关关闭不注册；开启且非空注册三工具并暴露 `skill_registry`；开启但空目录不注册。

示例技能：`skills/table-cleanup/`（`SKILL.md` + `scripts/describe.py` + `refs/pandas-tips.md`），演示「load → read_skill_resource 取脚本 → 写沙箱 run_python」的完整链路。

## 8. 影响面小结

**全部新增 + 少量增量接线，零改 `AgentLoop`/`RunState`/`ToolRegistry`/`ToolExecutor`。** 加载状态从消息历史推导，无共享可变态，并发安全。

**扩展点**：
- dispatch 子 agent 接入技能（给子 loop 的 ContextManager 也套 `SkillContextManager`）。
- 若技能规模增长，可加权限模型（RBAC gating）与按能力 / 角色的审计留痕。
