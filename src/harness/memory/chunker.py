from __future__ import annotations


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
    step = chunk_size - overlap
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + chunk_size])
        start += step
    # 末块若不超过 overlap，则已被前一块的重叠区完整覆盖，属冗余，丢弃。
    # overlap=0 时条件为 <=0 永不触发，真实尾块保留。
    if len(chunks) > 1 and len(chunks[-1]) <= overlap:
        chunks.pop()
    return chunks
