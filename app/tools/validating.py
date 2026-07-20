# app/tools/validating.py
"""每步正确性校验（实时层）：用装饰器包住高风险工具，规则/阈值判定，内核零改动。

两种校验策略：
- result 型（检索）：inner 正常返回文本，按文本判定；失败把 hint 追加到结果尾部，
  驱动模型下一步自纠正（不硬阻断）。
- exec 型（代码/命令）：inner 失败会 raise ToolError（非零退出/超时），捕获后 emit
  校验事件再原样重抛——语义不变，仍走内核 executor 的 is_error 自纠正。

校验结果经 harness.progress.emit(Progress(scope="check", ...)) 发到 SSE 流，前端展示
每步校验标记。校验逻辑自身异常绝不吞掉 inner 原结果（仅跳过该步校验）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from harness.events import Progress
from harness.progress import emit
from harness.tools.base import Tool, ToolError
from harness.types import ToolOutput

_log = logging.getLogger("app.validating")

# 与 SearchKnowledgeTool 的空命中文案、verify.py 的 _NO_HIT 保持一致
NO_HIT_MARK = "（未在知识库中检索到相关内容）"


@dataclass
class CheckResult:
    ok: bool
    text: str            # 前端展示文案
    hint: str = ""       # 追加到结果尾部、驱动自纠正的提示（仅 ok=False 生效）


class ValidatingTool(Tool):
    """包住 inner 工具做每步规则校验。透明代理 name/description/Params/schema。"""

    def __init__(
        self,
        inner: Tool,
        check: Callable[[str], CheckResult] | None = None,
        *,
        exec_mode: bool = False,
    ) -> None:
        self._inner = inner
        self._check = check
        self._exec_mode = exec_mode
        # 对外完全等同 inner：注册键、schema、参数模型都取 inner 的
        self.name = inner.name
        self.description = inner.description
        self.Params = inner.Params

    def schema(self) -> dict:
        return self._inner.schema()

    async def run(self, params):
        if self._exec_mode:
            return await self._run_exec(params)
        return await self._run_result(params)

    async def _run_exec(self, params):
        try:
            raw = await self._inner.run(params)
        except ToolError:
            emit(Progress(scope="check", text=f"{self.name} 执行未通过",
                          status="error", key=f"check:{self.name}"))
            raise                                    # 语义不变：继续走 is_error 自纠正
        emit(Progress(scope="check", text=f"{self.name} 执行通过",
                      status="ok", key=f"check:{self.name}"))
        return raw

    async def _run_result(self, params):
        raw = await self._inner.run(params)
        text = raw.text if isinstance(raw, ToolOutput) else raw
        try:
            verdict = self._check(text) if self._check else CheckResult(True, "")
        except Exception as e:                       # 校验器自身故障 → 放行不拦截
            _log.warning("每步校验异常，跳过（%s）：%s", self.name, e)
            return raw
        emit(Progress(scope="check", text=verdict.text,
                      status="ok" if verdict.ok else "error",
                      key=f"check:{self.name}"))
        if not verdict.ok and verdict.hint:
            return _append_hint(raw, verdict.hint)
        return raw


def _append_hint(raw, hint: str):
    """把 hint 追加到工具结果尾部，保持 str / ToolOutput 形态。"""
    note = f"\n\n[校验提示] {hint}"
    if isinstance(raw, ToolOutput):
        return ToolOutput(text=raw.text + note, follow_up=raw.follow_up)
    return (raw or "") + note


def relevance_check(text: str) -> CheckResult:
    """检索结果相关性：起步只判空命中（工具未暴露相似度分数）。"""
    t = (text or "").strip()
    if not t or NO_HIT_MARK in t:
        return CheckResult(
            ok=False, text="未命中知识库",
            hint="本次未检索到知识库依据，请勿臆造事实；如无把握请明确告知用户资料不足。")
    return CheckResult(ok=True, text="检索命中")
