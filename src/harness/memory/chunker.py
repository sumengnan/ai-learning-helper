from __future__ import annotations

# 句子/段落边界字符：优先在这些字符之后切分，避免把一句话从中间切成两半
_BOUNDARY = "。！？；!?;\n"


def _last_boundary(text: str, lo: int, hi: int) -> int:
    """返回 [lo, hi) 内最后一个边界字符之后的切点位置；窗口内无边界则返回 -1。"""
    lo = max(lo, 0)
    for i in range(hi - 1, lo - 1, -1):
        if text[i] in _BOUNDARY:
            return i + 1
    return -1


def chunk(text: str, chunk_size: int, overlap: int) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须为正")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须在 [0, chunk_size) 内")
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    # 边界回溯：在每块的末尾 window 字符内找最后一个句子边界，切在其后，避免半截句；
    # 窗口内无标点（如无边界的连续串）再退回硬切，既不切碎句子又保证向前推进。
    window = chunk_size // 4
    n = len(text)
    chunks: list[str] = []
    start = 0
    while start < n:
        hard_end = start + chunk_size
        if hard_end >= n:               # 末块：取到结尾，不再回溯
            chunks.append(text[start:])
            break
        end = hard_end
        cut = _last_boundary(text, hard_end - window, hard_end)
        if cut > start:                 # 窗口内找到边界 → 切在边界之后
            end = cut
        chunks.append(text[start:end])
        nxt = end - overlap
        start = nxt if nxt > start else end   # overlap=0 等极端情形下仍保证前进
    # 末块若不超过 overlap，则已被前一块的重叠区完整覆盖，属冗余，丢弃。
    # overlap=0 时条件为 <=0 永不触发，真实尾块保留。
    if len(chunks) > 1 and len(chunks[-1]) <= overlap:
        chunks.pop()
    return chunks
