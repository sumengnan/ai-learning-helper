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
    return chunks
