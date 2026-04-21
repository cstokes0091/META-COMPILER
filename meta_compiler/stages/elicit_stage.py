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
    "code-architecture",
    "scope-in",
    "scope-out",
    "requirements",
    "open_items",
    "agents_needed",
}

VALID_CONVENTION_DOMAINS = {"math", "code", "citation", "terminology"}
VALID_REQUIREMENT_SOURCES = {"user", "derived"}
VALID_DEFER_TARGETS = {"implementation", "future_work"}
VALID_AGENT_MODALITIES = {"document", "code"}
VALID_CODE_ARCH_ASPECTS = {
    "language",
    "libraries",
    "module_layout",
    "build_tooling",
    "runtime",
}
CODE_ARCH_PROJECT_TYPES = {"algorithm", "hybrid"}

_COMMA_LIST_FIELDS = {
    "citations",
    "constraints_applied",
    "key_constraints",
}

# Sublist fields use the indented `  - <name>: <value>` grammar (same as
# alternatives_rejected). The mapped string is the dict key the value goes
# under, e.g. inputs become [{"name": ..., "modality": ...}].
_SUBLIST_FIELDS: dict[str, str] = {
    "inputs": "modality",
    "outputs": "modality",
    "libraries": "description",
}

_NONE_SENTINELS = {"(none)", "none", "-"}

# Per-section required fields. "Section", "rationale", "citations" are
# required for every block and validated separately.
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "conventions": ("domain", "choice"),
    "architecture": ("component", "approach", "constraints_applied"),
    "code-architecture": ("aspect", "choice"),
    "scope-in": ("item",),
    "scope-out": ("item", "revisit_if"),
    "requirements": ("source", "description", "verification", "lens"),
    "open_items": ("description", "deferred_to", "owner"),
    "agents_needed": ("role", "responsibility", "inputs", "outputs", "key_constraints"),
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
_PROBE_LINE_RE = re.compile(r"^\s*-\s*Probe\s*:", re.IGNORECASE)

PROBE_COVERAGE_FLOOR = 4


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

        if sub_match and current_field in _SUBLIST_FIELDS:
            entry_name = sub_match.group(1).strip()
            entry_value = sub_match.group(2).strip()
            value_key = _SUBLIST_FIELDS[current_field]
            if not entry_name:
                raise DecisionBlockParseError(
                    f"line {source_line + offset}: '{current_field}' entry missing a name"
                )
            if not entry_value:
                raise DecisionBlockParseError(
                    f"line {source_line + offset}: '{current_field}' entry "
                    f"'{entry_name}' missing {value_key}"
                )
            bucket = fields.setdefault(current_field, [])
            if not isinstance(bucket, list):
                raise DecisionBlockParseError(
                    f"line {source_line + offset}: '{current_field}' must be a sublist"
                )
            bucket.append({"name": entry_name, value_key: entry_value})
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

            if label in _SUBLIST_FIELDS:
                # Sublist body is collected on subsequent indented lines. The
                # label line itself never carries an inline value — if one is
                # present, that's a malformed block.
                fields[label] = []
                if value:
                    raise DecisionBlockParseError(
                        f"line {source_line + offset}: '{label}' takes its body as an "
                        f"indented sublist of '  - <name>: <{_SUBLIST_FIELDS[label]}>' "
                        "lines, not an inline value"
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
        # required string fields, empty is an error. Sublist fields like
        # Inputs/Outputs must have at least one entry.
        if isinstance(value, str) and not value.strip():
            raise DecisionBlockParseError(
                f"line {source_line}: decision block '{name}' field '{required}' is empty"
            )
        if required in _SUBLIST_FIELDS and isinstance(value, list) and not value:
            raise DecisionBlockParseError(
                f"line {source_line}: decision block '{name}' field '{required}' "
                f"requires at least one '  - <name>: <{_SUBLIST_FIELDS[required]}>' entry"
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
    elif section == "agents_needed":
        for io_field in ("inputs", "outputs"):
            for entry in fields.get(io_field, []):
                modality = entry.get("modality") if isinstance(entry, dict) else None
                if modality not in VALID_AGENT_MODALITIES:
                    raise DecisionBlockParseError(
                        f"line {source_line}: decision block '{name}' {io_field.capitalize()} "
                        f"entry '{entry.get('name', '<unknown>') if isinstance(entry, dict) else entry}' "
                        f"modality '{modality}' must be one of {sorted(VALID_AGENT_MODALITIES)}"
                    )
    elif section == "code-architecture":
        aspect = fields.get("aspect", "")
        if aspect not in VALID_CODE_ARCH_ASPECTS:
            raise DecisionBlockParseError(
                f"line {source_line}: decision block '{name}' Aspect '{aspect}' must be one of "
                f"{sorted(VALID_CODE_ARCH_ASPECTS)}"
            )
        if aspect == "libraries":
            libraries = fields.get("libraries")
            if not isinstance(libraries, list) or not libraries:
                raise DecisionBlockParseError(
                    f"line {source_line}: decision block '{name}' Aspect=libraries requires a "
                    "non-empty 'Libraries:' sublist with '  - <name>: <description>' entries"
                )
        if aspect == "module_layout":
            layout = fields.get("module_layout")
            if not isinstance(layout, str) or not layout.strip():
                raise DecisionBlockParseError(
                    f"line {source_line}: decision block '{name}' Aspect=module_layout requires "
                    "a non-empty 'Module layout:' field"
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


def count_probes_per_block(transcript_text: str) -> list[dict[str, Any]]:
    """For each `### Decision:` block in the transcript, count `- Probe:`
    annotation lines in the prose between the previous block heading (or area
    heading) and this block's heading.

    Probes attribute to the *next* decision block — that is, probes walked
    before writing the block. Returns a list ordered by transcript position,
    each entry: ``{"block_name", "probe_count", "source_line"}``.
    """
    if not transcript_text.strip():
        return []
    lines = transcript_text.splitlines()
    results: list[dict[str, Any]] = []
    probes_buffer = 0
    for idx, line in enumerate(lines):
        block_match = _HEADING_BLOCK_RE.match(line)
        if block_match:
            results.append(
                {
                    "block_name": block_match.group(1).strip(),
                    "probe_count": probes_buffer,
                    "source_line": idx + 1,
                }
            )
            probes_buffer = 0
            continue
        if _HEADING_AREA_RE.match(line) and not line.startswith("### "):
            probes_buffer = 0
            continue
        if _PROBE_LINE_RE.match(line):
            probes_buffer += 1
    return results


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


def _compile_code_architecture(blocks: list[DecisionBlock]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for block in blocks:
        entry: dict[str, Any] = {
            "aspect": block.fields["aspect"],
            "choice": block.fields["choice"],
            "alternatives_rejected": [dict(alt) for alt in block.alternatives_rejected],
            "constraints_applied": list(block.fields.get("constraints_applied", [])),
            "citations": list(block.citations),
            "rationale": block.rationale,
        }
        libraries = block.fields.get("libraries")
        if isinstance(libraries, list) and libraries:
            entry["libraries"] = [dict(lib) for lib in libraries]
        module_layout = block.fields.get("module_layout")
        if isinstance(module_layout, str) and module_layout.strip():
            entry["module_layout"] = module_layout
        entries.append(entry)
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
                "inputs": [dict(item) for item in block.fields.get("inputs", [])],
                "outputs": [dict(item) for item in block.fields.get("outputs", [])],
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

    project_type = project_meta.get("project_type", "algorithm")

    decision_log: dict[str, Any] = {
        "meta": {
            "project_name": project_meta.get("project_name", "META-COMPILER Project"),
            "project_type": project_type,
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
    if project_type in CODE_ARCH_PROJECT_TYPES:
        decision_log["code_architecture"] = _compile_code_architecture(
            by_section["code-architecture"]
        )
    elif by_section["code-architecture"]:
        # report projects must not produce code-architecture decisions; surface
        # this as a parse-time invariant violation by raising rather than
        # silently dropping the blocks.
        raise DecisionBlockParseError(
            "decision_log: code-architecture decision blocks are not permitted for "
            "project_type=report"
        )

    return {"decision_log": decision_log}


def mechanical_fidelity_checks(
    blocks: list[DecisionBlock],
    compiled: dict[str, Any],
    known_citation_ids: set[str],
    transcript_text: str | None = None,
) -> list[dict[str, Any]]:
    """Run mechanical fidelity checks described in spec §5.2 and §8.3.

    Returns a list of check results. Each check is a dict with
    `name`, `result` (PASS|FAIL|WARN), `evidence`, and (on non-PASS)
    `remediation`. The caller aggregates to produce a non-zero CLI exit
    if any check returns FAIL. WARN signals an anti-shallow heuristic
    finding that the orchestrator semantic audit must judge.
    """
    checks: list[dict[str, Any]] = []
    root = compiled.get("decision_log", {})

    entry_count = (
        len(root.get("conventions", []))
        + len(root.get("architecture", []))
        + len(root.get("code_architecture", []) or [])
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

    if transcript_text is not None:
        per_block = count_probes_per_block(transcript_text)
        shallow = [
            row for row in per_block if row["probe_count"] < PROBE_COVERAGE_FLOOR
        ]
        checks.append(
            {
                "name": "probe_coverage",
                "result": "PASS" if not shallow else "WARN",
                "evidence": (
                    f"all {len(per_block)} blocks meet the {PROBE_COVERAGE_FLOOR}-probe floor"
                    if not shallow
                    else "; ".join(
                        f"{row['block_name']}@L{row['source_line']}={row['probe_count']}"
                        for row in shallow[:8]
                    )
                ),
                "remediation": (
                    f"Walk at least {PROBE_COVERAGE_FLOOR} probes from "
                    ".github/docs/stage-2-probes.md per decision block; annotate each "
                    "with `- Probe: <name> — <how addressed>` in the prose above the block."
                    if shallow
                    else ""
                ),
                "details": per_block,
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
- Section: <conventions | architecture | code-architecture | scope-in | scope-out | requirements | open_items | agents_needed>
- <section-specific required fields — see below>
- Rationale: <why, natural language>
- Citations: src-..., src-...   (use '(none)' if no citations apply)

Section-specific required fields:

- conventions: Domain (math|code|citation|terminology), Choice
- architecture: Component, Approach, Constraints applied. Alternatives rejected
  is optional but strongly preferred — write as an indented sublist of
  '  - <name>: <reason>'.
- code-architecture (algorithm/hybrid only; forbidden for report): Aspect
  (language|libraries|module_layout|build_tooling|runtime), Choice. When
  Aspect=libraries, also include a 'Libraries:' indented sublist of
  '  - <name>: <description>' entries (description should encode version
  pin and purpose, e.g. 'numpy: PSF math (>=1.26)'). When
  Aspect=module_layout, also include a 'Module layout:' line describing
  the package layout. Alternatives rejected and Constraints applied are
  optional but strongly preferred.
- scope-in: Item
- scope-out: Item, Revisit if
- requirements: Source (user|derived), Description (EARS-phrased),
  Verification, Lens (functional|performance|reliability|usability|security|
  maintainability|portability|constraint|data|interface|business-rule).
  Do not assign REQ-NNN IDs yourself — the --finalize step assigns them.
- open_items: Description, Deferred to (implementation|future_work), Owner
- agents_needed: Role, Responsibility, Inputs, Outputs, Key constraints.
  Inputs and Outputs are indented sublists where every entry is tagged
  with modality (document|code):
      - Inputs:
        - decision_log: document
      - Outputs:
        - scaffold: code
        - agents: document
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
        "code-architecture": [],
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
    project_type: str,
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
        f"Project type: {project_type}",
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

    gap_section_labels: dict[str, str] = {
        "conventions": "Conventions",
        "architecture": "Architecture",
    }
    if project_type in CODE_ARCH_PROJECT_TYPES:
        gap_section_labels["code-architecture"] = "Code Architecture"
    gap_section_labels.update(
        {
            "scope-in": "Scope (in)",
            "scope-out": "Scope (out)",
            "requirements": "Requirements",
            "open_items": "Open Items",
            "agents_needed": "Agents Needed",
        }
    )

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
    project_type: str,
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
        f"Project type: {project_type}",
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
            "Logical component-level decisions: chosen approach, alternatives rejected, constraints.",
        )
    )
    if project_type in CODE_ARCH_PROJECT_TYPES:
        lines.extend(
            _area_block(
                "Code Architecture",
                "code-architecture",
                (
                    "How the logical architecture is realized in code. Walk every aspect: "
                    "language (Aspect: language), library selection with version pins and "
                    "purpose (Aspect: libraries with a 'Libraries:' sublist), module/package "
                    "layout (Aspect: module_layout with a 'Module layout:' field), build and "
                    "test tooling (Aspect: build_tooling), and runtime/deploy target (Aspect: "
                    "runtime). At least one 'language' block AND one 'libraries' block are "
                    "required for algorithm/hybrid projects."
                ),
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
            (
                "Execution-time agent roles. Every block must declare typed Inputs and "
                "Outputs (modality: document|code) so Stage 3 knows what kind of artifact "
                "each agent consumes and produces."
                + (
                    " For report projects, every output modality must be 'document'."
                    if project_type == "report"
                    else ""
                )
            ),
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

    project_type = (
        manifest.get("workspace_manifest", {}).get("project_type") or "algorithm"
    )

    paths.stage2_brief_path.write_text(
        _render_brief(
            paths=paths,
            manifest=manifest,
            decision_log_version=next_version,
            generated_at=generated_at,
            project_type=project_type,
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
                project_type=project_type,
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


def _block_title_for_section(block: Any, revised_section: str) -> str:
    """Return the string used to test "fresh vs prior" for a block in a
    revised section. Test-only dummy objects may expose a plain `.title`
    attribute; real `DecisionBlock` objects expose `.name` plus typed
    fields keyed by section.
    """
    direct = getattr(block, "title", None)
    if direct:
        return str(direct)
    fields = getattr(block, "fields", {}) or {}
    if revised_section == "conventions":
        return str(getattr(block, "name", "") or fields.get("choice", ""))
    if revised_section == "architecture":
        return str(fields.get("component", "") or getattr(block, "name", ""))
    if revised_section == "scope":
        return str(fields.get("item", "") or getattr(block, "name", ""))
    if revised_section == "requirements":
        return str(fields.get("description", "") or getattr(block, "name", ""))
    if revised_section == "open_items":
        return str(fields.get("description", "") or getattr(block, "name", ""))
    if revised_section == "agents_needed":
        return str(fields.get("role", "") or getattr(block, "name", ""))
    return str(getattr(block, "name", ""))


def _check_reentry_block_freshness(
    transcript_blocks: list,
    cascade_report: dict,
    parent_log: dict,
) -> list[str]:
    """For each revised section in the cascade report, require >=1 block
    with a title that does NOT appear in the parent Decision Log.

    Returns list of issue strings, one per empty revised section. Empty
    list means pass. Called only when re-entry is detected.
    """
    revised = set(
        (cascade_report.get("cascade_report") or {}).get("revised_sections") or []
    )
    if not revised:
        return []

    dl = parent_log.get("decision_log") or {}
    issues: list[str] = []

    def _titles_for_revised(section: str) -> set[str]:
        titles: set[str] = set()
        if section == "conventions":
            for row in dl.get("conventions") or []:
                titles.add(str(row.get("name") or row.get("choice") or ""))
        elif section == "architecture":
            for row in dl.get("architecture") or []:
                titles.add(str(row.get("component") or ""))
        elif section == "scope":
            scope = dl.get("scope") or {}
            for row in (scope.get("in_scope") or []) + (scope.get("out_of_scope") or []):
                titles.add(str(row.get("item") or ""))
        elif section == "requirements":
            for row in dl.get("requirements") or []:
                titles.add(str(row.get("id") or row.get("description") or ""))
        elif section == "open_items":
            for row in dl.get("open_items") or []:
                titles.add(str(row.get("description") or ""))
        elif section == "agents_needed":
            for row in dl.get("agents_needed") or []:
                titles.add(str(row.get("role") or ""))
        return {t for t in titles if t}

    def _block_matches_revised(block_section: str, revised_name: str) -> bool:
        if revised_name == "scope":
            return block_section in {"scope-in", "scope-out"}
        return block_section == revised_name

    for section in sorted(revised):
        prior_titles = _titles_for_revised(section)
        has_fresh = False
        for b in transcript_blocks:
            b_section = getattr(b, "section", "") or ""
            if not _block_matches_revised(b_section, section):
                continue
            b_title = _block_title_for_section(b, section)
            if b_title and b_title not in prior_titles:
                has_fresh = True
                break
        if not has_fresh:
            issues.append(
                f"Revised section '{section}' has no fresh decision block in the transcript. "
                f"Add at least one decision block under that section whose title differs from the prior log."
            )
    return issues


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
    research = wm.get("research") or {}
    is_reentry = research.get("last_completed_stage") == "2-reentry-seeded"

    prior = latest_decision_log_path(paths)
    prior_version = prior[0] if prior is not None else None
    reason_for_revision = (
        f"Revision via prompt-as-conductor Stage 2 dialog" if prior_version else None
    )

    # Re-entry mode: require at least one fresh decision block per revised
    # section. The cascade report was emitted by stage2-reentry when the
    # transcript was seeded.
    if is_reentry:
        if prior is None:
            raise RuntimeError(
                "Re-entry state detected but no parent Decision Log exists."
            )
        _, parent_path = prior
        parent_log = load_yaml(parent_path) or {}
        reentry_version = research.get("reentry_version")
        cascade_path = (
            paths.stage2_runtime_dir / f"cascade_report_v{reentry_version}.yaml"
        )
        cascade_report = load_yaml(cascade_path) if cascade_path.exists() else {}

        freshness_issues = _check_reentry_block_freshness(
            transcript_blocks=blocks,
            cascade_report=cascade_report,
            parent_log=parent_log,
        )
        if freshness_issues:
            raise RuntimeError(
                "Re-entry block-freshness check failed:\n"
                + "\n".join(f"  - {issue}" for issue in freshness_issues)
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
        transcript_text=transcript_text,
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
    research_out = wm.setdefault("research", {})
    research_out["last_completed_stage"] = "2"
    if is_reentry:
        research_out.pop("reentry_version", None)
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

