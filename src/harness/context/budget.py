from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContextBudget:
    """按 token 预算切分上下文各层。

    available = context_window - response_reserve - system_tokens
    working_tokens = available * working_ratio（L1 最近原文预算，其余留给 L2/L3）。
    """
    context_window: int
    response_reserve: int
    working_ratio: float

    def available(self, system_tokens: int) -> int:
        return max(self.context_window - self.response_reserve - system_tokens, 0)

    def working_tokens(self, system_tokens: int) -> int:
        return max(int(self.available(system_tokens) * self.working_ratio), 0)
