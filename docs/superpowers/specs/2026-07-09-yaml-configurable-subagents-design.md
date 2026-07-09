# 子 agent 花名册 YAML 化 — 设计规格

> 状态：已批准，待实现
> 日期：2026-07-09
> 所属：harness 多 agent 编排的可配置化改造。把硬编码的子 agent 角色搬到外部 YAML，并借此把项目的 YAML 解析统一到 PyYAML。

## 1. 背景与目标

当前子 agent 角色（`researcher`/`coder`）**硬编码在 `app/assembly.py` 的 `enable_dispatch` 块**：`AgentSpec` 实例直接写在代码里。改角色 = 改核心装配代码。

目标：把子 agent 花名册变成**外部 YAML 配置**，改角色只需改文件，不动代码。

**非目标（YAGNI）**：dispatch 子 agent 接入技能、per-agent 预算/模型覆盖、运行时热重载花名册。

## 2. 关键决策（已确认）

| 决策点 | 选择 |
| --- | --- |
| YAML 组织 | **目录 `agents/*.yaml`，一文件一 agent**（与技能目录对称） |
| 与硬编码关系 | **YAML 完全接管**：删硬编码，`researcher`/`coder` 落成随仓库附带的默认 YAML |
| 工具降级 | **优雅降级**：`tool_names` 按工具池过滤，缺失丢弃；某 agent 工具全不可用则跳过（记 warning） |
| 解析器 | **引入 PyYAML**（显式声明依赖），并回改技能 `parse_frontmatter` 统一用之 |
| agent 名 | 取文件内 `name:` 字段（权威），文件名仅作组织 |

约束：`tool_names` 是列表、`system_prompt` 常为多行块标量 —— 手写 YAML 易错，故正式引入 `pyyaml`（已在 venv 中传递存在，此次显式声明）。

## 3. YAML 结构（`agents/<name>.yaml`，字段与 `AgentSpec` 1:1）

```yaml
name: researcher
description: 擅长检索与联网查资料      # 给主 agent 看的能力说明（进 roster.describe）
system_prompt: |
  你是研究员，用工具检索知识库/联网/抓网页查资料并给出结论。
tool_names:
  - search_memory
  - http_request
  - browse
```

## 4. 组件设计

### 4.1 `src/harness/orchestration/roster_loader.py`（新增，harness 层）

`load_roster(agents_dir) -> tuple[list[AgentSpec], list[str]]`：

- 扫 `agents_dir` 下 `*.yaml` / `*.yml`，按文件名排序，逐个 `yaml.safe_load`。
- **纯解析 + 结构校验，不涉及工具池**（与工具池的耦合留在 assembly）。
- 校验：内容须为映射；`name`/`description`/`system_prompt` 非空；`tool_names` 为字符串列表。
- **畸形（YAML 语法错 / 缺字段 / tool_names 非串列表 / 非映射 / 重名）→ 跳过该文件 + 记 warning，不抛异常**（与技能畸形跳过对称）；重名保留先出现者。
- 目录缺失/为空 → 返回 `([], [])`。

### 4.2 `app/assembly.py`（dispatch 块改造）

优雅降级留在此处（工具池 `pool` 在此可见）：

```python
if config.enable_dispatch:
    raw_specs, _warns = load_roster(config.agents_dir)
    specs = []
    for s in raw_specs:
        avail = [t for t in s.tool_names if t in pool]   # 缺失工具丢弃
        if avail:                                        # 全不可用则跳过该 agent
            specs.append(AgentSpec(s.name, s.description, s.system_prompt, avail))
    if specs:
        reg.register(DispatchTool(AgentRoster(specs), pool, client, budget=None, ...))
```

**行为与现状逐字等价**，仅 spec 来源从硬编码变为 YAML。`AgentSpec`/`AgentRoster`/`dispatch.py` 零改。

### 4.3 技能回改 `src/harness/skills/registry.py`

`parse_frontmatter(text)` 改用 `yaml.safe_load` 解析 `---` 块 → dict，**保持函数签名与返回语义不变**（无 `---` 前缀 → `({}, 原文)`；非 dict → `{}`；值统一转字符串）。现有技能 frontmatter 测试作回归。

### 4.4 配置与依赖

- `src/harness/config.py`：`agents_dir: str = "agents"`（与 `skills_dir` 对称）。
- `pyproject.toml`：`[project].dependencies` 增 `pyyaml>=6.0`。
- 仓库附带 `agents/researcher.yaml`、`agents/coder.yaml`（等价现有硬编码）。

## 5. 错误处理

- 单个 YAML 坏了只跳过它，记 warning，其余照常加载。
- 引用未启用工具 → assembly 层过滤丢弃；某 agent 工具全不可用 → 跳过该 agent；无任何有效 agent → 不注册 dispatch。
- `agents_dir` 缺失/空 → 空花名册。

## 6. 测试（TDD 先行）

- `tests/test_roster_loader.py`：合法多行/列表解析、缺目录、YAML 语法错跳过、缺字段跳过、`tool_names` 非串列表跳过、重名保留先者、非映射跳过、只认 `*.yaml`。
- `tests/app/test_assembly.py`：dispatch 开启且临时 `agents_dir` 有有效 agent → 注册；agent 工具全不可用 → 不注册；空 `agents_dir` → 不注册。
- `tests/test_skill_registry.py`：回改后 frontmatter 用例全绿（回归）。

## 7. 影响面小结

- **新增**：`roster_loader.py`、`agents/researcher.yaml`、`agents/coder.yaml`、`tests/test_roster_loader.py`
- **改动**：`pyproject.toml`（+pyyaml）、`harness/config.py`（+agents_dir）、`app/assembly.py`（dispatch 块）、`harness/skills/registry.py`（parse_frontmatter）、`tests/app/test_assembly.py`
- **零改**：`AgentSpec`/`AgentRoster`/`dispatch.py`

**扩展点**：per-agent 模型/预算覆盖、dispatch 子 agent 接入技能、花名册热重载。
