from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised when dependency missing
    yaml = None


class MissingDependencyError(RuntimeError):
    pass


def _ensure_yaml() -> None:
    if yaml is None:
        raise MissingDependencyError(
            "PyYAML is required. Install dependencies with: "
            "python3 -m pip install -r requirements.txt"
        )


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return {}
    _ensure_yaml()
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if data is not None else {}


def dump_yaml(path: Path, data: Any) -> None:
    _ensure_yaml()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=False)


def render_frontmatter(frontmatter: dict[str, Any]) -> str:
    _ensure_yaml()
    return yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()


def parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    if not markdown.startswith("---\n"):
        return {}, markdown

    marker = "\n---\n"
    end_pos = markdown.find(marker, 4)
    if end_pos == -1:
        return {}, markdown

    frontmatter_raw = markdown[4:end_pos]
    body = markdown[end_pos + len(marker) :]

    _ensure_yaml()
    data = yaml.safe_load(frontmatter_raw) or {}
    if not isinstance(data, dict):
        return {}, body
    return data, body
