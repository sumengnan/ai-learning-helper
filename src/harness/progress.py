# src/harness/progress.py
"""进度旁路：让工具（沙箱初始化、子 agent 派发等）在执行过程中向外发事件。

工具的 run() 只能返回字符串、无法 yield 事件；这里用 contextvar 存一个 emitter，
调用方（如 chat 路由）在进入 loop 前设置它、并把事件并入 SSE 流。默认 emitter 为空，
不设置时 emit() 无副作用（对纯 harness 使用透明）。
"""
from __future__ import annotations

import contextvars

_emitter: contextvars.ContextVar = contextvars.ContextVar("progress_emitter", default=None)


def set_emitter(fn):
    """设置进度 emitter（fn 接收一个事件对象），返回 token 供 reset。"""
    return _emitter.set(fn)


def reset_emitter(token) -> None:
    _emitter.reset(token)


def emit(event) -> None:
    fn = _emitter.get()
    if fn is not None:
        fn(event)
