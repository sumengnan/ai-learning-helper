# app/sources.py
"""AI 回复的来源标注：从工具调用中提炼「参考来源」，并在结果里接地内联编号。

设计见 docs/superpowers/specs/2026-07-11-ai-reply-sources-design.md。

- `build_source(tool_name, args, result)`：把一次「产生来源」的工具调用提炼成一条来源描述；
  非来源工具 / 空结果返回 None。
- `SourceSink`：一轮请求内累积来源并分配顺序号；交付门重试时每次尝试前 reset、交付时快照。
- `wrap_tool`：给工具包一层，成功返回时记源 + 在结果末尾追加 `〔…参考来源 [n]…〕` 标注，
  让模型在正文引用同一 `[n]`。异常先于记录抛出，故报错不记源。
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from urllib.parse import urlparse

from harness.tools.base import Tool
from harness.types import ToolOutput

# 供 chat.py 追加进系统提示，告知模型引用约定
SOURCE_GUIDE = (
    "\n\n【引用来源】你调用检索/联网/抽题/读附件等工具后，其结果末尾会带一个"
    "〔参考来源 [n]…〕标注。当你在回答正文里用到某条资料时，请在相应语句后写上对应的 "
    "[n]（例如「光合作用发生在叶绿体中[1]」）。只引用真实出现过的编号，不要编造。\n")

# 正文内联来源角标 [1]/[12]…；连同紧邻的前导空格一起吃掉，删后不留孤立空格。
_CITATION_RE = re.compile(r"[ \t]*\[\d+\]")


def strip_citations(text: str) -> str:
    """去掉正文里的内联来源角标 [n]（见 SOURCE_GUIDE 约定）。

    保存到知识库的是「可检索素材」：编号 [1] 脱离原对话后既无指向、又是语义检索噪声，
    故入库前剥离。只删纯数字角标 [n]，不动 [文字](链接)——后者已由 strip_markdown 处理。
    """
    if not text:
        return text
    return _CITATION_RE.sub("", text)


# 编排器内部的步骤标记 [s1]/[s12]…（计划步 id）。与来源角标 [n] 形似但来路完全不同：
# 它来自执行子步/汇总提示词里对前置产出的标注，模型复用内容时会连前缀一起抄出来。
# 连同紧邻的前后空格一起吃掉：漏出来的形态是「[s2] # 标题」，只删记号会留下前导空格。
_STEP_MARKER_RE = re.compile(r"[ \t]*\[s\d+\][ \t]*")


# 围栏代码块 ```…``` 与行内代码 `…`：剥角标时整段跳过。
# 代码里的 arr[1]/nums[0] 形态与角标 [n] 完全一致，_CITATION_RE 的前导空格又是可选的，
# 不跳过就会把 arr[1] 削成 arr——导出的代码笔记直接被改坏。
_CODE_SPAN_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)


def _outside_code(text: str, sub) -> str:
    """只对代码块之外的部分做替换，代码原样保留。"""
    out, last = [], 0
    for m in _CODE_SPAN_RE.finditer(text):
        out.append(sub(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(sub(text[last:]))
    return "".join(out)


def strip_citations_outside_code(text: str) -> str:
    """剥离正文角标 [n]，但跳过代码块（见 _outside_code）。用于交付给用户的成品文件。"""
    if not text:
        return text
    return _outside_code(text, lambda s: _CITATION_RE.sub("", s))


def strip_step_markers(text: str) -> str:
    """去掉漏进正文的内部步骤标记 [sN]。

    这是编排管道的内部记号，对用户毫无意义。根因已在 _build_prompt / _synth_user 里
    改掉（id 不再紧贴正文），这里是交付给用户前的兜底——成品文件不该带管道残留。
    """
    if not text:
        return text
    return _STEP_MARKER_RE.sub("", text)


def _domain(url: str) -> str:
    try:
        host = urlparse(url).hostname or url
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return url


def _clip(text: str, n: int = 80) -> str:
    t = " ".join((text or "").split())
    return t[:n] + "…" if len(t) > n else t


# http_request 结果以 "HTTP {status}" 开头（见 render_http_result），据此取状态码
_HTTP_STATUS = re.compile(r"^HTTP (\d{3})")

# 拦截 / 错误页标志（CDN 拦截、访问拒绝等）：命中则抓取实为失败，不算有效来源
_ERROR_PAGE_SIGNALS = (
    "the request could not be satisfied",   # CloudFront 拦截页
    "access denied", "403 forbidden", "404 not found", "error 1020",
    "请求无法满足", "访问被拒绝", "拒绝访问",
)


def _http_status(result: str) -> int | None:
    m = _HTTP_STATUS.match(result or "")
    return int(m.group(1)) if m else None


def _looks_error_page(result: str) -> bool:
    """抓取结果是否为拦截/错误页（标志文案在开头或标题里）。"""
    return any(s in (result or "")[:300].lower() for s in _ERROR_PAGE_SIGNALS)


def _no_real_content(result: str) -> bool:
    return "无可提取正文" in (result or "")


# RFC 2606/6761 保留给文档示例的域名，以及常见的域名停放/待售页文案。
# 这类页面真实存在、稳定返回 200、有标题有正文——所有「抓取是否成功」的判据都拦不住它们。
# 模型编造网址时最容易撞上 example.com 这一族，命中即视为无效抓取（不是真实资料来源）。
_PLACEHOLDER_HOSTS = frozenset({
    "example.com", "example.org", "example.net", "example.edu",
    "localhost", "127.0.0.1", "0.0.0.0",
})
_PLACEHOLDER_MARKS = (
    "this domain is for use in documentation examples",
    "domain is for use in illustrative examples",
    "此域名可用于文档示例",
    "this domain is parked", "domain is for sale", "buy this domain",
)


def final_url_of(result: str) -> str:
    """从抓取结果里取「最终URL：」行（跟随重定向后的真实地址）；取不到返回空串。"""
    for line in (result or "").splitlines():
        if line.startswith("最终URL："):
            return line[len("最终URL："):].strip()
    return ""


def is_placeholder_host(url: str) -> bool:
    """URL 的主机是否为保留/占位域名。

    子域名一并算：模型编造端点时最爱写 api.example.com、www.example.org 这种，
    只做精确匹配会全部漏过（此前就漏了）。
    """
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in _PLACEHOLDER_HOSTS)


def looks_placeholder_page(result: str, url: str = "") -> bool:
    """是否为占位/示例域名或域名停放页。优先按最终URL判域名，其次按页面文案。"""
    if is_placeholder_host(url or final_url_of(result)):
        return True
    return any(m in (result or "")[:600].lower() for m in _PLACEHOLDER_MARKS)


# ---- 各工具的来源 builder：入参 (args, result)，出参 dict|None（不含 index）----

def _b_search_knowledge(args: dict, result: str) -> dict | None:
    if not result or result.startswith("（未在知识库"):
        return None
    files: list[str] = []
    marker = "（来源："
    i = result.find(marker)
    while i != -1:
        j = result.find("）", i)
        name = result[i + len(marker):j].strip() if j != -1 else ""
        if name and name not in files:
            files.append(name)
        i = result.find(marker, j + 1 if j != -1 else i + len(marker))
    label = "、".join(files) if files else "知识库检索"
    return {"type": "knowledge", "label": _clip(label, 120)}


def _b_browse(args: dict, result: str) -> dict | None:
    # 只收抓到真实内容的网页：拦截/错误页、无正文、占位/停放域名一律不记源
    if (_looks_error_page(result) or _no_real_content(result)
            or looks_placeholder_page(result, str(args.get("url") or ""))):
        return None
    title = url = ""
    for line in (result or "").splitlines():
        if line.startswith("标题：") and not title:
            title = line[len("标题："):].strip()
        elif line.startswith("最终URL：") and not url:
            url = line[len("最终URL："):].strip()
    url = url or str(args.get("url") or "")
    label = title or _domain(url) or "网页"
    return {"type": "web", "label": _clip(label, 120), "url": url}


def _b_http(args: dict, result: str) -> dict | None:
    url = str(args.get("url") or "")
    if not url:
        return None
    # 只收 HTTP 200 且有真实内容的抓取：非 200 / 拦截错误页 / 无正文都不记源。
    # （浏览器兜底成功的结果无 "HTTP nnn" 前缀，status 为 None，仅按内容判定。）
    status = _http_status(result)
    if status is not None and status != 200:
        return None
    if (_looks_error_page(result) or _no_real_content(result)
            or looks_placeholder_page(result, url)):
        return None
    return {"type": "web", "label": _domain(url), "url": url}


def _b_sample_questions(args: dict, result: str) -> dict | None:
    if not result or result.startswith("题库为空"):
        return None
    count = None
    try:
        data = json.loads(result)
        if isinstance(data, list):
            count = len(data)
    except (ValueError, TypeError):
        count = args.get("count")
    label = f"题库抽题 {count} 道" if count else "题库抽题"
    return {"type": "question", "label": label}


def _b_sample_wrong_answers(args: dict, result: str) -> dict | None:
    if not result or result.startswith("错题集为空"):
        return None
    count = None
    try:
        data = json.loads(result)
        if isinstance(data, list):
            count = len(data)
    except (ValueError, TypeError):
        count = args.get("count")
    label = f"错题集抽题 {count} 道" if count else "错题集抽题"
    return {"type": "question", "label": label}


def _b_read_attachment(args: dict, result: str) -> dict | None:
    if not result or result.startswith("未找到该附件"):
        return None
    name = ""
    if result.startswith("「"):
        j = result.find("」")
        if j != -1:
            name = result[1:j]
    return {"type": "attachment", "label": name or "上传附件"}


def _b_recall_episodes(args: dict, result: str) -> dict | None:
    if not result or result.startswith("（无相关历史经验"):
        return None
    return {"type": "memory", "label": "历史经验片段"}


def _b_search_memory(args: dict, result: str) -> dict | None:
    """AI 自己记下的长期记忆。与知识库同为「检索到的东西」，但不是可引用的资料来源，
    故归 type=memory（前端另一种配色/图标），不与 knowledge 混淆。"""
    if not result or result.startswith("（未检索到相关的长期记忆"):
        return None
    return {"type": "memory", "label": "长期记忆"}


_CODE_LABEL = {"run_python": "Python 代码执行",
               "run_node": "Node 代码执行",
               "run_java": "Java 代码执行"}


def _b_code(tool_name: str):
    def build(args: dict, result: str) -> dict | None:
        return {"type": "code", "label": _CODE_LABEL[tool_name],
                "detail": _clip(result or "", 160)}
    return build


_BUILDERS = {
    "search_knowledge": _b_search_knowledge,
    "search_memory": _b_search_memory,
    "browse": _b_browse,
    "http_request": _b_http,
    "sample_questions": _b_sample_questions,
    "sample_wrong_answers": _b_sample_wrong_answers,
    "read_attachment": _b_read_attachment,
    "recall_episodes": _b_recall_episodes,
    "run_python": _b_code("run_python"),
    "run_node": _b_code("run_node"),
    "run_java": _b_code("run_java"),
}


def build_source(tool_name: str, args: dict, result: str) -> dict | None:
    """把一次工具调用提炼成来源描述；非来源工具 / 空结果返回 None。提炼绝不抛错。"""
    try:
        if tool_name.startswith("mcp__"):
            parts = tool_name.split("__", 2)
            server = parts[1] if len(parts) > 1 else ""
            tool = parts[2] if len(parts) > 2 else ""
            label = f"{server} · {tool}" if server and tool else tool_name
            return {"type": "mcp", "label": label, "detail": _clip(result or "", 160)}
        builder = _BUILDERS.get(tool_name)
        if builder is None:
            return None
        return builder(args or {}, result or "")
    except Exception:
        return None


def is_source_tool(tool_name: str) -> bool:
    return tool_name.startswith("mcp__") or tool_name in _BUILDERS


def _dedupe_key(desc: dict) -> str:
    # 同 URL / 同 (type,label) 视为同一来源
    return desc.get("url") or f"{desc['type']}::{desc['label']}"


class SourceSink:
    """一轮请求内的来源累积器：记源、分配顺序号、去重、快照。"""

    def __init__(self) -> None:
        self._items: list[dict] = []
        self._seen: dict[str, int] = {}
        self._paused = False

    def reset(self) -> None:
        self._items = []
        self._seen = {}

    @contextmanager
    def paused(self):
        """暂停记源。

        交付门校验器（跑答案里的代码块、核对引用链接可达性）用的是同一个已包记源层的
        registry，其工具调用会被当成模型的「参考来源」——但用户从没看见 AI 执行过这些，
        它们是后台基建行为。核链接尤其糟：模型只是在正文写了个 URL（可能是编的），从没
        读过它，却因为交付门去核实了一下反被「认证」成来源。
        """
        prev = self._paused
        self._paused = True
        try:
            yield
        finally:
            self._paused = prev

    def record(self, desc: dict) -> int | None:
        """记入一条来源，返回其编号（1 起）。重复来源复用既有编号。暂停中返回 None。"""
        if self._paused:
            return None
        key = _dedupe_key(desc)
        if key in self._seen:
            return self._seen[key]
        index = len(self._items) + 1
        item = {"index": index, **desc}
        self._items.append(item)
        self._seen[key] = index
        return index

    def snapshot(self) -> list[dict]:
        return [dict(it) for it in self._items]


class _SourceTaggedTool(Tool):
    """包装一个来源工具：成功返回时记源并在结果末尾追加接地标注。"""

    def __init__(self, inner: Tool, sink: SourceSink) -> None:
        self._inner = inner
        self._sink = sink
        self.name = inner.name
        self.description = inner.description
        self.Params = inner.Params

    def schema(self) -> dict:
        return self._inner.schema()

    async def run(self, params):
        raw = await self._inner.run(params)   # 异常先于记源抛出：报错不记源
        text = raw.text if isinstance(raw, ToolOutput) else raw
        try:
            args = params.model_dump() if hasattr(params, "model_dump") else {}
            desc = build_source(self.name, args, text if isinstance(text, str) else "")
        except Exception:
            desc = None
        if desc is None:
            return raw
        n = self._sink.record(desc)
        if n is None:      # 记源已暂停（交付门校验期）：既不记源，也不追加标注——
            return raw     # 那段标注会混进校验器要解析的工具结果里
        marker = (f"\n\n〔本结果对应参考来源 [{n}]：{desc['label']}，"
                  f"正文引用此来源请写 [{n}]〕")
        if isinstance(raw, ToolOutput):
            return ToolOutput(text=(text or "") + marker, follow_up=raw.follow_up)
        return (text or "") + marker


def wrap_tool(tool: Tool, sink: SourceSink) -> Tool:
    """来源工具包一层记源；非来源工具原样返回。"""
    return _SourceTaggedTool(tool, sink) if is_source_tool(tool.name) else tool
