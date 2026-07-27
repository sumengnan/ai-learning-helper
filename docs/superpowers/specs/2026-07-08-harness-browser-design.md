> ⚠️ **历史设计记录（已过时）**：harness 内核已抽成外部包
> [ai-harness-framework](https://github.com/sumengnan/ai-harness-framework)（import 名仍是 `harness`）。
> 本文是带日期的设计存档，文中的 `src/harness/` 路径与打包配置反映**当时**的仓库结构、未随抽包更新；
> 当前结构以 [架构文档](../../architecture-harness.md) 为准。

# AI Harness 浏览器工具（子项目③b-2）设计规格

- **日期**：2026-07-08
- **状态**：待实现（brainstorming 已定稿）
- **定位**：单用户自用工具，在①②③a③b-1 之上扩展
- **前置**：①②③a③b-1 已完成并在 `main`

---

## 0. 背景与范围

子项目③b 的浏览器部分（③b-2）：给 agent 一个 `browse(url)` 工具，用 headless 浏览器打开**动态渲染（JS）网页**、提取干净正文，补 ③b-1 `http_request` 抓不到内容的页面。抓到的正文可由 agent 用 ③a 的 `remember` 入知识库。

**存储/可观测性**：沿用 SQLite+sqlite-vec / OpenTelemetry（工具执行自动进②的 `tool_call` span）。

设计通则：严格 YAGNI；测试用 `FakeBrowser` 不下载 chromium/不联网；SSRF 策略**复用 ③b-1 的 `net/policy`**；不回归既有。

---

## 1. 范围与验收

### IN
- `Browser` 协议 + `PlaywrightBrowser`（生产，本地 chromium headless）+ `FakeBrowser`（测试，预设 HTML）。
- `extract_main_text(html)`：`trafilatura` 去样板提正文（纯函数）。
- `BrowseTool`：`browse(url)` → `check_url`（SSRF 复用 net/policy）→ `Browser.fetch` 渲染 → 提取正文 → 截断回填。

### OUT（预留扩展点）
截图、交互（click/fill/多步）、远程容器浏览器（`Browser` 协议留口）、并发多页会话、登录态保持。

### 验收标准
1. `FakeBrowser` + 预设文章 HTML → `browse` 返回去样板正文（含标题，不含导航/页脚噪声）。
2. `extract_main_text` 对含导航/广告的 HTML 只抽正文（单测）。
3. SSRF：`browse("http://169.254.169.254/")`、内网主机被 `check_url` 拒（`is_error`）。
4. 正文超 `browser_output_max_chars` 截断。
5. `browse` 在 agent loop 被调用、正文回填、模型据此作答（mock 模型 + `FakeBrowser`）。
6. `PlaywrightBrowser` 有 1 个可跳过集成测试（无 chromium/未设标志则 skip）。
7. ①②③a③b-1 原有测试不回归。

---

## 2. 架构与模块

**设计取向**：`Browser` 是协议（仿 `Sandbox`），`BrowseTool` 只依赖协议——生产注入 `PlaywrightBrowser`、测试注入 `FakeBrowser`。SSRF 策略**复用 ③b-1 的 `net/policy`**（不重造），网络出口配置（`http_allowed_domains`/`http_block_private`）HTTP 与浏览器共用。工具执行自动走②的 `tool_call` span/错误回填/截断。

```
src/harness/browser/           [新增]
├── __init__.py
├── base.py                Browser 协议 + PageResult
├── playwright_browser.py   PlaywrightBrowser（本地 chromium headless）
├── fake.py                FakeBrowser（测试：预设 HTML）
├── extract.py             extract_main_text(html)（trafilatura）
└── factory.py             build_browser(config)
src/harness/tools/builtins/browse_tool.py  BrowseTool
src/harness/config.py          [改] browser_* 配置
```

**依赖新增**：`playwright`（chromium 需 `playwright install chromium` 另装）、`trafilatura`。`FakeBrowser` 仅标准库。

**边界**：
- `Browser` 协议——测试用 `FakeBrowser`，不碰 chromium/网络。
- `extract.py` 纯函数（HTML→正文），独立单测。
- `net/policy` 复用——浏览器与 HTTP 共享 SSRF/白名单策略（DRY）。
- `BrowseTool` 持有 `Browser`，与①`CalculatorTool` 同构。
- **安全说明**：SSRF 由 `check_url` 拦（同 ③b-1 的 DNS rebinding 已知限制，白名单为强控制）；浏览器导航现对**每跳重定向**做 SSRF 校验（context.route 拦截导航请求，不合规即 abort），杜绝"公网页 302/JS 跳内网"绕过；子资源请求（img/script/xhr）不做策略校验（其内容不回传给 agent，属较低风险）；本地 chromium 自带渲染沙箱；不可信渲染的更强隔离靠未来"远程容器浏览器"（协议已留口）。

---

## 3. Browser 协议 + PageResult

```python
@dataclass
class PageResult:
    final_url: str      # 跟随重定向后的最终 URL
    title: str
    html: str           # 渲染完成后的完整 HTML

class Browser(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def fetch(self, url: str, timeout: float, wait_until: str) -> PageResult: ...
```

- 职责边界：`Browser` 只负责"URL → 渲染 → HTML/标题/最终URL"，不做提取、不做策略。
- `wait_until`：`load` / `domcontentloaded` / `networkidle`（配置，默认 `networkidle`）。

---

## 4. PlaywrightBrowser + FakeBrowser

**`PlaywrightBrowser`**（生产）：
- `start()` 惰性 `async_playwright().start()` + `chromium.launch(headless=...)`，复用一个 browser 实例。
- `fetch()`：`new_page(accept_downloads=False, user_agent=...)` → `page.goto(url, wait_until, timeout)` → `page.content()` + `page.title()` + `page.url` → **关闭该 page**（每次 fetch 独立 page、无跨页状态）。
- 健壮：禁下载、导航超时到即抛（→ 工具 `is_error`）。
- `close()` 关 browser + 停 playwright。

**`FakeBrowser`**（测试）：构造给 `{url: (title, html)}` 映射；`fetch()` 返回对应 `PageResult`，未命中抛错。不下载 chromium、不联网。

---

## 5. 提取 + BrowseTool

**提取** `browser/extract.py`（纯函数）：
```python
def extract_main_text(html: str) -> str:
    # trafilatura.extract(html) 去导航/广告/页脚返回正文；抽不到返回 ""
```

**`BrowseTool`**（`tools/builtins/browse_tool.py`，持有 `Browser`）：
```python
class BrowseTool(Tool):
    name = "browse"
    description = "用无头浏览器打开网页（含 JS 动态渲染）并提取正文，适合 http_request 抓不到内容的页面。"

    class Params(BaseModel):
        url: str

    def __init__(self, browser, allowed_domains, block_private, timeout,
                 wait_until, max_chars, resolve=None): ...

    async def run(self, params) -> str:
        check_url(params.url, self._allowed, self._block_private, **resolve_kw)  # PolicyError→is_error
        page = await self._browser.fetch(params.url, self._timeout, self._wait_until)
        text = extract_main_text(page.html)
        if not text.strip():
            return f"（页面无可提取正文）标题：{page.title}"
        return truncate(f"标题：{page.title}\n最终URL：{page.final_url}\n\n{text}", self._max_chars)
```

- SSRF/导航超时/渲染失败 → 走②的 `is_error` 回填自纠正。
- `truncate` 复用 ③b-1 `tools/builtins/_sandbox_util.truncate`。
- 装配：`build_browser(config)` 造 `PlaywrightBrowser`；`BrowseTool(browser, cfg.http_allowed_domains, cfg.http_block_private, cfg.browser_nav_timeout, cfg.browser_wait_until, cfg.browser_output_max_chars)` 注册。

---

## 6. 配置 / 测试 / 依赖

### 新增配置（`config.py`，安全默认）
```
browser_headless: bool = True
browser_nav_timeout: float = 30.0
browser_wait_until: str = "networkidle"   # load | domcontentloaded | networkidle
browser_output_max_chars: int = 8000
browser_user_agent: str = ""              # 空=Playwright 默认 UA
```
SSRF 复用现有 `http_allowed_domains` / `http_block_private`，不新增。

### 测试策略（`FakeBrowser` + 预设 HTML，不下载 chromium/不联网）
- `extract_main_text`：含 `<nav>`/`<footer>`/广告的文章 HTML → 返回正文、不含导航/页脚文字。
- `FakeBrowser`：`fetch` 返回预设 `PageResult`。
- `BrowseTool` 经 `ToolExecutor`：命中 → "标题+正文"；SSRF（内网/元数据 URL）→ `is_error`；无正文 → 明确提示；超长截断。
- 集成：`AgentLoop` + mock 模型（发 `browse`）+ `FakeBrowser` → 断言正文回填并被作答引用。
- `PlaywrightBrowser`：1 个 `@pytest.mark.skipif(无 chromium 或未设 HARNESS_BROWSER_IT)` 集成测试——导航到已知内容的 `data:` URL、断言 HTML 含预期文本。

### 依赖新增
`playwright`、`trafilatura`。`FakeBrowser` 仅标准库；chromium 二进制需 `playwright install chromium` 另装（仅生产/集成测试用）。

---

## 7. 后续衔接（备忘，非本次范围）

- **截图/交互**：`Browser` 协议加 `screenshot`/`click`/`fill`；多步抓取需页面会话保持。
- **远程容器浏览器**：第二个 `Browser` 实现（云容器跑 Playwright browser server，经 CDP 连），不可信渲染完全隔离——与 ③b-1 姿态统一。
- **③a-follow 情景记忆**、**③c 多 Agent 编排**、**③d 持久化**。
- App 层：`browse` 抓取正文 → agent 用 `remember` 入知识库 → 支撑"抓网上数据整理成知识库"。
