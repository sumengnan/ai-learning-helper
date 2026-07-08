# src/harness/browser/base.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class PageResult:
    final_url: str
    title: str
    html: str


@runtime_checkable
class Browser(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def fetch(self, url: str, timeout: float, wait_until: str) -> PageResult: ...
