from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .artifacts import ArtifactPaths, latest_decision_log_path, load_manifest
from .io import load_yaml, parse_frontmatter
from .stages.ingest_stage import validate_all_findings
from .utils import read_text_safe


VALID_PROJECT_TYPES = {"algorithm", "report", "hybrid"}
VALID_STATUS = {"initialized", "researched", "scaffolded", "active"}
VALID_REVIEW_VERDICTS = {"PROCEED", "ITERATE"}
VALID_CONVENTION_DOMAINS = {"math", "code", "citation", "terminology"}
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
        issues.append("workspace_manifest.project_type: must be algorithm|report|hybrid")

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
            issues.append("decision_log.meta.project_type: must be algorithm|report|hybrid")
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
                ["role", "responsibility", "reads", "writes", "key_constraints"],
                f"decision_log.agents_needed[{idx}]",
                issues,
            )

    return issues


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


def validate_scaffold(scaffold_root: Path) -> list[str]:
    issues: list[str] = []
    if not scaffold_root.exists():
        return [f"scaffold root missing: {scaffold_root}"]

    scaffold_manifest_path = scaffold_root / "SCAFFOLD_MANIFEST.yaml"
    project_type = None
    scaffold_meta: dict[str, Any] = {}

    if not scaffold_manifest_path.exists():
        issues.append("scaffold missing file: SCAFFOLD_MANIFEST.yaml")
    else:
        scaffold_manifest = load_yaml(scaffold_manifest_path)
        if not isinstance(scaffold_manifest, dict):
            issues.append("scaffold manifest must be an object")
        else:
            payload = scaffold_manifest.get("scaffold")
            if not isinstance(payload, dict):
                issues.append("scaffold manifest missing scaffold root object")
            else:
                scaffold_meta = payload
                project_type = payload.get("project_type")
                if project_type not in VALID_PROJECT_TYPES:
                    issues.append("scaffold manifest project_type must be algorithm|report|hybrid")

    expected_agent_count = scaffold_meta.get("agent_count")

    required_files = [
        scaffold_root / "ARCHITECTURE.md",
        scaffold_root / "CONVENTIONS.md",
        scaffold_root / "REQUIREMENTS_TRACED.md",
        scaffold_root / "EXECUTION_MANIFEST.yaml",
    ]
    for required in required_files:
        if not required.exists():
            issues.append(f"scaffold missing file: {required.name}")

    agents_dir = scaffold_root / "agents"
    if not agents_dir.exists():
        issues.append("scaffold missing agents directory")
    else:
        agent_specs = sorted(agents_dir.glob("*.md"))
        if not agent_specs:
            issues.append("scaffold agents directory has no .md agent specs")
        else:
            min_agents_by_type = {
                "algorithm": 3,
                "report": 4,
                "hybrid": 6,
            }
            if project_type in min_agents_by_type:
                minimum = min_agents_by_type[project_type]
                if len(agent_specs) < minimum:
                    issues.append(
                        f"scaffold has too few agent specs for {project_type}: {len(agent_specs)} < {minimum}"
                    )

            for agent_spec in agent_specs:
                text = read_text_safe(agent_spec)
                if "## Decisions Embedded" not in text:
                    issues.append(f"agent spec missing decisions section: {agent_spec.name}")
                if "## Requirement Trace" not in text:
                    issues.append(f"agent spec missing requirement trace section: {agent_spec.name}")

    docs_dir = scaffold_root / "docs"
    if not docs_dir.exists():
        issues.append("scaffold missing docs directory")

    skills_dir = scaffold_root / "docs" / "skills"
    if not skills_dir.exists():
        issues.append("scaffold missing docs/skills directory")
    else:
        skill_files = [path for path in skills_dir.glob("*.md") if path.is_file()]
        if not skill_files:
            issues.append("scaffold docs/skills directory has no .md skill files")

    instructions_dir = scaffold_root / "docs" / "instructions"
    if not instructions_dir.exists():
        issues.append("scaffold missing docs/instructions directory")
    else:
        instruction_files = [path for path in instructions_dir.glob("*.md") if path.is_file()]
        if len(instruction_files) < 2:
            issues.append("scaffold docs/instructions requires at least 2 instruction files")

    custom_root = scaffold_root / ".github"
    if not custom_root.exists():
        issues.append("scaffold missing .github customization directory")

    orchestrator_dir = scaffold_root / "orchestrator"
    if not orchestrator_dir.exists():
        issues.append("scaffold missing orchestrator directory")
    elif not (orchestrator_dir / "run_stage4.py").exists():
        issues.append("scaffold missing orchestrator/run_stage4.py")

    custom_agents_dir = custom_root / "agents"
    if not custom_agents_dir.exists():
        issues.append("scaffold missing .github/agents directory")
    else:
        custom_agents = sorted(custom_agents_dir.glob("*.agent.md"))
        if not custom_agents:
            issues.append("scaffold .github/agents directory has no .agent.md files")
        else:
            for agent_path in custom_agents:
                issues.extend(validate_custom_agent_file(agent_path))
            if isinstance(expected_agent_count, int) and expected_agent_count > 0 and len(custom_agents) != expected_agent_count:
                issues.append(
                    "scaffold custom agent count mismatch: "
                    f"expected {expected_agent_count}, found {len(custom_agents)}"
                )

    custom_skills_dir = custom_root / "skills"
    if not custom_skills_dir.exists():
        issues.append("scaffold missing .github/skills directory")
    else:
        custom_skills = sorted(custom_skills_dir.glob("*/SKILL.md"))
        if not custom_skills:
            issues.append("scaffold .github/skills directory has no SKILL.md files")
        else:
            for skill_path in custom_skills:
                issues.extend(validate_custom_skill_file(skill_path))

    custom_instructions_dir = custom_root / "instructions"
    if not custom_instructions_dir.exists():
        issues.append("scaffold missing .github/instructions directory")
    else:
        custom_instructions = sorted(custom_instructions_dir.glob("*.instructions.md"))
        if len(custom_instructions) < 2:
            issues.append("scaffold .github/instructions requires at least 2 instruction files")
        else:
            for instruction_path in custom_instructions:
                issues.extend(validate_custom_instruction_file(instruction_path))

    requirements_dir = scaffold_root / "requirements"
    if not requirements_dir.exists():
        issues.append("scaffold missing requirements directory")
    elif not (requirements_dir / "REQ_TRACE_MATRIX.md").exists():
        issues.append("scaffold missing requirements/REQ_TRACE_MATRIX.md")

    if project_type in {"algorithm", "hybrid"}:
        algorithm_required = [
            scaffold_root / "code" / "__init__.py",
            scaffold_root / "code" / "main.py",
            scaffold_root / "code" / "README.md",
            scaffold_root / "tests" / "test_requirements_trace.py",
        ]
        for required in algorithm_required:
            if not required.exists():
                issues.append(f"scaffold missing algorithm artifact: {required.relative_to(scaffold_root)}")

    if project_type in {"report", "hybrid"}:
        report_required = [
            scaffold_root / "report" / "OUTLINE.md",
            scaffold_root / "report" / "DRAFT.md",
            scaffold_root / "references" / "CITATION_STYLE.md",
            scaffold_root / "references" / "SOURCES.yaml",
        ]
        for required in report_required:
            if not required.exists():
                issues.append(f"scaffold missing report artifact: {required.relative_to(scaffold_root)}")

    if isinstance(expected_agent_count, int) and expected_agent_count > 0 and agents_dir.exists():
        actual = len(list(agents_dir.glob("*.md")))
        if actual != expected_agent_count:
            issues.append(
                f"scaffold manifest agent_count mismatch: expected {expected_agent_count}, found {actual}"
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
