"""Load and normalize wiki findings JSON for the post-dialogue pipeline.

Findings come in two shapes (doc / code) with a polymorphic
`source_type` / `file_metadata` discriminator matched by
`ingest_stage._is_code_finding`. This module normalizes both into a single
`FindingRecord` suitable for capability compile, contract extraction, and
skill synthesis.

The `finding_id` is a stable token `f"{citation_id}#{file_hash[:12]}"` so
capabilities can name a finding without knowing its on-disk filename; a
rerun of ingest against the same seed regenerates the same id.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .artifacts import ArtifactPaths


_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class FindingRecord:
    finding_id: str
    citation_id: str
    file_hash: str
    seed_path: str
    source_type: str  # "document" or "code"
    concepts: tuple[dict[str, Any], ...] = ()
    claims: tuple[dict[str, Any], ...] = ()
    quotes: tuple[dict[str, Any], ...] = ()
    tables_figures: tuple[dict[str, Any], ...] = ()
    path: Path | None = None


def _normalize_list(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    out: list[dict[str, Any]] = []
    for row in value:
        if isinstance(row, dict):
            out.append(row)
    return tuple(out)


def _is_code_shape(payload: dict[str, Any]) -> bool:
    if payload.get("source_type") == "code":
        return True
    return isinstance(payload.get("file_metadata"), dict)


def _derive_finding_id(citation_id: str, file_hash: str) -> str:
    short_hash = (file_hash or "")[:12]
    return f"{citation_id}#{short_hash}"


def _record_from_payload(payload: dict[str, Any], path: Path) -> FindingRecord | None:
    citation_id = str(payload.get("citation_id") or "").strip()
    file_hash = str(payload.get("file_hash") or "").strip()
    seed_path = str(payload.get("seed_path") or "").strip()
    if not citation_id or not file_hash:
        return None
    source_type = "code" if _is_code_shape(payload) else "document"
    return FindingRecord(
        finding_id=_derive_finding_id(citation_id, file_hash),
        citation_id=citation_id,
        file_hash=file_hash,
        seed_path=seed_path,
        source_type=source_type,
        concepts=_normalize_list(payload.get("concepts")),
        claims=_normalize_list(payload.get("claims")),
        quotes=_normalize_list(payload.get("quotes")),
        tables_figures=_normalize_list(payload.get("tables_figures")),
        path=path,
    )


def _iter_legacy_records(payload: dict[str, Any], path: Path) -> Iterable[FindingRecord]:
    # Legacy: {"source_id": "...", "findings": [...]}. Each row may itself carry
    # the full doc/code payload. The file_hash is inherited from the parent if
    # the row doesn't have one.
    source_id = str(payload.get("source_id") or "").strip()
    parent_file_hash = str(payload.get("file_hash") or "").strip() or "legacy"
    for row in payload.get("findings") or []:
        if not isinstance(row, dict):
            continue
        row_citation = str(row.get("citation_id") or source_id or "").strip()
        row_hash = str(row.get("file_hash") or parent_file_hash or "legacy").strip()
        if not row_citation:
            continue
        merged = {
            **row,
            "citation_id": row_citation,
            "file_hash": row_hash,
        }
        rec = _record_from_payload(merged, path)
        if rec is not None:
            yield rec


def load_all_findings(paths: ArtifactPaths) -> list[FindingRecord]:
    """Return every finding record under wiki/findings/*.json.

    Files that fail JSON parsing or lack citation_id/file_hash are skipped
    silently — `validate_findings_schema` and `validate_all_findings` are the
    canonical gates. This loader is best-effort so downstream stages can run
    against partial workspaces.
    """
    findings_dir = paths.findings_dir
    if not findings_dir.exists():
        return []
    out: list[FindingRecord] = []
    for json_path in sorted(findings_dir.glob("*.json")):
        try:
            with json_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if "findings" in payload and "citation_id" not in payload:
            out.extend(_iter_legacy_records(payload, json_path))
            continue
        rec = _record_from_payload(payload, json_path)
        if rec is not None:
            out.append(rec)
    return out


def build_finding_index(records: Iterable[FindingRecord]) -> dict[str, FindingRecord]:
    """Return a {finding_id -> record} mapping for citation resolution.

    If two records share a finding_id (same citation_id + same short hash),
    the last one wins — in practice this only happens when a file hasn't
    changed across ingest runs, so either is fine.
    """
    return {rec.finding_id: rec for rec in records}


def _tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN_SPLIT.split(text.lower()) if tok}


def concept_vocabulary(records: Iterable[FindingRecord]) -> set[str]:
    """Lowercased token union of concepts[].name and claims[].statement.

    Used by `_is_generic_trigger` to decide whether a trigger string contains
    any domain vocabulary. Tokens are extracted by lower-casing and splitting
    on any non-alphanumeric character so single-word names ("schema") and
    compound names ("decision-log") both contribute.
    """
    vocab: set[str] = set()
    for rec in records:
        for concept in rec.concepts:
            name = str(concept.get("name") or "")
            vocab |= _tokenize(name)
            aliases = concept.get("aliases") or []
            if isinstance(aliases, list):
                for alias in aliases:
                    vocab |= _tokenize(str(alias))
        for claim in rec.claims:
            statement = str(claim.get("statement") or "")
            vocab |= _tokenize(statement)
    return vocab


def decision_log_vocabulary(decision_log: dict[str, Any]) -> set[str]:
    """Bootstrap vocabulary when wiki/findings/ is empty.

    Draws from every text-bearing field on the decision log's top-level
    sections (conventions, architecture, code_architecture, requirements,
    scope). Not as rich as concept_vocabulary, but enough to check a v1
    bootstrap run where ingest hasn't produced findings yet.
    """
    vocab: set[str] = set()
    root = decision_log.get("decision_log") or {}
    for row in root.get("conventions") or []:
        if isinstance(row, dict):
            for key in ("name", "choice", "rationale"):
                vocab |= _tokenize(str(row.get(key) or ""))
    for row in root.get("architecture") or []:
        if isinstance(row, dict):
            for key in ("component", "approach"):
                vocab |= _tokenize(str(row.get(key) or ""))
    for row in root.get("code_architecture") or []:
        if isinstance(row, dict):
            for key in ("aspect", "choice", "rationale"):
                vocab |= _tokenize(str(row.get(key) or ""))
            for lib in row.get("libraries") or []:
                if isinstance(lib, dict):
                    for key in ("name", "description"):
                        vocab |= _tokenize(str(lib.get(key) or ""))
    for row in root.get("requirements") or []:
        if isinstance(row, dict):
            vocab |= _tokenize(str(row.get("description") or ""))
    scope = root.get("scope") or {}
    for key in ("in_scope", "out_of_scope"):
        for row in scope.get(key) or []:
            if isinstance(row, dict):
                vocab |= _tokenize(str(row.get("item") or ""))
    return vocab


# A conservative stop-word list. Tokens that survive the strip must carry
# domain meaning; this list is derived from the phrases the Plan rejected
# as generic ("use when implementing", "use when generating", etc.).
GENERIC_TRIGGER_STOPWORDS: frozenset[str] = frozenset({
    "use",
    "when",
    "implementing",
    "generating",
    "producing",
    "running",
    "executing",
    "the",
    "a",
    "an",
    "to",
    "for",
    "and",
    "or",
    "of",
    "in",
    "on",
    "with",
    "needed",
    "required",
    "any",
    "every",
    "this",
    "that",
    "is",
    "are",
    "be",
    "it",
    "at",
    "by",
    "as",
    "from",
    "into",
    "task",
    "work",
    "item",
})


def trigger_content_tokens(trigger: str) -> set[str]:
    """Return the non-stopword lowercased tokens of a trigger string."""
    return _tokenize(trigger) - GENERIC_TRIGGER_STOPWORDS
