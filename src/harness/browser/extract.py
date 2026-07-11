# src/harness/browser/extract.py
from __future__ import annotations

import html as _html
import re

import trafilatura

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def extract_main_text(html: str) -> str:
    """用 trafilatura 去样板（导航/广告/页脚）提取正文；抽不到返回空串。"""
    if not html:
        return ""
    try:
        return trafilatura.extract(html) or ""
    except Exception:
        return ""


def _extract_title(html: str) -> str:
    """标题：优先 trafilatura 元数据，回退 <title> 正则；抽不到返回空串。"""
    if not html:
        return ""
    try:
        meta = trafilatura.extract_metadata(html)
        if meta and getattr(meta, "title", None):
            return meta.title.strip()
    except Exception:
        pass
    m = _TITLE_RE.search(html)
    if m:
        return _html.unescape(m.group(1)).strip()
    return ""


def extract_title_and_text(html: str) -> tuple[str, str]:
    """返回 (标题, 正文)。正文复用 extract_main_text，标题走 _extract_title；
    抽不到分别返回空串。全程兜底，绝不抛异常。"""
    return _extract_title(html), extract_main_text(html)
