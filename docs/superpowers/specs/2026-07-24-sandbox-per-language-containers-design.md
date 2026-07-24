# 沙箱按语言容器化重构设计

日期：2026-07-24

## 背景与目标

当前沙箱是三层结构：

- **会话基础容器**（`sandbox_image`，如 centos）：跑 `run_shell`、fs 工具、承载工作区、播种上传附件。
- **语言子沙箱**（`sandbox_lang_images`）：`run_python/run_node/run_java` 用，按 (会话,语言) 缓存，执行前后与基础容器**互拷工作区**。
- **全局浏览器子沙箱**：独立，24h。

外加一套并存但基本闲置的 `sandbox_images` 路由机制（`RoutingSandbox`）。

问题：基础/子沙箱两层 + 工作区互拷复杂；配置项重叠（`sandbox_images` vs `sandbox_lang_images`）；旧提示词还误导模型可 `apt` 装包（已单独修）。

**目标**：拍平成「AI 按要执行的语言，自动起对应语言的容器」，去掉基础容器与子沙箱概念。

## 已确认的决策

1. **保留 `run_shell`**：按需起一个 shell 容器（镜像 `debian:12-slim`）。
2. **工作区不跨语言共享**：每种语言容器各自独立 tmpfs 工作区（沿用现有 uid 挂载技巧，非 root 可写，无需自定义镜像）。
3. **容器有效期**：1 小时空闲后销毁（有操作即续期），按 (会话,语言) 缓存复用。
4. **联网**：所有语言容器默认联网（`sandbox_network=bridge`），下载内容落工作区；禁 `apt/dnf install`（非 root + cap_drop 天然拦住，提示词已如实说明）。
5. **浏览器容器不动**（全局共用、24h）。
6. **文件工具按语言路由**：`write_file/read_file/list_files` 新增可选 `language` 参数——带则落该语言容器（`run_python` 能读到），缺省落 shell 容器。
7. **`save_download` 不受影响**：其 content 由模型当参数传入、app 侧写库，与容器无关。

## 新架构

```
每个会话，按需起容器，各自独立 tmpfs 工作区，按(会话,语言)缓存，1h 空闲销毁：
  run_python                       → python 容器
  run_node                         → node 容器
  run_java(+version)               → java / javaN 容器
  run_shell                        → shell 容器（debian:12-slim）
  write_file/read_file/list_files  → 按 language 参数落对应容器；缺省 → shell 容器
所有容器：联网(bridge)、非 root、cap_drop=ALL、启动时播种本会话 uploads
浏览器容器：不动（全局、24h）
save_download：app 侧写库，与容器无关
```

核心原语：**`for_language(language, version=None) -> 已启动的容器`**。所有沙箱工具都经它取到目标容器，再各自 `write_file` + `exec`。不再有「谁是主容器」。

## 组件改动

### 配置（`src/harness/config.py` + `app/config.py`）

- **删**：`sandbox_image`、`sandbox_images`、`sandbox_default_language`、`sandbox_sub_network`、`sandbox_sub_idle_timeout`。
- **加**：`sandbox_shell_image = "debian:12-slim"`（run_shell + 缺省 fs 落点）。
- **改**：`sandbox_lang_images` 成为唯一语言→镜像表；`sandbox_network` 默认 `bridge`（统一所有容器）；`sandbox_idle_timeout = 3600`（1h，统一所有容器）。

### 沙箱协议与实现（`src/harness/sandbox/`）

- `base.py`：`Sandbox` 协议加 `for_language(language=None, version=None)`。
- `docker.py`：加 `for_language` → `await self.start(); return self`（单容器，供直连/测试）。网络默认取 `sandbox_network`。
- `local.py`：加 `for_language` → `return self`。
- **删 `routing.py`（`RoutingSandbox`）**：其职责由 SandboxManager 的 (会话,语言) 管理取代。
- `factory.py`：`build_sandbox` 简化，`_docker_for` 保留供 manager 造语言容器。

### `app/sandbox_manager.py`（主改）

- 容器统一按 **`(conv_id, label)`** 存（`label`=`python`/`shell`/`java17`…），**取消基础容器 `_boxes`/子沙箱 `_subs` 之分**。
- `get_lang(conv, language, version)`：解析镜像（shell→`sandbox_shell_image`，其余→`sandbox_lang_images`，显式 version 无镜像则报错）→ 惰性建/复用容器 → 启动 → **新建时播种 uploads** → 返回。
- `set_uploads(conv, uploads)`：存本会话上传清单，并播种进该会话已存在的所有容器（新容器建时也播种）。取代 chat.py 里对基础容器的一次性播种。
- `destroy/close_all/_evict_idle`：按 `(conv,label)` 统一回收；单一 `sandbox_idle_timeout`。`get_browser`、`sweep_orphans` 不变。
- **删 `get_sub`、`run_code`、`_copy_workspace`、`_resolve_lang_image` 的回传逻辑**。
- `SandboxProxy`：加 `for_language`（委托 `manager.get_lang`）；协议方法（exec/write_file/…）缺省落 shell 容器（供 DNS 解析等内部调用）；加 `set_uploads`。

### 工具（`src/harness/tools/builtins/`）

- `fs_tools.py`：三个 fs 工具 Params 加可选 `language: str | None`；`run` 里 `box = await sandbox.for_language(language)` 再操作。
- `shell_tool.py`：`box = await sandbox.for_language("shell")`。
- `code_tool.py`：`_run_code` 简化为 `box = await sandbox.for_language(spec.language, version)` 再 `write_file`+`exec`，删 run_code/sandbox_for 分支。

### 装配与聊天（`app/assembly.py`、`app/api/chat.py`）

- assembly：`run_node`/`run_java` 按 `sandbox_lang_images` 是否含该语言注册；fs 工具照旧注册（现在带 language 参数）。
- chat.py：上传播种改为 `await harness.sandbox.set_uploads([(f"uploads/{fn}", data), …])`。

### 提示词（`app/sandbox_manager.py: sandbox_guide`）

按新架构改写：不再讲「基础容器 vs 子沙箱」，改讲「按语言各自独立容器、各自工作区、都联网、写文件带 language 落对应语言容器、缺省 shell」。禁 apt/dnf 那段已在前一提交改好。

## 影响面与测试

- 删 `tests/test_routing_sandbox.py`（RoutingSandbox 已移除）。
- 重写 `tests/app/test_lang_sandbox.py`（run_code→for_language）、`tests/app/test_sandbox_manager.py`（基础/子沙箱→统一语言容器 + set_uploads）、`tests/app/test_assembly.py`、`tests/test_orchestration_executor.py`（sandbox_guide 文案）。
- 新增：fs 工具 `language` 路由、`for_language` 的用例。
- 全量后端 + 前端跑绿后提交。

## 非目标（YAGNI）

- 不做跨语言共享工作区（已否；需自定义镜像，成本不划算）。
- 不动浏览器容器机制。
- 不改 `save_download` / 附件读取工具（app 侧，与容器无关）。
