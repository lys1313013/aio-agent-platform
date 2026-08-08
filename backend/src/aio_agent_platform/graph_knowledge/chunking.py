"""Text chunking for graph knowledge base documents.

Strategy: split by Markdown headings first, then break each section into
fixed-size windows with overlap. Pure character-based so it is language-agnostic
(CJK included).
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")

DEFAULT_CHUNK_SIZE = 800
DEFAULT_OVERLAP = 80


def _split_sections(content: str) -> list[str]:
    lines = content.split("\n")
    sections: list[str] = []
    current: list[str] = []
    for line in lines:
        if _HEADING_RE.match(line) and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current))
    return [sec.strip() for sec in sections if sec and sec.strip()]


def _chunk_fixed(text: str, chunk_size: int, overlap: int) -> list[str]:
    n = len(text)
    if n <= chunk_size:
        return [text] if text.strip() else []
    chunks: list[str] = []
    start = 0
    while start < n:
        end = min(start + chunk_size, n)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def chunk_text(
    content: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """Chunk content into a list of text chunks."""
    if not content or not content.strip():
        return []
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    sections = _split_sections(normalized)
    if not sections:
        sections = [normalized.strip()]
    chunks: list[str] = []
    for section in sections:
        chunks.extend(_chunk_fixed(section, chunk_size, overlap))
    return chunks
