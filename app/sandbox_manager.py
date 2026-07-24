# app/sandbox_manager.py
"""会话级沙箱：按「会话 + 语言」隔离容器。

装配期只把一个 sandbox 对象绑进所有工具，但这个对象是 SandboxProxy——它按「当前会话」
（contextvar）+「要执行的语言」解析出真实的 Sandbox 容器，真实容器由 SandboxManager
按 (conv_id, 语言) 惰性创建/缓存/销毁。AI 要跑哪种语言的代码，就自动起哪种语言的容器。

设计要点：
- 没有「基础容器 / 子沙箱」之分。每个 (会话,语言) 就是一个独立容器、各自独立 /workspace，
  互不可见（跨语言不共享文件，已知取舍）。
- run_shell 与「未指定 language 的 write_file/read_file/list_files」落 shell 容器
  （镜像 sandbox_shell_image）；run_python/run_node/run_java 落对应语言容器
  （sandbox_lang_images）；write_file 等带 language 参数时落该语言容器。
- 上传附件按会话登记（set_uploads），每个新建容器启动时播种进 /workspace/uploads/。
- 所有容器统一 sandbox_network（默认 bridge 联网）；非 root + cap_drop=ALL，装不了系统包。

生命周期：
- 首次在某会话内用到某语言时惰性创建其容器（沿用 DockerSandbox 的惰性 start）。
- 删除会话 → destroy(conv_id) 销毁该会话的**全部**语言容器。
- app 关停 → close_all()。
- 安全阀：空闲驱逐（sandbox_idle_timeout，默认 1h），每个容器各自计时。
- 容器打标签 mcp_sandbox=true + conv_id + lang，供进程重启后 sweep_orphans() 回收孤儿。
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from harness.sandbox.base import SandboxError
from harness.sandbox.factory import _docker_for, build_sandbox

_log = logging.getLogger("app.sandbox")


def _is_online(net) -> bool:
    """该网络配置是否可联网：'none'/空为禁网，其余（bridge/host/...）视为可联网。"""
    return str(net or "none").strip().lower() not in ("none", "")


def sandbox_guide(config) -> str:
    """按配置如实告诉模型沙箱的工作目录、按语言的容器与联网情况，避免它用宿主机路径、
    在禁网/非 root 环境里执意联网装系统包、或以为跨语言能共享文件。

    主聊天路径（chat.py）与编排器执行子步（executor.py）共用同一段文案，DRY。
    """
    ws = getattr(config, "sandbox_workspace", "/workspace")
    guide = (
        f"\n\n【沙箱工作目录】run_shell / run_python / run_node / run_java 等沙箱工具的当前工作目录（cwd）"
        f"就是 {ws}。读写文件用相对路径（相对 {ws}），或以 {ws}/ 开头的绝对路径；"
        f"生成的文件也放在这里。用户上传的附件在 {ws}/uploads/ 下。"
        f"不要使用宿主机路径（如 /Users、/home、/tmp）或其它臆想的目录——那些在沙箱里并不存在。"
        # write_file 写的是沙箱临时文件，随容器销毁；save_download 才产出用户拿得到的成品。
        f"\n【成品文件直接用 save_download】要交给用户下载/查看的最终产物（笔记、总结、报告、"
        f"导出文件等），直接调 save_download 生成即可，**不需要**先 write_file 落到沙箱再转存——"
        f"沙箱里的文件是过程中间物、随容器销毁，用户拿不到。"
        f"只有当后续步骤还要在沙箱里读取/处理该文件时，才先 write_file。")
    if getattr(config, "sandbox_backend", "") != "docker":
        return guide   # 本地后端跑在宿主机，无镜像/网络隔离概念，只给工作目录提醒
    online = _is_online(getattr(config, "sandbox_network", "none"))
    net = "可联网" if online else "禁止联网"
    shell_img = getattr(config, "sandbox_shell_image", "") or "（未配置）"
    lang = getattr(config, "sandbox_lang_images", None) or {}
    cpus = getattr(config, "sandbox_cpus", 1.0)
    mem = getattr(config, "sandbox_mem_limit", "") or "（未限）"
    disk = getattr(config, "sandbox_disk_limit", "") or "（未限）"
    exec_to = getattr(config, "sandbox_exec_timeout", 30)
    lines = [
        f"\n\n【资源上限】每个容器：CPU {cpus} 核、内存 {mem}、工作区磁盘 {disk}、单次执行超时 {exec_to} 秒。"
        f"单次执行超时罩住整条命令——**包括你在代码里起的 pip/子进程**，到点强杀（exit 124）；"
        f"在 subprocess 里设更长的 timeout 没用，外层这个才算数。"
        f"注意工作区 {ws} 是内存盘（tmpfs），其占用**算进内存额度**——所以别在沙箱里生成/下载超过内存额度的"
        f"大文件（大文件会先触内存上限被 OOM，而非磁盘上限）；CPU/内存吃满会被限流或直接杀掉进程。"
        f"要处理大数据就分块流式处理，不要一次性全load 进内存或落一个大文件。",
        f"\n\n【按语言各自独立的容器】每种语言在**自己的容器**内执行，各容器 {ws} **相互独立**、"
        f"互不可见（{net}）：",
        f"\n- run_shell、以及未指定 language 的 write_file/read_file/list_files → shell 容器（镜像 {shell_img}）"]
    if lang:
        primary = {k: lang[k] for k in ("python", "node", "java") if k in lang}
        shown = primary or dict(list(lang.items())[:3])
        imgs = "、".join(f"{k}→{v}" for k, v in shown.items())
        jvers = sorted(k for k in lang if k not in ("python", "node", "java"))
        ver = f"（run_java 可传 version 选 {', '.join(jvers)}）" if jvers else ""
        lines.append(f"\n- run_python / run_node / run_java → 对应语言容器（{imgs}）{ver}")
    lines.append(
        f"\n**要让某语言的代码读到你写的数据文件**：给 write_file/read_file/list_files 传 language 参数"
        f"（如 write_file(path, content, language=\"python\") 之后 run_python 就能读到）；"
        f"不传 language 则落 shell 容器，run_python 等**看不到**。跨语言之间文件也不共享。")
    # 用户实测踩坑：AI 在 python 容器 pip 装好依赖后，切去 run_shell 想接着用/继续 pip，结果
    # 到了独立的 shell 容器（没 python/pip、也看不到 pylibs），pip 一直报错、回不来。讲死这条。
    lines.append(
        f"\n**run_shell 是独立的 shell 容器**（{shell_img}），与 run_python/run_node/run_java 的容器"
        f"**完全隔开**：它看不到你在那些语言容器里装的包或写的文件，通常也**没有 python/pip/node**。"
        f"所以：**你在 run_python 里 pip 装的包、写的文件，只有 run_python 能用**；想在这套 python 环境里"
        f"执行 shell 命令（跑脚本、装包、看文件等），一律用 run_python 里的 `subprocess`/`os.system`"
        f"（同一容器），**不要切去 run_shell**——那是另一个容器，你装的东西它一概没有。run_shell 只用于"
        f"与语言环境无关的纯 shell 操作。")
    # 装依赖：先讲权限硬约束（防止照旧文案去 apt 白撞），再按网络讲可行路径
    if online:
        lines.append(
            "关于装依赖：沙箱是加固环境——**非 root 用户、禁止提权、系统目录不可写**。因此"
            "apt/dnf/apk 装系统包、以及 pip 全局装、npm -g 全局装一律会 Permission denied，不要尝试"
            "（即便可联网也一样，这是权限问题、不是网络问题）。"
            f"确需额外的语言包时，只能装到可写的工作目录 {ws} 内："
            f"Python 用 `pip install --no-cache-dir --target=<{ws} 下的子目录> 包名`，再把该目录加入 sys.path；"
            f"Node 在 {ws} 里 `npm i 包名` 装到本地 node_modules。"
            # 用户实测：AI 去 pip install llama-index，30s 执行超时被杀（exit 124）。大框架依赖成百上千、
            # 还常带 C 扩展，slim 镜像又没编译器，给再多时间也装不完。必须把这条讲死，否则 AI 白撞。
            f"**但装包受上面那条『单次执行超时 {exec_to}s』严格限制**：只有体量小、纯 Python、依赖少的包"
            f"才可能在预算内装完；镜像通常不带编译器，带 C/C++ 扩展的包（numpy 之外多数科学库、需编译的）装不了；"
            f"**大型框架（如 llama-index、langchain、torch、tensorflow、transformers 等）依赖极多、体量巨大，"
            f"必然超时/OOM/编译失败——不要尝试**，直接改用镜像预装的库，或如实告诉用户「该库在沙箱里装不了」并给替代思路。"
            f"任何情况下都优先使用镜像预装的库与命令；装包失败就改用预装的等价物，别反复重试。")
    else:
        lines.append(
            "关于装依赖：容器禁止联网，且非 root、系统目录不可写——apt/dnf、pip、npm 一律装不了，"
            "只能使用镜像已自带的标准库与预装命令，不要尝试联网安装（必然失败），改用镜像已有的等价命令。")
    return guide + "".join(lines)


# 工具名 → 语言（用于把 run_python 等映射到它将用的容器镜像）
_TOOL_LANG = {"run_python": "python", "run_node": "node", "run_java": "java", "run_shell": "shell"}


def tool_container_image(config, tool_name: str, args: dict | None = None) -> str | None:
    """容器工具（run_python/run_node/run_java/run_shell）**将要**用的镜像名。

    确定性映射（语言[+version]→镜像），故在工具开始执行时就能算出、无需等它跑完——
    让前端在「执行中」就能标出用的哪个镜像（run_python 可能跑很久，如 pip 装依赖）。
    非容器工具或非 docker 后端返回 None（前端不显示）。
    """
    if getattr(config, "sandbox_backend", "") != "docker":
        return None
    lang = _TOOL_LANG.get(tool_name)
    if lang is None:
        return None
    if lang == "shell":
        return getattr(config, "sandbox_shell_image", "") or None
    images = getattr(config, "sandbox_lang_images", None) or {}
    version = (args or {}).get("version")
    if version and f"{lang}{version}" in images:
        return images[f"{lang}{version}"]
    return images.get(lang) or getattr(config, "sandbox_shell_image", "") or None


# 当前请求所属会话；由 chat 处理器在 pump() 内 set，工具执行都在此上下文内。
_current_conv: ContextVar[str | None] = ContextVar("sandbox_conv", default=None)

_SANDBOX_LABEL = "mcp_sandbox"


def set_sandbox_conv(conv_id: str):
    """进入某会话的沙箱上下文，返回 token 供 reset。"""
    return _current_conv.set(conv_id)


def reset_sandbox_conv(token) -> None:
    _current_conv.reset(token)


@dataclass
class _Entry:
    box: object
    last_used: float = field(default_factory=time.monotonic)


class SandboxManager:
    """按 (conv_id, 语言) 管理真实 Sandbox 容器的生命周期。"""

    def __init__(self, config) -> None:
        self._config = config
        # 语言容器缓存：键 (conv_id, label)，label 形如 "python"/"shell"/"java17"/"local"。
        self._boxes: dict[tuple[str, str], _Entry] = {}
        # 浏览器沙箱：全局共用一个（跨会话），懒加载创建/启动、复用，24h 空闲或关停时销毁。
        self._browser: _Entry | None = None
        # 每会话上传附件清单 [(相对路径, bytes)]，新建语言容器时播种进 /workspace/uploads/。
        self._uploads: dict[str, list[tuple[str, bytes]]] = {}
        self._lock = asyncio.Lock()
        self._idle_timeout = float(getattr(config, "sandbox_idle_timeout", 0) or 0)
        self._browser_idle = float(getattr(config, "browser_sandbox_idle_timeout", 0) or 0)

    def _resolve_image(self, language: str | None, version: str | None) -> tuple[str, str | None]:
        """解析 (label, image)。

        本地后端：所有语言共用一个本地沙箱（label 恒 "local"，无镜像）。
        docker：language=None/"shell"/未知 → shell 容器；否则查 sandbox_lang_images
        （显式 version 但 f"{lang}{version}" 无镜像 → 报错，让模型换版本）。
        """
        cfg = self._config
        if getattr(cfg, "sandbox_backend", "") != "docker":
            return "local", None
        lang = language or "shell"
        if lang == "shell":
            return "shell", getattr(cfg, "sandbox_shell_image", "")
        images = getattr(cfg, "sandbox_lang_images", None) or {}
        if version:
            key = f"{lang}{version}"
            if key not in images:
                avail = ", ".join(sorted(images)) or "（无）"
                raise SandboxError(f"没有可用的语言/版本镜像：{key}。可用：{avail}")
            return key, images[key]
        if lang in images:
            return lang, images[lang]
        return "shell", getattr(cfg, "sandbox_shell_image", "")   # 未知语言 → 回退 shell 容器

    def _make_box(self, conv_id: str, label: str, image: str | None):
        labels = {_SANDBOX_LABEL: "true", "conv_id": conv_id, "lang": label}
        display = f"{label} 容器（{image}）" if image else f"{label} 沙箱"
        return build_sandbox(self._config, labels=labels, image=image, display_name=display)

    async def get_lang(self, conv_id: str, language: str | None = None,
                       version: str | None = None):
        """取（或惰性创建/复用）该会话某语言的容器，刷新空闲计时，播种上传附件后返回。"""
        if not conv_id:
            raise SandboxError("无当前会话上下文，无法解析沙箱")
        label, image = self._resolve_image(language, version)   # SandboxError→is_error
        async with self._lock:
            await self._evict_idle(keep=conv_id)
            key = (conv_id, label)
            entry = self._boxes.get(key)
            is_new = entry is None
            if is_new:
                entry = _Entry(box=self._make_box(conv_id, label, image))
                self._boxes[key] = entry
            entry.last_used = time.monotonic()
            box = entry.box
        await box.start()
        if is_new:                                  # 新容器：播种本会话已登记的上传附件
            await self._seed_into(box, self._uploads.get(conv_id, []))
        return box

    async def set_uploads(self, conv_id: str, uploads) -> None:
        """累加登记本会话新上传的附件（按路径去重合并），并把**这批新附件**播种进该会话
        已存在的所有容器；合并后的全量清单供之后新建的容器播种。

        累加而非覆盖：容器按语言惰性创建，后建的容器也要能看到之前几轮上传的附件
        （对齐旧「基础容器跨轮累积」的语义）。本轮无新附件则原样保留。"""
        if not conv_id:
            return
        new = list(uploads or [])
        if not new:
            return
        async with self._lock:
            merged = dict(self._uploads.get(conv_id, []))   # path -> data
            merged.update(new)
            self._uploads[conv_id] = list(merged.items())
            boxes = [e.box for (cid, _), e in self._boxes.items() if cid == conv_id]
        for box in boxes:                                   # 已存在的容器只需补这批新附件
            await self._seed_into(box, new)

    @staticmethod
    async def _seed_into(box, uploads) -> None:
        for path, data in uploads:
            try:
                await box.write_bytes(path, data)
            except Exception as e:   # 播种失败不应打断对话
                _log.warning("附件播种进容器失败 %s：%s", path, e)

    async def get_browser(self) -> tuple[object, bool]:
        """取（或惰性创建）**全局共用**的浏览器沙箱（专用 playwright 镜像），返回 (box, True)。

        全局一个、跨会话共用；空闲超 browser_sandbox_idle_timeout（默认 24h）由 manager 回收、
        下次用再重建，关停时销毁。返回 cached 恒为 True——调用方不得销毁它，生命周期归 manager。
        浏览器需真实出网（用 sandbox_network）与更大内存（browser_sandbox_mem_limit，防 Chromium OOM）。"""
        cfg = self._config
        async with self._lock:
            await self._evict_idle(keep="")   # 顺带回收空闲超时的容器（含 24h 全局浏览器）
            if self._browser is None:
                labels = {_SANDBOX_LABEL: "true", "role": "browser-global"}
                box = _docker_for(cfg, cfg.browser_sandbox_image, labels=labels,
                                  network=cfg.sandbox_network, display_name="浏览器沙箱",
                                  mem_limit=getattr(cfg, "browser_sandbox_mem_limit", None))
                self._browser = _Entry(box=box)
            self._browser.last_used = time.monotonic()
            return self._browser.box, True

    async def destroy(self, conv_id: str) -> None:
        """销毁某会话的**全部**语言容器（删除会话时调用）。不存在则静默。"""
        async with self._lock:
            keys = [k for k in self._boxes if k[0] == conv_id]
            entries = [(k, self._boxes.pop(k)) for k in keys]
            self._uploads.pop(conv_id, None)
        for (cid, label), entry in entries:
            await self._safe_close(entry.box, f"{cid}/{label}")

    async def close_all(self) -> None:
        """关停时销毁全部会话语言容器与全局浏览器沙箱。"""
        async with self._lock:
            items = list(self._boxes.items())
            browser = self._browser
            self._boxes.clear()
            self._uploads.clear()
            self._browser = None
        for (cid, label), entry in items:
            await self._safe_close(entry.box, f"{cid}/{label}")
        if browser is not None:
            await self._safe_close(browser.box, "浏览器")

    async def _evict_idle(self, keep: str) -> None:
        """惰性驱逐空闲超时的语言容器与全局浏览器（在锁内调用）。keep 为本次要用的会话，不驱逐。"""
        now = time.monotonic()
        dead: list = []
        if self._idle_timeout > 0:
            stale = [k for k, e in self._boxes.items()
                     if k[0] != keep and now - e.last_used > self._idle_timeout]
            dead = [(k, self._boxes.pop(k)) for k in stale]
        dead_browser = None
        if self._browser is not None and self._browser_idle > 0 \
                and now - self._browser.last_used > self._browser_idle:
            dead_browser = self._browser.box
            self._browser = None
        for (cid, label), entry in dead:
            await self._safe_close(entry.box, f"{cid}/{label}")
        if dead_browser is not None:
            await self._safe_close(dead_browser, "浏览器")

    @staticmethod
    async def _safe_close(box, who: str) -> None:
        try:
            await box.close()
        except Exception as e:  # 清理失败不应打断删除/关停流程
            _log.warning("销毁 %s 沙箱失败：%s", who, e)

    def sweep_orphans(self) -> None:
        """进程启动时回收上次遗留的沙箱容器（按标签）。best-effort，同步执行。

        刚启动时内存映射为空，daemon 上带 mcp_sandbox 标签的容器必是上次遗留的孤儿。
        """
        cfg = self._config
        if getattr(cfg, "sandbox_backend", "") != "docker" or not cfg.sandbox_docker_host:
            return
        try:
            import docker
            from docker.tls import TLSConfig
            client_cert = ((cfg.sandbox_docker_tls_client_cert, cfg.sandbox_docker_tls_client_key)
                           if cfg.sandbox_docker_tls_client_cert and cfg.sandbox_docker_tls_client_key
                           else None)
            tls = TLSConfig(client_cert=client_cert,
                            ca_cert=cfg.sandbox_docker_tls_ca_cert or None,
                            verify=cfg.sandbox_docker_tls_verify)
            client = docker.DockerClient(base_url=cfg.sandbox_docker_host, tls=tls)
            try:
                leftovers = client.containers.list(
                    all=True, filters={"label": f"{_SANDBOX_LABEL}=true"})
                for c in leftovers:
                    try:
                        c.remove(force=True)
                    except Exception as e:
                        _log.warning("回收孤儿容器 %s 失败：%s", getattr(c, "id", "?"), e)
                if leftovers:
                    _log.info("启动清扫：回收了 %d 个遗留沙箱容器", len(leftovers))
            finally:
                client.close()
        except Exception as e:  # daemon 不可达等，不阻断启动
            _log.warning("启动清扫沙箱孤儿容器失败：%s", e)


class SandboxProxy:
    """实现 Sandbox 协议：按当前会话上下文 + 语言把调用委托给真实的语言容器。

    - for_language(language, version)：取到该语言的容器（核心原语，代码/文件工具都用它）。
    - 协议方法（exec/write_file/read_file/list_files）**缺省落 shell 容器**——供 run_shell、
      未指定 language 的文件工具、以及 DNS 解析等内部调用。
    - set_uploads：登记本会话上传附件，供各容器播种。
    - close() 为空实现——真实容器生命周期归 SandboxManager（destroy/close_all/空闲驱逐）。
    """

    def __init__(self, manager: SandboxManager) -> None:
        self._m = manager
        self.workspace = manager._config.sandbox_workspace

    async def for_language(self, language: str | None = None, version: str | None = None):
        return await self._m.get_lang(_current_conv.get(), language, version)

    async def _shell(self):
        return await self._m.get_lang(_current_conv.get(), "shell")

    async def start(self) -> None:
        # 容器按需惰性创建（见 for_language / 各协议方法），无需在此预建
        return None

    async def close(self) -> None:
        return None

    async def exec(self, command: list, timeout: float, *, quiet: bool = False):
        return await (await self._shell()).exec(command, timeout, quiet=quiet)

    async def write_file(self, path: str, content: str) -> None:
        await (await self._shell()).write_file(path, content)

    async def write_bytes(self, path: str, data: bytes) -> None:
        await (await self._shell()).write_bytes(path, data)

    async def read_file(self, path: str) -> str:
        return await (await self._shell()).read_file(path)

    async def list_files(self, path: str = ".") -> list:
        return await (await self._shell()).list_files(path)

    async def set_uploads(self, uploads) -> None:
        await self._m.set_uploads(_current_conv.get(), uploads)
