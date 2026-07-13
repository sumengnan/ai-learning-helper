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
from urllib.parse import urlparse

from harness.tools.base import Tool
from harness.types import ToolOutput

# 供 chat.py 追加进系统提示，告知模型引用约定
SOURCE_GUIDE = (
    "\n\n【引用来源】你调用检索/联网/抽题/读附件等工具后，其结果末尾会带一个"
    "〔参考来源 [n]…〕标注。当你在回答正文里用到某条资料时，请在相应语句后写上对应的 "
    "[n]（例如「光合作用发生在叶绿体中[1]」）。只引用真实出现过的编号，不要编造。\n")


def _domain(url: str) -> str:
    try:
        host = urlparse(url).hostname or url
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return url


def _clip(text: str, n: int = 80) -> str:
    t = " ".join((text or "").split())
    return t[:n] + "…" if len(t) > n else t


# ---- 各工具的来源 builder：入参 (args, result)，出参 dict|None（不含 index）----

def _b_search_memory(args: dict, result: str) -> dict | None:
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


_CODE_LABEL = {"run_python": "Python 代码执行",
               "run_node": "Node 代码执行",
               "run_java": "Java 代码执行"}


def _b_code(tool_name: str):
    def build(args: dict, result: str) -> dict | None:
        return {"type": "code", "label": _CODE_LABEL[tool_name],
                "detail": _clip(result or "", 160)}
    return build


_BUILDERS = {
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

    def reset(self) -> None:
        self._items = []
        self._seen = {}

    def record(self, desc: dict) -> int:
        """记入一条来源，返回其编号（1 起）。重复来源复用既有编号。"""
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
        marker = (f"\n\n〔本结果对应参考来源 [{n}]：{desc['label']}，"
                  f"正文引用此来源请写 [{n}]〕")
        if isinstance(raw, ToolOutput):
            return ToolOutput(text=(text or "") + marker, follow_up=raw.follow_up)
        return (text or "") + marker


def wrap_tool(tool: Tool, sink: SourceSink) -> Tool:
    """来源工具包一层记源；非来源工具原样返回。"""
    return _SourceTaggedTool(tool, sink) if is_source_tool(tool.name) else tool
