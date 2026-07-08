# src/harness/browser/extract.py
from __future__ import annotations

import trafilatura


def extract_main_text(html: str) -> str:
    """用 trafilatura 去样板（导航/广告/页脚）提取正文；抽不到返回空串。"""
    if not html:
        return ""
    try:
        return trafilatura.extract(html) or ""
    except Exception:
        return ""
