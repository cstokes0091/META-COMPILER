from __future__ import annotations

import re
from pathlib import Path

from .artifacts import ArtifactPaths, load_manifest
from .io import dump_yaml, load_yaml, parse_frontmatter
from .utils import read_text_safe, slugify


INDEX_PAGE_ID = "__index__"
_NAV_PREFIX = "Wiki: "


def citation_anchor(citation_id: str) -> str:
    return slugify(citation_id) or citation_id


def citation_markdown_link(citation_id: str) -> str:
    return f"[{citation_id}](../index.md#{citation_anchor(citation_id)})"


def wiki_nav_markdown(wiki_name: str) -> str:
    return f"{_NAV_PREFIX}[{wiki_name}](../index.md)"


def inject_wiki_nav(body: str, wiki_name: str) -> str:
    if not wiki_name.strip():
        return body

    stripped = body.lstrip("\n")
    nav_pattern = re.compile(r"^Wiki:\s+\[[^\]]+\]\(\.\./index\.md\)\n\n?", re.MULTILINE)
    stripped = nav_pattern.sub("", stripped, count=1)
    return f"{wiki_nav_markdown(wiki_name)}\n\n{stripped}"


def heading_id(text: str) -> str:
    normalized = re.sub(r"[`*_#\[\]()<>]+", "", text).strip()
    return slugify(normalized) or "section"


def browser_href_for_markdown(href: str) -> str:
    if href.startswith(("http://", "https://", "mailto:")):
        return href

    if "#" in href:
        path, anchor = href.split("#", 1)
    else:
        path, anchor = href, ""

    normalized = path.strip()
    if normalized in {"", "#"}:
        return href

    if normalized.endswith("index.md"):
        target = f"?page={INDEX_PAGE_ID}"
    elif normalized.endswith(".md"):
        target = f"?page={Path(normalized).stem}"
    else:
        target = href

    if anchor and target.startswith("?"):
        return f"{target}#{anchor}"
    return target


def build_stage_flow_mermaid(include_stage4: bool = True) -> str:
    lines = [
        "flowchart LR",
        '  stage0["Stage 0\\nPrompt-driven init"] --> stage1a["Stage 1A\\nBreadth ingest"]',
        '  stage1a --> stage1a2["Stage 1A2\\nOrchestration"]',
        '  stage1a2 --> stage1b["Stage 1B\\nDepth pass"]',
        '  stage1b --> stage1c["Stage 1C\\nFresh review"]',
        '  stage1c --> stage2["Stage 2\\nVision elicitation"]',
        '  stage2 --> stage3["Stage 3\\nScaffold"]',
        '  prompts["Prompt contracts"] --> stage0',
        '  prompts --> stage1a2',
        '  prompts --> stage2',
        '  markdown["Markdown pages + citations"] --> stage1b',
        '  markdown --> stage1c',
        '  review["Review search artifacts"] --> stage1c',
    ]
    if include_stage4:
        lines.append('  stage3 --> stage4["Stage 4\\nExecute + pitch"]')
        lines.append('  stage4 --> output["Final output + PPTX"]')
    else:
        lines.append('  stage3 --> output["Execution contract"]')
    return "\n".join(lines)


def build_relationship_mermaid(pages_dir: Path) -> str:
    edges: set[tuple[str, str]] = set()
    for page_path in sorted(pages_dir.glob("*.md")):
        frontmatter, _ = parse_frontmatter(read_text_safe(page_path))
        source_id = str(frontmatter.get("id", page_path.stem))
        related = frontmatter.get("related", [])
        if not isinstance(related, list):
            continue
        for target in related:
            if isinstance(target, str) and target.strip():
                edges.add((source_id, target.strip()))

    if not edges:
        return 'graph TD\n  wiki["No explicit related links recorded yet"]'

    lines = ["graph TD"]
    for source_id, target_id in sorted(edges):
        lines.append(f"  {slugify(source_id) or 'page'}[{source_id!r}] --> {slugify(target_id) or 'related'}[{target_id!r}]")
    return "\n".join(lines)


def discover_prompt_contracts(paths: ArtifactPaths) -> list[str]:
    prompt_dir = paths.root.parent / "prompts"
    if not prompt_dir.exists():
        return []
    return sorted(path.name for path in prompt_dir.glob("*.prompt.md"))


def build_how_i_was_built_markdown(paths: ArtifactPaths, source_version: str) -> str:
    manifest = load_manifest(paths)
    wm = manifest.get("workspace_manifest", {}) if isinstance(manifest, dict) else {}
    wiki = wm.get("wiki", {}) if isinstance(wm, dict) else {}
    prompt_files = discover_prompt_contracts(paths)
    prompt_inventory = ", ".join(prompt_files) if prompt_files else "No prompt contracts discovered."
    pages_dir = paths.wiki_v2_pages_dir if source_version == "v2" and list(paths.wiki_v2_pages_dir.glob("*.md")) else paths.wiki_v1_pages_dir
    citations_root = load_yaml(paths.citations_index_path).get("citations", {})
    citation_count = len(citations_root) if isinstance(citations_root, dict) else 0
    flow_mermaid = build_stage_flow_mermaid(include_stage4=True)
    relationship_mermaid = build_relationship_mermaid(pages_dir)

    lines = [
        "## How I Was Built",
        "",
        f"- Wiki name: {wiki.get('name') or 'Not named yet'}",
        f"- Source version: {source_version}",
        f"- Page count: {wiki.get('page_count', 0)}",
        f"- Citation count: {citation_count}",
        f"- Prompt contracts: {prompt_inventory}",
        "",
        "### Logic Flow",
        "```mermaid",
        flow_mermaid,
        "```",
        "",
        "### Relationship Graph",
        "```mermaid",
        relationship_mermaid,
        "```",
        "",
    ]
    return "\n".join(lines)


def write_provenance_artifacts(paths: ArtifactPaths, source_version: str) -> dict[str, Path]:
    how_i_was_built = build_how_i_was_built_markdown(paths, source_version=source_version)
    flow_mermaid = build_stage_flow_mermaid(include_stage4=True)
    pages_dir = paths.wiki_v2_pages_dir if source_version == "v2" and list(paths.wiki_v2_pages_dir.glob("*.md")) else paths.wiki_v1_pages_dir
    relationship_mermaid = build_relationship_mermaid(pages_dir)

    how_path = paths.wiki_provenance_dir / "how_i_was_built.md"
    flow_path = paths.wiki_provenance_dir / "build_flow.mmd"
    graph_path = paths.wiki_provenance_dir / "relationship_graph.mmd"
    snapshot_path = paths.wiki_provenance_dir / "provenance_snapshot.yaml"

    how_path.write_text(how_i_was_built + "\n", encoding="utf-8")
    flow_path.write_text(flow_mermaid + "\n", encoding="utf-8")
    graph_path.write_text(relationship_mermaid + "\n", encoding="utf-8")
    dump_yaml(
        snapshot_path,
        {
            "provenance": {
                "source_version": source_version,
                "how_i_was_built_path": str(how_path),
                "build_flow_path": str(flow_path),
                "relationship_graph_path": str(graph_path),
            }
        },
    )

    return {
        "how_i_was_built": how_path,
        "build_flow": flow_path,
        "relationship_graph": graph_path,
        "snapshot": snapshot_path,
    }