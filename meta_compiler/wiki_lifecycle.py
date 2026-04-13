from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import parse_frontmatter
from .utils import iso_now, read_text_safe


def _extract_definition_summary(body: str) -> str:
    lines = body.splitlines()
    in_definition = False
    collected: list[str] = []

    for line in lines:
        if line.strip() == "## Definition":
            in_definition = True
            continue
        if in_definition and line.startswith("## "):
            break
        if in_definition:
            stripped = line.strip()
            if stripped:
                collected.append(stripped)
        if len(" ".join(collected)) > 180:
            break

    if not collected:
        return "No definition summary available."
    summary = " ".join(collected)
    return summary[:220]


def build_index_markdown(pages_dir: Path, title: str) -> str:
    pages = sorted(pages_dir.glob("*.md"))
    grouped: dict[str, list[dict[str, Any]]] = {}

    for page_path in pages:
        text = read_text_safe(page_path)
        frontmatter, body = parse_frontmatter(text)
        page_id = str(frontmatter.get("id", page_path.stem))
        page_type = str(frontmatter.get("type", "concept"))
        status = str(frontmatter.get("status", "raw"))
        sources = frontmatter.get("sources", []) if isinstance(frontmatter.get("sources", []), list) else []
        entry = {
            "id": page_id,
            "stem": page_path.stem,
            "status": status,
            "source_count": len(sources),
            "summary": _extract_definition_summary(body),
        }
        grouped.setdefault(page_type, []).append(entry)

    lines = [f"# {title}", "", "## Catalog"]
    for category in sorted(grouped.keys()):
        lines.append("")
        lines.append(f"### {category}")
        for entry in sorted(grouped[category], key=lambda item: item["id"]):
            lines.append(
                f"- [{entry['id']}](pages/{entry['stem']}.md)"
                f" - {entry['summary']}"
                f" (status: {entry['status']}, sources: {entry['source_count']})"
            )

    lines.extend(
        [
            "",
            "## Stats",
            f"- Total pages: {len(pages)}",
            f"- Categories: {len(grouped)}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_index(pages_dir: Path, index_path: Path, title: str) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(build_index_markdown(pages_dir, title=title), encoding="utf-8")


def append_log_entry(log_path: Path, operation: str, title: str, details: list[str]) -> None:
    now = iso_now()
    day = now[:10]
    heading = f"## [{day}] {operation} | {title}"

    entry_lines = [heading, f"- timestamp: {now}"]
    for detail in details:
        entry_lines.append(f"- {detail}")

    if not log_path.exists():
        content = ["# Wiki Log", "", *entry_lines, ""]
        log_path.write_text("\n".join(content), encoding="utf-8")
        return

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + "\n".join(entry_lines) + "\n")
