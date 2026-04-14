from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import ArtifactPaths, build_paths, load_manifest
from .io import load_yaml
from .io import parse_frontmatter
from .utils import iso_now, read_text_safe
from .wiki_rendering import citation_anchor, write_provenance_artifacts


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


def _build_citations_section(paths: ArtifactPaths) -> list[str]:
    index_payload = load_yaml(paths.citations_index_path)
    citations = index_payload.get("citations", {}) if isinstance(index_payload, dict) else {}
    if not isinstance(citations, dict) or not citations:
        return ["## Citations", "", "No citations recorded."]

    lines = ["## Citations", ""]
    for citation_id, citation in sorted(citations.items()):
        if not isinstance(citation, dict):
            continue
        human = str(citation.get("human") or "No human-readable label")
        source = citation.get("source", {}) if isinstance(citation.get("source"), dict) else {}
        source_type = str(source.get("type") or "unknown")
        source_path = str(source.get("path") or "")
        source_url = str(source.get("url") or "")
        status = str(citation.get("status") or "raw")

        lines.append(f"### {citation_id}")
        lines.append(f"- Human: {human}")
        lines.append(f"- Source type: {source_type}")
        if source_url:
            lines.append(f"- Source URL: [Open source]({source_url})")
        elif source_path:
            relative_path = (Path("..") / ".." / source_path.lstrip("/")).as_posix()
            lines.append(f"- Source file: [Open artifact]({relative_path})")
        lines.append(f"- Status: {status}")
        lines.append(f"- Anchor: #{citation_anchor(citation_id)}")
        lines.append("")

    return lines


def build_index_markdown(paths: ArtifactPaths, pages_dir: Path, title: str) -> str:
    pages = sorted(pages_dir.glob("*.md"))
    grouped: dict[str, list[dict[str, Any]]] = {}
    source_version = "v2" if pages_dir == paths.wiki_v2_pages_dir else "v1"
    provenance_paths = write_provenance_artifacts(paths, source_version=source_version)
    how_i_was_built = read_text_safe(provenance_paths["how_i_was_built"]).rstrip()
    what_i_built_path = paths.wiki_provenance_dir / "what_i_built.md"
    what_i_built = read_text_safe(what_i_built_path).rstrip() if what_i_built_path.exists() else ""
    manifest = load_manifest(paths)
    wiki_meta = manifest.get("workspace_manifest", {}).get("wiki", {}) if isinstance(manifest, dict) else {}
    wiki_name = str(wiki_meta.get("name") or "").strip()
    resolved_title = title if not wiki_name else f"{wiki_name} {source_version.upper()} Index"

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

    lines = [f"# {resolved_title}", "", how_i_was_built]

    if what_i_built:
        lines.extend(["", what_i_built])

    lines.extend(["", *_build_citations_section(paths), "", "## Catalog"])
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
    paths = build_paths(index_path.parents[2])
    index_path.write_text(build_index_markdown(paths, pages_dir, title=title), encoding="utf-8")


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
