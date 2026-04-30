from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .artifacts import ArtifactPaths, latest_decision_log_path, load_manifest
from .findings_loader import trigger_content_tokens
from .io import load_yaml, parse_frontmatter
from .project_types import (
    CODE_ARCH_REQUIRED_PROJECT_TYPES,
    VALID_PROJECT_TYPES,
    WORKFLOW_CONFIG_REQUIRED_PROJECT_TYPES,
)
from .stages.ingest_stage import validate_all_findings
from .utils import read_text_safe


VALID_STATUS = {"initialized", "researched", "scaffolded", "active"}
VALID_REVIEW_VERDICTS = {"PROCEED", "ITERATE"}
VALID_CONVENTION_DOMAINS = {"math", "code", "citation", "terminology"}
VALID_AGENT_MODALITIES = {"document", "code"}
VALID_CODE_ARCH_ASPECTS = {
    "language",
    "libraries",
    "module_layout",
    "build_tooling",
    "runtime",
}
VALID_AUTHOR_ROLES = {"external", "user_authored"}
VALID_CONSTRAINT_KINDS = {
    "tooling",
    "regulatory",
    "performance_target",
    "infrastructure",
    "resource",
    "timeline",
}
VALID_WORKFLOW_TRIGGERS = {"inbox_watch", "manual", "webhook", "schedule"}
VALID_WORKFLOW_IO_KINDS = {
    "tracked_doc",
    "comment_thread",
    "data_table",
    "tracked_edit",
    "comment_reply",
    "summary",
}
REQUIRED_PROBLEM_STATEMENT_SECTIONS = [
    "## Domain and Problem Space",
    "## Goals and Success Criteria",
    "## Constraints",
    "## Project Type",
    "## Additional Context",
]


def _require_fields(
    payload: dict[str, Any],
    fields: list[str],
    prefix: str,
    issues: list[str],
) -> None:
    for field in fields:
        if field not in payload:
            issues.append(f"{prefix}: missing required field '{field}'")


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    wm = manifest.get("workspace_manifest")
    if not isinstance(wm, dict):
        return ["workspace_manifest: missing root object"]

    _require_fields(
        wm,
        [
            "name",
            "created",
            "last_modified",
            "problem_domain",
            "project_type",
            "seeds",
            "wiki",
            "decision_logs",
            "status",
        ],
        "workspace_manifest",
        issues,
    )

    project_type = wm.get("project_type")
    if project_type not in VALID_PROJECT_TYPES:
        issues.append(
            f"workspace_manifest.project_type: must be one of {sorted(VALID_PROJECT_TYPES)}"
        )

    status = wm.get("status")
    if status not in VALID_STATUS:
        issues.append("workspace_manifest.status: must be initialized|researched|scaffolded|active")

    seeds = wm.get("seeds", {})
    if isinstance(seeds, dict):
        _require_fields(
            seeds,
            ["version", "last_updated", "document_count"],
            "workspace_manifest.seeds",
            issues,
        )
    else:
        issues.append("workspace_manifest.seeds: must be an object")

    wiki = wm.get("wiki", {})
    if isinstance(wiki, dict):
        _require_fields(
            wiki,
            ["version", "last_updated", "page_count"],
            "workspace_manifest.wiki",
            issues,
        )
    else:
        issues.append("workspace_manifest.wiki: must be an object")

    decision_logs = wm.get("decision_logs", [])
    if not isinstance(decision_logs, list):
        issues.append("workspace_manifest.decision_logs: must be a list")

    wiki = wm.get("wiki", {})
    if isinstance(wiki, dict) and "name" in wiki and not isinstance(wiki.get("name"), str):
        issues.append("workspace_manifest.wiki.name: must be a string when present")

    executions = wm.get("executions")
    if executions is not None and not isinstance(executions, list):
        issues.append("workspace_manifest.executions: must be a list")
    elif isinstance(executions, list):
        for idx, row in enumerate(executions):
            if not isinstance(row, dict):
                issues.append(f"workspace_manifest.executions[{idx}]: must be an object")
                continue
            _require_fields(
                row,
                ["version", "created", "output_dir"],
                f"workspace_manifest.executions[{idx}]",
                issues,
            )

    pitches = wm.get("pitches")
    if pitches is not None and not isinstance(pitches, list):
        issues.append("workspace_manifest.pitches: must be a list")
    elif isinstance(pitches, list):
        for idx, row in enumerate(pitches):
            if not isinstance(row, dict):
                issues.append(f"workspace_manifest.pitches[{idx}]: must be an object")
                continue
            _require_fields(
                row,
                ["version", "created", "pptx_path"],
                f"workspace_manifest.pitches[{idx}]",
                issues,
            )

    return issues


def validate_problem_statement(problem_statement_path: Path) -> list[str]:
    issues: list[str] = []
    if not problem_statement_path.exists():
        return [f"problem statement missing: {problem_statement_path.name}"]

    text = read_text_safe(problem_statement_path).strip()
    if not text:
        return ["problem statement is empty"]

    for section in REQUIRED_PROBLEM_STATEMENT_SECTIONS:
        if section not in text:
            issues.append(f"problem statement missing section '{section}'")

    template_markers = [
        "Define the measurable outcomes that indicate project success.",
        "List technical constraints, timeline constraints, and resource constraints.",
        "Capture assumptions, prior work references, and any known risks.",
    ]
    if any(marker in text for marker in template_markers):
        issues.append("problem statement still contains unedited template guidance")

    return issues


def validate_citation_index(index_payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    citations = index_payload.get("citations")
    if not isinstance(citations, dict):
        return ["citations.index: missing citations object"]

    for citation_id, citation in citations.items():
        if not isinstance(citation, dict):
            issues.append(f"citations.index.{citation_id}: must be an object")
            continue

        _require_fields(
            citation,
            ["human", "source", "metadata", "status"],
            f"citations.index.{citation_id}",
            issues,
        )

        source = citation.get("source", {})
        if isinstance(source, dict):
            _require_fields(
                source,
                ["type", "path"],
                f"citations.index.{citation_id}.source",
                issues,
            )
        else:
            issues.append(f"citations.index.{citation_id}.source: must be an object")

    return issues


def validate_findings_index(index_payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    root = index_payload.get("findings_index")
    if not isinstance(root, dict):
        return ["findings index: missing findings_index root"]

    _require_fields(
        root,
        ["version", "last_updated", "processed_seeds"],
        "findings_index",
        issues,
    )
    processed = root.get("processed_seeds")
    if not isinstance(processed, list):
        issues.append("findings_index.processed_seeds: must be a list")
        return issues

    for idx, row in enumerate(processed):
        if not isinstance(row, dict):
            issues.append(f"findings_index.processed_seeds[{idx}]: must be an object")
            continue
        _require_fields(
            row,
            ["citation_id", "file_hash", "seed_path", "findings_path", "extracted_at", "completeness"],
            f"findings_index.processed_seeds[{idx}]",
            issues,
        )
        completeness = row.get("completeness")
        if completeness not in {"full", "partial"}:
            issues.append(
                f"findings_index.processed_seeds[{idx}].completeness: must be full|partial"
            )

    return issues


def validate_wiki_page(markdown_path: Path) -> list[str]:
    issues: list[str] = []
    text = read_text_safe(markdown_path)
    frontmatter, body = parse_frontmatter(text)

    if str(frontmatter.get("type") or "") == "alias":
        # Alias redirect stubs follow a narrower schema.
        return validate_alias_page(markdown_path)

    required_frontmatter = ["id", "type", "created", "sources", "related", "status"]
    if not frontmatter:
        issues.append(f"{markdown_path.name}: missing frontmatter")
    else:
        _require_fields(frontmatter, required_frontmatter, markdown_path.name, issues)

    required_sections = [
        "## Definition",
        "## Key Claims",
        "## Relationships",
        "## Open Questions",
        "## Source Notes",
    ]
    for section in required_sections:
        if section not in body:
            issues.append(f"{markdown_path.name}: missing section '{section}'")

    return issues


def validate_alias_page(markdown_path: Path) -> list[str]:
    """Alias redirect stubs: must point at an existing canonical page and
    carry only the minimal frontmatter + a short body."""
    issues: list[str] = []
    text = read_text_safe(markdown_path)
    frontmatter, body = parse_frontmatter(text)
    name = markdown_path.name

    if not frontmatter:
        return [f"{name}: alias page missing frontmatter"]

    required = ["id", "type", "canonical", "created", "sources", "related", "status"]
    _require_fields(frontmatter, required, name, issues)

    if str(frontmatter.get("type") or "") != "alias":
        issues.append(f"{name}: alias page type must be 'alias'")

    canonical = str(frontmatter.get("canonical") or "").strip()
    if canonical:
        target = markdown_path.parent / f"{canonical}.md"
        if not target.exists():
            issues.append(
                f"{name}: canonical target '{canonical}' does not exist at {target.name}"
            )

    body_lines = [line for line in body.strip().splitlines() if line.strip()]
    # Permit: title, Definition heading, one-line redirect body. Anything
    # longer than ~6 non-empty lines likely means the stub wasn't actually
    # rewritten.
    if len(body_lines) > 8:
        issues.append(f"{name}: alias page body is longer than expected")

    return issues


def validate_concept_reconciliation_return(
    payload: dict[str, Any],
    *,
    bucket_key: str,
    expected_citation_ids: set[str],
) -> list[str]:
    """Validate one concept-reconciler subagent's JSON return.

    The orchestrator persists each subagent's return to
    `runtime/wiki_reconcile/subagent_returns/{bucket_key}.json`. Before the
    apply CLI synthesizes a proposal from those returns, each return is
    checked to ensure:

    * Required top-level keys (`alias_groups`, `distinct_concepts`) exist.
    * Every `members[*].source_citation_id` is in `expected_citation_ids`
      (i.e. came from the bucket the orchestrator handed the subagent).
    * Every member carries `name`, `evidence_locator` (object), and a
      non-empty `definition_excerpt`.

    Returns a list of issue strings prefixed with the bucket key. Empty
    list means the return is well-formed.
    """
    issues: list[str] = []
    prefix = f"concept_reconciliation_return[{bucket_key}]"

    if not isinstance(payload, dict):
        return [f"{prefix}: must be an object"]

    alias_groups = payload.get("alias_groups")
    if alias_groups is None:
        issues.append(f"{prefix}.alias_groups: missing (use [] when no merges)")
    elif not isinstance(alias_groups, list):
        issues.append(f"{prefix}.alias_groups: must be a list")
        alias_groups = []

    distinct_concepts = payload.get("distinct_concepts")
    if distinct_concepts is None:
        issues.append(f"{prefix}.distinct_concepts: missing (use [] when none)")
    elif not isinstance(distinct_concepts, list):
        issues.append(f"{prefix}.distinct_concepts: must be a list")

    for idx, group in enumerate(alias_groups or []):
        gp = f"{prefix}.alias_groups[{idx}]"
        if not isinstance(group, dict):
            issues.append(f"{gp}: must be an object")
            continue
        canonical = group.get("canonical_name")
        if not isinstance(canonical, str) or not canonical.strip():
            issues.append(f"{gp}.canonical_name: must be a non-empty string")
        members = group.get("members")
        if not isinstance(members, list) or len(members) < 2:
            issues.append(f"{gp}.members: must be a list with >=2 entries")
            members = members if isinstance(members, list) else []
        seen_citations: set[str] = set()
        for m_idx, member in enumerate(members):
            mp = f"{gp}.members[{m_idx}]"
            if not isinstance(member, dict):
                issues.append(f"{mp}: must be an object")
                continue
            name = member.get("name")
            if not isinstance(name, str) or not name.strip():
                issues.append(f"{mp}.name: must be a non-empty string")
            citation = member.get("source_citation_id")
            if not isinstance(citation, str) or not citation.strip():
                issues.append(f"{mp}.source_citation_id: must be a non-empty string")
            elif expected_citation_ids and citation not in expected_citation_ids:
                issues.append(
                    f"{mp}.source_citation_id: {citation!r} not in bucket "
                    f"source_citation_ids"
                )
            else:
                seen_citations.add(citation)
            locator = member.get("evidence_locator")
            if not isinstance(locator, dict):
                issues.append(f"{mp}.evidence_locator: must be an object")
            excerpt = member.get("definition_excerpt")
            if not isinstance(excerpt, str) or not excerpt.strip():
                issues.append(f"{mp}.definition_excerpt: must be a non-empty string")
        if len(seen_citations) < 2 and members:
            issues.append(
                f"{gp}: alias group spans only {len(seen_citations)} citation(s) — "
                "demote to distinct_concepts (require >=2 distinct sources)"
            )

    for idx, entry in enumerate(distinct_concepts or []):
        dp = f"{prefix}.distinct_concepts[{idx}]"
        if not isinstance(entry, dict):
            issues.append(f"{dp}: must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            issues.append(f"{dp}.name: must be a non-empty string")
        citation = entry.get("source_citation_id")
        if not isinstance(citation, str) or not citation.strip():
            issues.append(f"{dp}.source_citation_id: must be a non-empty string")
        elif expected_citation_ids and citation not in expected_citation_ids:
            issues.append(
                f"{dp}.source_citation_id: {citation!r} not in bucket "
                f"source_citation_ids"
            )

    return issues


def validate_cross_source_synthesis_return(
    payload: dict[str, Any],
    *,
    page_id: str,
    expected_citation_ids: set[str],
) -> list[str]:
    """Validate one cross-source-synthesizer subagent's JSON return.

    The orchestrator persists each subagent's return to
    `runtime/wiki_cross_source/subagent_returns/{page_id}.json`. Each return
    must satisfy:

    * Non-empty `definition`, `key_claims`, `open_questions` strings.
    * `citations_used` is a list with >=2 entries, all in
      `expected_citation_ids`.
    * The `definition` text references >=2 distinct citation IDs from
      `expected_citation_ids` (so the prose actually surfaces multi-source
      reconciliation, not a single-source paraphrase).
    * `inter_source_divergences` is a list (may be empty).
    """
    issues: list[str] = []
    prefix = f"cross_source_synthesis_return[{page_id}]"

    if not isinstance(payload, dict):
        return [f"{prefix}: must be an object"]

    for field in ("definition", "key_claims", "open_questions"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{prefix}.{field}: must be a non-empty string")

    citations_used = payload.get("citations_used")
    if not isinstance(citations_used, list):
        issues.append(f"{prefix}.citations_used: must be a list")
        citations_used = []
    else:
        if len(citations_used) < 2:
            issues.append(
                f"{prefix}.citations_used: must list >=2 sources "
                "(this pass exists to surface cross-source divergence)"
            )
        for idx, cid in enumerate(citations_used):
            if not isinstance(cid, str) or not cid.strip():
                issues.append(f"{prefix}.citations_used[{idx}]: must be a non-empty string")
                continue
            if expected_citation_ids and cid not in expected_citation_ids:
                issues.append(
                    f"{prefix}.citations_used[{idx}]: {cid!r} not in page "
                    "source_citation_ids"
                )

    definition = payload.get("definition") or ""
    if isinstance(definition, str) and expected_citation_ids:
        mentioned = {cid for cid in expected_citation_ids if cid in definition}
        if len(mentioned) < 2:
            issues.append(
                f"{prefix}.definition: must reference >=2 distinct citations inline "
                f"(found {sorted(mentioned)})"
            )

    divergences = payload.get("inter_source_divergences")
    if divergences is None:
        # Treat absence as empty list — explicit unanimous agreement.
        pass
    elif not isinstance(divergences, list):
        issues.append(f"{prefix}.inter_source_divergences: must be a list when present")
    else:
        for idx, row in enumerate(divergences):
            dp = f"{prefix}.inter_source_divergences[{idx}]"
            if not isinstance(row, dict):
                issues.append(f"{dp}: must be an object")
                continue
            for field in ("topic", "summary"):
                if not isinstance(row.get(field), str) or not row.get(field).strip():
                    issues.append(f"{dp}.{field}: must be a non-empty string")
            sources = row.get("sources")
            if not isinstance(sources, list) or not sources:
                issues.append(f"{dp}.sources: must be a non-empty list")

    return issues


# ---------------------------------------------------------------------------
# Stage 4 final-synthesis validators
# ---------------------------------------------------------------------------


_PACKAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_KEBAB_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_ENTRY_POINT_TARGET_RE = re.compile(r"^[a-z][a-z0-9_.]*:[a-zA-Z_][a-zA-Z0-9_]*$")
_REQ_RE_VALIDATION = re.compile(r"REQ-\d{3}")
_FRAGMENT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]+:[^\s]+$")


# Reserved Python identifiers that must never be used as a synthesized
# package name. Includes a representative subset of the standard library so
# the LLM cannot accidentally shadow stdlib imports, plus the project's own
# package name to prevent self-collision.
_RESERVED_PACKAGE_NAMES: frozenset[str] = frozenset({
    "abc", "argparse", "ast", "asyncio", "base64", "bisect", "calendar",
    "collections", "concurrent", "configparser", "contextlib", "copy",
    "csv", "ctypes", "datetime", "decimal", "difflib", "dis", "email",
    "enum", "errno", "fractions", "functools", "gc", "getpass", "glob",
    "gzip", "hashlib", "heapq", "hmac", "html", "http", "importlib",
    "inspect", "io", "ipaddress", "itertools", "json", "logging", "math",
    "multiprocessing", "operator", "os", "pathlib", "pickle", "pkgutil",
    "platform", "pprint", "queue", "random", "re", "secrets", "select",
    "shelve", "shutil", "signal", "site", "smtplib", "socket", "sqlite3",
    "ssl", "stat", "statistics", "string", "struct", "subprocess", "sys",
    "tempfile", "test", "tests", "textwrap", "threading", "time", "timeit",
    "tkinter", "token", "traceback", "types", "typing", "unicodedata",
    "unittest", "urllib", "uuid", "warnings", "weakref", "xml", "yaml",
    "zipfile", "zlib",
    "meta_compiler",
})

# Required README sections per synthesis modality. Subagents may add more
# sections; these are the floor.
_LIBRARY_README_REQUIRED_SECTIONS: tuple[str, ...] = (
    "Overview", "Installation", "Usage", "Capabilities",
)
_APPLICATION_README_REQUIRED_SECTIONS: tuple[str, ...] = (
    "Overview", "Run", "Configuration",
)


def _check_deduplication_audit(
    issues: list[str],
    prefix: str,
    payload: dict[str, Any],
    expected_fragments: set[str],
    accounted_for: set[str],
) -> None:
    """Verify every expected fragment is either accounted for in the layout
    or explicitly listed in `deduplications_applied[].dropped`.

    Mutates `issues`. Both `expected_fragments` and `accounted_for` are sets
    of `"<capability_id>:<relative_path>"` tokens.
    """
    deduplications = payload.get("deduplications_applied") or []
    if not isinstance(deduplications, list):
        issues.append(f"{prefix}.deduplications_applied: must be a list when present")
        deduplications = []

    dropped: set[str] = set()
    for idx, entry in enumerate(deduplications):
        ep = f"{prefix}.deduplications_applied[{idx}]"
        if not isinstance(entry, dict):
            issues.append(f"{ep}: must be an object")
            continue
        kept = entry.get("kept")
        if not isinstance(kept, str) or not _FRAGMENT_TOKEN_RE.match(kept):
            issues.append(
                f"{ep}.kept: must match '<capability>:<relative_path>'"
            )
        dropped_list = entry.get("dropped") or []
        if not isinstance(dropped_list, list) or not dropped_list:
            issues.append(f"{ep}.dropped: must be a non-empty list")
            dropped_list = []
        for jdx, token in enumerate(dropped_list):
            if not isinstance(token, str) or not _FRAGMENT_TOKEN_RE.match(token):
                issues.append(
                    f"{ep}.dropped[{jdx}]: must match '<capability>:<relative_path>'"
                )
                continue
            dropped.add(token)
        if not isinstance(entry.get("reason"), str) or not entry.get("reason").strip():
            issues.append(f"{ep}.reason: must be a non-empty string")

    silently_lost = expected_fragments - accounted_for - dropped
    if silently_lost:
        sample = sorted(silently_lost)[:5]
        issues.append(
            f"{prefix}: fragments missing from layout AND deduplications_applied "
            f"(silent loss is forbidden): {sample}"
            + (f" ... (+{len(silently_lost) - 5} more)" if len(silently_lost) > 5 else "")
        )


def _resolve_fragment_token(fragment: dict[str, Any]) -> str | None:
    capability = fragment.get("capability")
    rel = fragment.get("relative_path") or fragment.get("path")
    if not isinstance(capability, str) or not capability.strip():
        return None
    if not isinstance(rel, str) or not rel.strip():
        return None
    return f"{capability}:{rel}"


def validate_library_synthesis_return(
    payload: dict[str, Any],
    *,
    expected_fragments: set[str],
    expected_req_ids: set[str],
) -> list[str]:
    """Validate one library-synthesizer subagent's JSON return.

    The orchestrator persists this to
    `runtime/final_synthesis/subagent_returns/library.json`. Validation runs
    in the postflight CLI before any file is written under
    `executions/v{N}/final/library/`.

    `expected_fragments` is the set of `"<capability_id>:<relative_path>"`
    tokens for code fragments listed in the library work-plan slice. Every
    one must appear in `module_layout[].sources` or be explicitly dropped
    via `deduplications_applied[].dropped`.

    `expected_req_ids` is informational only here — REQ-trace continuity
    runs at apply time over the assembled tree.
    """
    issues: list[str] = []
    prefix = "library_synthesis_return"

    if not isinstance(payload, dict):
        return [f"{prefix}: must be an object"]

    if payload.get("modality") != "library":
        issues.append(f"{prefix}.modality: must be 'library'")

    package_name = payload.get("package_name")
    if not isinstance(package_name, str) or not _PACKAGE_NAME_RE.match(package_name or ""):
        issues.append(
            f"{prefix}.package_name: must match '^[a-z][a-z0-9_]*$'"
        )
    elif package_name in _RESERVED_PACKAGE_NAMES:
        issues.append(
            f"{prefix}.package_name: {package_name!r} collides with the Python "
            "stdlib or this project's own package; pick a domain-specific name"
        )

    module_layout = payload.get("module_layout") or []
    if not isinstance(module_layout, list) or not module_layout:
        issues.append(f"{prefix}.module_layout: must be a non-empty list")
        module_layout = []

    accounted_for: set[str] = set()
    layout_targets: dict[str, str] = {}
    for idx, entry in enumerate(module_layout):
        ep = f"{prefix}.module_layout[{idx}]"
        if not isinstance(entry, dict):
            issues.append(f"{ep}: must be an object")
            continue
        target = entry.get("target_path")
        if not isinstance(target, str) or not target.strip():
            issues.append(f"{ep}.target_path: must be a non-empty string")
        elif target in layout_targets:
            issues.append(
                f"{ep}.target_path: duplicate target {target!r} (also at "
                f"{layout_targets[target]})"
            )
        elif not target.endswith(".py"):
            issues.append(f"{ep}.target_path: must end with '.py'")
        else:
            layout_targets[target] = ep
        sources = entry.get("sources") or []
        if not isinstance(sources, list) or not sources:
            issues.append(f"{ep}.sources: must be a non-empty list")
            sources = []
        for jdx, src in enumerate(sources):
            sp = f"{ep}.sources[{jdx}]"
            if not isinstance(src, dict):
                issues.append(f"{sp}: must be an object")
                continue
            token = _resolve_fragment_token(src)
            if token is None:
                issues.append(
                    f"{sp}: must have non-empty 'capability' and 'relative_path'"
                )
                continue
            if expected_fragments and token not in expected_fragments:
                issues.append(
                    f"{sp}: fragment {token!r} not in work plan"
                )
                continue
            accounted_for.add(token)

    exports = payload.get("exports") or []
    if not isinstance(exports, list):
        issues.append(f"{prefix}.exports: must be a list")
    else:
        for idx, sym in enumerate(exports):
            if not isinstance(sym, str) or not sym.strip():
                issues.append(f"{prefix}.exports[{idx}]: must be a non-empty string")

    entry_points = payload.get("entry_points") or []
    if not isinstance(entry_points, list):
        issues.append(f"{prefix}.entry_points: must be a list")
        entry_points = []
    for idx, entry in enumerate(entry_points):
        ep = f"{prefix}.entry_points[{idx}]"
        if not isinstance(entry, dict):
            issues.append(f"{ep}: must be an object")
            continue
        name = entry.get("name")
        target = entry.get("target")
        if not isinstance(name, str) or not name.strip():
            issues.append(f"{ep}.name: must be a non-empty string")
        if not isinstance(target, str) or not _ENTRY_POINT_TARGET_RE.match(target or ""):
            issues.append(
                f"{ep}.target: must match '<module_path>:<callable>'"
            )

    readme_sections = payload.get("readme_sections") or []
    if not isinstance(readme_sections, list):
        issues.append(f"{prefix}.readme_sections: must be a list")
        readme_sections = []
    seen_headings: set[str] = set()
    for idx, section in enumerate(readme_sections):
        sp = f"{prefix}.readme_sections[{idx}]"
        if not isinstance(section, dict):
            issues.append(f"{sp}: must be an object")
            continue
        heading = section.get("heading")
        body = section.get("body")
        if not isinstance(heading, str) or not heading.strip():
            issues.append(f"{sp}.heading: must be a non-empty string")
        else:
            seen_headings.add(heading.strip())
        if not isinstance(body, str) or not body.strip():
            issues.append(f"{sp}.body: must be a non-empty string")
    missing_headings = [h for h in _LIBRARY_README_REQUIRED_SECTIONS if h not in seen_headings]
    if missing_headings:
        issues.append(
            f"{prefix}.readme_sections: missing required heading(s) {missing_headings}"
        )

    package_metadata = payload.get("package_metadata")
    if package_metadata is not None:
        if not isinstance(package_metadata, dict):
            issues.append(f"{prefix}.package_metadata: must be an object when present")
        else:
            for field in ("name", "description", "python_requires"):
                if field in package_metadata:
                    val = package_metadata[field]
                    if not isinstance(val, str) or not val.strip():
                        issues.append(
                            f"{prefix}.package_metadata.{field}: must be a non-empty string"
                        )

    _check_deduplication_audit(
        issues, prefix, payload, expected_fragments, accounted_for
    )

    _ = expected_req_ids  # surface unused-arg hint suppression
    return issues


def validate_document_synthesis_return(
    payload: dict[str, Any],
    *,
    expected_fragments: set[str],
    expected_citation_ids: set[str],
    expected_req_ids: set[str],
) -> list[str]:
    """Validate one document-synthesizer subagent's JSON return.

    Persisted at `runtime/final_synthesis/subagent_returns/document.json`.
    """
    issues: list[str] = []
    prefix = "document_synthesis_return"

    if not isinstance(payload, dict):
        return [f"{prefix}: must be an object"]

    if payload.get("modality") != "document":
        issues.append(f"{prefix}.modality: must be 'document'")

    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        issues.append(f"{prefix}.title: must be a non-empty string")

    abstract = payload.get("abstract")
    if not isinstance(abstract, str) or not abstract.strip():
        issues.append(f"{prefix}.abstract: must be a non-empty string")
    elif len(abstract) > 500:
        issues.append(
            f"{prefix}.abstract: must be <=500 characters (got {len(abstract)})"
        )

    intro_prose = payload.get("intro_prose")
    if not isinstance(intro_prose, str) or not intro_prose.strip():
        issues.append(f"{prefix}.intro_prose: must be a non-empty string")

    conclusion_prose = payload.get("conclusion_prose")
    if not isinstance(conclusion_prose, str) or not conclusion_prose.strip():
        issues.append(f"{prefix}.conclusion_prose: must be a non-empty string")

    section_order = payload.get("section_order") or []
    if not isinstance(section_order, list) or not section_order:
        issues.append(f"{prefix}.section_order: must be a non-empty list")
        section_order = []

    accounted_for: set[str] = set()
    inline_cites: set[str] = set()
    section_bodies: list[str] = []
    for idx, section in enumerate(section_order):
        sp = f"{prefix}.section_order[{idx}]"
        if not isinstance(section, dict):
            issues.append(f"{sp}: must be an object")
            continue
        heading = section.get("heading")
        if not isinstance(heading, str) or not heading.strip():
            issues.append(f"{sp}.heading: must be a non-empty string")

        source = section.get("source") or {}
        if not isinstance(source, dict):
            issues.append(f"{sp}.source: must be an object")
            source = {}
        capability = source.get("capability")
        rel = source.get("file") or source.get("relative_path") or source.get("path")
        synth_prose = source.get("synthesizer_prose")
        if isinstance(capability, str) and isinstance(rel, str) and capability.strip() and rel.strip():
            token = f"{capability}:{rel}"
            if expected_fragments and token not in expected_fragments:
                issues.append(f"{sp}.source: fragment {token!r} not in work plan")
            else:
                accounted_for.add(token)
            section_bodies.append("")  # body comes from on-disk fragment at apply time
        elif isinstance(synth_prose, str) and synth_prose.strip():
            section_bodies.append(synth_prose)
        else:
            issues.append(
                f"{sp}.source: must have either {{capability, file}} or non-empty "
                "'synthesizer_prose'"
            )
            continue

        transitions_after = section.get("transitions_after")
        if transitions_after is not None and (
            not isinstance(transitions_after, str) or not transitions_after.strip()
        ):
            issues.append(
                f"{sp}.transitions_after: must be a non-empty string when present"
            )
        elif isinstance(transitions_after, str):
            section_bodies.append(transitions_after)

        cites = section.get("citations_inline") or []
        if not isinstance(cites, list):
            issues.append(f"{sp}.citations_inline: must be a list")
            cites = []
        for jdx, cid in enumerate(cites):
            if not isinstance(cid, str) or not cid.strip():
                issues.append(
                    f"{sp}.citations_inline[{jdx}]: must be a non-empty string"
                )
                continue
            if expected_citation_ids and cid not in expected_citation_ids:
                issues.append(
                    f"{sp}.citations_inline[{jdx}]: {cid!r} not in citations index"
                )
            inline_cites.add(cid)

    references_unified = payload.get("references_unified") or []
    if not isinstance(references_unified, list):
        issues.append(f"{prefix}.references_unified: must be a list")
        references_unified = []
    ref_ids: set[str] = set()
    for idx, entry in enumerate(references_unified):
        rp = f"{prefix}.references_unified[{idx}]"
        if not isinstance(entry, dict):
            issues.append(f"{rp}: must be an object")
            continue
        cid = entry.get("id")
        human = entry.get("human")
        if not isinstance(cid, str) or not cid.strip():
            issues.append(f"{rp}.id: must be a non-empty string")
            continue
        if not isinstance(human, str) or not human.strip():
            issues.append(f"{rp}.human: must be a non-empty string")
        ref_ids.add(cid)

    unmatched_inline = inline_cites - ref_ids
    if unmatched_inline:
        sample = sorted(unmatched_inline)[:5]
        issues.append(
            f"{prefix}.references_unified: missing entries for inline cites {sample}"
        )

    # REQ coverage: every expected REQ id must appear in at least one section
    # body (synthesizer_prose) or transition. Section bodies that come from
    # on-disk fragments are checked at apply time over the assembled tree.
    if expected_req_ids:
        synth_text = "\n".join(section_bodies + [intro_prose or "", conclusion_prose or ""])
        if synth_text:
            req_in_prose = {rid for rid in expected_req_ids if rid in synth_text}
            # Note: REQs may legitimately live ONLY in fragment bodies that the
            # synthesizer is referencing (not duplicating). The validator does
            # NOT fail when synth-prose alone lacks a REQ — the postflight
            # REQ-trace continuity check catches that against the assembled
            # tree. The check here is informational.
            _ = req_in_prose

    _check_deduplication_audit(
        issues, prefix, payload, expected_fragments, accounted_for
    )

    return issues


def validate_application_synthesis_return(
    payload: dict[str, Any],
    *,
    expected_fragments: set[str],
    expected_buckets: set[str],
    expected_req_ids: set[str],
) -> list[str]:
    """Validate one workflow-synthesizer subagent's JSON return.

    Persisted at `runtime/final_synthesis/subagent_returns/application.json`.
    """
    import ast as _ast  # localized — only this validator parses Python

    issues: list[str] = []
    prefix = "application_synthesis_return"

    if not isinstance(payload, dict):
        return [f"{prefix}: must be an object"]

    if payload.get("modality") != "application":
        issues.append(f"{prefix}.modality: must be 'application'")

    application_name = payload.get("application_name")
    if not isinstance(application_name, str) or not _KEBAB_NAME_RE.match(application_name or ""):
        issues.append(
            f"{prefix}.application_name: must match '^[a-z][a-z0-9-]*$'"
        )

    directory_layout = payload.get("directory_layout") or {}
    if not isinstance(directory_layout, dict):
        issues.append(f"{prefix}.directory_layout: must be an object")
        directory_layout = {}

    missing_buckets = expected_buckets - set(directory_layout.keys())
    if missing_buckets:
        issues.append(
            f"{prefix}.directory_layout: missing required bucket(s) "
            f"{sorted(missing_buckets)}"
        )

    accounted_for: set[str] = set()
    for bucket, entries in directory_layout.items():
        bp = f"{prefix}.directory_layout.{bucket}"
        if not isinstance(entries, list):
            issues.append(f"{bp}: must be a list")
            continue
        for idx, entry in enumerate(entries):
            ep = f"{bp}[{idx}]"
            if not isinstance(entry, dict):
                issues.append(f"{ep}: must be an object")
                continue
            source = entry.get("source")
            target = entry.get("target")
            if not isinstance(source, str) or not _FRAGMENT_TOKEN_RE.match(source or ""):
                issues.append(
                    f"{ep}.source: must match '<capability>:<relative_path>'"
                )
                continue
            if expected_fragments and source not in expected_fragments:
                issues.append(f"{ep}.source: fragment {source!r} not in work plan")
                continue
            accounted_for.add(source)
            if not isinstance(target, str) or not target.strip():
                issues.append(f"{ep}.target: must be a non-empty string")

    if "tests" in directory_layout:
        tests_entries = directory_layout.get("tests") or []
        if isinstance(tests_entries, list) and not tests_entries:
            issues.append(
                f"{prefix}.directory_layout.tests: must be non-empty (every "
                "synthesized application ships with at least one test)"
            )

    entry_point = payload.get("entry_point") or {}
    if not isinstance(entry_point, dict):
        issues.append(f"{prefix}.entry_point: must be an object")
    else:
        filename = entry_point.get("filename")
        if not isinstance(filename, str) or not filename.endswith(".py"):
            issues.append(f"{prefix}.entry_point.filename: must end with '.py'")
        body = entry_point.get("body")
        if not isinstance(body, str) or not body.strip():
            issues.append(f"{prefix}.entry_point.body: must be a non-empty string")
        else:
            try:
                _ast.parse(body)
            except SyntaxError as exc:
                issues.append(
                    f"{prefix}.entry_point.body: invalid Python syntax: "
                    f"{exc.msg} (line {exc.lineno})"
                )
        invocation = entry_point.get("invocation")
        if not isinstance(invocation, str) or not invocation.strip():
            issues.append(f"{prefix}.entry_point.invocation: must be a non-empty string")

    env_vars = payload.get("environment_variables") or []
    if not isinstance(env_vars, list):
        issues.append(f"{prefix}.environment_variables: must be a list")
    else:
        for idx, entry in enumerate(env_vars):
            ep = f"{prefix}.environment_variables[{idx}]"
            if not isinstance(entry, dict):
                issues.append(f"{ep}: must be an object")
                continue
            name = entry.get("name")
            purpose = entry.get("purpose")
            required = entry.get("required")
            if not isinstance(name, str) or not name.strip():
                issues.append(f"{ep}.name: must be a non-empty string")
            if not isinstance(purpose, str) or not purpose.strip():
                issues.append(f"{ep}.purpose: must be a non-empty string")
            if not isinstance(required, bool):
                issues.append(f"{ep}.required: must be a boolean")

    dependencies = payload.get("dependencies") or []
    if not isinstance(dependencies, list):
        issues.append(f"{prefix}.dependencies: must be a list")
    else:
        for idx, dep in enumerate(dependencies):
            if not isinstance(dep, str) or not dep.strip():
                issues.append(
                    f"{prefix}.dependencies[{idx}]: must be a non-empty string "
                    "(format: 'package' or 'package==version')"
                )

    readme_sections = payload.get("readme_sections") or []
    if not isinstance(readme_sections, list):
        issues.append(f"{prefix}.readme_sections: must be a list")
        readme_sections = []
    seen_headings: set[str] = set()
    for idx, section in enumerate(readme_sections):
        sp = f"{prefix}.readme_sections[{idx}]"
        if not isinstance(section, dict):
            issues.append(f"{sp}: must be an object")
            continue
        heading = section.get("heading")
        body = section.get("body")
        if not isinstance(heading, str) or not heading.strip():
            issues.append(f"{sp}.heading: must be a non-empty string")
        else:
            seen_headings.add(heading.strip())
        if not isinstance(body, str) or not body.strip():
            issues.append(f"{sp}.body: must be a non-empty string")
    missing_headings = [
        h for h in _APPLICATION_README_REQUIRED_SECTIONS if h not in seen_headings
    ]
    if missing_headings:
        issues.append(
            f"{prefix}.readme_sections: missing required heading(s) {missing_headings}"
        )

    _check_deduplication_audit(
        issues, prefix, payload, expected_fragments, accounted_for
    )

    _ = expected_req_ids
    return issues


def validate_concept_reconciliation_proposal(payload: dict[str, Any]) -> list[str]:
    """Validate the YAML payload written by the wiki-concept-reconciliation
    orchestrator before `meta-compiler wiki-apply-reconciliation` consumes it."""
    issues: list[str] = []
    root = payload.get("concept_reconciliation_proposal")
    if not isinstance(root, dict):
        return ["concept_reconciliation_proposal: missing root object"]

    _require_fields(
        root,
        ["generated_at", "version", "alias_groups"],
        "concept_reconciliation_proposal",
        issues,
    )

    alias_groups = root.get("alias_groups")
    if not isinstance(alias_groups, list):
        issues.append("concept_reconciliation_proposal.alias_groups: must be a list")
        return issues

    for idx, group in enumerate(alias_groups):
        prefix = f"concept_reconciliation_proposal.alias_groups[{idx}]"
        if not isinstance(group, dict):
            issues.append(f"{prefix}: must be an object")
            continue
        _require_fields(group, ["canonical_name", "members", "justification"], prefix, issues)

        members = group.get("members")
        if not isinstance(members, list) or not members:
            issues.append(f"{prefix}.members: must be a non-empty list")
            continue

        for m_idx, member in enumerate(members):
            m_prefix = f"{prefix}.members[{m_idx}]"
            if not isinstance(member, dict):
                issues.append(f"{m_prefix}: must be an object")
                continue
            _require_fields(
                member,
                ["name", "source_citation_id", "evidence_locator", "definition_excerpt"],
                m_prefix,
                issues,
            )
            locator = member.get("evidence_locator")
            if locator is not None and not isinstance(locator, dict):
                issues.append(f"{m_prefix}.evidence_locator: must be an object")

    return issues


def validate_gap_report_merged(report: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    root = report.get("gap_report")
    if not isinstance(root, dict):
        return ["merged gap report: missing gap_report root"]

    _require_fields(root, ["generated_at", "gaps", "unresolved_count"], "gap_report", issues)
    gaps = root.get("gaps")
    if not isinstance(gaps, list):
        issues.append("gap_report.gaps: must be a list")
        return issues

    for idx, gap in enumerate(gaps):
        if not isinstance(gap, dict):
            issues.append(f"gap_report.gaps[{idx}]: must be an object")
            continue
        _require_fields(
            gap,
            ["id", "description", "severity", "type", "affected_concepts", "attribution", "status"],
            f"gap_report.gaps[{idx}]",
            issues,
        )

    return issues


def validate_debate_transcript(transcript: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    root = transcript.get("debate_transcript")
    if not isinstance(root, dict):
        return ["debate transcript: missing debate_transcript root"]

    _require_fields(
        root,
        ["generated_at", "round_1", "round_2", "round_3", "synthesis"],
        "debate_transcript",
        issues,
    )
    return issues


def validate_review_verdicts(report: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    panel = report.get("review_panel")
    if not isinstance(panel, dict):
        return ["review report: missing review_panel root"]

    _require_fields(panel, ["generated_at", "reviewers", "consensus"], "review_panel", issues)
    reviewers = panel.get("reviewers")
    if not isinstance(reviewers, dict):
        issues.append("review_panel.reviewers: must be an object")
        return issues

    for name in ["optimistic", "pessimistic", "pragmatic"]:
        reviewer = reviewers.get(name)
        if not isinstance(reviewer, dict):
            issues.append(f"review_panel.reviewers.{name}: missing reviewer verdict")
            continue

        _require_fields(
            reviewer,
            ["verdict", "confidence", "blocking_gaps", "non_blocking_gaps", "proceed_if"],
            f"review_panel.reviewers.{name}",
            issues,
        )
        verdict = reviewer.get("verdict")
        if verdict not in VALID_REVIEW_VERDICTS:
            issues.append(
                f"review_panel.reviewers.{name}.verdict: must be PROCEED|ITERATE"
            )

    return issues


def validate_stage_1a2_handoff(report: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    handoff = report.get("stage_1a2_handoff")
    if not isinstance(handoff, dict):
        return ["1A2 handoff: missing stage_1a2_handoff root"]

    _require_fields(
        handoff,
        [
            "generated_at",
            "decision",
            "reason",
            "iteration_count",
            "unresolved_gap_count",
            "ready_for_stage_2",
            "blocking_gaps",
            "non_blocking_gaps",
            "suggested_sources",
            "next_action",
            "ready_signal",
        ],
        "stage_1a2_handoff",
        issues,
    )
    decision = handoff.get("decision")
    if decision not in VALID_REVIEW_VERDICTS:
        issues.append("stage_1a2_handoff.decision: must be PROCEED|ITERATE")

    for field in ["blocking_gaps", "non_blocking_gaps", "suggested_sources"]:
        if not isinstance(handoff.get(field), list):
            issues.append(f"stage_1a2_handoff.{field}: must be a list")

    suggested_sources = handoff.get("suggested_sources", [])
    if isinstance(suggested_sources, list):
        for idx, source in enumerate(suggested_sources):
            if not isinstance(source, dict):
                issues.append(f"stage_1a2_handoff.suggested_sources[{idx}]: must be an object")
                continue
            _require_fields(
                source,
                ["title", "provider", "url"],
                f"stage_1a2_handoff.suggested_sources[{idx}]",
                issues,
            )

    return issues


def validate_source_bindings(bindings_payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    bindings = bindings_payload.get("bindings")
    if not isinstance(bindings, dict):
        return ["source bindings: missing bindings object"]

    for relative_path, row in bindings.items():
        if not isinstance(row, dict):
            issues.append(f"source bindings.{relative_path}: must be an object")
            continue
        _require_fields(
            row,
            ["citation_id", "sha256", "first_seen", "last_seen"],
            f"source bindings.{relative_path}",
            issues,
        )
        if "author_role" in row and row["author_role"] not in VALID_AUTHOR_ROLES:
            issues.append(
                f"source bindings.{relative_path}.author_role: must be one of "
                f"{sorted(VALID_AUTHOR_ROLES)}, got {row['author_role']!r}"
            )

    code_bindings = bindings_payload.get("code_bindings")
    if code_bindings is None:
        return issues
    if not isinstance(code_bindings, dict):
        issues.append("source bindings.code_bindings: must be an object when present")
        return issues

    for relative_path, row in code_bindings.items():
        if not isinstance(row, dict):
            issues.append(f"source bindings.code_bindings.{relative_path}: must be an object")
            continue
        _require_fields(
            row,
            ["type", "remote", "ref", "commit_sha", "cloned_at", "citation_id"],
            f"source bindings.code_bindings.{relative_path}",
            issues,
        )
        if row.get("type") != "code-repo":
            issues.append(
                f"source bindings.code_bindings.{relative_path}.type: must be 'code-repo'"
            )
        if "author_role" in row and row["author_role"] not in VALID_AUTHOR_ROLES:
            issues.append(
                f"source bindings.code_bindings.{relative_path}.author_role: must be one of "
                f"{sorted(VALID_AUTHOR_ROLES)}, got {row['author_role']!r}"
            )
    return issues


def validate_karpathy_index_log(wiki_dir: Path) -> list[str]:
    issues: list[str] = []
    index_path = wiki_dir / "index.md"
    log_path = wiki_dir / "log.md"

    if not index_path.exists():
        issues.append(f"{wiki_dir.name}: index.md missing")
    else:
        index_text = read_text_safe(index_path)
        if "## Catalog" not in index_text:
            issues.append(f"{wiki_dir.name}: index.md missing '## Catalog' section")

    if not log_path.exists():
        issues.append(f"{wiki_dir.name}: log.md missing")
    else:
        log_text = read_text_safe(log_path)
        if "## [" not in log_text:
            issues.append(f"{wiki_dir.name}: log.md missing parseable timestamp headings")

    return issues


def validate_decision_log(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    root = payload.get("decision_log")
    if not isinstance(root, dict):
        return ["decision_log: missing decision_log root"]

    _require_fields(
        root,
        [
            "meta",
            "conventions",
            "architecture",
            "scope",
            "requirements",
            "open_items",
            "agents_needed",
        ],
        "decision_log",
        issues,
    )

    meta = root.get("meta", {})
    project_type: str | None = None
    if isinstance(meta, dict):
        _require_fields(
            meta,
            [
                "project_name",
                "project_type",
                "created",
                "version",
                "parent_version",
                "reason_for_revision",
                "problem_statement_hash",
                "wiki_version",
            ],
            "decision_log.meta",
            issues,
        )
        if meta.get("project_type") not in VALID_PROJECT_TYPES:
            issues.append(
                f"decision_log.meta.project_type: must be one of {sorted(VALID_PROJECT_TYPES)}"
            )
        else:
            project_type = str(meta.get("project_type"))
        version = meta.get("version")
        if not isinstance(version, int) or version < 1:
            issues.append("decision_log.meta.version: must be int >= 1")
    else:
        issues.append("decision_log.meta: must be an object")

    conventions = root.get("conventions", [])
    if not isinstance(conventions, list):
        issues.append("decision_log.conventions: must be a list")
    else:
        for idx, row in enumerate(conventions):
            if not isinstance(row, dict):
                issues.append(f"decision_log.conventions[{idx}]: must be an object")
                continue
            _require_fields(
                row,
                ["name", "domain", "choice", "rationale", "citations"],
                f"decision_log.conventions[{idx}]",
                issues,
            )
            domain = row.get("domain")
            if domain not in VALID_CONVENTION_DOMAINS:
                issues.append(
                    f"decision_log.conventions[{idx}].domain: must be math|code|citation|terminology"
                )

    architecture = root.get("architecture", [])
    if not isinstance(architecture, list):
        issues.append("decision_log.architecture: must be a list")
    else:
        for idx, row in enumerate(architecture):
            if not isinstance(row, dict):
                issues.append(f"decision_log.architecture[{idx}]: must be an object")
                continue
            _require_fields(
                row,
                ["component", "approach", "alternatives_rejected", "constraints_applied", "citations"],
                f"decision_log.architecture[{idx}]",
                issues,
            )

    scope = root.get("scope", {})
    if not isinstance(scope, dict):
        issues.append("decision_log.scope: must be an object")
    else:
        _require_fields(scope, ["in_scope", "out_of_scope"], "decision_log.scope", issues)

    requirements = root.get("requirements", [])
    if not isinstance(requirements, list):
        issues.append("decision_log.requirements: must be a list")
    else:
        seen_ids: set[str] = set()
        for idx, row in enumerate(requirements):
            if not isinstance(row, dict):
                issues.append(f"decision_log.requirements[{idx}]: must be an object")
                continue
            _require_fields(
                row,
                ["id", "description", "source", "citations", "verification"],
                f"decision_log.requirements[{idx}]",
                issues,
            )
            req_id = row.get("id")
            if not isinstance(req_id, str) or not re.fullmatch(r"REQ-\d{3}", req_id):
                issues.append(f"decision_log.requirements[{idx}].id: must match REQ-NNN")
            elif req_id in seen_ids:
                issues.append(f"decision_log.requirements[{idx}].id: duplicate {req_id}")
            else:
                seen_ids.add(req_id)

    # Constraints are optional for back-compat (v1 logs predate this section).
    # When present, every row must satisfy id/description/kind/citations and
    # CON-NNN must be unique. Free-text "constraints_applied" entries on
    # architecture/code_architecture/agents_needed continue to be accepted —
    # entries that look like CON-NNN refs are cross-checked against the table.
    constraints_table = root.get("constraints")
    constraint_ids: set[str] = set()
    if constraints_table is None:
        constraints_table = []
    if not isinstance(constraints_table, list):
        issues.append("decision_log.constraints: must be a list when present")
    else:
        for idx, row in enumerate(constraints_table):
            prefix = f"decision_log.constraints[{idx}]"
            if not isinstance(row, dict):
                issues.append(f"{prefix}: must be an object")
                continue
            _require_fields(
                row, ["id", "description", "kind", "citations"], prefix, issues
            )
            con_id = row.get("id")
            if not isinstance(con_id, str) or not re.fullmatch(r"CON-\d{3}", con_id):
                issues.append(f"{prefix}.id: must match CON-NNN")
            elif con_id in constraint_ids:
                issues.append(f"{prefix}.id: duplicate {con_id}")
            else:
                constraint_ids.add(con_id)
            kind = row.get("kind")
            if kind is not None and kind not in VALID_CONSTRAINT_KINDS:
                issues.append(
                    f"{prefix}.kind: must be one of {sorted(VALID_CONSTRAINT_KINDS)} "
                    f"(got {kind!r})"
                )
            verification_required = row.get("verification_required")
            if verification_required is not None and not isinstance(verification_required, bool):
                issues.append(
                    f"{prefix}.verification_required: must be a boolean when present"
                )

    # Cross-reference: any constraints_applied / key_constraints entry that
    # matches CON-NNN must resolve to a constraints[] row. Free-text entries
    # are accepted (legacy v1 behaviour preserved).
    _CON_REF_RE = re.compile(r"^CON-\d{3}$")

    def _check_con_refs(items: Any, prefix: str) -> None:
        if not isinstance(items, list):
            return
        for j, entry in enumerate(items):
            if not isinstance(entry, str):
                continue
            if _CON_REF_RE.fullmatch(entry) and entry not in constraint_ids:
                issues.append(
                    f"{prefix}[{j}]: unresolved CON-NNN ref {entry!r}"
                )

    architecture_rows = root.get("architecture", [])
    if isinstance(architecture_rows, list):
        for idx, row in enumerate(architecture_rows):
            if isinstance(row, dict):
                _check_con_refs(
                    row.get("constraints_applied"),
                    f"decision_log.architecture[{idx}].constraints_applied",
                )
    code_arch_rows = root.get("code_architecture")
    if isinstance(code_arch_rows, list):
        for idx, row in enumerate(code_arch_rows):
            if isinstance(row, dict):
                _check_con_refs(
                    row.get("constraints_applied"),
                    f"decision_log.code_architecture[{idx}].constraints_applied",
                )
    agents_rows = root.get("agents_needed", [])
    if isinstance(agents_rows, list):
        for idx, row in enumerate(agents_rows):
            if isinstance(row, dict):
                _check_con_refs(
                    row.get("key_constraints"),
                    f"decision_log.agents_needed[{idx}].key_constraints",
                )

    open_items = root.get("open_items", [])
    if not isinstance(open_items, list):
        issues.append("decision_log.open_items: must be a list")
    else:
        for idx, row in enumerate(open_items):
            if not isinstance(row, dict):
                issues.append(f"decision_log.open_items[{idx}]: must be an object")
                continue
            _require_fields(
                row,
                ["description", "deferred_to", "owner"],
                f"decision_log.open_items[{idx}]",
                issues,
            )

    agents = root.get("agents_needed", [])
    if not isinstance(agents, list):
        issues.append("decision_log.agents_needed: must be a list")
    else:
        for idx, row in enumerate(agents):
            if not isinstance(row, dict):
                issues.append(f"decision_log.agents_needed[{idx}]: must be an object")
                continue
            _require_fields(
                row,
                ["role", "responsibility", "inputs", "outputs", "key_constraints"],
                f"decision_log.agents_needed[{idx}]",
                issues,
            )
            if "reads" in row or "writes" in row:
                issues.append(
                    f"decision_log.agents_needed[{idx}]: legacy 'reads'/'writes' fields are no "
                    "longer accepted — replace with typed 'inputs'/'outputs' (modality: "
                    "document|code). Run `meta-compiler migrate-decision-log --plan` to migrate."
                )
            _validate_agent_modality_list(
                row.get("inputs"),
                f"decision_log.agents_needed[{idx}].inputs",
                project_type=project_type,
                role="inputs",
                issues=issues,
            )
            _validate_agent_modality_list(
                row.get("outputs"),
                f"decision_log.agents_needed[{idx}].outputs",
                project_type=project_type,
                role="outputs",
                issues=issues,
            )

    code_arch = root.get("code_architecture")
    if project_type == "report":
        if code_arch not in (None, []):
            issues.append(
                "decision_log.code_architecture: must be omitted (or empty list) for "
                "project_type=report"
            )
    elif project_type in CODE_ARCH_REQUIRED_PROJECT_TYPES:
        if not isinstance(code_arch, list) or not code_arch:
            issues.append(
                "decision_log.code_architecture: required and must contain at least one "
                "entry for algorithm/hybrid projects"
            )
        else:
            seen_aspects: set[str] = set()
            for idx, row in enumerate(code_arch):
                prefix = f"decision_log.code_architecture[{idx}]"
                if not isinstance(row, dict):
                    issues.append(f"{prefix}: must be an object")
                    continue
                _require_fields(
                    row,
                    ["aspect", "choice", "rationale", "citations"],
                    prefix,
                    issues,
                )
                aspect = row.get("aspect")
                if aspect not in VALID_CODE_ARCH_ASPECTS:
                    issues.append(
                        f"{prefix}.aspect: must be one of "
                        f"{sorted(VALID_CODE_ARCH_ASPECTS)}"
                    )
                if isinstance(aspect, str):
                    seen_aspects.add(aspect)
                if aspect == "libraries":
                    libraries = row.get("libraries")
                    if not isinstance(libraries, list) or not libraries:
                        issues.append(
                            f"{prefix}.libraries: required when aspect='libraries' "
                            "and must be a non-empty list"
                        )
                    else:
                        for jdx, lib in enumerate(libraries):
                            if not isinstance(lib, dict):
                                issues.append(
                                    f"{prefix}.libraries[{jdx}]: must be an object"
                                )
                                continue
                            _require_fields(
                                lib,
                                ["name", "description"],
                                f"{prefix}.libraries[{jdx}]",
                                issues,
                            )
                if aspect == "module_layout":
                    layout = row.get("module_layout")
                    if not isinstance(layout, str) or not layout.strip():
                        issues.append(
                            f"{prefix}.module_layout: required string when "
                            "aspect='module_layout'"
                        )
            for required_aspect in ("language", "libraries"):
                if required_aspect not in seen_aspects:
                    issues.append(
                        f"decision_log.code_architecture: must contain at least one entry "
                        f"with aspect='{required_aspect}' for algorithm/hybrid projects"
                    )

    workflow_config = root.get("workflow_config")
    if project_type in WORKFLOW_CONFIG_REQUIRED_PROJECT_TYPES:
        if not isinstance(workflow_config, dict):
            issues.append(
                "decision_log.workflow_config: required object for "
                "project_type=workflow"
            )
        else:
            wc_prefix = "decision_log.workflow_config"
            _require_fields(
                workflow_config,
                ["trigger", "inputs", "outputs", "state_keys", "escalation_policy"],
                wc_prefix,
                issues,
            )
            trigger = workflow_config.get("trigger")
            if trigger is not None and trigger not in VALID_WORKFLOW_TRIGGERS:
                issues.append(
                    f"{wc_prefix}.trigger: must be one of "
                    f"{sorted(VALID_WORKFLOW_TRIGGERS)}, got {trigger!r}"
                )
            for key in ("inputs", "outputs"):
                rows = workflow_config.get(key)
                if rows is not None:
                    if not isinstance(rows, list):
                        issues.append(f"{wc_prefix}.{key}: must be a list")
                        continue
                    for idx, row in enumerate(rows):
                        if not isinstance(row, dict):
                            issues.append(
                                f"{wc_prefix}.{key}[{idx}]: must be an object"
                            )
                            continue
                        kind = row.get("kind")
                        if kind not in VALID_WORKFLOW_IO_KINDS:
                            issues.append(
                                f"{wc_prefix}.{key}[{idx}].kind: must be one of "
                                f"{sorted(VALID_WORKFLOW_IO_KINDS)}, got {kind!r}"
                            )
            state_keys = workflow_config.get("state_keys")
            if state_keys is not None:
                if not isinstance(state_keys, list) or not all(
                    isinstance(k, str) for k in state_keys
                ):
                    issues.append(
                        f"{wc_prefix}.state_keys: must be a list of strings"
                    )
            policy = workflow_config.get("escalation_policy")
            if policy is not None and not isinstance(policy, dict):
                issues.append(
                    f"{wc_prefix}.escalation_policy: must be an object"
                )
    elif workflow_config not in (None, {}):
        issues.append(
            "decision_log.workflow_config: must be omitted for non-workflow project types"
        )

    return issues


def _validate_agent_modality_list(
    items: Any,
    prefix: str,
    project_type: str | None,
    role: str,
    issues: list[str],
) -> None:
    if items is None:
        return
    if not isinstance(items, list):
        issues.append(f"{prefix}: must be a list of {{name, modality}} entries")
        return
    if not items:
        issues.append(f"{prefix}: must contain at least one entry")
        return
    for jdx, entry in enumerate(items):
        item_prefix = f"{prefix}[{jdx}]"
        if not isinstance(entry, dict):
            issues.append(
                f"{item_prefix}: must be a {{name, modality}} object"
            )
            continue
        _require_fields(entry, ["name", "modality"], item_prefix, issues)
        modality = entry.get("modality")
        if modality not in VALID_AGENT_MODALITIES:
            issues.append(
                f"{item_prefix}.modality: must be one of "
                f"{sorted(VALID_AGENT_MODALITIES)} (got {modality!r})"
            )
        if (
            project_type == "report"
            and role == "outputs"
            and modality == "code"
        ):
            issues.append(
                f"{item_prefix}.modality: report projects cannot declare 'code' "
                "outputs — use 'document'"
            )


def _validate_agent_delegation(frontmatter: dict[str, Any], agent_path: Path) -> list[str]:
    issues: list[str] = []
    tools = frontmatter.get("tools")
    if not isinstance(tools, list):
        issues.append(f"custom agent missing tools list: {agent_path.name}")
    elif "agent" not in tools:
        issues.append(f"custom agent missing agent tool: {agent_path.name}")

    agents = frontmatter.get("agents")
    if not isinstance(agents, list):
        issues.append(f"custom agent missing agents allowlist: {agent_path.name}")
    else:
        for required_agent in ["research", "explore"]:
            if required_agent not in agents:
                issues.append(
                    f"custom agent missing delegated subagent '{required_agent}': {agent_path.name}"
                )
    return issues


def validate_custom_agent_file(agent_path: Path) -> list[str]:
    issues: list[str] = []
    frontmatter, body = parse_frontmatter(read_text_safe(agent_path))
    if not frontmatter:
        return [f"custom agent missing frontmatter: {agent_path.name}"]

    _require_fields(frontmatter, ["description"], f"custom agent {agent_path.name}", issues)
    issues.extend(_validate_agent_delegation(frontmatter, agent_path))
    if "## Purpose" not in body:
        issues.append(f"custom agent missing purpose section: {agent_path.name}")
    if "## Decision Trace" not in body:
        issues.append(f"custom agent missing decision trace section: {agent_path.name}")
    return issues


def validate_custom_skill_file(skill_path: Path) -> list[str]:
    issues: list[str] = []
    frontmatter, body = parse_frontmatter(read_text_safe(skill_path))
    if not frontmatter:
        return [f"custom skill missing frontmatter: {skill_path.parent.name}/SKILL.md"]

    _require_fields(frontmatter, ["name", "description"], f"custom skill {skill_path.parent.name}", issues)
    if frontmatter.get("name") != skill_path.parent.name:
        issues.append(
            f"custom skill name mismatch: expected {skill_path.parent.name}, found {frontmatter.get('name')}"
        )
    if not body.strip():
        issues.append(f"custom skill body empty: {skill_path.parent.name}/SKILL.md")
    return issues


def validate_custom_instruction_file(instruction_path: Path) -> list[str]:
    issues: list[str] = []
    frontmatter, body = parse_frontmatter(read_text_safe(instruction_path))
    if not frontmatter:
        return [f"custom instruction missing frontmatter: {instruction_path.name}"]

    _require_fields(frontmatter, ["description"], f"custom instruction {instruction_path.name}", issues)
    if not body.strip():
        issues.append(f"custom instruction body empty: {instruction_path.name}")
    return issues


PALETTE_AGENT_NAMES: tuple[str, ...] = ("implementer", "reviewer", "researcher")


def validate_acceptance_spec_yaml_well_formed(
    spec_path: Path, *, capability_name: str
) -> list[str]:
    """Validate `verification/{hook_id}_spec.yaml` shape (Change B).

    The spec replaces the legacy pytest stub. The Stage 4 implementer
    translates each scenario into `work/<cap>/tests/test_acceptance.py`
    at step 0 of the work loop; the reviewer audits fidelity. The shape
    must therefore be machine-readable: format ∈ {gherkin, example_io},
    scenarios[] non-empty (unless spec_status is `pending_planner_spec`),
    each scenario carries given/when/then non-empty, and example_io
    scenarios carry concrete input/expected pairs.
    """
    issues: list[str] = []
    try:
        import yaml as _yaml  # local import; tests don't need this hot path

        payload = _yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    except (OSError, _yaml.YAMLError) as exc:
        return [
            f"verification spec unreadable: {spec_path.name} "
            f"(capability {capability_name}): {exc}"
        ]
    spec = payload.get("verification_spec")
    if not isinstance(spec, dict):
        return [
            f"verification spec missing root key `verification_spec`: "
            f"{spec_path.name} (capability {capability_name})"
        ]
    spec_status = spec.get("spec_status")
    if spec_status not in ("planner_provided", "pending_planner_spec"):
        issues.append(
            f"verification spec spec_status invalid in {spec_path.name}: "
            f"got {spec_status!r}, expected 'planner_provided' or "
            "'pending_planner_spec'"
        )
    fmt = spec.get("format")
    if fmt not in ("gherkin", "example_io"):
        issues.append(
            f"verification spec format invalid in {spec_path.name}: "
            f"got {fmt!r}, expected 'gherkin' or 'example_io'"
        )
    scenarios = spec.get("scenarios")
    if not isinstance(scenarios, list):
        issues.append(
            f"verification spec scenarios must be a list in {spec_path.name}"
        )
        return issues
    # Bootstrap / pending specs may legitimately have empty scenarios; the
    # reviewer ITERATEs with knowledge_gap until the planner re-runs Stage 2.5.
    if spec_status == "planner_provided" and not scenarios:
        issues.append(
            f"verification spec scenarios empty in {spec_path.name} "
            f"(capability {capability_name}): planner_provided specs must "
            "have at least one scenario"
        )
        return issues
    for s_idx, scenario in enumerate(scenarios):
        sprefix = f"verification spec {spec_path.name} scenarios[{s_idx}]"
        if not isinstance(scenario, dict):
            issues.append(f"{sprefix}: must be a mapping")
            continue
        for required_key in ("name", "given", "when", "then"):
            val = scenario.get(required_key)
            if not isinstance(val, str) or not val.strip():
                issues.append(f"{sprefix}.{required_key}: must be a non-empty string")
        if fmt == "example_io":
            examples = scenario.get("examples")
            if not isinstance(examples, list) or not examples:
                issues.append(
                    f"{sprefix}.examples: must be a non-empty list when "
                    "format is 'example_io'"
                )
                continue
            for e_idx, example in enumerate(examples):
                eprefix = f"{sprefix}.examples[{e_idx}]"
                if not isinstance(example, dict):
                    issues.append(f"{eprefix}: must be a mapping")
                    continue
                if "input" not in example:
                    issues.append(f"{eprefix}.input: required for example_io")
                if "expected" not in example:
                    issues.append(f"{eprefix}.expected: required for example_io")
    return issues


def validate_scaffold(scaffold_root: Path) -> list[str]:
    """Validate a post-dialogue scaffold (Commit 7 shape).

    Runs 11 ordered checks against the capability-driven layout:
      1. SCAFFOLD_MANIFEST + required top-level files.
      2. CapabilityGraph Pydantic validation.
      3. ContractManifest + per-contract validation.
      4. Finding-citation integrity (with v1 bootstrap exception).
      5. Skill <-> capability symmetry + no stub sections.
      6. Trigger specificity.
      7. Contract reuse (every contract referenced by >=1 capability).
      8. Capability coverage vs Stage 2 requirements.
      9. Verification harness presence.
     10. Repo-level palette sanity.
     11. Project-type-conditioned empty output buckets.
    """
    from .findings_loader import concept_vocabulary, load_all_findings
    from .artifacts import build_paths
    from .schemas import Capability, CapabilityGraph, Contract, ContractManifest, SkillFrontmatter

    issues: list[str] = []
    if not scaffold_root.exists():
        return [f"scaffold root missing: {scaffold_root}"]

    # ---- 1. Manifest + required files ----
    manifest_path = scaffold_root / "SCAFFOLD_MANIFEST.yaml"
    if not manifest_path.exists():
        return ["scaffold missing file: SCAFFOLD_MANIFEST.yaml"]
    scaffold_manifest = load_yaml(manifest_path) or {}
    manifest = scaffold_manifest.get("scaffold")
    if not isinstance(manifest, dict):
        return ["scaffold manifest missing scaffold root object"]
    project_type = manifest.get("project_type")
    if project_type not in VALID_PROJECT_TYPES:
        issues.append(
            f"scaffold manifest project_type must be one of {sorted(VALID_PROJECT_TYPES)}"
        )

    for rel in (
        "capabilities.yaml",
        "EXECUTION_MANIFEST.yaml",
        "DISPATCH_HINTS.yaml",
        "contracts/_manifest.yaml",
        "skills/INDEX.md",
    ):
        if not (scaffold_root / rel).exists():
            issues.append(f"scaffold missing file: {rel}")

    if issues:
        return issues

    # ---- 2. CapabilityGraph ----
    try:
        graph = CapabilityGraph.model_validate(
            (load_yaml(scaffold_root / "capabilities.yaml") or {}).get("capability_graph") or {}
        )
    except Exception as exc:  # pydantic ValidationError or missing keys
        issues.append(f"capabilities.yaml schema invalid: {exc}")
        return issues

    # ---- 3. Contract library ----
    try:
        contract_manifest = ContractManifest.model_validate(
            (load_yaml(scaffold_root / "contracts" / "_manifest.yaml") or {}).get("contract_manifest") or {}
        )
    except Exception as exc:
        issues.append(f"contracts/_manifest.yaml schema invalid: {exc}")
        return issues

    contracts_by_id: dict[str, Contract] = {}
    for entry in contract_manifest.entries:
        contract_path = scaffold_root / entry.path
        if not contract_path.exists():
            issues.append(f"contracts: file missing for {entry.contract_id} at {entry.path}")
            continue
        try:
            contracts_by_id[entry.contract_id] = Contract.model_validate(
                (load_yaml(contract_path) or {}).get("contract") or {}
            )
        except Exception as exc:
            issues.append(f"{entry.path}: schema invalid: {exc}")

    # ---- 4. Finding-citation integrity ----
    artifacts_root = scaffold_root.parent.parent  # workspace-artifacts/
    paths = build_paths(artifacts_root)
    finding_records = load_all_findings(paths)
    known_findings = {rec.finding_id for rec in finding_records}
    bootstrap = (not finding_records) and manifest.get("decision_log_version") == 1
    if bootstrap:
        citations_payload = load_yaml(paths.citations_index_path) or {}
        known_findings = set(
            (citations_payload.get("citations_index") or {}).get("citations", {}).keys()
        )

    for cap in graph.capabilities:
        for fid in cap.required_finding_ids:
            if fid in known_findings:
                continue
            if bootstrap and "#" in fid and fid.split("#", 1)[0] in known_findings:
                continue
            issues.append(
                f"capability {cap.name}: required_finding_id {fid} has no match "
                f"in {'wiki/findings/' if not bootstrap else 'wiki/citations/index.yaml (bootstrap)'}"
            )
    for contract in contracts_by_id.values():
        for fref in contract.required_findings:
            if fref.finding_id in known_findings:
                continue
            if bootstrap and fref.citation_id in known_findings:
                continue
            issues.append(
                f"contract {contract.contract_id}: finding {fref.finding_id} not resolvable"
            )

    # ---- 5. Skill <-> capability symmetry ----
    skills_dir = scaffold_root / "skills"
    for cap in graph.capabilities:
        skill_path = skills_dir / cap.name / "SKILL.md"
        if not skill_path.exists():
            issues.append(f"skill missing for capability {cap.name}")
            continue
        frontmatter, body = parse_frontmatter(read_text_safe(skill_path))
        try:
            fm = SkillFrontmatter.model_validate(frontmatter)
        except Exception as exc:
            issues.append(f"skill {skill_path.name}: frontmatter invalid ({exc})")
            continue
        if fm.name != cap.name:
            issues.append(f"skill {cap.name}: frontmatter name '{fm.name}' != capability name")
        if sorted(fm.triggers) != sorted(cap.when_to_use):
            issues.append(f"skill {cap.name}: triggers drift from capabilities.yaml")
        # No stub sections: every `## ` heading must have non-empty content.
        for heading, content in _iter_markdown_sections(body):
            if not content.strip():
                issues.append(f"skill {cap.name}: empty section '{heading}'")

    # ---- 6. Trigger specificity ----
    vocab = concept_vocabulary(finding_records)
    bootstrap_vocab: set[str] = set()
    if not vocab:
        # Bootstrap: derive tokens from the decision log.
        dlv = manifest.get("decision_log_version")
        if isinstance(dlv, int):
            dl_path = paths.decision_logs_dir / f"decision_log_v{dlv}.yaml"
            if dl_path.exists():
                from .findings_loader import decision_log_vocabulary

                bootstrap_vocab = decision_log_vocabulary(load_yaml(dl_path) or {})
    for cap in graph.capabilities:
        for trig in cap.when_to_use:
            if _is_generic_trigger(trig, vocab=vocab, bootstrap_vocab=bootstrap_vocab):
                issues.append(
                    f"capability {cap.name}: trigger '{trig}' is generic "
                    "(no domain vocabulary in findings or decision log)"
                )

    # ---- 7. Contract reuse ----
    referenced: set[str] = {cap.io_contract_ref for cap in graph.capabilities}
    for cap in graph.capabilities:
        for composed_name in cap.composes:
            other = next((c for c in graph.capabilities if c.name == composed_name), None)
            if other is not None:
                referenced.add(other.io_contract_ref)
    for cid in contracts_by_id:
        if cid not in referenced:
            issues.append(
                f"contract {cid}: unreferenced — every contract must back >=1 capability"
            )

    # ---- 8. Capability coverage ----
    dlv = manifest.get("decision_log_version")
    if isinstance(dlv, int):
        dl_path = paths.decision_logs_dir / f"decision_log_v{dlv}.yaml"
        if dl_path.exists():
            dl = load_yaml(dl_path) or {}
            req_ids = {
                row.get("id")
                for row in (dl.get("decision_log") or {}).get("requirements") or []
                if isinstance(row, dict) and row.get("id")
            }
            covered = {rid for cap in graph.capabilities for rid in cap.requirement_ids}
            for rid in sorted(req_ids - covered):
                issues.append(f"requirement {rid}: no capability covers it (coverage gate)")

    # ---- 9. Verification harness presence + spec well-formedness ----
    verification_dir = scaffold_root / "verification"
    for cap in graph.capabilities:
        if not cap.verification_required:
            # Constraint-only / policy capabilities deliberately skip stubs.
            continue
        for hook_id in cap.verification_hook_ids:
            spec = verification_dir / f"{hook_id}_spec.yaml"
            if not spec.exists():
                issues.append(
                    f"verification hook missing: verification/{hook_id}_spec.yaml "
                    f"(capability {cap.name})"
                )
                continue
            issues.extend(
                validate_acceptance_spec_yaml_well_formed(spec, capability_name=cap.name)
            )

    # ---- 10. Palette sanity at repo/workspace level ----
    workspace_root = artifacts_root.parent
    palette_dir = workspace_root / ".github" / "agents"
    for expected in PALETTE_AGENT_NAMES:
        if not (palette_dir / f"{expected}.agent.md").exists():
            issues.append(f"workspace palette missing agent: .github/agents/{expected}.agent.md")

    # ---- 11. Empty output buckets ----
    from .project_types import scaffold_subdirs_for as _subdirs
    if isinstance(project_type, str):
        for dname in sorted(_subdirs(project_type)):
            if not (scaffold_root / dname).is_dir():
                issues.append(f"scaffold missing output bucket: {dname}")

    return issues


def _iter_markdown_sections(body: str):
    """Yield (heading, content) pairs for every `## ` heading. Content is the
    text between headings (excluding the heading line itself)."""
    lines = body.splitlines()
    current_heading: str | None = None
    current_chunks: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current_heading is not None:
                yield current_heading, "\n".join(current_chunks)
            current_heading = line
            current_chunks = []
        else:
            current_chunks.append(line)
    if current_heading is not None:
        yield current_heading, "\n".join(current_chunks)


VALID_STAGE2_PRECHECK_VERDICTS = {"PROCEED", "BLOCK"}
VALID_STAGE2_POSTCHECK_VERDICTS = {"PROCEED", "REVISE"}


def _validate_stage2_check_entries(
    checks: Any, prefix: str, issues: list[str]
) -> None:
    if not isinstance(checks, list):
        issues.append(f"{prefix}: must be a list")
        return
    for idx, row in enumerate(checks):
        if not isinstance(row, dict):
            issues.append(f"{prefix}[{idx}]: must be an object")
            continue
        _require_fields(row, ["name", "result"], f"{prefix}[{idx}]", issues)
        result = row.get("result")
        if result not in {"PASS", "FAIL", "WARN"}:
            issues.append(f"{prefix}[{idx}].result: must be PASS|FAIL|WARN")


def validate_stage2_precheck_request(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    root = payload.get("stage2_precheck_request")
    if not isinstance(root, dict):
        return ["stage2 precheck request: missing stage2_precheck_request root"]

    _require_fields(
        root,
        [
            "generated_at",
            "decision_log_version",
            "mechanical_checks",
            "verdict_output_path",
        ],
        "stage2_precheck_request",
        issues,
    )
    _validate_stage2_check_entries(
        root.get("mechanical_checks"),
        "stage2_precheck_request.mechanical_checks",
        issues,
    )
    return issues


def validate_stage2_postcheck_request(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    root = payload.get("stage2_postcheck_request")
    if not isinstance(root, dict):
        return ["stage2 postcheck request: missing stage2_postcheck_request root"]

    _require_fields(
        root,
        [
            "generated_at",
            "decision_log_version",
            "inputs",
            "mechanical_checks",
            "verdict_output_path",
        ],
        "stage2_postcheck_request",
        issues,
    )
    inputs = root.get("inputs")
    if isinstance(inputs, dict):
        _require_fields(
            inputs,
            ["transcript", "decision_log"],
            "stage2_postcheck_request.inputs",
            issues,
        )
    else:
        issues.append("stage2_postcheck_request.inputs: must be an object")
    _validate_stage2_check_entries(
        root.get("mechanical_checks"),
        "stage2_postcheck_request.mechanical_checks",
        issues,
    )
    return issues


def validate_stage2_verdict(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    root = payload.get("stage2_orchestrator_verdict")
    if not isinstance(root, dict):
        return ["stage2 verdict: missing stage2_orchestrator_verdict root"]

    _require_fields(
        root,
        ["stage", "verdict", "generated_at", "decision_log_version", "checks", "summary"],
        "stage2_orchestrator_verdict",
        issues,
    )
    stage = root.get("stage")
    verdict = root.get("verdict")
    if stage == "preflight":
        if verdict not in VALID_STAGE2_PRECHECK_VERDICTS:
            issues.append(
                "stage2_orchestrator_verdict.verdict: preflight must be PROCEED|BLOCK"
            )
    elif stage == "postflight":
        if verdict not in VALID_STAGE2_POSTCHECK_VERDICTS:
            issues.append(
                "stage2_orchestrator_verdict.verdict: postflight must be PROCEED|REVISE"
            )
    else:
        issues.append(
            "stage2_orchestrator_verdict.stage: must be preflight|postflight"
        )

    _validate_stage2_check_entries(
        root.get("checks"),
        "stage2_orchestrator_verdict.checks",
        issues,
    )
    return issues


def validate_stage_4(paths: ArtifactPaths) -> list[str]:
    issues: list[str] = []

    execution_dirs = sorted([path for path in paths.executions_dir.glob("v*") if path.is_dir()])
    if not execution_dirs:
        issues.append("4: no execution output directories found")
    else:
        latest_execution = execution_dirs[-1]
        manifest_path = latest_execution / "FINAL_OUTPUT_MANIFEST.yaml"
        if not manifest_path.exists():
            issues.append(f"4: final output manifest missing: {manifest_path.relative_to(paths.root)}")

        # Final-synthesis sub-stage: when the workspace manifest reports
        # "4-synthesized", the assembled tree at executions/v{N}/final/ and a
        # final_synthesis_report.yaml must both be present. When the manifest
        # is at "4" or "3" the synthesis sub-stage was skipped (either
        # intentionally or because this run predates it) — informational only.
        manifest = load_manifest(paths)
        last_completed = ""
        if manifest:
            research = (
                manifest.get("workspace_manifest", {}).get("research", {})
                if isinstance(manifest, dict)
                else {}
            )
            last_completed = str(research.get("last_completed_stage", ""))
        if last_completed == "4-synthesized":
            final_dir = latest_execution / "final"
            report_path = latest_execution / "final_synthesis_report.yaml"
            if not final_dir.exists() or not any(final_dir.rglob("*")):
                issues.append(
                    f"4: stage state is '4-synthesized' but {final_dir.relative_to(paths.root)} "
                    "is missing or empty"
                )
            if not report_path.exists():
                issues.append(
                    f"4: stage state is '4-synthesized' but "
                    f"{report_path.relative_to(paths.root)} is missing"
                )

    pitch_files = sorted(paths.pitches_dir.glob("pitch_v*.pptx"))
    if not pitch_files:
        issues.append("4: no pitch deck generated")

    what_i_built_path = paths.wiki_provenance_dir / "what_i_built.md"
    if not what_i_built_path.exists():
        issues.append("4: what_i_built.md missing from wiki provenance")

    return issues


def validate_stage(paths: ArtifactPaths, stage: str) -> list[str]:
    issues: list[str] = []

    if stage in {"all", "0", "init"}:
        issues.extend(validate_problem_statement(paths.root.parent / "PROBLEM_STATEMENT.md"))

    if stage in {"all", "manifest"}:
        manifest = load_manifest(paths)
        if not manifest:
            issues.append("manifest: not found")
        else:
            issues.extend(validate_manifest(manifest))

    if stage in {"all", "1a", "citations"}:
        citations = load_yaml(paths.citations_index_path)
        if not citations:
            issues.append("citations: index not found")
        else:
            issues.extend(validate_citation_index(citations))

        if paths.findings_index_path.exists():
            findings_index = load_yaml(paths.findings_index_path)
            if not findings_index:
                issues.append("findings index: file is empty")
            else:
                issues.extend(validate_findings_index(findings_index))

        findings_files = sorted(paths.findings_dir.glob("*.json")) if paths.findings_dir.exists() else []
        if findings_files:
            findings_validation = validate_all_findings(artifacts_root=paths.root)
            for row in findings_validation.get("per_file", []):
                for finding_issue in row.get("issues", []):
                    issues.append(f"findings: {finding_issue}")

        source_bindings = load_yaml(paths.source_bindings_path)
        if source_bindings:
            issues.extend(validate_source_bindings(source_bindings))
        else:
            issues.append("source bindings: file missing")

        if paths.wiki_v1_pages_dir.exists():
            pages = sorted(paths.wiki_v1_pages_dir.glob("*.md"))
            if not pages:
                issues.append("wiki v1: no pages found")
            for page in pages:
                issues.extend(validate_wiki_page(page))

        if paths.wiki_v1_dir.exists():
            issues.extend(validate_karpathy_index_log(paths.wiki_v1_dir))

    if stage in {"all", "1b", "depth"}:
        merged_path = paths.reports_dir / "merged_gap_report.yaml"
        transcript_path = paths.reports_dir / "debate_transcript.yaml"
        merged = load_yaml(merged_path)
        transcript = load_yaml(transcript_path)

        if merged:
            issues.extend(validate_gap_report_merged(merged))
        else:
            issues.append("1B: merged gap report missing")

        if transcript:
            issues.extend(validate_debate_transcript(transcript))
        else:
            issues.append("1B: debate transcript missing")

        health = load_yaml(paths.reports_dir / "wiki_health_report.yaml")
        if not health:
            issues.append("1B: wiki health report missing")

        if paths.wiki_v2_pages_dir.exists() and list(paths.wiki_v2_pages_dir.glob("*.md")):
            issues.extend(validate_karpathy_index_log(paths.wiki_v2_dir))

    if stage in {"all", "1c", "review"}:
        review_path = paths.reviews_dir / "review_verdicts.yaml"
        review_report = load_yaml(review_path)
        if review_report:
            issues.extend(validate_review_verdicts(review_report))
        else:
            issues.append("1C: review verdicts missing")

        handoff_path = paths.reviews_dir / "1a2_handoff.yaml"
        handoff_report = load_yaml(handoff_path)
        if handoff_report:
            issues.extend(validate_stage_1a2_handoff(handoff_report))
        else:
            issues.append("1C: Stage 1A2 handoff missing")

    if stage in {"all", "2", "decision-log"}:
        latest = latest_decision_log_path(paths)
        if latest is None:
            if stage in {"2", "decision-log"}:
                issues.append("2: decision log missing")
        else:
            _, decision_log_path = latest
            decision_log = load_yaml(decision_log_path)
            issues.extend(validate_decision_log(decision_log))

        # Stage 2 prompt-as-conductor runtime artifacts are optional — they
        # only exist mid-flow. When present they must pass schema validation.
        if paths.stage2_precheck_request_path.exists():
            payload = load_yaml(paths.stage2_precheck_request_path)
            if payload:
                issues.extend(validate_stage2_precheck_request(payload))
        if paths.stage2_precheck_verdict_path.exists():
            payload = load_yaml(paths.stage2_precheck_verdict_path)
            if payload:
                issues.extend(validate_stage2_verdict(payload))
        if paths.stage2_postcheck_request_path.exists():
            payload = load_yaml(paths.stage2_postcheck_request_path)
            if payload:
                issues.extend(validate_stage2_postcheck_request(payload))
        if paths.stage2_postcheck_verdict_path.exists():
            payload = load_yaml(paths.stage2_postcheck_verdict_path)
            if payload:
                issues.extend(validate_stage2_verdict(payload))

        # Wiki search runs as Step 0 of elicit-vision --start. Once the
        # operator has entered Stage 2 the results.yaml must exist; the brief
        # has a placeholder when it doesn't, but the dialog is materially
        # weaker without it.
        manifest = load_manifest(paths)
        last_completed = ""
        if manifest:
            research = (
                manifest.get("workspace_manifest", {}).get("research", {})
                if isinstance(manifest, dict)
                else {}
            )
            last_completed = str(research.get("last_completed_stage", ""))
        if last_completed in {"2", "2-reentry-seeded", "3", "4"}:
            if not paths.wiki_search_results_path.exists():
                issues.append(
                    "2: wiki_search/results.yaml missing — re-run "
                    "`meta-compiler elicit-vision --start` so Step 0 wiki-search "
                    "fires (or pass --skip-wiki-search to bypass)"
                )
            else:
                from .stages.wiki_search_stage import validate_wiki_search_results

                payload = load_yaml(paths.wiki_search_results_path)
                if payload:
                    issues.extend(validate_wiki_search_results(payload))

    if stage in {"all", "3", "scaffold"}:
        scaffold_dirs = sorted(
            [path for path in paths.scaffolds_dir.glob("v*") if path.is_dir()],
            key=lambda row: row.name,
        )
        if not scaffold_dirs:
            if stage in {"3", "scaffold"}:
                issues.append("3: no scaffold directories found")
        else:
            issues.extend(validate_scaffold(scaffold_dirs[-1]))

    if stage in {"all", "4", "phase4", "pitch"}:
        issues.extend(validate_stage_4(paths))

    return issues


def _is_generic_trigger(
    trigger: str,
    vocab: set[str],
    *,
    bootstrap_vocab: set[str] | None = None,
) -> bool:
    """Return True if `trigger` fails the trigger-specificity rule.

    A trigger passes if, after stripping stop-words, at least one remaining
    token appears in `vocab` (the primary concept vocabulary from findings).
    If `vocab` is empty and a `bootstrap_vocab` is provided, fall back to
    decision-log tokens: require >=1 non-stopword token present in
    `bootstrap_vocab`.

    The rule rejects triggers like "use when implementing" (stop-words only)
    and "use when needed" (no domain tokens after stripping). It accepts
    "validate decision log schema" when the vocabulary knows "schema" or
    "decision".

    Not yet wired into validate_scaffold (that happens in Commit 7). Exposed
    here so the Commit 3 capability-compile stage and the
    validate_trigger_specificity hook can share the implementation.
    """
    tokens = trigger_content_tokens(trigger)
    if not tokens:
        return True  # nothing but stop-words
    if vocab:
        return not (tokens & vocab)
    if bootstrap_vocab is not None:
        return not (tokens & bootstrap_vocab)
    # No vocabulary available at all: only stop-word check applies.
    return False

