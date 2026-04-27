"""Stage 2.5: implementation planning brief + plan extraction.

Inserts an LLM-driven planning pass between Stage 2 (decision log) and
Stage 3 (capability compile). The CLI here is bookend-only:

* `run_plan_implementation_start` — reads the decision log + findings +
  citation index and writes a planning brief at
  `runtime/plan/brief.md`. The brief is the input the
  `implementation-planner` agent consumes when proposing a phased plan.
* `run_plan_implementation_finalize` — validates the
  `decision-logs/implementation_plan_v{N}.md` markdown the agent wrote,
  extracts the fenced `capability_plan` YAML block, validates it against
  the decision log, and persists `decision-logs/plan_extract_v{N}.yaml`.

The Stage 3 capability compile reads `plan_extract_v{N}.yaml` (when
present) to build capabilities N-to-M with REQ/CON ids instead of the
legacy 1-to-1 row mapping. The legacy path remains the fallback when no
plan extract exists.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..artifacts import build_paths, ensure_layout
from ..findings_loader import (
    FindingRecord,
    concept_vocabulary,
    decision_log_vocabulary,
    load_all_findings,
    trigger_content_tokens,
)
from ..io import dump_yaml, load_yaml
from ..utils import iso_now
from ._decision_log_utils import resolve_decision_log


REQUIRED_PLAN_SECTIONS: tuple[str, ...] = (
    "Overview",
    "Phases",
    "Capabilities",
    "Dependencies",
    "Risks",
    "Open Questions",
)

_FENCED_BLOCK_RE = re.compile(
    r"```yaml\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)
_HEADING_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")


# ---------------------------------------------------------------------------
# Preflight: render the planning brief
# ---------------------------------------------------------------------------


def _format_requirements(decision_log: dict[str, Any]) -> list[str]:
    rows = decision_log.get("requirements") or []
    if not rows:
        return ["_No requirements compiled yet._"]
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = row.get("id", "REQ-???")
        desc = row.get("description", "").strip()
        lens = row.get("lens", "")
        out.append(f"- **{rid}** ({lens}): {desc}")
    return out


def _format_constraints(decision_log: dict[str, Any]) -> list[str]:
    rows = decision_log.get("constraints") or []
    if not rows:
        return ["_No constraints captured._"]
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = row.get("id", "CON-???")
        kind = row.get("kind", "")
        desc = row.get("description", "").strip()
        verify = "verify=true" if row.get("verification_required") else "verify=false"
        out.append(f"- **{cid}** [{kind}, {verify}]: {desc}")
    return out


def _format_architecture(decision_log: dict[str, Any]) -> list[str]:
    rows = decision_log.get("architecture") or []
    if not rows:
        return ["_No architecture decisions yet._"]
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        component = row.get("component", "(unnamed)")
        approach = row.get("approach", "").strip()
        constraints = row.get("constraints_applied") or []
        cstr = f" (constraints: {', '.join(str(c) for c in constraints)})" if constraints else ""
        out.append(f"- **{component}** → {approach}{cstr}")
    return out


def _format_code_architecture(decision_log: dict[str, Any]) -> list[str]:
    rows = decision_log.get("code_architecture") or []
    if not rows:
        return ["_No code-architecture decisions (or this is a report project)._"]
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        aspect = row.get("aspect", "(unnamed)")
        choice = row.get("choice", "").strip()
        out.append(f"- **{aspect}** → {choice}")
    return out


def _format_agents(decision_log: dict[str, Any]) -> list[str]:
    rows = decision_log.get("agents_needed") or []
    if not rows:
        return ["_No agents declared._"]
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = row.get("role", "(unnamed)")
        resp = row.get("responsibility", "").strip()
        out.append(f"- **{role}** — {resp}")
    return out


def _format_citations(citation_index: dict[str, Any]) -> list[str]:
    if not isinstance(citation_index, dict):
        return ["_No citation index found._"]
    citations = citation_index.get("citations") or {}
    if not isinstance(citations, dict):
        return ["_No citations registered._"]
    out: list[str] = []
    for cid in sorted(citations.keys()):
        entry = citations[cid]
        if not isinstance(entry, dict):
            continue
        human = entry.get("human") or cid
        out.append(f"- `{cid}` — {human}")
    return out or ["_No citations registered._"]


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, str) and item.strip()]


def _known_citation_ids(decision_log: dict[str, Any]) -> set[str]:
    inner = decision_log.get("decision_log") if "decision_log" in decision_log else decision_log
    if not isinstance(inner, dict):
        return set()
    citations: set[str] = set()
    for section in (
        "conventions",
        "architecture",
        "requirements",
        "constraints",
        "agents_needed",
        "code_architecture",
    ):
        for row in inner.get(section) or []:
            if isinstance(row, dict):
                citations.update(_as_string_list(row.get("citations")))
    return citations


def _index_findings_by_citation(records: list[FindingRecord]) -> dict[str, list[FindingRecord]]:
    indexed: dict[str, list[FindingRecord]] = {}
    for record in records:
        indexed.setdefault(record.citation_id, []).append(record)
    return indexed


def _summarize_finding(record: FindingRecord) -> str:
    concepts = [str(row.get("name") or "").strip() for row in record.concepts]
    concepts = [name for name in concepts if name]
    claims = [str(row.get("statement") or "").strip() for row in record.claims]
    claims = [claim for claim in claims if claim]
    parts: list[str] = []
    if concepts:
        parts.append("concepts: " + ", ".join(concepts[:4]))
    if claims:
        claim = claims[0]
        if len(claim) > 160:
            claim = claim[:157].rstrip() + "..."
        parts.append("claim: " + claim)
    return "; ".join(parts) or "finding available"


def _format_row_evidence(row: dict[str, Any], findings_by_citation: dict[str, list[FindingRecord]]) -> list[str]:
    citations = _as_string_list(row.get("citations"))
    if not citations:
        return ["  - _No citations on this row._"]
    lines: list[str] = []
    for citation_id in citations[:6]:
        records = findings_by_citation.get(citation_id) or []
        if not records:
            lines.append(f"  - `{citation_id}`: citation present; no extracted findings loaded yet.")
            continue
        for record in records[:2]:
            lines.append(
                f"  - `{record.finding_id}` (`{citation_id}`): {_summarize_finding(record)}"
            )
    return lines


def _format_planner_evidence_context(
    paths,
    decision_log: dict[str, Any],
) -> list[str]:
    """Compact evidence pack for the implementation planner.

    The planner needs concrete domain nouns and cited claims before it writes
    capability triggers and steps. This section is intentionally extractive and
    short; raw seed reading remains outside the deterministic CLI.
    """
    findings = load_all_findings(paths)
    findings_by_citation = _index_findings_by_citation(findings)
    inner = decision_log.get("decision_log") if "decision_log" in decision_log else decision_log
    if not isinstance(inner, dict):
        return ["_Decision log payload missing._"]

    vocab = sorted((concept_vocabulary(findings) | decision_log_vocabulary(decision_log)) - {""})
    lines: list[str] = []
    if vocab:
        lines.append("Available trigger vocabulary (prefer these nouns in `explicit_triggers`):")
        lines.append("- " + ", ".join(vocab[:80]))
    else:
        lines.append("_No trigger vocabulary available yet; use precise nouns from the REQ/CON descriptions._")

    lines.append("")
    lines.append("Cited evidence by REQ/CON:")
    row_count = 0
    for section, id_prefix in (("requirements", "REQ"), ("constraints", "CON")):
        for row in inner.get(section) or []:
            if not isinstance(row, dict):
                continue
            row_id = str(row.get("id") or f"{id_prefix}-???")
            desc = str(row.get("description") or "").strip()
            lines.append(f"- **{row_id}**: {desc}")
            lines.extend(_format_row_evidence(row, findings_by_citation))
            row_count += 1
    if row_count == 0:
        lines.append("- _No REQ/CON rows to map._")
    return lines


def _wiki_evidence_lines(paths) -> list[str]:
    """Top concept pages by source count, with their first-paragraph summary."""
    if not paths.wiki_v2_pages_dir.exists():
        return ["_v2 wiki not built yet._"]
    rows: list[tuple[int, str, str]] = []
    from ..io import parse_frontmatter
    from ..utils import read_text_safe

    for page in sorted(paths.wiki_v2_pages_dir.glob("*.md")):
        text = read_text_safe(page)
        frontmatter, body = parse_frontmatter(text)
        if str(frontmatter.get("type") or "") != "concept":
            continue
        sources = frontmatter.get("sources") or []
        n_sources = len([s for s in sources if isinstance(s, str) and s.strip()])
        # Extract first non-empty line under ## Definition.
        summary = ""
        if "## Definition" in body:
            body_after = body.split("## Definition", 1)[1]
            for line in body_after.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    summary = line
                    break
        rows.append((n_sources, page.stem, summary))
    rows.sort(key=lambda r: -r[0])
    if not rows:
        return ["_No concept pages found in wiki v2._"]
    out: list[str] = []
    for n_sources, page_id, summary in rows[:10]:
        if summary:
            out.append(f"- `{page_id}` ({n_sources} src): {summary}")
        else:
            out.append(f"- `{page_id}` ({n_sources} src)")
    return out


def render_planning_brief(
    paths,
    decision_log: dict[str, Any],
    decision_log_version: int,
    generated_at: str,
) -> str:
    """Render the markdown brief the implementation-planner agent consumes."""
    citation_index = load_yaml(paths.citations_index_path) or {}
    inner = decision_log.get("decision_log") or decision_log

    sections: list[str] = []
    sections.append("# Implementation Planning Brief")
    sections.append("")
    sections.append(f"Generated: {generated_at}")
    sections.append(
        f"Decision Log: decision-logs/decision_log_v{decision_log_version}.yaml"
    )
    sections.append(f"Decision Log version: v{decision_log_version}")
    sections.append("")
    sections.append("## Requirements (REQ-NNN)")
    sections.extend(_format_requirements(inner))
    sections.append("")
    sections.append("## Constraints (CON-NNN)")
    sections.extend(_format_constraints(inner))
    sections.append("")
    sections.append("## Architecture decisions")
    sections.extend(_format_architecture(inner))
    sections.append("")
    sections.append("## Code architecture")
    sections.extend(_format_code_architecture(inner))
    sections.append("")
    sections.append("## Agents needed")
    sections.extend(_format_agents(inner))
    sections.append("")
    sections.append("## Wiki evidence")
    sections.extend(_wiki_evidence_lines(paths))
    sections.append("")
    sections.append("## Planner evidence context")
    sections.extend(_format_planner_evidence_context(paths, decision_log))
    sections.append("")
    sections.append("## Citation inventory")
    sections.extend(_format_citations(citation_index))
    sections.append("")
    sections.append("## Plan format")
    sections.append("")
    sections.append(
        "You MUST produce `decision-logs/implementation_plan_v"
        f"{decision_log_version}.md` with these `##` headings, in order, "
        "each non-empty:"
    )
    sections.append("")
    for heading in REQUIRED_PLAN_SECTIONS:
        sections.append(f"- {heading}")
    sections.append("")
    sections.append(
        "The **Capabilities** section MUST end with one fenced ```yaml``` block "
        "of the form:"
    )
    sections.append("")
    sections.append("```yaml")
    sections.append("capability_plan:")
    sections.append("  version: 2")
    sections.append("  capabilities:")
    sections.append("    - name: <slug>")
    sections.append("      phase: <short phase name>")
    sections.append("      objective: <one concrete outcome this capability achieves>")
    sections.append("      description: <one sentence>")
    sections.append("      requirement_ids: [REQ-NNN, ...]    # may be empty")
    sections.append("      constraint_ids: [CON-NNN, ...]     # may be empty")
    sections.append("      verification_required: true|false")
    sections.append("      composes: [<other capability names>]")
    sections.append("      explicit_triggers: [<domain-specific trigger phrase>, ...]")
    sections.append("      evidence_refs: [<finding_id or citation_id>, ...]")
    sections.append("      implementation_steps:")
    sections.append("        - <imperative implementation step>")
    sections.append("      acceptance_criteria:")
    sections.append("        - <observable pass/fail criterion>")
    sections.append("      parallelizable: true|false")
    sections.append("      rationale: <one sentence>")
    sections.append("```")
    sections.append("")
    sections.append("Capability rules:")
    sections.append("")
    sections.append(
        "- A capability may absorb multiple REQs (one cap covers REQ-001 + "
        "REQ-004) or a single REQ may split into multiple capabilities."
    )
    sections.append(
        "- Constraint-only capabilities (`requirement_ids: []`, "
        "`constraint_ids: [CON-...]`) are valid — they represent CI gates "
        "or runtime checks that enforce a CON-NNN."
    )
    sections.append(
        "- `verification_required: false` means Stage 3 will NOT generate a "
        "pytest stub for this capability. Use it when the capability is a "
        "tooling pin or policy fact rather than a behaviour to verify."
    )
    sections.append(
        "- Every REQ-NNN in the decision log MUST be covered by ≥1 "
        "capability's `requirement_ids`. Uncovered CONs warn but don't block."
    )
    sections.append(
        "- `composes` names other capabilities in this same plan. No "
        "self-loops, no dangling refs."
    )
    sections.append(
        "- For `capability_plan.version: 2`, each verification-required capability "
        "must include concrete `implementation_steps`, `acceptance_criteria`, "
        "`explicit_triggers`, and `evidence_refs`. Write the markdown plan as "
        "the human-readable step-by-step source of truth; the YAML is the "
        "machine-readable extract."
    )
    return "\n".join(sections) + "\n"


def run_plan_implementation_start(
    artifacts_root: Path,
    workspace_root: Path,
    decision_log_version: int | None = None,
) -> dict[str, Any]:
    """Stage 2.5 preflight: bundle the planning brief.

    Re-running overwrites brief.md but never touches
    `decision-logs/implementation_plan_v{N}.md`.
    """
    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    paths.plan_runtime_dir.mkdir(parents=True, exist_ok=True)

    version, dl_path, payload = resolve_decision_log(paths, decision_log_version)
    generated_at = iso_now()
    brief = render_planning_brief(
        paths=paths,
        decision_log=payload,
        decision_log_version=version,
        generated_at=generated_at,
    )
    paths.plan_brief_path.write_text(brief, encoding="utf-8")

    plan_path = paths.implementation_plan_path(version)
    return {
        "status": "ready_for_planner",
        "decision_log_version": version,
        "brief_path": str(paths.plan_brief_path.relative_to(paths.root).as_posix()),
        "plan_path": str(plan_path.relative_to(paths.root).as_posix()),
        "plan_exists": plan_path.exists(),
        "instruction": (
            "Invoke @implementation-planner next. It reads the brief, asks "
            "clarifying questions, and writes the markdown plan. Then run "
            "`meta-compiler plan-implementation --finalize`."
        ),
    }


# ---------------------------------------------------------------------------
# Postflight: parse + validate the plan markdown
# ---------------------------------------------------------------------------


def _split_plan_sections(text: str) -> dict[str, str]:
    """Map `##` headings to their body text. Headings outside REQUIRED_PLAN_SECTIONS
    are ignored. Body excludes the heading line itself but preserves blank lines.
    """
    lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            title = match.group("title").strip()
            current = title if title in REQUIRED_PLAN_SECTIONS else None
            if current is not None:
                sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _extract_capability_plan_block(capabilities_body: str) -> tuple[dict | None, list[str]]:
    """Find the fenced ```yaml``` block whose top-level key is `capability_plan`."""
    issues: list[str] = []
    matches = list(_FENCED_BLOCK_RE.finditer(capabilities_body))
    if not matches:
        issues.append("Capabilities section: missing fenced ```yaml``` block")
        return None, issues
    payload: dict[str, Any] | None = None
    for match in matches:
        body = match.group("body")
        try:
            parsed = load_yaml_string(body)
        except Exception as exc:  # noqa: BLE001
            issues.append(f"Capabilities section: YAML parse error ({exc})")
            continue
        if not isinstance(parsed, dict):
            continue
        if "capability_plan" in parsed:
            payload = parsed
            break
    if payload is None:
        issues.append(
            "Capabilities section: no fenced YAML block had `capability_plan:` "
            "as its top-level key"
        )
    return payload, issues


def load_yaml_string(text: str) -> Any:
    """Safe yaml.safe_load wrapper. Imports yaml at call time to keep the
    module import light."""
    import yaml

    return yaml.safe_load(text)


def _slug_re_check(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name))


def _non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and any(
        isinstance(item, str) and item.strip() for item in value
    )


def _string_list_issues(value: Any, field_name: str, prefix: str) -> list[str]:
    issues: list[str] = []
    if value is None:
        return issues
    if not isinstance(value, list):
        return [f"{prefix}.{field_name}: must be a list"]
    for item_idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(f"{prefix}.{field_name}[{item_idx}]: must be a non-empty string")
    return issues


def _validate_v2_capability_fields(
    cap: dict[str, Any],
    *,
    prefix: str,
    known_citations: set[str],
    decision_vocab: set[str],
) -> list[str]:
    issues: list[str] = []
    verification_required = cap.get("verification_required") is not False

    for field_name in (
        "implementation_steps",
        "acceptance_criteria",
        "explicit_triggers",
        "evidence_refs",
    ):
        issues.extend(_string_list_issues(cap.get(field_name), field_name, prefix))

    if "phase" in cap and not isinstance(cap.get("phase"), str):
        issues.append(f"{prefix}.phase: must be a string when provided")
    if "objective" in cap and not isinstance(cap.get("objective"), str):
        issues.append(f"{prefix}.objective: must be a string when provided")
    if "parallelizable" in cap and not isinstance(cap.get("parallelizable"), bool):
        issues.append(f"{prefix}.parallelizable: must be a boolean when provided")

    if not verification_required:
        return issues

    for field_name in (
        "implementation_steps",
        "acceptance_criteria",
        "explicit_triggers",
        "evidence_refs",
    ):
        if not _non_empty_string_list(cap.get(field_name)):
            issues.append(
                f"{prefix}.{field_name}: must include at least one concrete entry "
                "when verification_required is true"
            )

    trigger_values = [
        str(item).strip()
        for item in cap.get("explicit_triggers") or []
        if isinstance(item, str) and item.strip()
    ]
    effective_vocab = decision_vocab or set()
    for trigger_idx, trigger in enumerate(trigger_values):
        tokens = trigger_content_tokens(trigger)
        if not tokens:
            issues.append(
                f"{prefix}.explicit_triggers[{trigger_idx}]: must contain at least one "
                "domain noun after stop-word stripping"
            )
            continue
        if effective_vocab and not (tokens & effective_vocab):
            issues.append(
                f"{prefix}.explicit_triggers[{trigger_idx}]: {trigger!r} does not overlap "
                "the decision-log trigger vocabulary"
            )

    evidence_refs = [
        str(item).strip()
        for item in cap.get("evidence_refs") or []
        if isinstance(item, str) and item.strip()
    ]
    for ref_idx, ref in enumerate(evidence_refs):
        citation_id = ref.split("#", 1)[0]
        if known_citations and citation_id not in known_citations:
            issues.append(
                f"{prefix}.evidence_refs[{ref_idx}]: {ref!r} does not resolve to a "
                "citation used by the decision log"
            )
    return issues


def validate_plan_extract(
    payload: dict[str, Any],
    *,
    decision_log: dict[str, Any],
) -> list[str]:
    """Validate the `capability_plan` block extracted from the plan markdown.

    Decision log payload should be the inner `decision_log:` dict (resolved
    via `_decision_log_utils.resolve_decision_log`). Returns a list of issue
    strings; empty list means the plan extract is well-formed.
    """
    issues: list[str] = []
    plan = payload.get("capability_plan")
    if not isinstance(plan, dict):
        return ["plan_extract: missing `capability_plan` root object"]

    capabilities = plan.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        issues.append("plan_extract.capability_plan.capabilities: must be a non-empty list")
        return issues
    try:
        plan_version = int(plan.get("version") or 1)
    except (TypeError, ValueError):
        issues.append("plan_extract.capability_plan.version: must be an integer when provided")
        plan_version = 1

    inner = decision_log.get("decision_log") if "decision_log" in decision_log else decision_log
    if not isinstance(inner, dict):
        return ["plan_extract: decision log payload missing root"]
    known_citations = _known_citation_ids(inner)
    decision_vocab = decision_log_vocabulary({"decision_log": inner})

    valid_req_ids = {
        str(row.get("id"))
        for row in inner.get("requirements") or []
        if isinstance(row, dict) and row.get("id")
    }
    valid_con_ids = {
        str(row.get("id"))
        for row in inner.get("constraints") or []
        if isinstance(row, dict) and row.get("id")
    }

    seen_names: set[str] = set()
    cap_by_name: dict[str, dict[str, Any]] = {}
    covered_req_ids: set[str] = set()
    for idx, cap in enumerate(capabilities):
        prefix = f"plan_extract.capabilities[{idx}]"
        if not isinstance(cap, dict):
            issues.append(f"{prefix}: must be an object")
            continue
        name = cap.get("name")
        if not isinstance(name, str) or not _slug_re_check(name):
            issues.append(
                f"{prefix}.name: must be a slug-style string (lowercase, dash-separated)"
            )
        elif name in seen_names:
            issues.append(f"{prefix}.name: duplicate {name!r}")
        else:
            seen_names.add(name)
            cap_by_name[name] = cap

        description = cap.get("description")
        if not isinstance(description, str) or not description.strip():
            issues.append(f"{prefix}.description: must be a non-empty string")
        rationale = cap.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            issues.append(f"{prefix}.rationale: must be a non-empty string")
        verification_required = cap.get("verification_required")
        if not isinstance(verification_required, bool):
            issues.append(f"{prefix}.verification_required: must be a boolean")

        req_ids = cap.get("requirement_ids") or []
        if not isinstance(req_ids, list):
            issues.append(f"{prefix}.requirement_ids: must be a list")
        else:
            for r_idx, rid in enumerate(req_ids):
                if not isinstance(rid, str):
                    issues.append(f"{prefix}.requirement_ids[{r_idx}]: must be a string")
                    continue
                if rid not in valid_req_ids:
                    issues.append(
                        f"{prefix}.requirement_ids[{r_idx}]: {rid!r} not in decision log"
                    )
                else:
                    covered_req_ids.add(rid)

        con_ids = cap.get("constraint_ids") or []
        if not isinstance(con_ids, list):
            issues.append(f"{prefix}.constraint_ids: must be a list")
        else:
            for c_idx, cid in enumerate(con_ids):
                if not isinstance(cid, str):
                    issues.append(f"{prefix}.constraint_ids[{c_idx}]: must be a string")
                    continue
                if cid not in valid_con_ids:
                    issues.append(
                        f"{prefix}.constraint_ids[{c_idx}]: {cid!r} not in decision log"
                    )

        composes = cap.get("composes") or []
        if not isinstance(composes, list):
            issues.append(f"{prefix}.composes: must be a list")

        if plan_version >= 2:
            issues.extend(
                _validate_v2_capability_fields(
                    cap,
                    prefix=prefix,
                    known_citations=known_citations,
                    decision_vocab=decision_vocab,
                )
            )

    # Two-pass: composes must point to other declared cap names, no self-loops.
    for idx, cap in enumerate(capabilities):
        if not isinstance(cap, dict):
            continue
        name = cap.get("name")
        composes = cap.get("composes") or []
        if not isinstance(composes, list):
            continue
        for c_idx, comp in enumerate(composes):
            prefix = f"plan_extract.capabilities[{idx}].composes[{c_idx}]"
            if not isinstance(comp, str):
                issues.append(f"{prefix}: must be a string")
                continue
            if comp not in cap_by_name:
                issues.append(f"{prefix}: {comp!r} is not another capability's name")
            if comp == name:
                issues.append(f"{prefix}: self-loop detected")

    # Every REQ-NNN must be covered by at least one capability.
    for missing in sorted(valid_req_ids - covered_req_ids):
        issues.append(
            f"plan_extract: requirement {missing} is not covered by any capability "
            "(every REQ must appear in >=1 capability's requirement_ids)"
        )

    return issues


def parse_plan_markdown(
    text: str, decision_log: dict[str, Any]
) -> tuple[dict | None, list[str]]:
    """Validate the plan markdown structure + extract the capability_plan block.

    Returns (extracted_plan, issues). When issues is non-empty, the plan
    extract should NOT be persisted.
    """
    issues: list[str] = []
    sections = _split_plan_sections(text)

    for required in REQUIRED_PLAN_SECTIONS:
        if required not in sections:
            issues.append(f"plan markdown: missing required section '## {required}'")
        elif not sections[required].strip():
            issues.append(f"plan markdown: section '## {required}' is empty")

    if "Capabilities" not in sections:
        return None, issues

    extracted, block_issues = _extract_capability_plan_block(sections["Capabilities"])
    issues.extend(block_issues)
    if extracted is None:
        return None, issues

    schema_issues = validate_plan_extract(extracted, decision_log=decision_log)
    issues.extend(schema_issues)
    return extracted, issues


def run_plan_implementation_finalize(
    artifacts_root: Path,
    workspace_root: Path,
    decision_log_version: int | None = None,
) -> dict[str, Any]:
    """Stage 2.5 postflight: validate plan markdown + extract capability_plan.

    Writes `decision-logs/plan_extract_v{N}.yaml` for Stage 3 to consume.
    """
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    version, _dl_path, payload = resolve_decision_log(paths, decision_log_version)
    plan_path = paths.implementation_plan_path(version)
    if not plan_path.exists():
        raise FileNotFoundError(
            f"Implementation plan markdown missing: {plan_path}. "
            "Invoke @implementation-planner to write it."
        )

    text = plan_path.read_text(encoding="utf-8")
    extracted, issues = parse_plan_markdown(text, decision_log=payload)
    if issues:
        raise RuntimeError(
            "Implementation plan validation failed:\n  - " + "\n  - ".join(issues)
        )
    assert extracted is not None  # validator returns None only with issues

    plan = extracted["capability_plan"]
    extract_payload = {
        "plan_extract": {
            "generated_at": iso_now(),
            "decision_log_version": version,
            "source": str(plan_path.relative_to(paths.root).as_posix()),
            "version": plan.get("version", 1),
            "capabilities": plan.get("capabilities", []),
        }
    }
    extract_path = paths.plan_extract_path(version)
    extract_path.parent.mkdir(parents=True, exist_ok=True)
    dump_yaml(extract_path, extract_payload)

    return {
        "status": "extracted",
        "decision_log_version": version,
        "plan_path": str(plan_path.relative_to(paths.root).as_posix()),
        "extract_path": str(extract_path.relative_to(paths.root).as_posix()),
        "capability_count": len(plan.get("capabilities", []) or []),
    }
