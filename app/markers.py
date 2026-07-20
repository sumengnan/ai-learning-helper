# app/markers.py
"""工具结果里的机读标记（〔下载ID:x〕〔知识ID:x〕〔题目ID:x〕）。

标记供服务端追踪本轮产物、前端渲染下载按钮，用户与模型都不该看到那串 id：
用户拿它做不了任何事，模型看见了就会当成有用信息抄进正文（「知识库ID：ba87f8…」）。

当前轮由 ToolOutput.marker 挡在模型上下文之外；本模块管的是另一条缝——历史回放：
落库的 steps 带着标记，下一轮会被 ConversationStore.messages 喂回模型。

题目ID 例外：start_exam 的说明明确要模型从〔题目ID:...〕里取 id 来指定考题，不能剥。
"""
from __future__ import annotations

import re

# 只剥对模型无用的两类；题目ID 留着（模型要用它调 start_exam）
_HIDDEN_MARKER_RE = re.compile(r"〔(?:下载|知识)ID:[^〕]*〕")


def strip_hidden_markers(text: str) -> str:
    return _HIDDEN_MARKER_RE.sub("", text or "")
