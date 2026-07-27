> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# AI Harness 浏览器工具（子项目③b-2）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** `browse(url)` 工具——headless 浏览器渲染 JS 网页 + `trafilatura` 提正文，`browse` 前复用 ③b-1 `net/policy` 做 SSRF 防护。

**架构：** `browser/`（Browser 协议 + PlaywrightBrowser + FakeBrowser + extract + factory）；`BrowseTool` 只依赖协议。测试用 FakeBrowser + trafilatura 直测，不下载 chromium。

**技术栈：** 沿用①②③a③b-1 · 新增 `playwright`、`trafilatura`。

**规格：** `docs/superpowers/specs/2026-07-08-harness-browser-design.md`

**提交规范：** git 身份已是 sumengnan，默认提交；**任何 commit message 不得出现 Claude/AI/Co-Authored-By 等署名**。

---

## 文件结构与职责

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/harness/browser/base.py` | 新增 | `Browser` 协议 + `PageResult` |
| `src/harness/browser/extract.py` | 新增 | `extract_main_text(html)`（trafilatura，纯函数） |
| `src/harness/browser/fake.py` | 新增 | `FakeBrowser`（测试：预设 HTML） |
| `src/harness/browser/playwright_browser.py` | 新增 | `PlaywrightBrowser`（本地 chromium headless） |
| `src/harness/browser/factory.py` | 新增 | `build_browser(config)` |
| `src/harness/tools/builtins/browse_tool.py` | 新增 | `BrowseTool`（check_url + fetch + extract + 截断） |
| `src/harness/config.py` | 改 | browser_* 配置 |
| `pyproject.toml` | 改 | `playwright`、`trafilatura` |

---

## 任务 0：依赖与配置

**文件：** 改 `pyproject.toml`、`src/harness/config.py`、测试 `tests/test_config.py`

- [ ] **步骤 1：`pyproject.toml` 的 `dependencies` 追加**

```toml
    "playwright>=1.44",
    "trafilatura>=1.9",
```

- [ ] **步骤 2：`uv sync`**　运行：`uv sync`　预期：装上 playwright/trafilatura（chromium 二进制本计划不需要，测试用 FakeBrowser）。

- [ ] **步骤 3：写失败测试**（`tests/test_config.py` 追加）

```python
def test_browser_defaults():
    cfg = HarnessConfig(api_key="k")
    assert cfg.browser_headless is True
    assert cfg.browser_nav_timeout == 30.0
    assert cfg.browser_wait_until == "networkidle"
    assert cfg.browser_output_max_chars == 8000
    assert cfg.browser_user_agent == ""
```

运行：预期 FAIL。

- [ ] **步骤 4：改 `src/harness/config.py`** 末尾追加：

```python
    # 浏览器
    browser_headless: bool = True
    browser_nav_timeout: float = 30.0
    browser_wait_until: str = "networkidle"   # load | domcontentloaded | networkidle
    browser_output_max_chars: int = 8000
    browser_user_agent: str = ""
```

- [ ] **步骤 5：跑通并 commit**

运行：`uv run pytest tests/test_config.py -v`　预期全 pass。
```bash
git add pyproject.toml uv.lock src/harness/config.py tests/test_config.py
git commit -m "chore: 浏览器依赖与配置项"
```

---

## 任务 1：Browser 协议 + 提取 + FakeBrowser

**文件：** 创建 `src/harness/browser/__init__.py`、`base.py`、`extract.py`、`fake.py`、测试 `tests/test_browser_extract.py`、`tests/test_fake_browser.py`

- [ ] **步骤 1：建包**　运行：`touch src/harness/browser/__init__.py`

- [ ] **步骤 2：写失败测试**

```python
# tests/test_browser_extract.py
from harness.browser.extract import extract_main_text

ARTICLE_HTML = """
<html><head><title>光合作用</title></head><body>
<nav>首页 关于 联系我们 登录 注册</nav>
<article>
<h1>光合作用的原理</h1>
<p>光合作用是绿色植物利用光能，把二氧化碳和水转化为储存能量的有机物，并释放氧气的过程。
这一过程主要发生在叶绿体中，是地球上几乎所有生命能量的最终来源，对维持大气中氧气与二氧化碳的平衡至关重要。</p>
<p>光合作用分为光反应和暗反应两个阶段。光反应在类囊体膜上进行，把光能转化为化学能并释放氧气；
暗反应在基质中进行，利用这些化学能固定二氧化碳，最终合成葡萄糖等有机物。</p>
</article>
<footer>版权所有 © 2026 保留所有权利 隐私政策 网站地图</footer>
</body></html>
"""


def test_extract_returns_main_text():
    text = extract_main_text(ARTICLE_HTML)
    assert "光合作用是绿色植物" in text
    assert "光反应和暗反应" in text


def test_extract_strips_boilerplate():
    text = extract_main_text(ARTICLE_HTML)
    assert "登录 注册" not in text
    assert "版权所有" not in text


def test_extract_empty_html():
    assert extract_main_text("") == ""
```

```python
# tests/test_fake_browser.py
import pytest
from harness.browser.fake import FakeBrowser
from harness.browser.base import PageResult


async def test_fake_returns_preset():
    fb = FakeBrowser({"http://x/": ("标题", "<p>hi</p>")})
    await fb.start()
    r = await fb.fetch("http://x/", timeout=5, wait_until="load")
    assert isinstance(r, PageResult)
    assert r.title == "标题" and r.html == "<p>hi</p>" and r.final_url == "http://x/"
    await fb.close()


async def test_fake_unknown_url_raises():
    fb = FakeBrowser({})
    with pytest.raises(RuntimeError):
        await fb.fetch("http://missing/", timeout=5, wait_until="load")
```

运行：预期 FAIL。

- [ ] **步骤 3：实现 `src/harness/browser/base.py`**

```python
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
```

- [ ] **步骤 4：实现 `src/harness/browser/extract.py`**

```python
# src/harness/browser/extract.py
from __future__ import annotations

import trafilatura


def extract_main_text(html: str) -> str:
    """用 trafilatura 去样板（导航/广告/页脚）提取正文；抽不到返回空串。"""
    if not html:
        return ""
    return trafilatura.extract(html) or ""
```

- [ ] **步骤 5：实现 `src/harness/browser/fake.py`**

```python
# src/harness/browser/fake.py
from __future__ import annotations

from .base import PageResult


class FakeBrowser:
    """测试用 Browser：返回预设 HTML，不下载 chromium、不联网。"""

    def __init__(self, pages: dict[str, tuple[str, str]]) -> None:
        self._pages = pages   # {url: (title, html)}
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.started = False

    async def fetch(self, url: str, timeout: float, wait_until: str) -> PageResult:
        if url not in self._pages:
            raise RuntimeError(f"FakeBrowser 无预设页面：{url}")
        title, html = self._pages[url]
        return PageResult(final_url=url, title=title, html=html)
```

- [ ] **步骤 6：跑通并 commit**

运行：`uv run pytest tests/test_browser_extract.py tests/test_fake_browser.py -v`　预期：5 passed。
> 若 `test_extract_*` 因 trafilatura 对该测试 HTML 抽取策略不同而失败，可微调测试 HTML（加长正文/调整结构）使 trafilatura 稳定抽出正文——保持"正文命中、样板剔除"的断言意图不变。
```bash
git add src/harness/browser/__init__.py src/harness/browser/base.py src/harness/browser/extract.py src/harness/browser/fake.py tests/test_browser_extract.py tests/test_fake_browser.py
git commit -m "feat: Browser 协议 + trafilatura 正文提取 + FakeBrowser"
```

---

## 任务 2：BrowseTool

**文件：** 创建 `src/harness/tools/builtins/browse_tool.py`、测试 `tests/test_browse_tool.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/test_browse_tool.py
import pytest

from harness.browser.fake import FakeBrowser
from harness.tools.base import ToolRegistry, ToolExecutor
from harness.tools.builtins.browse_tool import BrowseTool
from harness.types import ToolCall

ARTICLE = """
<html><head><title>标题T</title></head><body>
<nav>登录 注册</nav>
<article><p>这是一段足够长的正文内容，用于验证浏览器工具能够正确渲染并提取页面主体文字，
而不是把导航栏和页脚一起塞进结果里，从而保证喂给知识库的内容是干净的。</p></article>
<footer>版权所有 2026</footer></body></html>
"""


def _tool(pages, resolve):
    fb = FakeBrowser(pages)
    return BrowseTool(fb, allowed_domains=[], block_private=True, timeout=5,
                      wait_until="load", max_chars=8000, resolve=resolve)


async def test_browse_returns_extracted_text():
    tool = _tool({"http://example.com/a": ("标题T", ARTICLE)},
                 resolve=lambda h: ["93.184.216.34"])
    out = await tool.run(tool.Params(url="http://example.com/a"))
    assert "标题T" in out
    assert "足够长的正文内容" in out
    assert "登录 注册" not in out


async def test_browse_ssrf_is_error():
    tool = _tool({}, resolve=lambda h: ["127.0.0.1"])
    reg = ToolRegistry(); reg.register(tool)
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="browse",
                                  arguments={"url": "http://internal/"}))
    assert r.is_error is True


async def test_browse_metadata_ip_literal_blocked():
    tool = _tool({}, resolve=None)   # 用真实 default_resolve；IP 字面量不走网络
    reg = ToolRegistry(); reg.register(tool)
    ex = ToolExecutor(reg)
    r = await ex.execute(ToolCall(id="c1", name="browse",
                                  arguments={"url": "http://169.254.169.254/"}))
    assert r.is_error is True


async def test_browse_empty_content_message():
    tool = _tool({"http://example.com/e": ("空页", "<html><body></body></html>")},
                 resolve=lambda h: ["93.184.216.34"])
    out = await tool.run(tool.Params(url="http://example.com/e"))
    assert "无可提取正文" in out
```

运行：预期 FAIL。

- [ ] **步骤 2：实现 `src/harness/tools/builtins/browse_tool.py`**

```python
# src/harness/tools/builtins/browse_tool.py
from __future__ import annotations

from pydantic import BaseModel

from ..base import Tool
from ...browser.base import Browser
from ...browser.extract import extract_main_text
from ...net.policy import check_url
from ._sandbox_util import truncate


class BrowseTool(Tool):
    name = "browse"
    description = "用无头浏览器打开网页（含 JS 动态渲染）并提取正文，适合 http_request 抓不到内容的页面。"

    class Params(BaseModel):
        url: str

    def __init__(self, browser: Browser, allowed_domains, block_private: bool = True,
                 timeout: float = 30.0, wait_until: str = "networkidle",
                 max_chars: int = 8000, resolve=None) -> None:
        self._browser = browser
        self._allowed = allowed_domains
        self._block_private = block_private
        self._timeout = timeout
        self._wait_until = wait_until
        self._max_chars = max_chars
        self._resolve_kw = {"resolve": resolve} if resolve is not None else {}

    async def run(self, params: "BrowseTool.Params") -> str:
        check_url(params.url, self._allowed, self._block_private, **self._resolve_kw)  # PolicyError→is_error
        page = await self._browser.fetch(params.url, self._timeout, self._wait_until)
        text = extract_main_text(page.html)
        if not text.strip():
            return f"（页面无可提取正文）标题：{page.title}"
        return truncate(f"标题：{page.title}\n最终URL：{page.final_url}\n\n{text}", self._max_chars)
```

- [ ] **步骤 3：跑通并 commit**

运行：`uv run pytest tests/test_browse_tool.py -v`　预期：4 passed。
```bash
git add src/harness/tools/builtins/browse_tool.py tests/test_browse_tool.py
git commit -m "feat: browse 工具（SSRF 校验 + 渲染 + 正文提取）"
```

---

## 任务 3：PlaywrightBrowser + factory + 可跳过集成测试

**文件：** 创建 `src/harness/browser/playwright_browser.py`、`src/harness/browser/factory.py`、测试 `tests/test_browser_playwright.py`

- [ ] **步骤 1：实现 `src/harness/browser/playwright_browser.py`**

```python
# src/harness/browser/playwright_browser.py
from __future__ import annotations

from .base import PageResult


class PlaywrightBrowser:
    """本地 chromium headless。每次 fetch 用独立 context/page、无跨页状态。"""

    def __init__(self, headless: bool = True, user_agent: str = "") -> None:
        self._headless = headless
        self._user_agent = user_agent or None
        self._pw = None
        self._browser = None

    async def start(self) -> None:
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)

    async def close(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    async def fetch(self, url: str, timeout: float, wait_until: str) -> PageResult:
        await self.start()
        context = await self._browser.new_context(
            accept_downloads=False, user_agent=self._user_agent)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout * 1000)
            html = await page.content()
            title = await page.title()
            final_url = page.url
            return PageResult(final_url=final_url, title=title, html=html)
        finally:
            await context.close()
```

- [ ] **步骤 2：实现 `src/harness/browser/factory.py`**

```python
# src/harness/browser/factory.py
from __future__ import annotations

from .playwright_browser import PlaywrightBrowser


def build_browser(config):
    return PlaywrightBrowser(headless=config.browser_headless,
                             user_agent=config.browser_user_agent)
```

- [ ] **步骤 3：写可跳过集成测试**

```python
# tests/test_browser_playwright.py
import os
import pytest

from harness.browser.playwright_browser import PlaywrightBrowser


@pytest.mark.skipif(not os.getenv("HARNESS_BROWSER_IT"),
                    reason="需要已安装 chromium（playwright install chromium）+ 设 HARNESS_BROWSER_IT")
async def test_playwright_fetch_data_url():
    br = PlaywrightBrowser(headless=True)
    await br.start()
    try:
        page = await br.fetch(
            "data:text/html,<title>T</title><p>你好世界内容</p>",
            timeout=15, wait_until="load")
        assert "你好世界内容" in page.html
        assert page.title == "T"
    finally:
        await br.close()
```

- [ ] **步骤 4：跑通并 commit**

运行：`uv run pytest tests/test_browser_playwright.py -v`　预期：1 skipped（未设 `HARNESS_BROWSER_IT`）。
```bash
git add src/harness/browser/playwright_browser.py src/harness/browser/factory.py tests/test_browser_playwright.py
git commit -m "feat: PlaywrightBrowser（本地 chromium）+ factory + 可跳过集成测试"
```

---

## 任务 4：端到端集成 + demo

**文件：** 测试 `tests/test_browser_integration.py`、新增 `examples/browser_demo.py`

- [ ] **步骤 1：写集成测试**（agent loop 中调用 browse，mock 模型 + FakeBrowser）

```python
# tests/test_browser_integration.py
from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.browser.fake import FakeBrowser
from harness.tools.builtins.browse_tool import BrowseTool
from harness.llm.base import StreamChunk, ToolCallDelta
from harness.events import ToolFinished, RunFinished

PAGE = """
<html><head><title>Py教程</title></head><body>
<nav>菜单</nav>
<article><p>Python 是一门通用编程语言，语法简洁、生态丰富，广泛用于数据分析、Web 开发与人工智能等领域，
非常适合初学者作为第一门编程语言来学习和实践。</p></article>
<footer>脚注</footer></body></html>
"""


async def test_agent_browses_page(make_mock, text_turn):
    fb = FakeBrowser({"http://example.com/py": ("Py教程", PAGE)})
    reg = ToolRegistry()
    reg.register(BrowseTool(fb, allowed_domains=[], block_private=True, timeout=5,
                            wait_until="load", max_chars=8000,
                            resolve=lambda h: ["93.184.216.34"]))
    browse_turn = [
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="browse",
            arguments='{"url": "http://example.com/py"}')),
        StreamChunk(type="done"),
    ]
    loop = AgentLoop(client=make_mock([browse_turn, text_turn("据网页，Python 适合初学者")]),
                     registry=reg, context=ContextManager(system_prompt="s"),
                     max_steps=5, run_id_factory=lambda: "r1")
    events = [e async for e in loop.run("介绍下 Python")]
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert "通用编程语言" in finished[0].result.content
    assert finished[0].result.is_error is False
    assert isinstance(events[-1], RunFinished)
```

运行：`uv run pytest tests/test_browser_integration.py -v`　预期：1 passed。

- [ ] **步骤 2：新增 `examples/browser_demo.py`**

```python
# examples/browser_demo.py
"""浏览器工具手动验收：让 agent 抓取一个真实网页并总结。
需要 .env 配好聊天端点，且已 `playwright install chromium`。

运行：uv run python examples/browser_demo.py "抓取 https://example.com 并总结"
"""
from __future__ import annotations

import asyncio
import sys

from harness.config import HarnessConfig
from harness.context.manager import ContextManager
from harness.llm.openai_compat import OpenAICompatibleClient
from harness.loop.agent_loop import AgentLoop
from harness.browser.factory import build_browser
from harness.tools.base import ToolRegistry
from harness.tools.builtins.browse_tool import BrowseTool
from harness.events import TextDelta, ToolStarted, ToolFinished, RunFinished, RunError


async def main(msg: str) -> None:
    cfg = HarnessConfig()
    browser = build_browser(cfg)
    reg = ToolRegistry()
    reg.register(BrowseTool(browser, cfg.http_allowed_domains, cfg.http_block_private,
                            cfg.browser_nav_timeout, cfg.browser_wait_until,
                            cfg.browser_output_max_chars))
    loop = AgentLoop(
        client=OpenAICompatibleClient(cfg), registry=reg,
        context=ContextManager(system_prompt="你可以用 browse 打开网页抓取正文来回答问题。"),
        max_steps=cfg.max_steps, model_name=cfg.model)
    try:
        async for ev in loop.run(msg):
            if isinstance(ev, TextDelta):
                print(ev.text, end="", flush=True)
            elif isinstance(ev, ToolStarted):
                print(f"\n[抓取] {ev.tool_call.arguments}")
            elif isinstance(ev, ToolFinished):
                print(f"[正文] {ev.result.content[:200]}")
            elif isinstance(ev, RunFinished):
                print(f"\n\n[完成] {ev.message.content}")
            elif isinstance(ev, RunError):
                print(f"\n\n[出错] {ev.error}")
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "抓取 https://example.com 并总结要点"))
```

- [ ] **步骤 3：全量测试并 commit**

运行：`uv run pytest -q`　预期：全绿（`test_integration_real` + docker + playwright 集成 均 skipped）。
```bash
git add tests/test_browser_integration.py examples/browser_demo.py
git commit -m "feat: 浏览器端到端集成测试 + demo"
```

---

## 完成标准（对照规格验收）

- [ ] `FakeBrowser` + 文章 HTML → `browse` 返回去样板正文（含标题）（任务 1/2）
- [ ] `extract_main_text` 只抽正文、剔除导航/页脚（任务 1）
- [ ] SSRF：内网/元数据 URL → `is_error`（任务 2）
- [ ] 正文超限截断（任务 2，复用 truncate）
- [ ] `browse` 在 agent loop 被调用、正文回填、作答（任务 4）
- [ ] `PlaywrightBrowser` 有可跳过集成测试（任务 3）
- [ ] 全程 FakeBrowser 不下载 chromium/不联网；①②③a③b-1 无回归（`uv run pytest` 全绿）
```
