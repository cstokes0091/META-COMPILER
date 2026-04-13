from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path


STOPWORDS = {
    "about",
    "after",
    "against",
    "algorithm",
    "along",
    "already",
    "also",
    "among",
    "analyze",
    "because",
    "before",
    "being",
    "between",
    "build",
    "built",
    "chat",
    "codebase",
    "context",
    "create",
    "created",
    "critical",
    "decision",
    "domain",
    "during",
    "each",
    "first",
    "from",
    "future",
    "goals",
    "human",
    "implementation",
    "into",
    "items",
    "llm",
    "meta",
    "model",
    "more",
    "need",
    "open",
    "output",
    "project",
    "research",
    "review",
    "scope",
    "source",
    "stage",
    "system",
    "that",
    "their",
    "there",
    "these",
    "this",
    "through",
    "tool",
    "using",
    "version",
    "what",
    "when",
    "which",
    "with",
    "without",
    "work",
    "workspace",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def sha256_strings(lines: list[str]) -> str:
    normalized = "\n".join(lines).encode("utf-8")
    return sha256_bytes(normalized)


def slugify(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized)
    return normalized.strip("-")


def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def extract_keywords(text: str, max_terms: int = 10) -> list[str]:
    words = re.findall(r"[a-zA-Z]{6,}", text.lower())
    seen: list[str] = []
    for word in words:
        if word in STOPWORDS:
            continue
        if word not in seen:
            seen.append(word)
        if len(seen) >= max_terms:
            break
    return seen
