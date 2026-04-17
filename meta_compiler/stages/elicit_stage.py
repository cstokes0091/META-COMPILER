from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..artifacts import (
    build_paths,
    ensure_layout,
    latest_decision_log_path,
    load_manifest,
    save_manifest,
)
from ..io import dump_yaml, load_yaml
from ..utils import iso_now, read_text_safe, sha256_bytes
from ..validation import validate_decision_log


# ---------------------------------------------------------------------------
# Decision block parsing (Stage 2 prompt-as-conductor)
#
# The Stage 2 prompt instructs the LLM to append decision blocks to
# `workspace-artifacts/runtime/stage2/transcript.md`. Each block is compiled
# mechanically into an entry in `decision_log_v<N>.yaml` at --finalize time.
#
# The block grammar is intentionally strict: an unknown Section value, a
# missing required field, or a malformed sublist raises a parse error so the
# compile step fails loudly instead of silently dropping decisions.
# ---------------------------------------------------------------------------


VALID_SECTIONS = {
    "conventions",
    "architecture",
    "scope-in",
    "scope-out",
    "requirements",
    "open_items",
    "agents_needed",
}

VALID_CONVENTION_DOMAINS = {"math", "code", "citation", "terminology"}
VALID_REQUIREMENT_SOURCES = {"user", "derived"}
VALID_DEFER_TARGETS = {"implementation", "future_work"}

_COMMA_LIST_FIELDS = {
    "citations",
    "constraints_applied",
    "reads",
    "writes",
    "key_constraints",
}

_NONE_SENTINELS = {"(none)", "none", "-"}

# Per-section required fields. "Section", "rationale", "citations" are
# required for every block and validated separately.
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "conventions": ("domain", "choice"),
    "architecture": ("component", "approach", "constraints_applied"),
    "scope-in": ("item",),
    "scope-out": ("item", "revisit_if"),
    "requirements": ("source", "description", "verification", "lens"),
    "open_items": ("description", "deferred_to", "owner"),
    "agents_needed": ("role", "responsibility", "reads", "writes", "key_constraints"),
}


@dataclass(frozen=True)
class DecisionBlock:
    """One parsed ### Decision: ... block from a Stage 2 transcript."""

    name: str
    section: str
    rationale: str
    citations: tuple[str, ...]
    fields: dict[str, Any] = field(default_factory=dict)
    # Optional sublist for architecture's `alternatives rejected` — list of
    # {"name": ..., "reason": ...} dicts.
    alternatives_rejected: tuple[dict[str, str], ...] = ()
    raw_source: str = ""
    source_line: int = 0


class DecisionBlockParseError(ValueError):
    """Raised when a decision block cannot be parsed."""


def _normalize_field_key(raw: str) -> str:
    """Normalize a field label into a canonical key.

    "Section" → "section"; "Revisit if" → "revisit_if"; "Alternatives
    rejected" → "alternatives_rejected"; "Deferred to" → "deferred_to"; etc.
    """
    lowered = raw.strip().lower()
    return re.sub(r"[\s-]+", "_", lowered)


def _parse_comma_list(raw: str) -> list[str]:
    stripped = raw.strip()
    if not stripped or stripped.lower() in _NONE_SENTINELS:
        return []
    return [item.strip() for item in stripped.split(",") if item.strip()]


_HEADING_BLOCK_RE = re.compile(r"^### Decision:\s*(.+?)\s*$")
_HEADING_AREA_RE = re.compile(r"^##\s+.+$")
_FIELD_RE = re.compile(r"^-\s+([A-Za-z][A-Za-z \-]*?):\s*(.*)$")
_SUBFIELD_RE = re.compile(r"^  -\s+([^:]+?):\s*(.*)$")


def _collect_block_lines(lines: list[str], start_idx: int) -> tuple[list[str], int]:
    """Collect lines belonging to one decision block starting at start_idx.

    The block ends at the next `### Decision:` line, the next `## ` heading,
    or end-of-file. Returns (collected_lines, end_idx_exclusive).
    """
    end_idx = start_idx + 1
    while end_idx < len(lines):
        line = lines[end_idx]
        if _HEADING_BLOCK_RE.match(line):
            break
        if _HEADING_AREA_RE.match(line) and not line.startswith("### "):
            break
        end_idx += 1
    return lines[start_idx:end_idx], end_idx


def _parse_single_block(block_lines: list[str], source_line: int) -> DecisionBlock:
    if not block_lines:
        raise DecisionBlockParseError("empty block")

    heading_match = _HEADING_BLOCK_RE.match(block_lines[0])
    if not heading_match:
        raise DecisionBlockParseError(f"line {source_line}: block does not start with '### Decision:'")
    name = heading_match.group(1).strip()
    if not name:
        raise DecisionBlockParseError(f"line {source_line}: decision block is missing a name")

    fields: dict[str, Any] = {}
    alternatives: list[dict[str, str]] = []
    current_field: str | None = None

    for offset, line in enumerate(block_lines[1:], start=1):
        stripped = line.strip()
        if not stripped:
            current_field = None
            continue

        sub_match = _SUBFIELD_RE.match(line)
        field_match = _FIELD_RE.match(line)

        if sub_match and current_field == "alternatives_rejected":
            alt_name = sub_match.group(1).strip()
            alt_reason = sub_match.group(2).strip()
            if not alt_name:
                raise DecisionBlockParseError(
                    f"line {source_line + offset}: alternative entry missing a name"
                )
            alternatives.append({"name": alt_name, "reason": alt_reason})
            continue

        if field_match:
            label = _normalize_field_key(field_match.group(1))
            value = field_match.group(2).strip()
            current_field = label

            if label == "alternatives_rejected":
                # The body lives in subsequent indented `  - name: reason` lines.
                # An inline value on the label line itself is allowed but unusual.
                if value:
                    alt_parts = value.split(":", 1)
                    if len(alt_parts) == 2:
                        alternatives.append(
                            {"name": alt_parts[0].strip(), "reason": alt_parts[1].strip()}
                        )
                continue

            if label in _COMMA_LIST_FIELDS:
                fields[label] = _parse_comma_list(value)
            else:
                fields[label] = value
            continue

        # Non-field, non-subfield line inside a block — ignore (prose is
        # allowed between blocks but discouraged inside; we tolerate it).

    section = fields.pop("section", None)
    if not isinstance(section, str) or not section:
        raise DecisionBlockParseError(
            f"line {source_line}: decision block '{name}' is missing required field 'Section'"
        )
    section = section.strip()
    if section not in VALID_SECTIONS:
        raise DecisionBlockParseError(
            f"line {source_line}: decision block '{name}' has unknown Section '{section}'. "
            f"Valid: {sorted(VALID_SECTIONS)}"
        )

    rationale = fields.pop("rationale", "")
    if not isinstance(rationale, str) or not rationale.strip():
        raise DecisionBlockParseError(
            f"line {source_line}: decision block '{name}' is missing required field 'Rationale'"
        )

    citations_raw = fields.pop("citations", None)
    if citations_raw is None:
        raise DecisionBlockParseError(
            f"line {source_line}: decision block '{name}' is missing required field 'Citations' "
            "(use '(none)' if no citations apply)"
        )
    if not isinstance(citations_raw, list):
        citations_raw = _parse_comma_list(str(citations_raw))

    for required in _REQUIRED_FIELDS[section]:
        if required not in fields:
            raise DecisionBlockParseError(
                f"line {source_line}: decision block '{name}' (Section: {section}) "
                f"is missing required field '{required}'"
            )
        value = fields[required]
        # Accept zero-length list fields like `- Constraints applied: (none)` ONLY
        # when the underlying Decision Log schema accepts an empty list. For
        # required string fields, empty is an error.
        if isinstance(value, str) and not value.strip():
            raise DecisionBlockParseError(
                f"line {source_line}: decision block '{name}' field '{required}' is empty"
            )

    if section == "conventions":
        domain = fields.get("domain", "")
        if domain not in VALID_CONVENTION_DOMAINS:
            raise DecisionBlockParseError(
                f"line {source_line}: decision block '{name}' Domain '{domain}' must be one of "
                f"{sorted(VALID_CONVENTION_DOMAINS)}"
            )
    elif section == "requirements":
        source_value = fields.get("source", "")
        if source_value not in VALID_REQUIREMENT_SOURCES:
            raise DecisionBlockParseError(
                f"line {source_line}: decision block '{name}' Source '{source_value}' must be one of "
                f"{sorted(VALID_REQUIREMENT_SOURCES)}"
            )
    elif section == "open_items":
        deferred_to = fields.get("deferred_to", "")
        if deferred_to not in VALID_DEFER_TARGETS:
            raise DecisionBlockParseError(
                f"line {source_line}: decision block '{name}' Deferred to '{deferred_to}' must be one of "
                f"{sorted(VALID_DEFER_TARGETS)}"
            )

    return DecisionBlock(
        name=name,
        section=section,
        rationale=rationale.strip(),
        citations=tuple(str(c) for c in citations_raw),
        fields=fields,
        alternatives_rejected=tuple(alternatives),
        raw_source="\n".join(block_lines),
        source_line=source_line,
    )


def parse_decision_blocks(transcript_text: str) -> tuple[list[DecisionBlock], list[str]]:
    """Extract all decision blocks from a Stage 2 transcript.

    Returns (blocks, errors). If errors is non-empty, the caller must treat
    the parse as failed (the CLI exits nonzero). `blocks` may still contain
    successfully-parsed blocks alongside errors, which is useful for
    diagnostics but not for compile.
    """
    if not transcript_text.strip():
        return [], []

    lines = transcript_text.splitlines()
    blocks: list[DecisionBlock] = []
    errors: list[str] = []

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if not _HEADING_BLOCK_RE.match(line):
            idx += 1
            continue

        block_lines, next_idx = _collect_block_lines(lines, idx)
        try:
            block = _parse_single_block(block_lines, source_line=idx + 1)
            blocks.append(block)
        except DecisionBlockParseError as exc:
            errors.append(str(exc))
        idx = next_idx

    return blocks, errors


# ---------------------------------------------------------------------------
# Decision Log compilation
# ---------------------------------------------------------------------------


def _compile_conventions(blocks: list[DecisionBlock]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for block in blocks:
        entries.append(
            {
                "name": block.name,
                "domain": block.fields["domain"],
                "choice": block.fields["choice"],
                "rationale": block.rationale,
                "citations": list(block.citations),
            }
        )
    return entries


def _compile_architecture(blocks: list[DecisionBlock]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for block in blocks:
        entries.append(
            {
                "component": block.fields["component"],
                "approach": block.fields["approach"],
                "alternatives_rejected": [dict(alt) for alt in block.alternatives_rejected],
                "constraints_applied": list(block.fields.get("constraints_applied", [])),
                "citations": list(block.citations),
                "rationale": block.rationale,
            }
        )
    return entries


def _compile_scope(
    in_blocks: list[DecisionBlock], out_blocks: list[DecisionBlock]
) -> dict[str, list[dict[str, Any]]]:
    in_scope = [
        {
            "item": block.fields["item"],
            "rationale": block.rationale,
            "citations": list(block.citations),
        }
        for block in in_blocks
    ]
    out_of_scope = [
        {
            "item": block.fields["item"],
            "rationale": block.rationale,
            "revisit_if": block.fields["revisit_if"],
            "citations": list(block.citations),
        }
        for block in out_blocks
    ]
    return {"in_scope": in_scope, "out_of_scope": out_of_scope}


def _compile_requirements(blocks: list[DecisionBlock]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for idx, block in enumerate(blocks, start=1):
        entries.append(
            {
                "id": f"REQ-{idx:03d}",
                "description": block.fields["description"],
                "source": block.fields["source"],
                "citations": list(block.citations),
                "verification": block.fields["verification"],
                "lens": block.fields["lens"],
                "rationale": block.rationale,
            }
        )
    return entries


def _compile_open_items(blocks: list[DecisionBlock]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for block in blocks:
        entries.append(
            {
                "description": block.fields["description"],
                "deferred_to": block.fields["deferred_to"],
                "owner": block.fields["owner"],
                "rationale": block.rationale,
                "citations": list(block.citations),
            }
        )
    return entries


def _compile_agents(blocks: list[DecisionBlock]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for block in blocks:
        entries.append(
            {
                "role": block.fields["role"],
                "responsibility": block.fields["responsibility"],
                "reads": list(block.fields.get("reads", [])),
                "writes": list(block.fields.get("writes", [])),
                "key_constraints": list(block.fields.get("key_constraints", [])),
                "rationale": block.rationale,
                "citations": list(block.citations),
            }
        )
    return entries


def compile_decision_log(
    blocks: list[DecisionBlock],
    project_meta: dict[str, Any],
    prior_version: int | None = None,
    reason_for_revision: str | None = None,
    problem_statement_hash: str = "",
    wiki_version: str = "",
    use_case: str = "",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Turn parsed decision blocks into a full decision_log payload.

    project_meta is a dict with at least `project_name` and `project_type`.
    The caller is responsible for validating the returned payload via
    `validate_decision_log`.
    """
    version = (prior_version or 0) + 1

    by_section: dict[str, list[DecisionBlock]] = {name: [] for name in VALID_SECTIONS}
    for block in blocks:
        by_section[block.section].append(block)

    return {
        "decision_log": {
            "meta": {
                "project_name": project_meta.get("project_name", "META-COMPILER Project"),
                "project_type": project_meta.get("project_type", "algorithm"),
                "created": created_at or iso_now(),
                "version": version,
                "parent_version": prior_version,
                "reason_for_revision": reason_for_revision,
                "problem_statement_hash": problem_statement_hash,
                "wiki_version": wiki_version,
                "use_case": use_case,
            },
            "conventions": _compile_conventions(by_section["conventions"]),
            "architecture": _compile_architecture(by_section["architecture"]),
            "scope": _compile_scope(
                in_blocks=by_section["scope-in"],
                out_blocks=by_section["scope-out"],
            ),
            "requirements": _compile_requirements(by_section["requirements"]),
            "open_items": _compile_open_items(by_section["open_items"]),
            "agents_needed": _compile_agents(by_section["agents_needed"]),
        }
    }


def mechanical_fidelity_checks(
    blocks: list[DecisionBlock],
    compiled: dict[str, Any],
    known_citation_ids: set[str],
) -> list[dict[str, Any]]:
    """Run mechanical fidelity checks described in spec §5.2 and §8.3.

    Returns a list of check results. Each check is a dict with
    `name`, `result` (PASS|FAIL), `evidence`, and (on FAIL) `remediation`.
    The caller aggregates to produce a non-zero CLI exit if any FAILed.
    """
    checks: list[dict[str, Any]] = []
    root = compiled.get("decision_log", {})

    entry_count = (
        len(root.get("conventions", []))
        + len(root.get("architecture", []))
        + len(root.get("scope", {}).get("in_scope", []) or [])
        + len(root.get("scope", {}).get("out_of_scope", []) or [])
        + len(root.get("requirements", []))
        + len(root.get("open_items", []))
        + len(root.get("agents_needed", []))
    )
    block_count = len(blocks)
    checks.append(
        {
            "name": "block_count_matches_entry_count",
            "result": "PASS" if block_count == entry_count else "FAIL",
            "evidence": f"{block_count} transcript blocks, {entry_count} decision log entries",
            "remediation": (
                "Re-run --finalize after fixing the transcript; a mismatch indicates a compile bug."
                if block_count != entry_count
                else ""
            ),
        }
    )

    unresolved_citations: list[tuple[str, str]] = []
    for block in blocks:
        for cid in block.citations:
            if cid not in known_citation_ids:
                unresolved_citations.append((block.name, cid))
    checks.append(
        {
            "name": "citation_ids_resolve",
            "result": "PASS" if not unresolved_citations else "FAIL",
            "evidence": (
                "all citation IDs resolve to the citation index"
                if not unresolved_citations
                else "; ".join(
                    f"{name}→{cid}" for name, cid in unresolved_citations[:8]
                )
            ),
            "remediation": (
                "Replace unresolved citation IDs in the transcript or "
                "add the citation to workspace-artifacts/wiki/citations/index.yaml."
                if unresolved_citations
                else ""
            ),
        }
    )

    req_ids = [row.get("id") for row in root.get("requirements", []) if isinstance(row, dict)]
    expected = [f"REQ-{idx:03d}" for idx in range(1, len(req_ids) + 1)]
    checks.append(
        {
            "name": "req_ids_sequential",
            "result": "PASS" if req_ids == expected else "FAIL",
            "evidence": (
                f"REQ ids = {req_ids[:5]}"
                if req_ids
                else "no requirements in decision log"
            ),
            "remediation": (
                "REQ-NNN assignment is the compile step's job; nonsequential IDs indicate a bug."
                if req_ids != expected
                else ""
            ),
        }
    )

    schema_issues = validate_decision_log(compiled)
    checks.append(
        {
            "name": "schema_validates",
            "result": "PASS" if not schema_issues else "FAIL",
            "evidence": (
                "decision_log schema passes validate_decision_log"
                if not schema_issues
                else f"{len(schema_issues)} schema issues: {schema_issues[:3]}"
            ),
            "remediation": (
                "Fix schema issues before proceeding."
                if schema_issues
                else ""
            ),
        }
    )

    return checks


# ---------------------------------------------------------------------------
# Stage 2 --start / --finalize
#
# Prompt-as-conductor bookends. See .github/docs/stage-2-hardening.md §5.
# ---------------------------------------------------------------------------


_DECISION_BLOCK_FORMAT_DOC = """\
### Decision: <short name>
- Section: <conventions | architecture | scope-in | scope-out | requirements | open_items | agents_needed>
- <section-specific required fields — see below>
- Rationale: <why, natural language>
- Citations: src-..., src-...   (use '(none)' if no citations apply)

Section-specific required fields:

- conventions: Domain (math|code|citation|terminology), Choice
- architecture: Component, Approach, Constraints applied. Alternatives rejected
  is optional but strongly preferred — write as an indented sublist of
  '  - <name>: <reason>'.
- scope-in: Item
- scope-out: Item, Revisit if
- requirements: Source (user|derived), Description (EARS-phrased),
  Verification, Lens (functional|performance|reliability|usability|security|
  maintainability|portability|constraint|data|interface|business-rule).
  Do not assign REQ-NNN IDs yourself — the --finalize step assigns them.
- open_items: Description, Deferred to (implementation|future_work), Owner
- agents_needed: Role, Responsibility, Reads, Writes, Key constraints
"""


def _problem_statement_hash(workspace_root: Path) -> str:
    statement_path = workspace_root / "PROBLEM_STATEMENT.md"
    if not statement_path.exists():
        return sha256_bytes(b"")
    return sha256_bytes(read_text_safe(statement_path).encode("utf-8"))


def _mechanical_check(name: str, result: str, evidence: str = "", remediation: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "result": result}
    if evidence:
        entry["evidence"] = evidence
    if remediation:
        entry["remediation"] = remediation
    return entry


def _preflight_checks(
    paths,
    workspace_root: Path,
    override_iterate_reason: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run mechanical preflight checks. Returns (check_entries, blocking_reasons)."""
    from ..validation import validate_problem_statement  # local import avoids cycle at load

    checks: list[dict[str, Any]] = []
    blocking: list[str] = []

    manifest = load_manifest(paths)
    if not manifest:
        checks.append(
            _mechanical_check(
                "manifest_present",
                "FAIL",
                evidence=f"{paths.manifest_path} missing",
                remediation="Run `meta-compiler meta-init` first.",
            )
        )
        blocking.append("manifest missing — run meta-init")
    else:
        checks.append(
            _mechanical_check(
                "manifest_present",
                "PASS",
                evidence=f"manifest loaded from {paths.manifest_path.name}",
            )
        )

    problem_statement_path = workspace_root / "PROBLEM_STATEMENT.md"
    ps_issues = validate_problem_statement(problem_statement_path)
    if ps_issues:
        checks.append(
            _mechanical_check(
                "problem_statement_complete",
                "FAIL",
                evidence="; ".join(ps_issues[:3]),
                remediation="Edit PROBLEM_STATEMENT.md to fill in all required sections.",
            )
        )
        blocking.append("problem statement incomplete")
    else:
        checks.append(
            _mechanical_check(
                "problem_statement_complete",
                "PASS",
                evidence="all required sections present, no template markers",
            )
        )

    if paths.wiki_v2_pages_dir.exists():
        wiki_pages = list(paths.wiki_v2_pages_dir.glob("*.md"))
    else:
        wiki_pages = []
    if not wiki_pages:
        checks.append(
            _mechanical_check(
                "wiki_v2_populated",
                "FAIL",
                evidence="no pages under workspace-artifacts/wiki/v2/pages/",
                remediation="Run Stage 1B (`meta-compiler research-depth`) to populate wiki v2.",
            )
        )
        blocking.append("wiki v2 has no pages")
    else:
        checks.append(
            _mechanical_check(
                "wiki_v2_populated",
                "PASS",
                evidence=f"{len(wiki_pages)} pages",
            )
        )

    citations_payload = load_yaml(paths.citations_index_path) or {}
    citations = citations_payload.get("citations", {})
    if not isinstance(citations, dict) or not citations:
        checks.append(
            _mechanical_check(
                "citation_index_nonempty",
                "FAIL",
                evidence="citations index empty or malformed",
                remediation="Re-run Stage 1A (`meta-compiler research-breadth`) to populate citations.",
            )
        )
        blocking.append("citation index empty")
    else:
        checks.append(
            _mechanical_check(
                "citation_index_nonempty",
                "PASS",
                evidence=f"{len(citations)} citations",
            )
        )

    gap_report_path = paths.reports_dir / "merged_gap_report.yaml"
    if not gap_report_path.exists():
        checks.append(
            _mechanical_check(
                "gap_report_present",
                "FAIL",
                evidence=f"{gap_report_path.name} missing",
                remediation="Run Stage 1B (`meta-compiler research-depth`) to generate the gap report.",
            )
        )
        blocking.append("gap report missing")
    else:
        checks.append(
            _mechanical_check(
                "gap_report_present",
                "PASS",
                evidence=str(gap_report_path.relative_to(paths.root).as_posix()),
            )
        )

    handoff_path = paths.reviews_dir / "1a2_handoff.yaml"
    handoff_payload = load_yaml(handoff_path) if handoff_path.exists() else {}
    handoff_decision = (
        handoff_payload.get("stage_1a2_handoff", {}).get("decision")
        if isinstance(handoff_payload, dict)
        else None
    )
    if handoff_decision == "PROCEED":
        checks.append(
            _mechanical_check(
                "stage_1c_proceed",
                "PASS",
                evidence="decision: PROCEED",
            )
        )
    elif handoff_decision == "ITERATE" and override_iterate_reason:
        checks.append(
            _mechanical_check(
                "stage_1c_proceed",
                "WARN",
                evidence=(
                    f"decision: ITERATE overridden with reason: {override_iterate_reason}"
                ),
                remediation="",
            )
        )
    elif handoff_decision == "ITERATE":
        checks.append(
            _mechanical_check(
                "stage_1c_proceed",
                "FAIL",
                evidence="decision: ITERATE",
                remediation=(
                    "Either iterate Stage 1B to address blocking gaps, or re-run "
                    "`meta-compiler elicit-vision --start --override-iterate \"<reason>\"`."
                ),
            )
        )
        blocking.append("Stage 1C handoff decision is ITERATE (no override provided)")
    else:
        checks.append(
            _mechanical_check(
                "stage_1c_proceed",
                "FAIL",
                evidence=f"handoff missing or malformed: {handoff_path}",
                remediation="Run Stage 1C (`meta-compiler review`) to produce the handoff.",
            )
        )
        blocking.append("Stage 1C handoff missing")

    return checks, blocking


def _gap_annotations_by_section(paths) -> dict[str, list[str]]:
    """Group merged-gap-report gaps by the Decision Log section they inform.

    Heuristic mapping based on gap type and severity — not exact but good
    enough to seed dialog. The orchestrator's semantic preflight is what
    surfaces gaps the CLI can't.
    """
    gap_report_path = paths.reports_dir / "merged_gap_report.yaml"
    payload = load_yaml(gap_report_path) if gap_report_path.exists() else {}
    gaps = (payload.get("gap_report", {}) if isinstance(payload, dict) else {}).get(
        "gaps", []
    )
    buckets: dict[str, list[str]] = {
        "conventions": [],
        "architecture": [],
        "scope-in": [],
        "scope-out": [],
        "requirements": [],
        "open_items": [],
        "agents_needed": [],
    }
    if not isinstance(gaps, list):
        return buckets
    for gap in gaps[:40]:
        if not isinstance(gap, dict):
            continue
        description = str(gap.get("description", "")).strip()
        gap_type = str(gap.get("type", ""))
        severity = str(gap.get("severity", ""))
        if not description:
            continue
        line = f"[{severity}] {description}"
        if gap_type in {"structural", "connection"}:
            buckets["architecture"].append(line)
        elif gap_type == "coverage":
            buckets["scope-in"].append(line)
        elif gap_type == "evidence":
            buckets["requirements"].append(line)
        elif gap_type == "epistemic":
            buckets["open_items"].append(line)
        else:
            buckets["architecture"].append(line)

    for key in buckets:
        buckets[key] = buckets[key][:8]
    return buckets


def _citation_inventory_lines(paths) -> list[str]:
    payload = load_yaml(paths.citations_index_path) or {}
    citations = payload.get("citations", {})
    if not isinstance(citations, dict):
        return []
    lines: list[str] = []
    for cid, entry in sorted(citations.items()):
        if not isinstance(entry, dict):
            continue
        human = str(entry.get("human", "")).strip() or cid
        source_type = str((entry.get("source") or {}).get("type", "")) or "unknown"
        lines.append(f"- `{cid}` — {human} ({source_type})")
    return lines[:50]


def _render_brief(
    paths,
    manifest: dict[str, Any],
    decision_log_version: int,
    generated_at: str,
) -> str:
    wm = manifest.get("workspace_manifest", {}) if isinstance(manifest, dict) else {}
    wiki = wm.get("wiki", {}) if isinstance(wm, dict) else {}
    citation_inventory = _citation_inventory_lines(paths)
    gap_buckets = _gap_annotations_by_section(paths)

    header = [
        "# Stage 2 Brief",
        "",
        f"Generated: {generated_at}",
        f"Decision Log version: v{decision_log_version}",
        f"Wiki version: {wiki.get('version') or '(unset)'}",
        "",
        "## Where to look",
        "",
        "- PROBLEM_STATEMENT.md",
        "- workspace-artifacts/wiki/v2/index.md",
        "- workspace-artifacts/wiki/citations/index.yaml",
        "- workspace-artifacts/wiki/reports/merged_gap_report.yaml",
        "- workspace-artifacts/wiki/reviews/1a2_handoff.yaml",
        "",
        "## Open gaps (top, grouped by Decision Log section)",
        "",
    ]

    gap_section_labels = {
        "conventions": "Conventions",
        "architecture": "Architecture",
        "scope-in": "Scope (in)",
        "scope-out": "Scope (out)",
        "requirements": "Requirements",
        "open_items": "Open Items",
        "agents_needed": "Agents Needed",
    }

    body_lines: list[str] = []
    for section_key, label in gap_section_labels.items():
        items = gap_buckets.get(section_key, [])
        if not items:
            continue
        body_lines.append(f"### {label}")
        body_lines.extend([f"- {item}" for item in items])
        body_lines.append("")
    if not body_lines:
        body_lines = [
            "_No gaps flagged by Stage 1B — the review surface is clean at the mechanical level. "
            "The orchestrator's semantic preflight may still surface coverage issues._",
            "",
        ]

    footer = [
        "## Citation inventory",
        "",
    ]
    if citation_inventory:
        footer.extend(citation_inventory)
    else:
        footer.append("- (no citations registered)")
    footer.extend(
        [
            "",
            "## Decision block format",
            "",
            "```markdown",
            _DECISION_BLOCK_FORMAT_DOC.rstrip(),
            "```",
            "",
            "## Decision Log schema",
            "",
            "See `META-COMPILER.md` § \"Decision Log Schema\" for the full YAML shape. You do not",
            "author YAML; the `meta-compiler elicit-vision --finalize` step compiles your",
            "transcript into the canonical `decision_log_v<N>.yaml`.",
            "",
            "## Transcript path",
            "",
            "Append your decision blocks (and surrounding prose) to:",
            "",
            "    workspace-artifacts/runtime/stage2/transcript.md",
            "",
        ]
    )

    return "\n".join(header + body_lines + footer)


def _render_transcript_skeleton(
    paths,
    decision_log_version: int,
    generated_at: str,
) -> str:
    gap_buckets = _gap_annotations_by_section(paths)

    def _area_block(heading: str, key: str, hint: str) -> list[str]:
        items = gap_buckets.get(key, [])
        lines = [f"## Decision Area: {heading}", "", f"_{hint}_", ""]
        if items:
            lines.append("Gaps flagged by Stage 1B relevant here:")
            lines.extend([f"- {item}" for item in items])
            lines.append("")
        return lines

    lines = [
        f"# Stage 2 Transcript — v{decision_log_version}",
        "",
        f"Generated: {generated_at}",
        "",
        "_Append prose (the conversation) and decision blocks (the commitments)_",
        "_below the appropriate heading. The --finalize step compiles only the_",
        "_decision blocks; the surrounding prose is preserved as audit trail._",
        "",
    ]
    lines.extend(
        _area_block(
            "Conventions",
            "conventions",
            "Locked conventions: math notation, code style, citation policy, terminology.",
        )
    )
    lines.extend(
        _area_block(
            "Architecture",
            "architecture",
            "Component-level decisions: chosen approach, alternatives rejected, constraints.",
        )
    )
    lines.extend(
        _area_block(
            "Scope (in)",
            "scope-in",
            "What the project is building. Use Section: scope-in.",
        )
    )
    lines.extend(
        _area_block(
            "Scope (out)",
            "scope-out",
            "What is explicitly excluded, with a revisit condition. Use Section: scope-out.",
        )
    )
    lines.extend(
        _area_block(
            "Requirements",
            "requirements",
            (
                "Walk the lens matrix per in-scope item (functional, performance, "
                "reliability, usability, security, maintainability, portability, "
                "constraint, data, interface, business-rule). Phrase each as EARS. "
                "Do not assign REQ-NNN IDs — the compile step handles that."
            ),
        )
    )
    lines.extend(
        _area_block(
            "Open Items",
            "open_items",
            "Unresolved questions deferred to implementation or future work.",
        )
    )
    lines.extend(
        _area_block(
            "Agents Needed",
            "agents_needed",
            "Execution-time agent roles surfaced by the decisions above.",
        )
    )
    return "\n".join(lines)


def _write_precheck_request(
    paths,
    manifest: dict[str, Any],
    decision_log_version: int,
    checks: list[dict[str, Any]],
    override_iterate_reason: str | None,
    generated_at: str,
) -> Path:
    wm = manifest.get("workspace_manifest", {}) if isinstance(manifest, dict) else {}
    wiki = wm.get("wiki", {}) if isinstance(wm, dict) else {}
    payload = {
        "stage2_precheck_request": {
            "generated_at": generated_at,
            "decision_log_version": decision_log_version,
            "wiki_version": wiki.get("version") or "",
            "inputs": {
                "problem_statement": "PROBLEM_STATEMENT.md",
                "wiki_v2": str(paths.wiki_v2_dir.relative_to(paths.root).as_posix()),
                "citation_index": str(
                    paths.citations_index_path.relative_to(paths.root).as_posix()
                ),
                "gap_report": str(
                    (paths.reports_dir / "merged_gap_report.yaml")
                    .relative_to(paths.root)
                    .as_posix()
                ),
                "review_handoff": str(
                    (paths.reviews_dir / "1a2_handoff.yaml")
                    .relative_to(paths.root)
                    .as_posix()
                ),
            },
            "mechanical_checks": checks,
            "override": {
                "iterate_override": override_iterate_reason,
            },
            "verdict_output_path": str(
                paths.stage2_precheck_verdict_path.relative_to(paths.root).as_posix()
            ),
        }
    }
    dump_yaml(paths.stage2_precheck_request_path, payload)
    return paths.stage2_precheck_request_path


def run_elicit_vision_start(
    artifacts_root: Path,
    workspace_root: Path,
    override_iterate_reason: str | None = None,
) -> dict[str, Any]:
    """Stage 2 Step 1 — CLI preflight bookend.

    Writes brief.md, transcript.md skeleton, and precheck_request.yaml into
    `workspace-artifacts/runtime/stage2/`. Raises on mechanical prereq
    failure (the CLI wrapper converts to nonzero exit).
    """
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    manifest = load_manifest(paths)
    if not manifest:
        raise RuntimeError(
            "Manifest not found. Run `meta-compiler meta-init` first."
        )

    checks, blocking = _preflight_checks(
        paths=paths,
        workspace_root=workspace_root,
        override_iterate_reason=override_iterate_reason,
    )

    prior = latest_decision_log_path(paths)
    next_version = (prior[0] + 1) if prior is not None else 1

    generated_at = iso_now()

    # Write the precheck_request artifact regardless of pass/fail so the
    # operator (and the orchestrator agent) can see the evidence. On
    # failure, raise after persisting.
    _write_precheck_request(
        paths=paths,
        manifest=manifest,
        decision_log_version=next_version,
        checks=checks,
        override_iterate_reason=override_iterate_reason,
        generated_at=generated_at,
    )

    if blocking:
        # Surface the failing checks in the error message so the operator can
        # act without hunting through YAML.
        blocking_lines = "\n".join(f"  - {reason}" for reason in blocking)
        raise RuntimeError(
            "Stage 2 preflight blocked. Failing checks:\n"
            f"{blocking_lines}\n"
            f"See {paths.stage2_precheck_request_path.relative_to(paths.root).as_posix()} "
            "for full evidence and remediation hints."
        )

    paths.stage2_brief_path.write_text(
        _render_brief(
            paths=paths,
            manifest=manifest,
            decision_log_version=next_version,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )

    # Don't clobber an existing transcript — Stage 2 is supposed to survive
    # dialog interruptions. Re-running --start updates the brief but keeps
    # whatever the LLM has been writing.
    if not paths.stage2_transcript_path.exists() or paths.stage2_transcript_path.stat().st_size == 0:
        paths.stage2_transcript_path.write_text(
            _render_transcript_skeleton(
                paths=paths,
                decision_log_version=next_version,
                generated_at=generated_at,
            ),
            encoding="utf-8",
        )

    return {
        "status": "ready_for_orchestrator",
        "brief_path": str(paths.stage2_brief_path.relative_to(paths.root).as_posix()),
        "transcript_path": str(
            paths.stage2_transcript_path.relative_to(paths.root).as_posix()
        ),
        "precheck_request_path": str(
            paths.stage2_precheck_request_path.relative_to(paths.root).as_posix()
        ),
        "decision_log_version": next_version,
        "instruction": "Invoke @stage2-orchestrator mode=preflight next.",
    }


# ---------------------------------------------------------------------------
# Stage 2 --finalize
# ---------------------------------------------------------------------------


_USE_CASE_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL
)


def _extract_use_case(transcript_text: str) -> str | None:
    """Optional frontmatter at the top of transcript.md can declare `use_case`.

    If absent, the compile step falls back to a default and flags the
    absence as a minor finding.
    """
    match = _USE_CASE_FRONTMATTER_RE.match(transcript_text)
    if not match:
        return None
    try:
        import yaml  # local import keeps module import cheap
    except ImportError:
        return None
    try:
        data = yaml.safe_load(match.group("body")) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("use_case")
    return str(value).strip() if value else None


def _write_postcheck_request(
    paths,
    compiled: dict[str, Any],
    checks: list[dict[str, Any]],
    generated_at: str,
) -> Path:
    decision_log_version = (
        compiled.get("decision_log", {}).get("meta", {}).get("version", 0)
    )
    payload = {
        "stage2_postcheck_request": {
            "generated_at": generated_at,
            "decision_log_version": decision_log_version,
            "inputs": {
                "transcript": str(
                    paths.stage2_transcript_path.relative_to(paths.root).as_posix()
                ),
                "decision_log": str(
                    (paths.decision_logs_dir / f"decision_log_v{decision_log_version}.yaml")
                    .relative_to(paths.root)
                    .as_posix()
                ),
            },
            "mechanical_checks": checks,
            "verdict_output_path": str(
                paths.stage2_postcheck_verdict_path.relative_to(paths.root).as_posix()
            ),
        }
    }
    dump_yaml(paths.stage2_postcheck_request_path, payload)
    return paths.stage2_postcheck_request_path


def run_elicit_vision_finalize(
    artifacts_root: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    """Stage 2 Step 4 — CLI postflight bookend.

    Parses decision blocks from transcript.md, compiles a Decision Log YAML,
    runs mechanical fidelity checks, and writes postcheck_request.yaml. Any
    parse error or fidelity failure aborts before the compiled YAML is
    persisted so bad output never reaches the decision-logs directory.
    """
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    if not paths.stage2_transcript_path.exists():
        raise RuntimeError(
            "Stage 2 transcript missing. "
            "Run `meta-compiler elicit-vision --start` first."
        )

    # Encourage running preflight first but don't hard-block; the orchestrator
    # verdict file is optional from the CLI's perspective (the prompt enforces
    # it).
    transcript_text = read_text_safe(paths.stage2_transcript_path)
    blocks, parse_errors = parse_decision_blocks(transcript_text)

    if parse_errors:
        error_block = "\n".join(f"  - {err}" for err in parse_errors)
        raise RuntimeError(
            "Transcript parse failed. Fix the flagged blocks and re-run:\n"
            f"{error_block}"
        )

    if not blocks:
        raise RuntimeError(
            "Transcript has no decision blocks. Continue the dialog until at "
            "least one decision has been locked, then re-run --finalize."
        )

    manifest = load_manifest(paths)
    if not manifest:
        raise RuntimeError("Manifest missing. Run `meta-compiler meta-init` first.")
    wm = manifest["workspace_manifest"]

    prior = latest_decision_log_path(paths)
    prior_version = prior[0] if prior is not None else None
    reason_for_revision = (
        f"Revision via prompt-as-conductor Stage 2 dialog" if prior_version else None
    )

    default_use_case = (
        f"Stage 2 dialog v{(prior_version or 0) + 1}"
        if prior_version is None
        else f"Stage 2 re-entry from v{prior_version}"
    )
    use_case = _extract_use_case(transcript_text) or default_use_case

    generated_at = iso_now()

    compiled = compile_decision_log(
        blocks=blocks,
        project_meta={
            "project_name": wm.get("name") or "META-COMPILER Project",
            "project_type": wm.get("project_type") or "algorithm",
        },
        prior_version=prior_version,
        reason_for_revision=reason_for_revision,
        problem_statement_hash=_problem_statement_hash(workspace_root),
        wiki_version=wm.get("wiki", {}).get("version") or "",
        use_case=use_case,
        created_at=generated_at,
    )

    citations_payload = load_yaml(paths.citations_index_path) or {}
    known_citation_ids = set(
        (citations_payload.get("citations") or {}).keys()
    )

    checks = mechanical_fidelity_checks(
        blocks=blocks,
        compiled=compiled,
        known_citation_ids=known_citation_ids,
    )
    failures = [c for c in checks if c["result"] == "FAIL"]

    if failures:
        # Persist the request so the operator has the evidence, then abort.
        _write_postcheck_request(
            paths=paths,
            compiled=compiled,
            checks=checks,
            generated_at=generated_at,
        )
        failure_block = "\n".join(
            f"  - {c['name']}: {c.get('evidence', '')}" for c in failures
        )
        raise RuntimeError(
            "Mechanical fidelity check failed. Compiled Decision Log was NOT "
            "written. Failing checks:\n"
            f"{failure_block}\n"
            f"See {paths.stage2_postcheck_request_path.relative_to(paths.root).as_posix()} "
            "for full evidence."
        )

    new_version = compiled["decision_log"]["meta"]["version"]
    decision_log_path = (
        paths.decision_logs_dir / f"decision_log_v{new_version}.yaml"
    )
    decision_log_path.parent.mkdir(parents=True, exist_ok=True)
    dump_yaml(decision_log_path, compiled)

    decision_logs = wm.setdefault("decision_logs", [])
    entry = {
        "version": new_version,
        "created": compiled["decision_log"]["meta"]["created"],
        "parent_version": compiled["decision_log"]["meta"]["parent_version"],
        "reason_for_revision": compiled["decision_log"]["meta"]["reason_for_revision"],
        "use_case": compiled["decision_log"]["meta"]["use_case"],
        "scaffold_path": None,
    }
    # Upsert on version (re-running --finalize should overwrite, not duplicate).
    decision_logs[:] = [
        row
        for row in decision_logs
        if not (isinstance(row, dict) and row.get("version") == new_version)
    ]
    decision_logs.append(entry)
    wm.setdefault("research", {})["last_completed_stage"] = "2"
    save_manifest(paths, manifest)

    _write_postcheck_request(
        paths=paths,
        compiled=compiled,
        checks=checks,
        generated_at=generated_at,
    )

    # Legacy draft cleanup — the old interactive path wrote to this path.
    legacy_draft = paths.runtime_dir / "decision_log_draft.yaml"
    if legacy_draft.exists():
        try:
            legacy_draft.unlink()
        except OSError:
            pass

    return {
        "status": "compiled",
        "decision_log_path": str(
            decision_log_path.relative_to(paths.root).as_posix()
        ),
        "decision_log_version": new_version,
        "block_count": len(blocks),
        "requirement_count": len(compiled["decision_log"]["requirements"]),
        "postcheck_request_path": str(
            paths.stage2_postcheck_request_path.relative_to(paths.root).as_posix()
        ),
        "instruction": "Invoke @stage2-orchestrator mode=postflight next.",
    }

