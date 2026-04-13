from __future__ import annotations

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


def _write_agent_specs(agents_dir: Path, decision_log: dict[str, Any], version: int, project_type: str) -> int:
    root = decision_log["decision_log"]
    agents = _merged_agents(project_type=project_type, root=root)

    architecture = [row for row in root.get("architecture", []) if isinstance(row, dict)]
    conventions = [row for row in root.get("conventions", []) if isinstance(row, dict)]
    requirements = [row for row in root.get("requirements", []) if isinstance(row, dict)]
    citations = _collect_citation_ids(root)

    for existing in agents_dir.glob("*.md"):
        existing.unlink()

    for idx, row in enumerate(agents, start=1):
        role = str(row.get("role", f"agent-{idx}"))
        filename = f"{slugify(role) or f'agent-{idx}'}.md"
        path = agents_dir / filename

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

    return len(agents)


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


def _write_skill_files(skills_dir: Path, decision_log: dict[str, Any], project_type: str, version: int) -> int:
    root = decision_log["decision_log"]
    architecture = [row for row in root.get("architecture", []) if isinstance(row, dict)]
    conventions = [row for row in root.get("conventions", []) if isinstance(row, dict)]

    for existing in skills_dir.glob("*.md"):
        existing.unlink()

    files: list[tuple[str, list[str]]] = []
    files.append(
        (
            "core-scaffold-skill.md",
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
    for filename, lines in files:
        (skills_dir / filename).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        count += 1

    return count


def _write_instruction_docs(instructions_dir: Path, decision_log: dict[str, Any], project_type: str, version: int) -> int:
    root = decision_log["decision_log"]
    requirements = [row for row in root.get("requirements", []) if isinstance(row, dict)]

    for existing in instructions_dir.glob("*.md"):
        existing.unlink()

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
        decision_log,
        version=version,
        project_type=project_type,
    )
    skill_file_count = _write_skill_files(
        layout["skills"],
        decision_log,
        project_type=project_type,
        version=version,
    )
    instruction_file_count = _write_instruction_docs(
        layout["instructions"],
        decision_log,
        project_type=project_type,
        version=version,
    )
    requirements_file_count = _write_requirements_matrix(layout["requirements"], decision_log)
    code_file_count = _write_code_starter_files(layout, decision_log, project_type=project_type)
    report_file_count = _write_report_starter_files(layout, decision_log, project_type=project_type)

    scaffold_manifest = {
        "scaffold": {
            "generated_at": iso_now(),
            "decision_log_version": version,
            "decision_log_path": str(decision_log_path),
            "project_type": project_type,
            "agent_count": agent_count,
            "skill_file_count": skill_file_count,
            "instruction_file_count": instruction_file_count,
            "requirements_file_count": requirements_file_count,
            "code_file_count": code_file_count,
            "report_file_count": report_file_count,
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
            updated = True
            break
    if not updated:
        decision_logs.append(
            {
                "version": version,
                "created": decision_log["decision_log"]["meta"].get("created"),
                "use_case": decision_log["decision_log"]["meta"].get("use_case"),
                "scaffold_path": str(scaffold_root),
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
        "requirements_file_count": requirements_file_count,
        "code_file_count": code_file_count,
        "report_file_count": report_file_count,
    }
