from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import ArtifactPaths
from .io import load_yaml, parse_frontmatter
from .utils import read_text_safe, slugify


def _slugify_name(name: str) -> str:
    slug = slugify(name)
    if not slug:
        return ""
    if slug.startswith("concept-"):
        return slug[len("concept-") :]
    return slug


def _json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class WikiPage:
    path: Path
    frontmatter: dict[str, Any]
    body: str

    @property
    def page_id(self) -> str:
        return str(self.frontmatter.get("id", self.path.stem))

    @property
    def page_type(self) -> str:
        return str(self.frontmatter.get("type", "concept"))


class WikiQueryInterface:
    def __init__(self, paths: ArtifactPaths, prefer_v2: bool = True):
        self.paths = paths
        self.pages_dir = self._resolve_pages_dir(prefer_v2=prefer_v2)
        self.citations_index = load_yaml(paths.citations_index_path) or {"citations": {}}

    def _resolve_pages_dir(self, prefer_v2: bool) -> Path:
        if prefer_v2 and self.paths.wiki_v2_pages_dir.exists() and list(self.paths.wiki_v2_pages_dir.glob("*.md")):
            return self.paths.wiki_v2_pages_dir
        return self.paths.wiki_v1_pages_dir

    def _load_pages(self) -> list[WikiPage]:
        pages: list[WikiPage] = []
        for page_path in sorted(self.pages_dir.glob("*.md")):
            text = read_text_safe(page_path)
            frontmatter, body = parse_frontmatter(text)
            pages.append(WikiPage(path=page_path, frontmatter=frontmatter, body=body))
        return pages

    def _find_page(self, name: str) -> WikiPage | None:
        normalized = name.strip().lower()
        for page in self._load_pages():
            if page.page_id.lower() == normalized or page.path.stem.lower() == normalized:
                return page
        return None

    def _extract_section_lines(self, body: str, section_name: str) -> list[str]:
        lines = body.splitlines()
        heading = f"## {section_name}"
        in_section = False
        extracted: list[str] = []

        for line in lines:
            if line.strip() == heading:
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if in_section:
                extracted.append(line)
        return extracted

    def get_concept(self, name: str) -> dict[str, Any] | None:
        page = self._find_page(name)
        if page is None:
            return None
        return {
            "id": page.page_id,
            "path": str(page.path),
            "frontmatter": page.frontmatter,
            "body": page.body,
        }

    def list_pages(self) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        for page in self._load_pages():
            title = page.page_id
            for line in page.body.splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    title = stripped[2:].strip() or page.page_id
                    break

            pages.append(
                {
                    "id": page.page_id,
                    "title": title,
                    "type": page.page_type,
                    "path": str(page.path),
                }
            )

        return pages

    def search_wiki(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        tokens = [token for token in re.findall(r"[a-zA-Z0-9]+", query.lower()) if token]
        scored: list[tuple[float, WikiPage]] = []

        for page in self._load_pages():
            text = (page.page_id + "\n" + page.body).lower()
            score = 0.0
            for token in tokens:
                if token in page.page_id.lower():
                    score += 2.0
                score += text.count(token) * 0.25
            if score > 0:
                scored.append((score, page))

        scored.sort(key=lambda row: row[0], reverse=True)
        results: list[dict[str, Any]] = []
        for score, page in scored[:limit]:
            definition_lines = self._extract_section_lines(page.body, "Definition")
            summary = " ".join(line.strip() for line in definition_lines if line.strip())
            results.append(
                {
                    "concept_id": page.page_id,
                    "path": str(page.path),
                    "relevance_score": round(score, 2),
                    "summary": summary[:220] if summary else "No summary available.",
                    "type": page.page_type,
                }
            )
        return results

    def get_relationships(self, concept: str) -> dict[str, Any]:
        target = concept.strip().lower()
        pages = self._load_pages()

        outbound: list[dict[str, Any]] = []
        inbound: list[dict[str, Any]] = []

        for page in pages:
            related = page.frontmatter.get("related", [])
            related_values = related if isinstance(related, list) else []
            for rel in related_values:
                if not isinstance(rel, str):
                    continue
                if page.page_id.lower() == target:
                    outbound.append({"type": "related", "target": rel})
                if rel.lower() == target:
                    inbound.append({"type": "related", "source": page.page_id})

            relationships_section = self._extract_section_lines(page.body, "Relationships")
            for line in relationships_section:
                if ":" not in line:
                    continue
                if page.page_id.lower() == target:
                    outbound.append({"type": "structured", "value": line.strip()})
                if target in line.lower():
                    inbound.append({"type": "structured", "source": page.page_id, "value": line.strip()})

        return {
            "concept": concept,
            "inbound": inbound,
            "outbound": outbound,
        }

    def get_equations(self, concept: str) -> list[str]:
        item = self.get_concept(concept)
        if not item:
            return []

        body = item["body"]
        equations: list[str] = []
        equations.extend(re.findall(r"\$[^\$]+\$", body))
        equations.extend(re.findall(r"\\\[(.*?)\\\]", body, flags=re.DOTALL))
        return equations

    def get_citations(self, concept: str) -> list[dict[str, Any]]:
        page = self._find_page(concept)
        if not page:
            return []

        sources = page.frontmatter.get("sources", [])
        citation_ids = sources if isinstance(sources, list) else []
        citations_root = self.citations_index.get("citations", {})
        result: list[dict[str, Any]] = []

        for citation_id in citation_ids:
            citation = citations_root.get(citation_id)
            if not isinstance(citation, dict):
                continue
            result.append(
                {
                    "citation_id": citation_id,
                    "human": citation.get("human"),
                    "source": citation.get("source", {}),
                    "metadata": citation.get("metadata", {}),
                    "status": citation.get("status"),
                }
            )
        return result

    def get_open_questions(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for page in self._load_pages():
            lines = self._extract_section_lines(page.body, "Open Questions")
            for line in lines:
                trimmed = line.strip()
                if not trimmed.startswith("-"):
                    continue
                question = trimmed.lstrip("-").strip()
                if not question:
                    continue
                results.append({"concept": page.page_id, "question": question})
        return results

    def get_debate_transcript(self, topic: str | None = None) -> dict[str, Any]:
        transcript_path = self.paths.reports_dir / "debate_transcript.yaml"
        transcript = load_yaml(transcript_path) or {}
        if not topic:
            return transcript

        serialized = str(transcript).lower()
        if topic.lower() in serialized:
            return transcript
        return {"debate_transcript": {"topic": topic, "match": False}}


    def compute_health_metrics(self) -> dict[str, Any]:
        pages = self._load_pages()
        inbound_counts = {page.page_id: 0 for page in pages}

        for page in pages:
            related = page.frontmatter.get("related", [])
            if isinstance(related, list):
                for rel in related:
                    if isinstance(rel, str) and rel in inbound_counts:
                        inbound_counts[rel] += 1

        orphan_pages: list[str] = []
        sparse_citation_pages: list[str] = []
        weak_relationship_pages: list[str] = []
        alias_pages: list[str] = []
        canonical_concept_pages: list[str] = []

        for page in pages:
            related = page.frontmatter.get("related", [])
            related_count = len(related) if isinstance(related, list) else 0
            source_count = len(page.frontmatter.get("sources", [])) if isinstance(page.frontmatter.get("sources", []), list) else 0

            page_type = page.page_type
            if page_type == "alias":
                alias_pages.append(page.page_id)
            aliases = page.frontmatter.get("aliases")
            if isinstance(aliases, list) and any(isinstance(a, str) and a.strip() for a in aliases):
                canonical_concept_pages.append(page.page_id)

            if inbound_counts.get(page.page_id, 0) == 0 and related_count == 0:
                orphan_pages.append(page.page_id)
            if source_count == 0:
                sparse_citation_pages.append(page.page_id)
            if related_count == 0:
                weak_relationship_pages.append(page.page_id)

        # Concepts whose name appears in findings under ≥2 citation_ids but
        # which haven't been captured in any page's `aliases:` yet.
        known_alias_slugs: set[str] = {_slugify_name(page_id) for page_id in alias_pages}
        for page in pages:
            aliases = page.frontmatter.get("aliases")
            if isinstance(aliases, list):
                for alias in aliases:
                    if isinstance(alias, str) and alias.strip():
                        known_alias_slugs.add(_slugify_name(alias))
                known_alias_slugs.add(_slugify_name(page.page_id))

        findings_dir = self.paths.findings_dir
        concept_source_map: dict[str, set[str]] = {}
        concept_display: dict[str, str] = {}
        if findings_dir.exists():
            for findings_path in sorted(findings_dir.glob("*.json")):
                try:
                    payload = _json_load(findings_path)
                except Exception:  # pragma: no cover - malformed file
                    continue
                if not isinstance(payload, dict):
                    continue
                citation_id = str(payload.get("citation_id") or "")
                for concept in payload.get("concepts", []) or []:
                    if not isinstance(concept, dict):
                        continue
                    name = str(concept.get("name") or "").strip()
                    if not name:
                        continue
                    slug = _slugify_name(name)
                    concept_source_map.setdefault(slug, set()).add(citation_id)
                    concept_display.setdefault(slug, name)

        unreconciled_concept_candidates: list[dict[str, Any]] = []
        for slug, sources in concept_source_map.items():
            if slug in known_alias_slugs:
                continue
            if len(sources) < 2:
                continue
            unreconciled_concept_candidates.append(
                {
                    "name": concept_display.get(slug, slug),
                    "sources": sorted(sources),
                }
            )
        unreconciled_concept_candidates.sort(key=lambda row: row["name"].lower())

        # Canonical pages backed by ≥2 citation_ids whose Definition hasn't
        # been stamped with `source: cross_source_synthesis`.
        edit_manifest_path = self.paths.wiki_v2_dir / "edit_manifest.yaml"
        edit_manifest = load_yaml(edit_manifest_path) or {}
        edit_pages = (
            edit_manifest.get("wiki_v2_edit_manifest", {}).get("pages", {})
            if isinstance(edit_manifest, dict)
            else {}
        )
        if not isinstance(edit_pages, dict):
            edit_pages = {}

        concepts_with_multiple_sources_but_no_synthesis: list[str] = []
        for page in pages:
            if page.page_type != "concept":
                continue
            sources = page.frontmatter.get("sources") or []
            citation_count = len({str(s) for s in sources if isinstance(s, str) and s.strip()})
            if citation_count < 2:
                continue
            entry = edit_pages.get(page.path.name)
            if not isinstance(entry, dict) or entry.get("source") != "cross_source_synthesis":
                concepts_with_multiple_sources_but_no_synthesis.append(page.page_id)

        open_questions = self.get_open_questions()
        return {
            "page_count": len(pages),
            "orphan_pages": orphan_pages,
            "sparse_citation_pages": sparse_citation_pages,
            "weak_relationship_pages": weak_relationship_pages,
            "alias_groups_count": len(alias_pages),
            "canonical_concept_pages": canonical_concept_pages,
            "unreconciled_concept_candidates": unreconciled_concept_candidates,
            "concepts_with_multiple_sources_but_no_synthesis": concepts_with_multiple_sources_but_no_synthesis,
            "open_question_count": len(open_questions),
            "open_questions": open_questions,
        }
