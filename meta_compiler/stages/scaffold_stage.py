from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from ..artifacts import (
    build_paths,
    ensure_layout,
    latest_decision_log_path,
    load_manifest,
    save_manifest,
)
from ..io import dump_yaml, load_yaml, render_frontmatter
from ..utils import iso_now, slugify
from ..validation import validate_decision_log


def _resolve_decision_log(paths, decision_log_version: int | None) -> tuple[int, Path, dict[str, Any]]:
    if decision_log_version is None:
        latest = latest_decision_log_path(paths)
        if latest is None:
            raise RuntimeError("No decision log found. Run elicit-vision first.")
        version, path = latest
    else:
        version = decision_log_version
        path = paths.decision_logs_dir / f"decision_log_v{version}.yaml"
        if not path.exists():
            raise RuntimeError(f"Decision log not found at {path}")

    payload = load_yaml(path)
    if not payload:
        raise RuntimeError(f"Decision log is empty: {path}")

    issues = validate_decision_log(payload)
    if issues:
        raise RuntimeError("Decision Log validation failed:\n" + "\n".join(issues))

    return version, path, payload


def _ensure_scaffold_layout(scaffold_root: Path, project_type: str) -> dict[str, Path]:
    dirs = {
        "root": scaffold_root,
        "agents": scaffold_root / "agents",
        "docs": scaffold_root / "docs",
        "skills": scaffold_root / "docs" / "skills",
        "instructions": scaffold_root / "docs" / "instructions",
        "requirements": scaffold_root / "requirements",
        "customizations": scaffold_root / ".github",
        "custom_agents": scaffold_root / ".github" / "agents",
        "custom_skills": scaffold_root / ".github" / "skills",
        "custom_instructions": scaffold_root / ".github" / "instructions",
        "orchestrator": scaffold_root / "orchestrator",
    }

    if project_type in {"algorithm", "hybrid"}:
        dirs["code"] = scaffold_root / "code"
        dirs["tests"] = scaffold_root / "tests"
    if project_type in {"report", "hybrid"}:
        dirs["report"] = scaffold_root / "report"
        dirs["references"] = scaffold_root / "references"

    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    return dirs


def _render_architecture_doc(decision_log: dict[str, Any], version: int) -> str:
    root = decision_log["decision_log"]
    meta = root["meta"]
    lines = [
        "# ARCHITECTURE",
        "",
        f"Decision Log Version: v{version}",
        f"Project: {meta.get('project_name')}",
        f"Project Type: {meta.get('project_type')}",
        "",
        "## Components",
    ]

    architecture = root.get("architecture", [])
    if architecture:
        for row in architecture:
            lines.append("")
            lines.append(f"### {row.get('component')}")
            lines.append(f"- Approach: {row.get('approach')}")
            constraints = row.get("constraints_applied", [])
            lines.append(f"- Constraints: {', '.join(constraints) if constraints else 'None'}")
            citations = row.get("citations", [])
            lines.append(f"- Citations: {', '.join(citations) if citations else 'None'}")
    else:
        lines.append("- No architecture components captured.")

    lines.extend(
        [
            "",
            "## Execution Notes",
            "- This scaffold is generated strictly from Decision Log data.",
            "- Any change in decisions should trigger a new scaffold version.",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_conventions_doc(decision_log: dict[str, Any]) -> str:
    conventions = decision_log["decision_log"].get("conventions", [])
    lines = ["# CONVENTIONS", ""]

    if not conventions:
        lines.append("No conventions captured in Decision Log.")
        return "\n".join(lines) + "\n"

    for row in conventions:
        lines.append(f"## {row.get('name')}")
        lines.append(f"- Domain: {row.get('domain')}")
        lines.append(f"- Choice: {row.get('choice')}")
        lines.append(f"- Rationale: {row.get('rationale')}")
        citations = row.get("citations", [])
        lines.append(f"- Citations: {', '.join(citations) if citations else 'None'}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_requirements_doc(decision_log: dict[str, Any]) -> str:
    requirements = decision_log["decision_log"].get("requirements", [])
    lines = ["# REQUIREMENTS_TRACED", ""]

    if not requirements:
        lines.append("No requirements captured in Decision Log.")
        return "\n".join(lines) + "\n"

    for row in requirements:
        lines.append(f"## {row.get('id')}")
        lines.append(f"- Description: {row.get('description')}")
        lines.append(f"- Source: {row.get('source')}")
        lines.append(f"- Verification: {row.get('verification')}")
        citations = row.get("citations", [])
        lines.append(f"- Citations: {', '.join(citations) if citations else 'None'}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        candidate = str(item).strip()
        if candidate:
            items.append(candidate)
    return items


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _merge_ordered(left: list[str], right: list[str]) -> list[str]:
    return _ordered_unique(left + right)


def _collect_constraints(root: dict[str, Any]) -> list[str]:
    constraints: list[str] = []
    for row in root.get("architecture", []):
        if not isinstance(row, dict):
            continue
        constraints.extend(_as_string_list(row.get("constraints_applied", [])))
    return _ordered_unique(constraints)


def _collect_citation_ids(root: dict[str, Any]) -> list[str]:
    citations: list[str] = []
    for section_name in ["conventions", "architecture", "requirements"]:
        for row in root.get(section_name, []):
            if not isinstance(row, dict):
                continue
            citations.extend(_as_string_list(row.get("citations", [])))
    return _ordered_unique(citations)


def _canonical_agents(project_type: str, root: dict[str, Any]) -> list[dict[str, Any]]:
    global_constraints = _collect_constraints(root)
    canonical: list[dict[str, Any]] = [
        {
            "role": "scaffold-generator",
            "responsibility": "Generate project structure and traceable artifacts from the Decision Log.",
            "reads": ["decision_log"],
            "writes": ["scaffold", "agents", "docs", "requirements"],
            "key_constraints": _merge_ordered(
                [
                    "input is Decision Log only",
                    "do not read wiki or raw sources",
                    "trace outputs to requirement and citation IDs",
                ],
                global_constraints,
            ),
        }
    ]

    if project_type in {"algorithm", "hybrid"}:
        canonical.extend(
            [
                {
                    "role": "algorithm-implementer",
                    "responsibility": "Translate the Decision Log's architecture, data model, and requirements into a working implementation under code/, with tests/ exercising each requirement ID.",
                    "reads": ["decision_log", "architecture", "requirements", "conventions"],
                    "writes": ["code", "tests"],
                    "key_constraints": _merge_ordered(
                        [
                            "write executable Python, not markdown placeholders",
                            "replace the scaffold stub in code/main.py with the real implementation",
                            "every public function must trace to a requirement ID",
                            "add tests/ coverage for each requirement in REQ_TRACE_MATRIX.md",
                        ],
                        global_constraints,
                    ),
                },
                {
                    "role": "math-conventions-agent",
                    "responsibility": "Normalize mathematical notation and assumptions across generated code and docs.",
                    "reads": ["decision_log", "conventions", "requirements"],
                    "writes": ["docs", "code", "tests"],
                    "key_constraints": _merge_ordered(
                        ["use only approved math conventions", "avoid introducing uncited formalisms"],
                        global_constraints,
                    ),
                },
                {
                    "role": "scope-reduction-agent",
                    "responsibility": "Remove work outside explicit in-scope decisions before implementation starts.",
                    "reads": ["decision_log", "scope", "requirements", "architecture"],
                    "writes": ["docs", "code"],
                    "key_constraints": _merge_ordered(
                        ["treat out-of-scope items as veto unless revised in Stage 2"],
                        global_constraints,
                    ),
                },
            ]
        )

    if project_type in {"report", "hybrid"}:
        canonical.extend(
            [
                {
                    "role": "report-writer",
                    "responsibility": "Draft report/OUTLINE.md and report/DRAFT.md from the Decision Log's architecture and requirements, grounding every claim in the citation index.",
                    "reads": ["decision_log", "architecture", "requirements", "conventions"],
                    "writes": ["report", "docs"],
                    "key_constraints": _merge_ordered(
                        [
                            "produce a full draft, not frontmatter-only stubs",
                            "every section cites an ID resolvable via wiki/citations/index.yaml",
                            "cover every requirement ID declared in REQ_TRACE_MATRIX.md",
                            "outline sections must match the architecture decomposition in the Decision Log",
                        ],
                        global_constraints,
                    ),
                },
                {
                    "role": "citation-manager-agent",
                    "responsibility": "Maintain citation inventory and source traceability for report outputs.",
                    "reads": ["decision_log", "requirements", "conventions"],
                    "writes": ["references", "report"],
                    "key_constraints": _merge_ordered(
                        ["preserve citation IDs exactly as recorded"],
                        global_constraints,
                    ),
                },
                {
                    "role": "style-conventions-agent",
                    "responsibility": "Apply writing and terminology conventions consistently across report drafts.",
                    "reads": ["decision_log", "conventions", "scope"],
                    "writes": ["docs", "report"],
                    "key_constraints": _merge_ordered(
                        ["do not override constraints captured in architecture decisions"],
                        global_constraints,
                    ),
                },
                {
                    "role": "narrative-structure-agent",
                    "responsibility": "Map architecture decisions and requirements into a coherent report narrative.",
                    "reads": ["decision_log", "architecture", "requirements"],
                    "writes": ["report", "docs"],
                    "key_constraints": _merge_ordered(
                        ["cover all requirement IDs in narrative plan"],
                        global_constraints,
                    ),
                },
            ]
        )

    return canonical


def _normalize_agent_row(row: dict[str, Any], fallback_role: str) -> dict[str, Any]:
    role = str(row.get("role") or fallback_role).strip() or fallback_role
    return {
        "role": role,
        "responsibility": str(row.get("responsibility") or "No responsibility specified.").strip(),
        "reads": _as_string_list(row.get("reads", [])),
        "writes": _as_string_list(row.get("writes", [])),
        "key_constraints": _as_string_list(row.get("key_constraints", [])),
    }


def _merged_agents(project_type: str, root: dict[str, Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def upsert(candidate: dict[str, Any], prefer_candidate: bool) -> None:
        normalized = _normalize_agent_row(candidate, fallback_role="agent")
        key = slugify(normalized["role"]) or normalized["role"].lower()
        existing = merged.get(key)
        if existing is None:
            merged[key] = normalized
            order.append(key)
            return

        existing["reads"] = _merge_ordered(existing.get("reads", []), normalized.get("reads", []))
        existing["writes"] = _merge_ordered(existing.get("writes", []), normalized.get("writes", []))
        existing["key_constraints"] = _merge_ordered(
            existing.get("key_constraints", []),
            normalized.get("key_constraints", []),
        )

        if prefer_candidate and normalized.get("responsibility"):
            existing["responsibility"] = normalized["responsibility"]
            existing["role"] = normalized["role"]

    for row in _canonical_agents(project_type, root):
        upsert(row, prefer_candidate=False)

    for idx, row in enumerate(root.get("agents_needed", []), start=1):
        if not isinstance(row, dict):
            continue
        if "role" not in row:
            row = dict(row)
            row["role"] = f"agent-{idx}"
        upsert(row, prefer_candidate=True)

    return [merged[key] for key in order]


def _write_agent_specs(
    agents_dir: Path,
    custom_agents_dir: Path,
    decision_log: dict[str, Any],
    version: int,
    project_type: str,
) -> int:
    root = decision_log["decision_log"]
    agents = _merged_agents(project_type=project_type, root=root)

    architecture = [row for row in root.get("architecture", []) if isinstance(row, dict)]
    conventions = [row for row in root.get("conventions", []) if isinstance(row, dict)]
    requirements = [row for row in root.get("requirements", []) if isinstance(row, dict)]
    citations = _collect_citation_ids(root)

    _clear_directory_files(agents_dir, "*.md")
    _clear_directory_files(custom_agents_dir, "*.agent.md")

    for idx, row in enumerate(agents, start=1):
        role = str(row.get("role", f"agent-{idx}"))
        slug = slugify(role) or f"agent-{idx}"
        path = agents_dir / f"{slug}.md"
        custom_path = custom_agents_dir / f"{slug}.agent.md"

        content_lines = [
            f"# Agent Spec: {role}",
            "",
            f"Decision Log Version: v{version}",
            f"Project Type: {project_type}",
            f"Responsibility: {row.get('responsibility')}",
            "",
            "## Reads",
        ]
        reads = row.get("reads", [])
        if isinstance(reads, list) and reads:
            content_lines.extend([f"- {item}" for item in reads])
        else:
            content_lines.append("- None")

        content_lines.append("")
        content_lines.append("## Writes")
        writes = row.get("writes", [])
        if isinstance(writes, list) and writes:
            content_lines.extend([f"- {item}" for item in writes])
        else:
            content_lines.append("- None")

        content_lines.append("")
        content_lines.append("## Key Constraints")
        constraints = row.get("key_constraints", [])
        if isinstance(constraints, list) and constraints:
            content_lines.extend([f"- {item}" for item in constraints])
        else:
            content_lines.append("- None")

        content_lines.append("")
        content_lines.append("## Decisions Embedded")
        if architecture:
            for component in architecture[:8]:
                component_name = component.get("component", "component")
                approach = component.get("approach", "unspecified")
                refs = _as_string_list(component.get("citations", []))
                content_lines.append(
                    f"- Architecture: {component_name} -> {approach}"
                    + (f" (citations: {', '.join(refs)})" if refs else "")
                )
        else:
            content_lines.append("- No architecture decisions recorded.")

        if conventions:
            for convention in conventions[:8]:
                refs = _as_string_list(convention.get("citations", []))
                content_lines.append(
                    f"- Convention ({convention.get('domain')}): {convention.get('choice')}"
                    + (f" (citations: {', '.join(refs)})" if refs else "")
                )
        else:
            content_lines.append("- No conventions captured.")

        content_lines.append("")
        content_lines.append("## Requirement Trace")
        if requirements:
            for requirement in requirements:
                req_id = requirement.get("id", "REQ-UNK")
                description = requirement.get("description", "")
                verification = requirement.get("verification", "")
                content_lines.append(f"- {req_id}: {description}")
                if verification:
                    content_lines.append(f"  Verification: {verification}")
        else:
            content_lines.append("- No requirements captured.")

        content_lines.append("")
        content_lines.append("## Citation Anchors")
        if citations:
            for citation_id in citations:
                content_lines.append(f"- {citation_id}")
        else:
            content_lines.append("- No citation IDs captured in Decision Log.")

        content_lines.append("")
        content_lines.append("## Stage 3 Guardrails")
        content_lines.append("- Input is Decision Log only; do not consume wiki or raw sources.")
        content_lines.append("- Preserve scope boundaries unless Stage 2 issues a revised decision log.")
        content_lines.append("- Generated from Decision Log entries; update via Stage 2 re-entry if needed.")

        path.write_text("\n".join(content_lines) + "\n", encoding="utf-8")

        custom_frontmatter = {
            "name": slug,
            "description": _agent_description(role, row, project_type=project_type),
            "tools": _infer_agent_tools(row),
            "agents": _default_subagent_allowlist(),
            "user-invocable": False,
        }
        custom_body_lines = [
            f"You are the {role} execution agent for scaffold version v{version}.",
            "",
            "## Purpose",
            str(row.get("responsibility") or "No responsibility specified."),
            "",
            "## Inputs",
        ]

        if isinstance(reads, list) and reads:
            custom_body_lines.extend([f"- {item}" for item in reads])
        else:
            custom_body_lines.append("- None")

        custom_body_lines.extend(["", "## Outputs"])
        if isinstance(writes, list) and writes:
            custom_body_lines.extend([f"- {item}" for item in writes])
        else:
            custom_body_lines.append("- None")

        custom_body_lines.extend(["", "## Constraints"])
        if isinstance(constraints, list) and constraints:
            custom_body_lines.extend([f"- {item}" for item in constraints])
        else:
            custom_body_lines.append("- None")
        custom_body_lines.extend(
            [
                "- Input is the Decision Log and scaffold artifacts only.",
                "- Preserve requirement IDs and citation IDs exactly as recorded.",
                "- Use the 'explore' subagent for fast local discovery and narrow searches.",
                "- Use the 'research' subagent for deeper multi-source investigation and synthesis.",
                "- Escalate missing decisions to Stage 2 instead of improvising.",
                "",
                "## Decision Trace",
            ]
        )

        if architecture:
            for component in architecture[:5]:
                component_name = component.get("component", "component")
                approach = component.get("approach", "unspecified")
                custom_body_lines.append(f"- Architecture: {component_name} -> {approach}")
        if conventions:
            for convention in conventions[:5]:
                custom_body_lines.append(
                    f"- Convention ({convention.get('domain')}): {convention.get('choice')}"
                )
        if requirements:
            for requirement in requirements[:8]:
                custom_body_lines.append(
                    f"- {requirement.get('id', 'REQ-UNK')}: {requirement.get('description', '')}"
                )
        if not architecture and not conventions and not requirements:
            custom_body_lines.append("- No decision trace entries captured.")

        custom_path.write_text(
            _markdown_with_frontmatter(custom_frontmatter, custom_body_lines),
            encoding="utf-8",
        )

    return len(agents)


def _infer_output_kind(writes: list[str], responsibility: str) -> str:
    writes_set = {str(w).lower() for w in writes}
    if writes_set & {"code", "tests"}:
        return "code"
    if writes_set & {"report", "references", "docs", "documentation"}:
        return "document"
    lowered = responsibility.lower()
    if any(token in lowered for token in ("code", "test", "algorithm", "implementation")):
        return "code"
    if any(token in lowered for token in ("report", "draft", "outline", "narrative", "document")):
        return "document"
    return "artifact"


def _write_ralph_loop_agents(
    custom_agents_dir: Path,
    custom_instructions_dir: Path,
    decision_log: dict[str, Any],
    version: int,
    project_type: str,
) -> dict[str, int]:
    """Emit one reviewer per implementer plus a shared execution-orchestrator.

    This gives every writing/editing agent a fresh-context reviewer that
    verifies the work against Decision Log constraints and requirement traces
    before the orchestrator accepts the change. The reviewer contract is
    format-agnostic: the reviewer chooses the validator (tests+typecheck for
    code, markdown-lint+schema for documents) based on `output_kind`.
    """
    root = decision_log["decision_log"]
    agents = _merged_agents(project_type=project_type, root=root)

    reviewers_written = 0
    for idx, row in enumerate(agents, start=1):
        role = str(row.get("role", f"agent-{idx}"))
        slug = slugify(role) or f"agent-{idx}"
        writes = _as_string_list(row.get("writes", []))
        reads = _as_string_list(row.get("reads", []))
        output_kind = _infer_output_kind(writes, str(row.get("responsibility", "")))

        reviewer_frontmatter = {
            "name": f"{slug}-reviewer",
            "description": (
                f"Fresh-context reviewer for the {role} implementer. "
                f"Validates {output_kind} output against decision log constraints, requirement traceability, "
                "and citation fidelity. Returns PASS or REVISE with actionable gaps."
            ),
            "tools": ["read", "search"],
            "agents": [],
            "user-invocable": False,
            "argument-hint": f"Path to the artifact produced by {slug}",
        }
        reviewer_body = [
            f"You are the {role} reviewer. Fresh context. You did not write the artifact you are reviewing.",
            "",
            "## Role",
            f"Validate the latest output from the `{slug}` implementer against the Decision Log, requirement trace matrix, and scaffold guardrails. Return PASS when the artifact meets every gate, REVISE otherwise with a concrete list of gaps.",
            "",
            "## Output Kind",
            f"- {output_kind}",
            "",
            "## Validation Gates",
        ]
        if output_kind == "code":
            reviewer_body.extend([
                "- Unit tests referenced in REQUIREMENTS_TRACED.md exist and pass.",
                "- Type checker (mypy/pyright) reports zero new errors on the modified files.",
                "- Every requirement ID in `requirements/REQ_TRACE_MATRIX.md` that applies to this artifact has a corresponding assertion or test.",
                "- Citations referenced in code comments resolve to `workspace-artifacts/wiki/citations/index.yaml`.",
            ])
        elif output_kind == "document":
            reviewer_body.extend([
                "- Markdown is well-formed and follows CONVENTIONS.md.",
                "- Every claim is backed by a citation ID that resolves to `workspace-artifacts/wiki/citations/index.yaml`.",
                "- Section headings match the outline declared in the Decision Log or scaffold OUTLINE.md.",
                "- Requirement IDs in `requirements/REQ_TRACE_MATRIX.md` that apply to this artifact are explicitly traced.",
            ])
        else:
            reviewer_body.extend([
                "- Artifact is syntactically valid for its declared format.",
                "- Requirement IDs applicable to this artifact are traced.",
                "- Citations referenced resolve to the canonical citation index.",
                "- Scaffold guardrails (no chat-history-only decisions, no orphaned outputs) hold.",
            ])
        reviewer_body.extend([
            "",
            "## Contract",
            "Return exactly one JSON object with this shape:",
            "```json",
            "{",
            '  "verdict": "PASS | REVISE",',
            '  "output_kind": "' + output_kind + '",',
            '  "checked_requirements": ["REQ-NNN", ...],',
            '  "blocking_gaps": ["string", ...],',
            '  "non_blocking_gaps": ["string", ...],',
            '  "proposed_fixes": ["string", ...]',
            "}",
            "```",
            "",
            "## Inputs",
        ])
        if reads:
            reviewer_body.extend([f"- {item}" for item in reads])
        else:
            reviewer_body.append("- Decision Log and scaffold artifacts only")
        reviewer_body.extend([
            "",
            "## Constraints",
            "- DO NOT modify the artifact — audit only.",
            "- DO NOT approve when any blocking gap is present.",
            "- DO NOT invent requirement IDs or citations.",
            f"- Decision log version under review: v{version}.",
        ])

        reviewer_path = custom_agents_dir / f"{slug}-reviewer.agent.md"
        reviewer_path.write_text(
            _markdown_with_frontmatter(reviewer_frontmatter, reviewer_body),
            encoding="utf-8",
        )
        reviewers_written += 1

    orchestrator_frontmatter = {
        "name": "execution-orchestrator",
        "description": (
            "Run the scaffold Stage 4 ralph loop: pick an implementer from the registry, "
            "invoke it, invoke its reviewer, revise until PASS, then advance. "
            "Terminates when all registry entries are resolved or after the cycle cap."
        ),
        "tools": ["read", "search", "edit", "execute", "agent", "todo"],
        "agents": ["*"],
        "user-invocable": True,
        "argument-hint": "Optional: specific agent slug to run, otherwise walks the full registry",
    }
    orchestrator_body = [
        "You are the scaffold Execution Orchestrator.",
        "",
        "## Responsibility",
        "Drive the ralph loop for every implementer in `AGENT_REGISTRY.yaml`:",
        "1. Load `AGENT_REGISTRY.yaml` and build a dependency DAG using each agent's `inputs` and `outputs`.",
        "2. Walk the DAG in topological order. For each implementer:",
        "   a. Invoke the implementer with its scoped wiki brief and the current decision log.",
        "   b. Invoke the matching `<slug>-reviewer` agent in fresh context against the produced artifact.",
        "   c. If `verdict: PASS`, mark the registry entry `status: completed` and advance.",
        "   d. If `verdict: REVISE` and `cycle < 3`, feed the reviewer's `blocking_gaps` and `proposed_fixes` back to the implementer. Increment cycle.",
        "   e. If `cycle == 3`, force-advance and log an `open_item` in the execution manifest.",
        "3. Write `executions/v<N>/ralph_loop_log.yaml` summarising cycles, verdicts, and unresolved gaps.",
        "",
        "## Constraints",
        "- DO NOT skip the reviewer step — every implementer output must be reviewed in fresh context.",
        "- DO NOT exceed 3 revision cycles per agent.",
        "- DO NOT invent registry entries; only dispatch to agents that appear in `AGENT_REGISTRY.yaml`.",
        "- DO pass the agent's declared `scoped_wiki_brief` paths, not the whole wiki.",
        "",
        "## Inputs",
        "- `AGENT_REGISTRY.yaml` (scaffold root)",
        "- `EXECUTION_MANIFEST.yaml`",
        "- `requirements/REQ_TRACE_MATRIX.md`",
        "- `workspace-artifacts/decision-logs/decision_log_v<N>.yaml`",
        "",
        "## Outputs",
        "- `executions/v<N>/ralph_loop_log.yaml`",
        "- `executions/v<N>/FINAL_OUTPUT_MANIFEST.yaml` (already written by run_stage4.py)",
    ]
    (custom_agents_dir / "execution-orchestrator.agent.md").write_text(
        _markdown_with_frontmatter(orchestrator_frontmatter, orchestrator_body),
        encoding="utf-8",
    )

    ralph_instruction = {
        "description": "Ralph loop protocol for scaffold implementers",
    }
    ralph_body = [
        "# Ralph Loop Instructions",
        "",
        "Every implementer in this scaffold follows the orchestrator -> implement -> review -> loop pattern.",
        "",
        "## Pattern",
        "",
        "1. **Orchestrator** reads `AGENT_REGISTRY.yaml`, picks the next unblocked implementer, and invokes it with a scoped brief.",
        "2. **Implementer** produces its declared output artifact, writing only to paths it owns in the registry.",
        "3. **Reviewer** (fresh context) validates the artifact against decision log constraints, requirement trace, and citation fidelity. Returns PASS or REVISE.",
        "4. On REVISE, orchestrator feeds `blocking_gaps` and `proposed_fixes` back to the implementer. Max 3 cycles.",
        "5. On PASS, registry entry is marked `completed` and the orchestrator advances.",
        "",
        "## Format-agnostic Review",
        "",
        "Reviewers pick their validator based on the implementer's `output_kind`:",
        "",
        "- `code` — unit tests + type checker + requirement trace.",
        "- `document` — markdown lint + citation resolution + outline compliance + requirement trace.",
        "- `artifact` — format-specific syntactic checks + requirement trace.",
        "",
        "## Termination",
        "",
        "- Every registry entry reaches `status: completed` or `status: force-advanced`.",
        "- Force-advanced entries are logged in `executions/v<N>/ralph_loop_log.yaml` and in the Decision Log `open_items` list on the next Stage 2 re-entry.",
    ]
    (custom_instructions_dir / "ralph-loop.instructions.md").write_text(
        "---\n" + render_frontmatter(ralph_instruction) + "\n---\n" + "\n".join(ralph_body).rstrip() + "\n",
        encoding="utf-8",
    )

    return {
        "reviewers_written": reviewers_written,
        "orchestrator_written": 1,
        "instruction_written": 1,
    }


def _infer_agent_triggers(row: dict[str, Any], project_type: str) -> list[str]:
    triggers: list[str] = []
    writes = {str(w).lower() for w in _as_string_list(row.get("writes", []))}
    role = str(row.get("role", "")).lower()
    responsibility = str(row.get("responsibility", "")).lower()

    if writes & {"code", "tests"}:
        triggers.append("implementation_scope_code")
    if writes & {"report", "references"}:
        triggers.append("implementation_scope_report")
    if "citation" in role or "citation" in responsibility:
        triggers.append("citation_touch")
    if "review" in role or "audit" in role:
        triggers.append("artifact_ready_for_review")
    if "scaffold" in role:
        triggers.append("decision_log_updated")
    if not triggers:
        triggers.append(f"project_type_{project_type}")
    return triggers


def _infer_scoped_wiki_brief(row: dict[str, Any]) -> list[str]:
    brief: list[str] = []
    role = str(row.get("role", "")).lower()
    responsibility = str(row.get("responsibility", "")).lower()
    keywords = set()
    for token in re.findall(r"[a-z]{4,}", role + " " + responsibility):
        keywords.add(token)
    for keyword in sorted(keywords):
        brief.append(f"workspace-artifacts/wiki/v2/pages/{keyword}.md")
        if len(brief) >= 4:
            break
    brief.append("workspace-artifacts/wiki/citations/index.yaml")
    return brief


def _write_agent_registry(
    scaffold_root: Path,
    decision_log: dict[str, Any],
    project_type: str,
    version: int,
) -> tuple[Path, int]:
    """Emit AGENT_REGISTRY.yaml declaring every scaffolded agent's IO + triggers.

    The registry is what the execution-orchestrator walks at runtime. Each
    entry captures inputs (so the DAG can be built), outputs (so ownership
    never overlaps), capabilities (tools), triggers_when (predicates over the
    current work item), and scoped_wiki_brief (the subset of the wiki the
    agent needs).
    """
    root = decision_log["decision_log"]
    agents = _merged_agents(project_type=project_type, root=root)

    entries: list[dict[str, Any]] = []
    for idx, row in enumerate(agents, start=1):
        role = str(row.get("role", f"agent-{idx}"))
        slug = slugify(role) or f"agent-{idx}"
        writes = _as_string_list(row.get("writes", []))
        reads = _as_string_list(row.get("reads", []))
        output_kind = _infer_output_kind(writes, str(row.get("responsibility", "")))
        triggers = _infer_agent_triggers(row, project_type)
        scoped_brief = _infer_scoped_wiki_brief(row)

        entries.append({
            "slug": slug,
            "role": role,
            "responsibility": row.get("responsibility", ""),
            "output_kind": output_kind,
            "inputs": reads or ["decision_log"],
            "outputs": writes or ["scaffold"],
            "capabilities": _infer_agent_tools(row),
            "triggers_when": triggers,
            "reviewer": f"{slug}-reviewer",
            "scoped_wiki_brief": scoped_brief,
            "status": "pending",
            "max_cycles": 3,
        })

    registry = {
        "agent_registry": {
            "generated_at": iso_now(),
            "decision_log_version": version,
            "project_type": project_type,
            "orchestrator": "execution-orchestrator",
            "entries": entries,
        }
    }
    registry_path = scaffold_root / "AGENT_REGISTRY.yaml"
    dump_yaml(registry_path, registry)
    return registry_path, len(entries)


def _write_requirements_matrix(requirements_dir: Path, decision_log: dict[str, Any]) -> int:
    root = decision_log["decision_log"]
    requirements = [row for row in root.get("requirements", []) if isinstance(row, dict)]

    lines = [
        "# REQ_TRACE_MATRIX",
        "",
        "| Requirement | Description | Verification | Citations |",
        "| --- | --- | --- | --- |",
    ]

    if requirements:
        for row in requirements:
            req_id = str(row.get("id", "REQ-UNK"))
            description = str(row.get("description", "")).replace("|", "\\|")
            verification = str(row.get("verification", "")).replace("|", "\\|")
            citations = ", ".join(_as_string_list(row.get("citations", []))).replace("|", "\\|")
            lines.append(f"| {req_id} | {description} | {verification} | {citations} |")
    else:
        lines.append("| REQ-000 | No requirements captured | Add requirements in Stage 2 | None |")

    (requirements_dir / "REQ_TRACE_MATRIX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1


def _markdown_with_frontmatter(frontmatter: dict[str, Any], body_lines: list[str]) -> str:
    return "---\n" + render_frontmatter(frontmatter) + "\n---\n" + "\n".join(body_lines).rstrip() + "\n"


def _infer_agent_tools(row: dict[str, Any]) -> list[str]:
    writes = set(_as_string_list(row.get("writes", [])))
    responsibility = str(row.get("responsibility", "")).lower()
    tools = ["read", "search", "agent"]

    if writes:
        tools.append("edit")
    if writes & {"code", "tests", "report", "references", "scaffold"} or "generate" in responsibility:
        tools.append("execute")
    if writes & {"references"} or "citation" in responsibility or "source" in responsibility:
        tools.append("web")

    return _ordered_unique(tools)


def _default_subagent_allowlist() -> list[str]:
    return ["explore", "research"]


def _agent_description(role: str, row: dict[str, Any], project_type: str) -> str:
    reads = ", ".join(_as_string_list(row.get("reads", []))) or "Decision Log artifacts"
    writes = ", ".join(_as_string_list(row.get("writes", []))) or "scaffold outputs"
    return (
        f"Use when executing the {role} role in a META-COMPILER {project_type} scaffold. "
        f"Reads {reads}. Writes {writes}. Preserves Decision Log constraints, requirement traceability, and citation fidelity."
    )


def _clear_directory_files(directory: Path, pattern: str) -> None:
    for existing in directory.glob(pattern):
        if existing.is_file():
            existing.unlink()


def _clear_directory_tree(directory: Path) -> None:
    for existing in directory.iterdir():
        if existing.is_dir():
            shutil.rmtree(existing)
        else:
            existing.unlink()


def _write_execution_contract(
    layout: dict[str, Path],
    decision_log: dict[str, Any],
    project_type: str,
    version: int,
) -> tuple[Path, Path]:
    root = decision_log["decision_log"]
    requirement_ids = [
        row.get("id", "REQ-UNK")
        for row in root.get("requirements", [])
        if isinstance(row, dict)
    ] or ["REQ-000"]
    citation_ids = _collect_citation_ids(root)

    execution_manifest_path = layout["root"] / "EXECUTION_MANIFEST.yaml"
    dump_yaml(
        execution_manifest_path,
        {
            "execution": {
                "generated_at": iso_now(),
                "decision_log_version": version,
                "project_type": project_type,
                "orchestrator_path": "orchestrator/run_stage4.py",
                "default_output_dir": f"workspace-artifacts/executions/v{version}",
                "requirement_ids": requirement_ids,
                "citation_ids": citation_ids,
            }
        },
    )

    script_lines = [
        "from __future__ import annotations",
        "",
        "import argparse",
        "import importlib.util",
        "from pathlib import Path",
        "",
        "import yaml",
        "",
        "",
        "SCAFFOLD_ROOT = Path(__file__).resolve().parents[1]",
        f"PROJECT_TYPE = {project_type!r}",
        f"DECISION_LOG_VERSION = {version}",
        f"REQUIREMENT_IDS = {requirement_ids!r}",
        f"CITATION_IDS = {citation_ids!r}",
        "",
        "",
        "def _load_agent_registry():",
        "    registry_path = SCAFFOLD_ROOT / 'AGENT_REGISTRY.yaml'",
        "    if not registry_path.exists():",
        "        return []",
        "    with registry_path.open('r', encoding='utf-8') as handle:",
        "        payload = yaml.safe_load(handle) or {}",
        "    root = payload.get('agent_registry', {})",
        "    entries = root.get('entries', [])",
        "    return entries if isinstance(entries, list) else []",
        "",
        "",
        "def _topological_dispatch_plan(entries):",
        "    produced = {output for entry in entries for output in entry.get('outputs', [])}",
        "    resolved = set()",
        "    plan = []",
        "    remaining = list(entries)",
        "    guard = 0",
        "    while remaining and guard < len(entries) * len(entries) + 1:",
        "        progressed = False",
        "        for entry in list(remaining):",
        "            deps = [inp for inp in entry.get('inputs', []) if inp in produced and inp not in resolved]",
        "            blocked = [d for d in deps if d not in resolved]",
        "            if not blocked:",
        "                plan.append(entry)",
        "                resolved.update(entry.get('outputs', []))",
        "                remaining.remove(entry)",
        "                progressed = True",
        "        if not progressed:",
        "            # Cycle or missing inputs: append remaining in declared order.",
        "            plan.extend(remaining)",
        "            remaining = []",
        "        guard += 1",
        "    return plan",
        "",
        "",
        "def _load_generated_module():",
        "    module_path = SCAFFOLD_ROOT / 'code' / 'main.py'",
        "    if not module_path.exists():",
        "        return None",
        "    spec = importlib.util.spec_from_file_location('generated_stage4_main', module_path)",
        "    if spec is None or spec.loader is None:",
        "        return None",
        "    module = importlib.util.module_from_spec(spec)",
        "    spec.loader.exec_module(module)",
        "    return module",
        "",
        "",
        "def _write_text(path: Path, text: str) -> None:",
        "    path.parent.mkdir(parents=True, exist_ok=True)",
        "    path.write_text(text.rstrip() + '\\n', encoding='utf-8')",
        "",
        "",
        "def main() -> int:",
        "    parser = argparse.ArgumentParser(description='Run the scaffold Stage 4 orchestrator.')",
        "    parser.add_argument('--output-dir', required=True)",
        "    args = parser.parse_args()",
        "",
        "    output_dir = Path(args.output_dir).resolve()",
        "    output_dir.mkdir(parents=True, exist_ok=True)",
        "    deliverables: list[dict[str, str]] = []",
        "    execution_notes: list[str] = []",
        "",
        "    # Build and persist the ralph-loop dispatch plan from AGENT_REGISTRY.yaml.",
        "    # The LLM-driven execution-orchestrator agent walks this plan at runtime,",
        "    # invoking each implementer and its reviewer in fresh context.",
        "    registry_entries = _load_agent_registry()",
        "    dispatch_plan = _topological_dispatch_plan(registry_entries)",
        "    plan_payload = {",
        "        'ralph_loop_plan': {",
        "            'decision_log_version': DECISION_LOG_VERSION,",
        "            'project_type': PROJECT_TYPE,",
        "            'entries': [",
        "                {",
        "                    'slug': entry.get('slug'),",
        "                    'role': entry.get('role'),",
        "                    'reviewer': entry.get('reviewer'),",
        "                    'output_kind': entry.get('output_kind'),",
        "                    'triggers_when': entry.get('triggers_when', []),",
        "                    'scoped_wiki_brief': entry.get('scoped_wiki_brief', []),",
        "                    'status': entry.get('status', 'pending'),",
        "                }",
        "                for entry in dispatch_plan",
        "            ],",
        "        }",
        "    }",
        "    plan_path = output_dir / 'ralph_loop_plan.yaml'",
        "    with plan_path.open('w', encoding='utf-8') as handle:",
        "        yaml.safe_dump(plan_payload, handle, sort_keys=False, allow_unicode=False)",
        "    deliverables.append({'kind': 'ralph-loop-plan', 'path': str(plan_path)})",
        "    execution_notes.append(f'dispatch_plan_entries={len(dispatch_plan)}')",
        "",
        "    if PROJECT_TYPE in {'algorithm', 'hybrid'}:",
        "        generated_module = _load_generated_module()",
        "        workflow_state = 'not-run'",
        "        if generated_module is not None:",
        "            runner = getattr(generated_module, 'run_workflow', None)",
        "            if callable(runner):",
        "                runner()",
        "                workflow_state = 'run_workflow_executed'",
        "        algorithm_output = output_dir / 'algorithm_output.md'",
        "        _write_text(",
        "            algorithm_output,",
        "            '\\n'.join([",
        "                '# Algorithm Output',",
        "                '',",
        "                f'- Decision Log Version: v{DECISION_LOG_VERSION}',",
        "                f'- Workflow state: {workflow_state}',",
        "                f'- Requirement IDs: {', '.join(REQUIREMENT_IDS)}',",
        "                f'- Citation IDs: {', '.join(CITATION_IDS) if CITATION_IDS else 'None'}',",
        "                '',",
        "                'This artifact is the executable handoff produced by the scaffold orchestrator.',",
        "            ])",
        "        )",
        "        deliverables.append({'kind': 'algorithm-output', 'path': str(algorithm_output)})",
        "        execution_notes.append(workflow_state)",
        "",
        "    if PROJECT_TYPE in {'report', 'hybrid'}:",
        "        outline_path = SCAFFOLD_ROOT / 'report' / 'OUTLINE.md'",
        "        draft_path = SCAFFOLD_ROOT / 'report' / 'DRAFT.md'",
        "        outline = outline_path.read_text(encoding='utf-8') if outline_path.exists() else '# Missing Outline\\n'",
        "        draft = draft_path.read_text(encoding='utf-8') if draft_path.exists() else '# Missing Draft\\n'",
        "        report_output = output_dir / 'report_output.md'",
        "        _write_text(report_output, '\\n'.join(['# Report Output', '', outline.strip(), '', draft.strip()]))",
        "        deliverables.append({'kind': 'report-output', 'path': str(report_output)})",
        "        execution_notes.append('report_artifacts_compiled')",
        "",
        "    summary_path = output_dir / 'final_product_summary.md'",
        "    _write_text(",
        "        summary_path,",
        "        '\\n'.join([",
        "            '# Final Product Summary',",
        "            '',",
        "            f'- Project type: {PROJECT_TYPE}',",
        "            f'- Decision Log Version: v{DECISION_LOG_VERSION}',",
        "            f'- Requirement IDs: {', '.join(REQUIREMENT_IDS)}',",
        "            f'- Execution notes: {', '.join(execution_notes) if execution_notes else 'none'}',",
        "        ])",
        "    )",
        "    deliverables.append({'kind': 'final-product-summary', 'path': str(summary_path)})",
        "",
        "    manifest = {",
        "        'final_output': {",
        "            'decision_log_version': DECISION_LOG_VERSION,",
        "            'project_type': PROJECT_TYPE,",
        "            'deliverables': deliverables,",
        "            'requirement_ids': REQUIREMENT_IDS,",
        "            'citation_ids': CITATION_IDS,",
        "            'execution_notes': execution_notes,",
        "        }",
        "    }",
        "    with (output_dir / 'FINAL_OUTPUT_MANIFEST.yaml').open('w', encoding='utf-8') as handle:",
        "        yaml.safe_dump(manifest, handle, sort_keys=False, allow_unicode=False)",
        "    return 0",
        "",
        "",
        "if __name__ == '__main__':",
        "    raise SystemExit(main())",
    ]
    orchestrator_path = layout["orchestrator"] / "run_stage4.py"
    orchestrator_path.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
    return execution_manifest_path, orchestrator_path


def _write_what_i_built_artifact(
    paths,
    decision_log: dict[str, Any],
    version: int,
    project_type: str,
    scaffold_root: Path,
    agent_count: int,
    skill_file_count: int,
    instruction_file_count: int,
    requirements_file_count: int,
    code_file_count: int,
    report_file_count: int,
) -> Path:
    root = decision_log["decision_log"]
    architecture = [row for row in root.get("architecture", []) if isinstance(row, dict)]
    requirements = [row for row in root.get("requirements", []) if isinstance(row, dict)]
    agents_needed = [row for row in root.get("agents_needed", []) if isinstance(row, dict)]

    lines = [
        "## What I Built",
        "",
        f"- Scaffold version: v{version}",
        f"- Project type: {project_type}",
        f"- Scaffold root: {scaffold_root}",
        f"- Agent specs: {agent_count}",
        f"- Skill files: {skill_file_count}",
        f"- Instruction files: {instruction_file_count}",
        f"- Requirement artifacts: {requirements_file_count}",
        f"- Code artifacts: {code_file_count}",
        f"- Report artifacts: {report_file_count}",
        "",
        "### Decisions Carried Forward",
    ]

    if architecture:
        for row in architecture[:8]:
            lines.append(f"- {row.get('component')}: {row.get('approach')}")
    else:
        lines.append("- No architecture decisions captured.")

    lines.extend(["", "### Requirement Spine"])
    if requirements:
        for row in requirements[:12]:
            lines.append(f"- {row.get('id')}: {row.get('description')}")
    else:
        lines.append("- No requirements captured.")

    lines.extend(["", "### Execution Path"])
    lines.append("- Stage 3 emits orchestrator/run_stage4.py as the deterministic Stage 4 runner.")
    lines.append("- Stage 4 executes that orchestrator to create final product artifacts and a pitch deck.")
    lines.append("- Generated agents default to the explore/research subagent palette for downstream work.")

    if agents_needed:
        lines.extend(["", "### Agent Roles"])
        for row in agents_needed[:10]:
            lines.append(f"- {row.get('role')}: {row.get('responsibility')}")

    output_path = paths.wiki_provenance_dir / "what_i_built.md"
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _write_skill_files(
    skills_dir: Path,
    custom_skills_dir: Path,
    decision_log: dict[str, Any],
    project_type: str,
    version: int,
) -> int:
    root = decision_log["decision_log"]
    architecture = [row for row in root.get("architecture", []) if isinstance(row, dict)]
    conventions = [row for row in root.get("conventions", []) if isinstance(row, dict)]

    _clear_directory_files(skills_dir, "*.md")
    _clear_directory_tree(custom_skills_dir)

    files: list[tuple[str, str, str, list[str]]] = []
    files.append(
        (
            "core-scaffold-skill.md",
            "core-scaffold",
            "Use when generating deterministic scaffold outputs from a META-COMPILER Decision Log while preserving requirement IDs, citations, and scope boundaries.",
            [
                "# Skill: Core Scaffold Generation",
                "",
                f"Decision Log Version: v{version}",
                f"Project Type: {project_type}",
                "",
                "## Goal",
                "Generate deterministic scaffolds from the Decision Log with explicit traceability.",
                "",
                "## Inputs",
                "- decision_log",
                "",
                "## Required Behaviors",
                "- Respect in-scope and out-of-scope boundaries.",
                "- Propagate requirement IDs into generated outputs.",
                "- Preserve citation IDs exactly as provided.",
            ],
        )
    )

    if project_type in {"algorithm", "hybrid"}:
        files.append(
            (
                "math-conventions-skill.md",
                "math-conventions",
                "Use when applying approved mathematical notation and formal assumptions across algorithm or hybrid scaffold outputs.",
                [
                    "# Skill: Math Conventions",
                    "",
                    "## Goal",
                    "Apply approved mathematical notation and formal assumptions consistently.",
                    "",
                    "## Conventions",
                ]
                + [f"- {row.get('name')}: {row.get('choice')}" for row in conventions if row.get("domain") == "math"]
                + ["", "## Constraints", "- Do not introduce new uncited formalisms."],
            )
        )
        files.append(
            (
                "scope-reduction-skill.md",
                "scope-reduction",
                "Use when pruning out-of-scope implementation work before expanding a META-COMPILER scaffold.",
                [
                    "# Skill: Scope Reduction",
                    "",
                    "## Goal",
                    "Prune non-essential implementation work before code expansion.",
                    "",
                    "## Architecture Components",
                ]
                + [f"- {row.get('component')}: {row.get('approach')}" for row in architecture]
                + ["", "## Constraints", "- Reject out-of-scope work unless explicitly revised in Stage 2."],
            )
        )

    if project_type in {"report", "hybrid"}:
        files.append(
            (
                "citation-manager-skill.md",
                "citation-manager",
                "Use when preserving citation IDs and source traceability across report or hybrid scaffold outputs.",
                [
                    "# Skill: Citation Management",
                    "",
                    "## Goal",
                    "Maintain citation IDs and source traceability across report outputs.",
                    "",
                    "## Constraints",
                    "- Do not mutate citation IDs.",
                    "- Keep requirement references aligned with cited evidence.",
                ],
            )
        )
        files.append(
            (
                "narrative-structure-skill.md",
                "narrative-structure",
                "Use when translating architecture decisions and requirements into a coherent report narrative plan.",
                [
                    "# Skill: Narrative Structure",
                    "",
                    "## Goal",
                    "Translate decision architecture into report structure with requirement traceability.",
                    "",
                    "## Sections",
                ]
                + [f"- {row.get('component')}" for row in architecture]
                + ["", "## Constraints", "- Cover each requirement ID at least once in planned sections."],
            )
        )

    count = 0
    for filename, folder_name, description, lines in files:
        (skills_dir / filename).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        skill_dir = custom_skills_dir / folder_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _markdown_with_frontmatter(
                {
                    "name": folder_name,
                    "description": description,
                },
                lines,
            ),
            encoding="utf-8",
        )
        count += 1

    return count


def _write_instruction_docs(
    instructions_dir: Path,
    custom_instructions_dir: Path,
    decision_log: dict[str, Any],
    project_type: str,
    version: int,
) -> int:
    root = decision_log["decision_log"]
    requirements = [row for row in root.get("requirements", []) if isinstance(row, dict)]

    _clear_directory_files(instructions_dir, "*.md")
    _clear_directory_files(custom_instructions_dir, "*.instructions.md")

    common = [
        "# EXECUTION_INSTRUCTIONS",
        "",
        f"Decision Log Version: v{version}",
        f"Project Type: {project_type}",
        "",
        "## Stage 3 Contract",
        "- Read only Decision Log artifacts.",
        "- Preserve constraints and scope decisions.",
        "- Emit traceable outputs with requirement and citation anchors.",
        "",
        "## Requirement IDs",
    ]

    if requirements:
        for row in requirements:
            common.append(f"- {row.get('id')}: {row.get('description')}")
    else:
        common.append("- No requirements captured; re-run Stage 2 to improve traceability.")

    (instructions_dir / "execution-instructions.md").write_text(
        "\n".join(common).rstrip() + "\n",
        encoding="utf-8",
    )
    (custom_instructions_dir / "execution.instructions.md").write_text(
        _markdown_with_frontmatter(
            {
                "description": "Use when implementing or editing scaffold deliverables generated from a META-COMPILER Decision Log. Enforces Stage 3 contract, scope boundaries, and traceability.",
                "name": "execution-instructions",
                "applyTo": ["code/**", "report/**", "tests/**", "references/**", "requirements/**"],
            },
            common,
        ),
        encoding="utf-8",
    )

    trace_lines = [
        "# DECISION_TRACE_INSTRUCTIONS",
        "",
        "## Workflow",
        "1. Read conventions and architecture from Decision Log.",
        "2. Map outputs to requirement IDs.",
        "3. Attach citation IDs to claims and constraints.",
        "",
        "## Guardrails",
        "- Do not infer uncaptured design decisions.",
        "- Escalate missing information to Stage 2 instead of improvising.",
    ]
    (instructions_dir / "decision-trace-instructions.md").write_text(
        "\n".join(trace_lines) + "\n",
        encoding="utf-8",
    )
    (custom_instructions_dir / "decision-trace.instructions.md").write_text(
        _markdown_with_frontmatter(
            {
                "description": "Use when mapping scaffold outputs to requirement IDs, citation IDs, and Decision Log constraints.",
                "name": "decision-trace-instructions",
                "applyTo": ["code/**", "report/**", "tests/**", "references/**", "requirements/**"],
            },
            trace_lines,
        ),
        encoding="utf-8",
    )

    type_lines: list[str]
    if project_type in {"algorithm", "hybrid"}:
        type_lines = [
            "# ALGORITHM_TRACK_INSTRUCTIONS",
            "",
            "## Deliverables",
            "- code/main.py starter aligned with REQ IDs.",
            "- tests/test_requirements_trace.py enforcing trace anchors.",
            "",
            "## Constraints",
            "- Keep implementation stubs narrow and within in-scope decisions.",
        ]
    else:
        type_lines = [
            "# REPORT_TRACK_INSTRUCTIONS",
            "",
            "## Deliverables",
            "- report/OUTLINE.md mapped to architecture and requirements.",
            "- references/SOURCES.yaml seeded from citation IDs.",
            "",
            "## Constraints",
            "- Preserve requirement traceability in every section plan.",
        ]

    if project_type == "hybrid":
        type_lines.extend(
            [
                "",
                "## Hybrid Note",
                "- Execute algorithm and report tracks in parallel with shared requirement IDs.",
            ]
        )

    (instructions_dir / "type-track-instructions.md").write_text(
        "\n".join(type_lines) + "\n",
        encoding="utf-8",
    )
    apply_to = ["code/**", "tests/**"] if project_type == "algorithm" else ["report/**", "references/**"]
    if project_type == "hybrid":
        apply_to = ["code/**", "tests/**", "report/**", "references/**"]
    (custom_instructions_dir / "type-track.instructions.md").write_text(
        _markdown_with_frontmatter(
            {
                "description": "Use when editing deliverables specific to the active META-COMPILER project track and project type.",
                "name": "type-track-instructions",
                "applyTo": apply_to,
            },
            type_lines,
        ),
        encoding="utf-8",
    )

    return 3


def _write_code_starter_files(layout: dict[str, Path], decision_log: dict[str, Any], project_type: str) -> int:
    if project_type not in {"algorithm", "hybrid"}:
        return 0

    root = decision_log["decision_log"]
    requirements = [
        row.get("id", "REQ-UNK")
        for row in root.get("requirements", [])
        if isinstance(row, dict)
    ]
    requirement_list = requirements or ["REQ-000"]

    conventions = [row for row in root.get("conventions", []) if isinstance(row, dict)]
    architecture = [row for row in root.get("architecture", []) if isinstance(row, dict)]
    citation_ids = _collect_citation_ids(root)

    code_files = {
        layout["code"] / "__init__.py": "\n",
        layout["code"] / "main.py": (
            '"""Stage 3 generated starter module.\n'
            "\n"
            "This module is intentionally minimal and traces to Decision Log requirement IDs.\n"
            '"""\n\n'
            f"REQUIREMENT_IDS = {requirement_list!r}\n\n"
            f"CITATION_IDS = {citation_ids!r}\n\n"
            "\n"
            "def run_workflow() -> None:\n"
            "    \"\"\"Entrypoint stub aligned to Stage 3 scaffold constraints.\"\"\"\n"
            "    # Implement behavior against REQUIREMENT_IDS before expanding scope.\n"
            "    return None\n"
        ),
        layout["code"] / "README.md": (
            "# Code Scaffold\n\n"
            "This directory contains starter code generated from the Decision Log.\n"
            f"Tracked requirements: {', '.join(requirement_list)}\n"
        ),
    }

    # Semantic self-tests: meaningful assertions the scaffold must satisfy
    test_lines = [
        "\"\"\"Semantic self-tests for scaffold integrity.",
        "",
        "These tests verify that the scaffold's generated artifacts maintain",
        "traceability and consistency with the Decision Log.",
        "\"\"\"",
        "import re",
        "from pathlib import Path",
        "",
        "SCAFFOLD_ROOT = Path(__file__).resolve().parents[1]",
        "",
        "",
        "def test_requirement_ids_present_in_code() -> None:",
        "    code_path = SCAFFOLD_ROOT / 'code' / 'main.py'",
        "    text = code_path.read_text(encoding='utf-8')",
        "    assert 'REQUIREMENT_IDS' in text",
        f"    for req_id in {requirement_list!r}:",
        "        assert req_id in text, f'Missing requirement {{req_id}} in main.py'",
        "",
        "",
        "def test_citation_ids_present_in_code() -> None:",
        "    code_path = SCAFFOLD_ROOT / 'code' / 'main.py'",
        "    text = code_path.read_text(encoding='utf-8')",
        "    assert 'CITATION_IDS' in text",
        "",
        "",
        "def test_agent_specs_embed_decisions() -> None:",
        "    agents_dir = SCAFFOLD_ROOT / 'agents'",
        "    agent_specs = list(agents_dir.glob('*.md'))",
        f"    assert len(agent_specs) >= 1, 'No agent specs found'",
        "    for spec in agent_specs:",
        "        text = spec.read_text(encoding='utf-8')",
        "        assert '## Decisions Embedded' in text, f'{{spec.name}} missing decisions'",
        "        assert '## Requirement Trace' in text, f'{{spec.name}} missing req trace'",
        "",
        "",
        "def test_custom_agent_files_have_frontmatter() -> None:",
        "    agents_dir = SCAFFOLD_ROOT / '.github' / 'agents'",
        "    agent_specs = list(agents_dir.glob('*.agent.md'))",
        "    assert len(agent_specs) >= 1, 'No custom agent files found'",
        "    for spec in agent_specs:",
        "        text = spec.read_text(encoding='utf-8')",
        "        assert text.startswith('---\\n'), f'{{spec.name}} missing frontmatter'",
        "        assert 'description:' in text, f'{{spec.name}} missing description frontmatter'",
        "        assert 'agent' in text, f'{{spec.name}} missing agent tool support'",
        "        assert 'explore' in text, f'{{spec.name}} missing explore allowlist entry'",
        "        assert 'research' in text, f'{{spec.name}} missing research allowlist entry'",
        "        assert '## Decision Trace' in text, f'{{spec.name}} missing decision trace'",
        "",
        "",
        "def test_custom_skills_exist() -> None:",
        "    skill_files = list((SCAFFOLD_ROOT / '.github' / 'skills').glob('*/SKILL.md'))",
        "    assert len(skill_files) >= 1, 'No custom skill files found'",
        "    for skill in skill_files:",
        "        text = skill.read_text(encoding='utf-8')",
        "        assert text.startswith('---\\n'), f'{{skill.parent.name}} missing frontmatter'",
        "        assert 'description:' in text, f'{{skill.parent.name}} missing description frontmatter'",
        "",
        "",
        "def test_custom_instructions_exist() -> None:",
        "    instruction_files = list((SCAFFOLD_ROOT / '.github' / 'instructions').glob('*.instructions.md'))",
        "    assert len(instruction_files) >= 2, 'Too few custom instruction files found'",
        "    for instruction in instruction_files:",
        "        text = instruction.read_text(encoding='utf-8')",
        "        assert text.startswith('---\\n'), f'{{instruction.name}} missing frontmatter'",
        "        assert 'description:' in text, f'{{instruction.name}} missing description frontmatter'",
        "",
        "",
        "def test_requirements_traced_covers_all_ids() -> None:",
        "    req_path = SCAFFOLD_ROOT / 'REQUIREMENTS_TRACED.md'",
        "    text = req_path.read_text(encoding='utf-8')",
        f"    for req_id in {requirement_list!r}:",
        "        assert req_id in text, f'REQUIREMENTS_TRACED.md missing {{req_id}}'",
        "",
        "",
        "def test_conventions_doc_exists_and_nonempty() -> None:",
        "    conv_path = SCAFFOLD_ROOT / 'CONVENTIONS.md'",
        "    text = conv_path.read_text(encoding='utf-8')",
        "    assert len(text.strip()) > 20, 'CONVENTIONS.md is essentially empty'",
        "",
        "",
        "def test_trace_matrix_covers_requirements() -> None:",
        "    matrix_path = SCAFFOLD_ROOT / 'requirements' / 'REQ_TRACE_MATRIX.md'",
        "    text = matrix_path.read_text(encoding='utf-8')",
        "    assert '| Requirement |' in text, 'Missing table header'",
        f"    for req_id in {requirement_list!r}:",
        "        assert req_id in text, f'Trace matrix missing {{req_id}}'",
        "",
        "",
        "def test_execution_contract_exists() -> None:",
        "    assert (SCAFFOLD_ROOT / 'EXECUTION_MANIFEST.yaml').exists()",
        "    assert (SCAFFOLD_ROOT / 'orchestrator' / 'run_stage4.py').exists()",
        "",
    ]

    # Add citation-specific test if citations exist
    if citation_ids:
        test_lines.extend([
            "",
            "def test_sources_yaml_covers_citations() -> None:",
            "    sources_path = SCAFFOLD_ROOT / 'references' / 'SOURCES.yaml'",
            "    if not sources_path.exists():",
            "        return  # Only applicable for report/hybrid",
            "    text = sources_path.read_text(encoding='utf-8')",
            f"    for cid in {citation_ids!r}:",
            "        assert cid in text, f'SOURCES.yaml missing citation {{cid}}'",
            "",
        ])

    # Add math conventions test if math conventions exist
    math_conventions = [c for c in conventions if c.get("domain") == "math"]
    if math_conventions:
        test_lines.extend([
            "",
            "def test_math_conventions_defined() -> None:",
            "    conv_path = SCAFFOLD_ROOT / 'CONVENTIONS.md'",
            "    text = conv_path.read_text(encoding='utf-8')",
            f"    expected_names = {[c.get('name', '') for c in math_conventions]!r}",
            "    for name in expected_names:",
            "        assert name in text, f'Missing math convention: {{name}}'",
            "",
        ])

    code_files[layout["tests"] / "test_requirements_trace.py"] = "\n".join(test_lines) + "\n"

    for path, content in code_files.items():
        path.write_text(content, encoding="utf-8")

    return len(code_files)


def _write_report_starter_files(layout: dict[str, Path], decision_log: dict[str, Any], project_type: str) -> int:
    if project_type not in {"report", "hybrid"}:
        return 0

    root = decision_log["decision_log"]
    architecture = [row for row in root.get("architecture", []) if isinstance(row, dict)]
    requirements = [row for row in root.get("requirements", []) if isinstance(row, dict)]
    citation_ids = _collect_citation_ids(root)

    outline_lines = ["# Report Outline", "", "## Executive Summary", "- Context and objective", ""]
    if architecture:
        outline_lines.append("## Architecture-Driven Sections")
        for row in architecture:
            outline_lines.append(f"- {row.get('component')}: {row.get('approach')}")
    else:
        outline_lines.extend(["## Architecture-Driven Sections", "- No architecture components captured."])

    outline_lines.extend(["", "## Requirements Coverage"])
    if requirements:
        for row in requirements:
            outline_lines.append(f"- {row.get('id')}: {row.get('description')}")
    else:
        outline_lines.append("- No requirements captured.")

    (layout["report"] / "OUTLINE.md").write_text("\n".join(outline_lines) + "\n", encoding="utf-8")

    draft_lines = [
        "# Draft Report",
        "",
        "## Introduction",
        "- Problem statement and scope.",
        "",
        "## Methods",
        "- Populate with architecture and requirement-backed details.",
        "",
        "## Results",
        "- Include evidence tied to citation IDs.",
        "",
        "## Conclusion",
        "- Summarize requirement satisfaction and open items.",
    ]
    (layout["report"] / "DRAFT.md").write_text("\n".join(draft_lines) + "\n", encoding="utf-8")

    citation_conventions = [
        row for row in root.get("conventions", []) if isinstance(row, dict) and row.get("domain") == "citation"
    ]
    if citation_conventions:
        style_lines = ["# Citation Style", ""]
        for row in citation_conventions:
            style_lines.append(f"- {row.get('name')}: {row.get('choice')}")
            style_lines.append(f"  Rationale: {row.get('rationale')}")
    else:
        style_lines = [
            "# Citation Style",
            "",
            "- Default: preserve citation IDs from Decision Log and list full metadata in downstream tooling.",
        ]
    (layout["references"] / "CITATION_STYLE.md").write_text("\n".join(style_lines) + "\n", encoding="utf-8")

    sources_payload = {
        "sources": [
            {
                "citation_id": citation_id,
                "status": "pending_lookup",
                "notes": "Populate metadata from citation index in downstream execution phase.",
            }
            for citation_id in citation_ids
        ]
    }
    if not sources_payload["sources"]:
        sources_payload["sources"].append(
            {
                "citation_id": "none",
                "status": "missing",
                "notes": "No citation IDs available in decision log.",
            }
        )
    dump_yaml(layout["references"] / "SOURCES.yaml", sources_payload)

    return 4


def run_scaffold(
    artifacts_root: Path,
    decision_log_version: int | None = None,
) -> dict[str, Any]:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    version, decision_log_path, decision_log = _resolve_decision_log(paths, decision_log_version)
    project_type = decision_log["decision_log"]["meta"].get("project_type", "algorithm")

    scaffold_root = paths.scaffolds_dir / f"v{version}"
    layout = _ensure_scaffold_layout(scaffold_root, project_type=project_type)

    architecture_doc = _render_architecture_doc(decision_log, version=version)
    conventions_doc = _render_conventions_doc(decision_log)
    requirements_doc = _render_requirements_doc(decision_log)

    (layout["root"] / "ARCHITECTURE.md").write_text(architecture_doc, encoding="utf-8")
    (layout["root"] / "CONVENTIONS.md").write_text(conventions_doc, encoding="utf-8")
    (layout["root"] / "REQUIREMENTS_TRACED.md").write_text(requirements_doc, encoding="utf-8")

    agent_count = _write_agent_specs(
        layout["agents"],
        layout["custom_agents"],
        decision_log,
        version=version,
        project_type=project_type,
    )
    registry_path, registry_entry_count = _write_agent_registry(
        layout["root"],
        decision_log,
        project_type=project_type,
        version=version,
    )
    skill_file_count = _write_skill_files(
        layout["skills"],
        layout["custom_skills"],
        decision_log,
        project_type=project_type,
        version=version,
    )
    instruction_file_count = _write_instruction_docs(
        layout["instructions"],
        layout["custom_instructions"],
        decision_log,
        project_type=project_type,
        version=version,
    )
    ralph_counts = _write_ralph_loop_agents(
        layout["custom_agents"],
        layout["custom_instructions"],
        decision_log,
        version=version,
        project_type=project_type,
    )
    requirements_file_count = _write_requirements_matrix(layout["requirements"], decision_log)
    code_file_count = _write_code_starter_files(layout, decision_log, project_type=project_type)
    report_file_count = _write_report_starter_files(layout, decision_log, project_type=project_type)
    execution_manifest_path, orchestrator_path = _write_execution_contract(
        layout,
        decision_log,
        project_type=project_type,
        version=version,
    )
    what_i_built_path = _write_what_i_built_artifact(
        paths,
        decision_log,
        version=version,
        project_type=project_type,
        scaffold_root=scaffold_root,
        agent_count=agent_count,
        skill_file_count=skill_file_count,
        instruction_file_count=instruction_file_count,
        requirements_file_count=requirements_file_count,
        code_file_count=code_file_count,
        report_file_count=report_file_count,
    )

    scaffold_manifest = {
        "scaffold": {
            "generated_at": iso_now(),
            "decision_log_version": version,
            "decision_log_path": str(decision_log_path),
            "project_type": project_type,
            "agent_count": agent_count,
            "skill_file_count": skill_file_count,
            "instruction_file_count": instruction_file_count,
            "customization_root": str(layout["customizations"]),
            "requirements_file_count": requirements_file_count,
            "code_file_count": code_file_count,
            "report_file_count": report_file_count,
            "execution_manifest_path": str(execution_manifest_path),
            "orchestrator_path": str(orchestrator_path),
            "what_i_built_path": str(what_i_built_path),
            "agent_registry_path": str(registry_path),
            "agent_registry_entries": registry_entry_count,
            "reviewers_written": ralph_counts["reviewers_written"],
            "root": str(scaffold_root),
        }
    }
    dump_yaml(layout["root"] / "SCAFFOLD_MANIFEST.yaml", scaffold_manifest)

    wm_payload = load_manifest(paths)
    if not wm_payload:
        raise RuntimeError("Manifest not found. Run meta-init first.")

    wm = wm_payload["workspace_manifest"]
    wm["status"] = "scaffolded"
    wm.setdefault("research", {})["last_completed_stage"] = "3"

    decision_logs = wm.setdefault("decision_logs", [])
    updated = False
    for row in decision_logs:
        if isinstance(row, dict) and row.get("version") == version:
            row["scaffold_path"] = str(scaffold_root)
            row["execution_manifest_path"] = str(execution_manifest_path)
            updated = True
            break
    if not updated:
        decision_logs.append(
            {
                "version": version,
                "created": decision_log["decision_log"]["meta"].get("created"),
                "use_case": decision_log["decision_log"]["meta"].get("use_case"),
                "scaffold_path": str(scaffold_root),
                "execution_manifest_path": str(execution_manifest_path),
            }
        )

    save_manifest(paths, wm_payload)

    return {
        "scaffold_root": str(scaffold_root),
        "decision_log_version": version,
        "project_type": project_type,
        "agent_count": agent_count,
        "skill_file_count": skill_file_count,
        "instruction_file_count": instruction_file_count,
        "customization_root": str(layout["customizations"]),
        "requirements_file_count": requirements_file_count,
        "code_file_count": code_file_count,
        "report_file_count": report_file_count,
        "execution_manifest_path": str(execution_manifest_path),
        "orchestrator_path": str(orchestrator_path),
        "what_i_built_path": str(what_i_built_path),
        "agent_registry_path": str(registry_path),
        "agent_registry_entries": registry_entry_count,
        "reviewers_written": ralph_counts["reviewers_written"],
    }
