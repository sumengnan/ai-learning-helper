# src/harness/browser/sandboxed_browser.py
from __future__ import annotations

import json
from pathlib import Path

from .base import PageResult
from ..net import policy as _policy_mod

# Chromium 在 cap_drop=ALL + no-new-privileges + 非 root + 小 /dev/shm 下的必备参数
DEFAULT_LAUNCH_ARGS = [
    "--no-sandbox", "--disable-setuid-sandbox",
    "--disable-dev-shm-usage", "--disable-gpu",
]

_RUNNER_SRC = Path(__file__).with_name("browse_runner.py").read_text()
_POLICY_SRC = Path(_policy_mod.__file__).read_text()   # 逐字节副本 → 容器内 _policy.py，零漂移


class SandboxedBrowser:
    """在沙箱容器内跑无头 Chromium 抓网页（实现 Browser 协议）。

    与 SandboxedHttpRequestTool 对称：渲染/出网/DNS 解析都发生在容器内；SSRF 逐跳
    校验用宿主 net/policy.py 的逐字节副本在容器内执行。不拥有容器——容器由 assembly 属主创建/销毁。

    安全：fetch 的 url_validator 参数仅为满足 Browser 协议而保留，被有意忽略——宿主回调无法
    跨容器逐跳调用；等价策略由容器内 runner 依据 allowed_domains/block_private 执行。
    """

    def __init__(self, sandbox, allowed_domains, block_private: bool = True,
                 user_agent: str = "", launch_args=None,
                 timeout_margin: float = 10.0) -> None:
        self._sandbox = sandbox
        self._allowed = list(allowed_domains)
        self._block_private = block_private
        self._user_agent = user_agent
        self._launch_args = list(launch_args) if launch_args else list(DEFAULT_LAUNCH_ARGS)
        self._timeout_margin = timeout_margin
        self._provisioned = False

    async def start(self) -> None:
        await self._sandbox.start()   # 幂等；容器可能已由其他工具启动

    async def close(self) -> None:
        pass   # 共享容器由其属主（assembly/sandbox）关闭

    async def _provision(self) -> None:
        if self._provisioned:
            return
        await self._sandbox.write_file("_browse_runner.py", _RUNNER_SRC)
        await self._sandbox.write_file("_policy.py", _POLICY_SRC)
        self._provisioned = True

    async def fetch(self, url: str, timeout: float, wait_until: str,
                    url_validator=None) -> PageResult:
        await self._provision()
        await self._sandbox.write_file("_browse_input.json", json.dumps({
            "url": url, "timeout": timeout, "wait_until": wait_until,
            "allowed_domains": self._allowed, "block_private": self._block_private,
            "user_agent": self._user_agent, "launch_args": self._launch_args,
        }))
        res = await self._sandbox.exec(
            ["python3", "_browse_runner.py"], timeout + self._timeout_margin)

        out = None
        try:
            out = json.loads(await self._sandbox.read_file("_browse_output.json"))
        except Exception:
            out = None   # runner 崩溃前未落盘 → 下面用 stderr 兜底
        if not out or not out.get("ok"):
            err = out.get("error") if out else None
            raise RuntimeError(
                err or res.stderr.strip() or f"browse 失败（exit {res.exit_code}）")
        return PageResult(final_url=out["final_url"], title=out["title"],
                          html=out["html"])
