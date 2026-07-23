# app/logging_setup.py
"""全局日志配置 + 请求关联 id。

项目此前无任何 logging 配置，getLogger 全靠「last resort」handler（仅 WARNING+ 到
stderr），INFO/DEBUG 被静默丢弃 —— 这是「看不到运行状况」的根因。此模块提供：

- configure_logging()：幂等地给根 logger 装一个统一格式的 StreamHandler，级别由
  HARNESS_LOG_LEVEL 环境变量控制（默认 INFO），并把吵闹的第三方库压到 WARNING。
- set_log_context(**fields)：把 user/run_id/conv_id 等塞进 contextvar，之后同一异步任务内
  每条日志自动带上 `[user=.. run_id=.. conv_id=..]` 前缀，便于把一次请求从头到尾串起来看，
  也能一眼看出是哪个账号在操作。字段是**合并**语义：中间件在请求入口设的 user 不会被
  业务代码后续设的 conv_id/run_id 冲掉。
"""
from __future__ import annotations

import logging
import os
from contextvars import ContextVar

# 当前执行上下文的关联字段（有序键值对，空元组=无）。contextvars 天然随 await/子任务传播。
_log_ctx: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("log_ctx", default=())

_DEFAULT_LEVEL = "INFO"
# 默认压到 WARNING 的吵闹第三方 logger（它们的 INFO/DEBUG 会淹没应用日志）
_NOISY = ("httpx", "httpcore", "openai", "urllib3", "asyncio", "docker",
          "python_multipart", "multipart", "watchfiles")


class _CorrelationFilter(logging.Filter):
    """给每条经过本 handler 的日志记录注入 `corr` 字段，供格式串引用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.corr = " ".join(f"{k}={v}" for k, v in _log_ctx.get()) or "-"
        return True


class _LogContextToken:
    def __init__(self, token) -> None:
        self._token = token

    def __enter__(self) -> "_LogContextToken":
        return self

    def __exit__(self, *exc) -> None:
        _log_ctx.reset(self._token)


def set_log_context(**fields) -> _LogContextToken:
    """把关联字段并入当前上下文，返回可用作 `with` 的 token（退出即还原）。
    用法：`with set_log_context(conv_id=..., run_id=...): ...`。空值字段忽略。
    同名字段覆盖旧值、位置不变；已有的其它字段（如入口中间件设的 user）保留。"""
    merged = dict(_log_ctx.get())
    merged.update({k: str(v) for k, v in fields.items() if v})
    token = _log_ctx.set(tuple(merged.items()))
    return _LogContextToken(token)


def configure_logging(level: str | None = None) -> None:
    """幂等配置根 logger。级别优先级：显式 level > HARNESS_LOG_LEVEL > INFO。
    多次调用只装一次 handler，仅更新级别。"""
    lvl = (level or os.environ.get("HARNESS_LOG_LEVEL") or _DEFAULT_LEVEL).upper()
    root = logging.getLogger()
    root.setLevel(lvl)
    for h in root.handlers:
        if getattr(h, "_harness_configured", False):   # 已装过 → 只更新级别
            h.setLevel(lvl)
            for name in _NOISY:
                logging.getLogger(name).setLevel(logging.WARNING)
            return
    handler = logging.StreamHandler()
    handler._harness_configured = True                 # type: ignore[attr-defined]
    handler.setLevel(lvl)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-7s [%(corr)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    handler.addFilter(_CorrelationFilter())
    root.addHandler(handler)
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
