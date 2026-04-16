"""audit-requirements command: Baseline Stage 2 Decision Log audit.

The CLI produces a deterministic starter audit (scope coverage, citation
fidelity, lens count, EARS phrasing check) and writes
`workspace-artifacts/decision-logs/requirements_audit.yaml`. The
`requirements-auditor` agent is then invoked in fresh context to enrich the
report (contradictions, proposed additions, semantic coverage) and set the
final verdict.

The agent may overwrite the baseline file — the CLI output is a safe scaffold,
not the final audit. If the agent is not invoked, the baseline verdict still
reflects real coverage gaps the CLI could detect without LLM judgment.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..artifacts import ArtifactPaths, build_paths, ensure_layout, latest_decision_log_path
from ..io import dump_yaml, load_yaml
from ..utils import iso_now, read_text_safe, slugify


LENSES = [
    "functional",
    "performance",
    "reliability",
    "usability",
    "security",
    "maintainability",
    "portability",
    "constraint",
    "data",
    "interface",
    "business-rule",
]

EARS_TRIGGER_PATTERNS = [
    re.compile(r"^\s*when\b", re.IGNORECASE),
    re.compile(r"^\s*while\b", re.IGNORECASE),
    re.compile(r"^\s*if\b.*\bthen\b", re.IGNORECASE),
    re.compile(r"^\s*where\b", re.IGNORECASE),
    re.compile(r"^\s*the\s+\S+\s+shall\b", re.IGNORECASE),
]


def _extract_problem_section(problem_text: str, heading: str) -> list[str]:
    if not problem_text:
        return []
    lines = problem_text.splitlines()
    collected: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == heading:
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped:
            collected.append(stripped.lstrip("-* "))
    return collected


def _load_citation_ids(paths: ArtifactPaths) -> set[str]:
    raw = load_yaml(paths.citations_index_path)
    if not isinstance(raw, dict):
        return set()
    citations = raw.get("citations", {})
    if not isinstance(citations, dict):
        return set()
    return set(citations.keys())


def _req_lens(req: dict[str, Any]) -> str:
    """Return the lens for a REQ. Defaults to 'functional' when absent."""
    lens = req.get("lens")
    if isinstance(lens, str) and lens in LENSES:
        return lens
    # Heuristic: infer from description
    description = (req.get("description") or "").lower()
    if any(word in description for word in ("latency", "seconds", "throughput", "time budget", "within")):
        return "performance"
    if any(word in description for word in ("fails", "recovery", "durab", "retry")):
        return "reliability"
    if any(word in description for word in ("auth", "permission", "encrypt", "token")):
        return "security"
    if any(word in description for word in ("schema", "format", "input", "output")):
        return "data"
    if any(word in description for word in ("api", "interface", "protocol", "contract")):
        return "interface"
    if description.startswith("the system shall honour the constraint"):
        return "constraint"
    return "functional"


def _is_ears_compliant(description: str) -> bool:
    if not description:
        return False
    if "shall" not in description.lower():
        return False
    return any(pat.search(description) for pat in EARS_TRIGGER_PATTERNS)


def _scope_item_match(req_description: str, item: str) -> bool:
    if not req_description or not item:
        return False
    slug = slugify(item)
    tokens = [tok for tok in slug.split("-") if len(tok) >= 3]
    if not tokens:
        return False
    desc_lower = req_description.lower()
    return all(tok in desc_lower for tok in tokens[:3])


def run_audit_requirements(
    artifacts_root: Path,
    workspace_root: Path,
    decision_log_version: int | None = None,
) -> dict[str, Any]:
    """Compute a baseline audit for the latest (or specified) Decision Log."""
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    if decision_log_version is not None:
        decision_log_path = paths.decision_logs_dir / f"decision_log_v{decision_log_version}.yaml"
        if not decision_log_path.exists():
            raise RuntimeError(f"decision log v{decision_log_version} not found at {decision_log_path}")
        version = decision_log_version
    else:
        latest = latest_decision_log_path(paths)
        if latest is None:
            raise RuntimeError("no decision log found. Run `meta-compiler elicit-vision` first.")
        version, decision_log_path = latest

    payload = load_yaml(decision_log_path)
    if not isinstance(payload, dict) or "decision_log" not in payload:
        raise RuntimeError(f"decision log {decision_log_path.name} is not a valid decision_log file")

    log = payload["decision_log"]
    scope = log.get("scope", {}) if isinstance(log.get("scope"), dict) else {}
    in_scope = scope.get("in_scope", []) if isinstance(scope.get("in_scope"), list) else []
    requirements = log.get("requirements", []) if isinstance(log.get("requirements"), list) else []

    # Problem statement constraints
    problem_statement_path = workspace_root / "PROBLEM_STATEMENT.md"
    problem_text = read_text_safe(problem_statement_path) if problem_statement_path.exists() else ""
    constraints = _extract_problem_section(problem_text, "## Constraints")

    citation_ids = _load_citation_ids(paths)

    # Coverage — scope items
    uncovered_scope: list[str] = []
    for entry in in_scope:
        if not isinstance(entry, dict):
            continue
        item = entry.get("item")
        if not isinstance(item, str) or not item.strip():
            continue
        covered = any(
            _scope_item_match(str(req.get("description", "")), item)
            for req in requirements
            if isinstance(req, dict)
        )
        if not covered:
            uncovered_scope.append(item)

    # Coverage — problem statement constraints
    uncovered_constraints: list[str] = []
    for constraint in constraints:
        needle = slugify(constraint)
        tokens = [tok for tok in needle.split("-") if len(tok) >= 4][:3]
        if not tokens:
            continue
        covered = False
        for req in requirements:
            if not isinstance(req, dict):
                continue
            desc_lower = str(req.get("description", "")).lower()
            if all(tok in desc_lower for tok in tokens):
                covered = True
                break
        if not covered:
            uncovered_constraints.append(constraint)

    # Per-REQ findings
    lens_counts = {lens: 0 for lens in LENSES}
    req_findings: list[dict[str, Any]] = []
    for req in requirements:
        if not isinstance(req, dict):
            continue
        req_id = req.get("id", "")
        description = str(req.get("description", ""))
        lens = _req_lens(req)
        lens_counts[lens] = lens_counts.get(lens, 0) + 1

        req_issues: list[str] = []
        req_citations = req.get("citations", []) or []
        if not isinstance(req_citations, list) or not req_citations:
            req_issues.append("no citations")
            citations_resolve = False
        else:
            unresolved = [cid for cid in req_citations if cid not in citation_ids]
            citations_resolve = not unresolved
            if unresolved:
                req_issues.append(f"citations not in index: {unresolved}")

        ears_compliant = _is_ears_compliant(description)
        if not ears_compliant:
            req_issues.append("not EARS-compliant (missing trigger/shall)")

        req_findings.append({
            "id": req_id,
            "lens": lens,
            "ears_compliant": ears_compliant,
            "citations_resolve": citations_resolve,
            "issues": req_issues,
        })

    # Blockers
    blocking_gaps: list[str] = []
    for item in uncovered_scope:
        blocking_gaps.append(f"scope item '{item}' has zero requirement coverage")
    for constraint in uncovered_constraints:
        blocking_gaps.append(f"problem-statement constraint not captured: '{constraint}'")
    for finding in req_findings:
        if not finding["citations_resolve"] and "no citations" in finding["issues"]:
            blocking_gaps.append(f"REQ {finding['id']} has zero citations")

    # Non-blocking gaps
    non_blocking_gaps: list[str] = []
    ears_non_compliant = sum(1 for f in req_findings if not f["ears_compliant"])
    if ears_non_compliant:
        non_blocking_gaps.append(f"{ears_non_compliant} requirements lack EARS phrasing")
    lenses_populated = sum(1 for count in lens_counts.values() if count > 0)
    if lenses_populated <= 1 and requirements:
        non_blocking_gaps.append(
            "requirements lack non-functional coverage — consider performance, reliability, security"
        )
    if not requirements:
        blocking_gaps.append("decision log has zero requirements")

    verdict = "PROCEED" if not blocking_gaps else "REVISE"

    audit = {
        "requirements_audit": {
            "decision_log_version": version,
            "audited_at": iso_now(),
            "audited_by": "cli-baseline",
            "verdict": verdict,
            "coverage": {
                "scope_items_total": len(in_scope),
                "scope_items_uncovered": uncovered_scope,
                "problem_constraints_total": len(constraints),
                "problem_constraints_uncovered": uncovered_constraints,
                "lens_counts": lens_counts,
            },
            "req_findings": req_findings,
            "contradictions": [],
            "proposed_additions": [],
            "blocking_gaps": blocking_gaps,
            "non_blocking_gaps": non_blocking_gaps,
        }
    }

    audit_path = paths.decision_logs_dir / "requirements_audit.yaml"
    dump_yaml(audit_path, audit)

    return {
        "status": "baseline_written",
        "decision_log_version": version,
        "decision_log_path": str(decision_log_path.relative_to(paths.root.parent).as_posix())
        if paths.root.parent in decision_log_path.parents
        else str(decision_log_path),
        "audit_path": str(audit_path.relative_to(paths.root.parent).as_posix())
        if paths.root.parent in audit_path.parents
        else str(audit_path),
        "verdict": verdict,
        "requirements_total": len(requirements),
        "scope_items_uncovered": len(uncovered_scope),
        "problem_constraints_uncovered": len(uncovered_constraints),
        "blocking_gaps": len(blocking_gaps),
        "non_blocking_gaps": len(non_blocking_gaps),
        "instruction": (
            "Invoke the requirements-auditor agent in fresh context. It will "
            "enrich this baseline with contradictions and proposed additions, "
            "then rewrite the file with its final verdict."
        ),
    }
