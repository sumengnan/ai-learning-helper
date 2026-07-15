# src/harness/tools/builtins/http_tool.py
from __future__ import annotations

import httpx
from pydantic import BaseModel

from ..base import Tool
from ...browser.extract import extract_title_and_text
from ...net.policy import PolicyError, check_url

# 疑似「被防抓 / 需 JS 渲染」的信号：命中则值得改用浏览器重抓
_BLOCK_STATUS = frozenset({401, 403, 429, 503})
_BLOCK_SIGNALS = (
    "cloudflare", "captcha", "verify you are human", "attention required",
    "access denied", "enable javascript", "just a moment", "checking your browser",
    "请开启javascript", "请开启 javascript", "人机验证", "拦截", "访问被拒绝",
)


def looks_blocked(status: int, body: str) -> bool:
    """HTTP 结果是否疑似防抓/需 JS：据状态码与正文特征启发式判断。"""
    if status in _BLOCK_STATUS:
        return True
    low = body[:4000].lower()
    return any(s in low for s in _BLOCK_SIGNALS)


async def browser_fallback_or_none(fn, url: str, reason: str) -> str | None:
    """尝试用浏览器兜底抓取；成功返回带说明前缀的正文，无兜底或失败返回 None。"""
    if fn is None:
        return None
    try:
        text = await fn(url)
    except Exception:            # noqa: BLE001  兜底失败（如镜像缺 Playwright）不应掩盖原结果
        return None
    return f"（{reason}，已自动改用浏览器抓取）\n\n{text}"


def merge_user_agent(headers: dict | None, user_agent: str) -> dict | None:
    """把默认 UA 并进请求头；调用方显式传的 User-Agent 优先（大小写不敏感）。

    空 UA 是最典型的爬虫特征，不少站点据此直接 403，故默认必须带一个。
    """
    if not user_agent:
        return headers
    out = dict(headers or {})
    if any(k.lower() == "user-agent" for k in out):
        return out
    out["User-Agent"] = user_agent
    return out


def _looks_like_html(content_type: str, body: str) -> bool:
    """判定响应是否为 HTML：优先 Content-Type，缺失时嗅探正文开头。"""
    if "html" in content_type.lower():
        return True
    if content_type:            # 有明确非 HTML 类型（json/纯文本等）则不嗅探
        return False
    head = body[:2048].lstrip().lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<html" in head


def render_http_result(status: int, url: str, content_type: str, body: str,
                       suffix: str, raw: bool) -> str:
    """HTML 响应返回解析后的标题+正文；其余（JSON/纯文本/API）原样透传。"""
    if not raw and _looks_like_html(content_type, body):
        title, text = extract_title_and_text(body)
        head = f"HTTP {status}\n标题：{title}\n最终URL：{url}\n\n"
        return f"{head}{text or '（无可提取正文）'}{suffix}"
    return f"HTTP {status}\n{body}{suffix}"


class HttpRequestTool(Tool):
    name = "http_request"
    description = ("发起 HTTP(S) 请求抓取网页或调用外部 API。默认可访问公网，禁止内网地址。"
                   "网页默认返回解析后的标题+正文；需要原始 HTML 时传 raw=true。"
                   "抓取失败或页面疑似被防抓/需 JS 渲染时，会自动改用无头浏览器重试（若已启用）。")

    class Params(BaseModel):
        url: str
        method: str = "GET"
        headers: dict | None = None
        body: str | None = None
        raw: bool = False

    def __init__(self, allowed_domains, block_private: bool = True, timeout: float = 30.0,
                 max_bytes: int = 5_000_000, max_redirects: int = 5,
                 client_factory=None, resolve=None, browser_fallback=None,
                 user_agent: str = "") -> None:
        self._allowed = allowed_domains
        self._block_private = block_private
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._user_agent = user_agent
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(follow_redirects=False, timeout=timeout))
        self._resolve_kw = {"resolve": resolve} if resolve is not None else {}
        # 可选：async (url)->str，http 抓取出错或疑似被防抓时自动改用它（浏览器）重抓
        self._browser_fallback = browser_fallback

    def set_browser_fallback(self, fn) -> None:
        self._browser_fallback = fn

    async def _fetch(self, params: "HttpRequestTool.Params") -> tuple[int, str, str, str, str]:
        url = params.url
        headers = merge_user_agent(params.headers, self._user_agent)
        async with self._client_factory() as client:
            for _ in range(self._max_redirects + 1):
                check_url(url, self._allowed, self._block_private, **self._resolve_kw)  # PolicyError→is_error
                async with client.stream(params.method, url,
                                         headers=headers, content=params.body) as resp:
                    if resp.is_redirect and "location" in resp.headers:
                        url = str(httpx.URL(url).join(resp.headers["location"]))
                        continue
                    chunks, total = [], 0
                    async for b in resp.aiter_bytes():
                        chunks.append(b)
                        total += len(b)
                        if total > self._max_bytes:
                            break
                    body = b"".join(chunks).decode(errors="replace")[: self._max_bytes]
                    suffix = "…(已截断)" if total > self._max_bytes else ""
                    ctype = resp.headers.get("content-type", "")
                    return resp.status_code, url, ctype, body, suffix
        raise RuntimeError(f"超过最大重定向次数（{self._max_redirects}）")

    async def run(self, params: "HttpRequestTool.Params") -> str:
        try:
            status, final_url, ctype, body, suffix = await self._fetch(params)
        except PolicyError:
            raise                       # 安全拦截：不兜底（浏览器同策略也会拦）
        except Exception as e:          # noqa: BLE001  网络类错误 → 尝试浏览器兜底
            fb = await browser_fallback_or_none(
                self._browser_fallback, params.url, f"http_request 出错：{e}")
            if fb is not None:
                return fb
            raise
        # 请求成功但疑似被防抓/需 JS 渲染 → 改用浏览器；兜底不可用则退回原结果
        if not params.raw and looks_blocked(status, body):
            fb = await browser_fallback_or_none(
                self._browser_fallback, final_url, f"HTTP {status} 疑似防抓或需 JS 渲染")
            if fb is not None:
                return fb
        return render_http_result(status, final_url, ctype, body, suffix, params.raw)
