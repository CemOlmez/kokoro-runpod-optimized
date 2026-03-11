import re
from typing import List

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_text(text: str, max_chars_per_chunk: int) -> List[str]:
    """
    Split text by sentence boundaries first, then enforce max length deterministically.
    Fallback for very long sentence fragments is hard slicing.
    """
    clean = normalize_text(text)
    if not clean:
        return []
    if len(clean) <= max_chars_per_chunk:
        return [clean]

    sentence_parts = SENTENCE_SPLIT_RE.split(clean)
    chunks: list[str] = []
    current = ""

    for part in sentence_parts:
        part = part.strip()
        if not part:
            continue

        candidate = part if not current else f"{current} {part}"
        if len(candidate) <= max_chars_per_chunk:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(part) <= max_chars_per_chunk:
            current = part
            continue

        start = 0
        while start < len(part):
            end = min(start + max_chars_per_chunk, len(part))
            chunks.append(part[start:end].strip())
            start = end

    if current:
        chunks.append(current)

    return [chunk for chunk in chunks if chunk]
