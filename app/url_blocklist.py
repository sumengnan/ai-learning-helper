# app/url_blocklist.py
"""抓取失败的网址记忆：失败即登记，下次跳过并让模型换一个来源。

模型没有搜索工具，候选网址靠猜或从已抓页面里提链接，撞死链是常态；而 404/5xx 目前
`is_error=False`、正文首行才有个不起眼的 `HTTP 404`，模型不会意识到抓失败了，于是
下一轮接着撞。这里把失败落库并在下次**发请求前**短路，把「这个网址抓不了」变成模型
必须处理的显式错误。

按失败类型分级（永久拉黑会让可用源只减不增，且是静默变差）：
  - 超时/连接失败/5xx/429  → 该 URL，1 小时（临时故障，很快自愈）
  - 404/410               → 该 URL，30 天（页面没了）
  - 401/403               → 整个域名，7 天（反爬/要登录，通常是整站行为）
  - 内容为空/解析不出正文   → 该 URL，7 天

登记是全局的（不分用户）：网址抓不抓得到是网站的属性，不是用户的属性，一个用户撞上的
死链没必要让其他人再撞一遍。临时故障 TTL 只有 1 小时，误伤自愈。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from harness.net.policy import PolicyError
from harness.tools.base import Tool, ToolError
from harness.types import ToolOutput

from .sources import is_placeholder_host

_log = logging.getLogger("app.url_blocklist")


class UrlBlockedError(ToolError):
    """本次抓取因命中失败登记被跳过（未发起请求）。

    独立于普通 ToolError：调用方要能区分「这个网址我们**知道**它坏」与「抓取时抖了一下」。
    交付门的 facts 核查据此把命中登记的链接判为死链，而不是当基建故障放行。
    """

    def __init__(self, message: str, record: dict) -> None:
        super().__init__(message)
        self.record = record

# 受管的抓取工具（沙箱版与宿主版同名 http_request，故按名匹配即可覆盖两者）
FETCH_TOOLS = frozenset({"http_request", "browse"})

HOUR, DAY = 3600, 86400
TTL_TRANSIENT = HOUR          # 超时/连接失败/5xx/429
TTL_GONE = 30 * DAY           # 404/410
TTL_FORBIDDEN = 7 * DAY       # 401/403（整域）
TTL_EMPTY = 7 * DAY           # 无正文/拦截页

# http_request 结果以 "HTTP {status}" 开头（见 harness render_http_result）
_HTTP_STATUS = re.compile(r"^HTTP (\d{3})")
# 浏览器兜底成功的结果前缀（见 harness browser_fallback_or_none）——这是**成功**，不登记
_BROWSER_FALLBACK = "已自动改用浏览器抓取"
# 抓到的是拦截/错误页（CDN 拦截页等常以 200 返回，状态码看不出来）
_ERROR_PAGE_SIGNALS = (
    "the request could not be satisfied", "access denied", "403 forbidden",
    "404 not found", "error 1020", "请求无法满足", "访问被拒绝", "拒绝访问",
)
_NO_CONTENT_SIGNALS = ("无可提取正文", "页面无可提取正文")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def norm_url(url: str) -> str:
    """规范化：去 fragment、小写 host、去掉 www.。保留 query（不同 query 是不同页面）。"""
    try:
        p = urlsplit(url.strip())
        host = (p.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if p.port:
            host = f"{host}:{p.port}"
        return urlunsplit((p.scheme.lower(), host, p.path.rstrip("/") or "/", p.query, ""))
    except Exception:
        return (url or "").strip()


def domain_of(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _looks_error_page(text: str) -> bool:
    return any(s in (text or "")[:300].lower() for s in _ERROR_PAGE_SIGNALS)


def _no_real_content(text: str) -> bool:
    return any(s in (text or "") for s in _NO_CONTENT_SIGNALS)


def classify(url: str, text: str | None, exc: BaseException | None) -> tuple | None:
    """判定这次抓取算不算失败、该登记什么。

    返回 (key, scope, reason, status, ttl_seconds)；成功则返回 None。
    """
    if exc is not None:
        if isinstance(exc, PolicyError):
            return None      # 安全策略拦截（内网/白名单）：不是网站的错，重查也不花钱
        # 走到这说明浏览器兜底也没救回来（兜底成功会返回文本而非抛异常）
        return (norm_url(url), "url", f"抓取出错：{type(exc).__name__}: {exc}"[:200],
                None, TTL_TRANSIENT)

    text = text or ""
    # 浏览器兜底救回来了 → 这次抓取成功（除非救回来的也是空页）
    if _BROWSER_FALLBACK in text[:200] and not _no_real_content(text):
        return None

    m = _HTTP_STATUS.match(text)
    status = int(m.group(1)) if m else None
    if status is not None:
        if status in (404, 410):
            return (norm_url(url), "url", f"HTTP {status}（页面不存在）", status, TTL_GONE)
        if status in (401, 403):
            d = domain_of(url)
            if d:      # 反爬/要登录通常是整站行为，只拉黑单页等于没拉
                return (d, "domain", f"HTTP {status}（拒绝访问，疑似反爬或需登录）",
                        status, TTL_FORBIDDEN)
            return (norm_url(url), "url", f"HTTP {status}（拒绝访问）", status, TTL_FORBIDDEN)
        if status == 429 or 500 <= status <= 599:
            return (norm_url(url), "url", f"HTTP {status}（服务端暂时不可用）",
                    status, TTL_TRANSIENT)
        if status != 200 and _looks_error_page(text):
            return (norm_url(url), "url", f"HTTP {status}（错误页）", status, TTL_EMPTY)

    # 状态码 200 也可能是 CDN 拦截页 / 空壳页
    if _looks_error_page(text):
        return (norm_url(url), "url", "抓到的是拦截页/错误页", status, TTL_EMPTY)
    if _no_real_content(text):
        return (norm_url(url), "url", "页面无可提取正文", status, TTL_EMPTY)
    return None


class UrlBlockStore:
    """抓取失败网址登记表（全局，不分用户）。过期条目按 until 过滤，读时不算数。"""

    def __init__(self, db_path: str | None = None, conn=None) -> None:
        if conn is None:
            from .db import open_db
            conn = open_db(db_path or ":memory:")
            from .db import migrate
            migrate(conn)
        self._conn = conn

    def block(self, key: str, scope: str, reason: str, status: int | None,
              ttl_seconds: int) -> None:
        now = _now()
        until = (now + timedelta(seconds=ttl_seconds)).isoformat()
        self._conn.execute(
            "INSERT INTO url_blocklist(key, scope, reason, status, until, hits,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET scope=excluded.scope, reason=excluded.reason,"
            " status=excluded.status, until=excluded.until, hits=url_blocklist.hits + 1,"
            " updated_at=excluded.updated_at",
            (key, scope, reason, status, until, now.isoformat(), now.isoformat()))
        self._conn.commit()

    def check(self, url: str) -> dict | None:
        """该 URL（或其域名）是否在未过期的登记里。命中返回记录，否则 None。"""
        keys = [norm_url(url)]
        d = domain_of(url)
        if d:
            keys.append(d)
        row = self._conn.execute(
            "SELECT key, scope, reason, status, until, hits FROM url_blocklist"
            " WHERE key IN (%s) AND until > ?"
            " ORDER BY CASE scope WHEN 'url' THEN 0 ELSE 1 END LIMIT 1"
            % ",".join("?" * len(keys)),
            (*keys, _now().isoformat())).fetchone()
        if row is None:
            return None
        return {"key": row[0], "scope": row[1], "reason": row[2], "status": row[3],
                "until": row[4], "hits": row[5]}

    def prune(self) -> int:
        cur = self._conn.execute("DELETE FROM url_blocklist WHERE until <= ?",
                                 (_now().isoformat(),))
        self._conn.commit()
        return cur.rowcount

    def list_active(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT key, scope, reason, status, until, hits FROM url_blocklist"
            " WHERE until > ? ORDER BY updated_at DESC", (_now().isoformat(),)).fetchall()
        return [{"key": r[0], "scope": r[1], "reason": r[2], "status": r[3],
                 "until": r[4], "hits": r[5]} for r in rows]


def _blocked_message(url: str, rec: dict) -> str:
    what = "该域名" if rec["scope"] == "domain" else "该网址"
    return (f"跳过抓取 {url}：{what}近期抓取失败过（{rec['reason']}），本次未发起请求。"
            f"请改用其它网址或来源；若同一信息有其它站点，直接换一个站点抓。")


class _BlocklistGuardedTool(Tool):
    """抓取工具外挂一层：抓前查登记（命中即短路），抓后按失败类型登记。"""

    def __init__(self, inner: Tool, store: UrlBlockStore) -> None:
        self._inner = inner
        self._store = store
        self.name = inner.name
        self.description = inner.description
        self.Params = inner.Params

    def schema(self) -> dict:
        return self._inner.schema()

    async def run(self, params):
        url = getattr(params, "url", None)
        if not url:
            return await self._inner.run(params)
        try:
            rec = self._store.check(url)
        except Exception:      # noqa: BLE001  登记表故障不该阻断正常抓取
            rec = None
        if rec is not None:
            # ToolError → is_error=True 且文案原样回传，模型必须处理，不会当成正文
            raise UrlBlockedError(_blocked_message(url, rec), rec)

        try:
            raw = await self._inner.run(params)
        except BaseException as e:
            self._record(url, None, e)
            raise
        text = raw.text if isinstance(raw, ToolOutput) else raw
        self._record(url, text if isinstance(text, str) else "", None)
        return raw

    def _record(self, url: str, text: str | None, exc: BaseException | None) -> None:
        try:
            hit = classify(url, text, exc)
        except Exception:      # noqa: BLE001
            return
        if hit is None:
            return
        key, scope, reason, status, ttl = hit
        try:
            self._store.block(key, scope, reason, status, ttl)
        except Exception:      # noqa: BLE001  登记失败不影响本次抓取结果
            _log.warning("登记失败网址 %s 出错", key, exc_info=True)
            return
        _log.info("登记抓取失败 %s=%s（%s），%d 秒内跳过", scope, key, reason, ttl)


class _PlaceholderGuardedTool(Tool):
    """抓取前拦掉指向保留/占位域名的请求。

    模型没有真实网址时会照着 API 文档的样子编一个（实测 https://api.example.com/ai-trends、
    https://example.com/ai-agent-advancements）。这类域名真实存在且恒返回 200，抓完才判
    「是占位页」既浪费一次往返，也让模型误以为自己拿到了数据。所以要在**发请求之前**拦掉，
    并当场告诉它正确做法是改用联网搜索工具——只说「不许」而不给出路，它只会换个编造的网址重试。
    """

    def __init__(self, inner: Tool) -> None:
        self._inner = inner
        self.name = inner.name
        self.description = inner.description
        self.Params = inner.Params

    def schema(self) -> dict:
        return self._inner.schema()

    async def run(self, params):
        url = getattr(params, "url", None)
        if url and is_placeholder_host(str(url)):
            raise ToolError(
                f"未发起请求：{url} 指向文档示例用的保留域名（example.com/.org/.net/.edu 一族），"
                "不是真实可用的接口或页面——这个网址多半是你凭印象编的。\n"
                "正确做法：先用联网搜索工具找到真实来源，再抓它给出的网址；"
                "不要再猜别的网址，也不要把本次当作已获取到数据。")
        return await self._inner.run(params)


def guard_fetch_tool(tool: Tool, store: UrlBlockStore | None) -> Tool:
    """抓取工具包守卫；其余工具原样返回。

    占位域名拦截**不依赖** store：它是纯粹的 URL 合法性判断，与失败记忆无关，
    没配登记表时同样必须生效（此前整层守卫都挂在 store 上，store 为 None 就全失效）。
    """
    if tool.name not in FETCH_TOOLS:
        return tool
    guarded = _PlaceholderGuardedTool(tool)
    return _BlocklistGuardedTool(guarded, store) if store is not None else guarded
