from __future__ import annotations

from pathlib import Path

from ..artifacts import build_paths, ensure_layout, load_manifest, save_manifest
from ..io import dump_yaml, load_yaml
from ..utils import iso_now
from ..wiki_lifecycle import append_log_entry


def _format_gap_for_review(gap: dict, blocking: bool) -> dict:
    if blocking:
        return {
            "description": gap.get("description", ""),
            "why_blocking": "Severity is critical and unresolved.",
        }
    return {
        "description": gap.get("description", ""),
        "impact_if_ignored": "Potential quality degradation if deferred.",
    }


def _build_verdict(perspective: str, gaps: list[dict], health_metrics: dict) -> dict:
    critical = [gap for gap in gaps if gap.get("severity") == "critical"]
    major = [gap for gap in gaps if gap.get("severity") == "major"]
    minor = [gap for gap in gaps if gap.get("severity") == "minor"]
    orphan_count = len(health_metrics.get("orphan_pages", []))
    sparse_citation_count = len(health_metrics.get("sparse_citation_pages", []))

    if perspective == "optimistic":
        proceed = len(critical) <= 1 and sparse_citation_count <= 2
        base = 0.7
    elif perspective == "pessimistic":
        proceed = len(critical) == 0 and len(major) <= 2 and orphan_count <= 3
        base = 0.8
    else:  # pragmatic
        proceed = len(critical) <= 2 and len(gaps) <= 12 and sparse_citation_count <= 4
        base = 0.75

    confidence_penalty = (
        (0.18 * len(critical))
        + (0.05 * len(major))
        + (0.01 * len(minor))
        + (0.02 * orphan_count)
        + (0.03 * sparse_citation_count)
    )
    confidence = max(0.05, min(0.99, base - confidence_penalty))

    blocking_gaps = [_format_gap_for_review(gap, blocking=True) for gap in critical[:8]]
    non_blocking_gaps = [_format_gap_for_review(gap, blocking=False) for gap in (major + minor)[:10]]

    proceed_if = (
        "Critical unresolved gaps are reduced to zero."
        if not proceed
        else "Current coverage is sufficient to proceed to Stage 2."
    )

    result = {
        "verdict": "PROCEED" if proceed else "ITERATE",
        "confidence": round(confidence, 2),
        "blocking_gaps": blocking_gaps,
        "non_blocking_gaps": non_blocking_gaps,
        "proceed_if": proceed_if,
        "health_snapshot": {
            "orphan_page_count": orphan_count,
            "sparse_citation_count": sparse_citation_count,
        },
    }
    return result


def compute_consensus(verdicts: dict[str, dict], iteration_count: int) -> dict:
    proceed_votes = sum(1 for item in verdicts.values() if item.get("verdict") == "PROCEED")

    if iteration_count >= 3:
        return {
            "decision": "PROCEED",
            "reason": "iteration_cap_reached",
            "forced": True,
            "proceed_votes": proceed_votes,
            "requires_human_judgment": False,
        }

    if proceed_votes == 3:
        return {
            "decision": "PROCEED",
            "reason": "unanimous_proceed",
            "forced": False,
            "proceed_votes": proceed_votes,
            "requires_human_judgment": False,
        }

    if proceed_votes == 2:
        return {
            "decision": "PROCEED",
            "reason": "majority_proceed",
            "forced": False,
            "proceed_votes": proceed_votes,
            "requires_human_judgment": True,
        }

    return {
        "decision": "ITERATE",
        "reason": "insufficient_coverage",
        "forced": False,
        "proceed_votes": proceed_votes,
        "requires_human_judgment": False,
    }


def run_review(artifacts_root: Path) -> dict:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    merged_report_path = paths.reports_dir / "merged_gap_report.yaml"
    merged_report = load_yaml(merged_report_path)
    if not merged_report:
        raise RuntimeError("Merged gap report missing. Run research-depth first.")

    gap_root = merged_report.get("gap_report", {})
    if not isinstance(gap_root, dict):
        raise RuntimeError("Merged gap report schema is invalid.")
    gaps = gap_root.get("gaps", [])
    if not isinstance(gaps, list):
        raise RuntimeError("Merged gap report gaps field is invalid.")
    health_metrics = gap_root.get("health", {}) if isinstance(gap_root, dict) else {}
    if not isinstance(health_metrics, dict):
        health_metrics = {}

    manifest = load_manifest(paths)
    if not manifest:
        raise RuntimeError("Manifest not found. Run meta-init first.")

    wm = manifest["workspace_manifest"]
    research = wm.setdefault("research", {})
    iteration_count = int(research.get("iteration_count", 0))

    verdicts = {
        "optimistic": _build_verdict("optimistic", gaps, health_metrics),
        "pessimistic": _build_verdict("pessimistic", gaps, health_metrics),
        "pragmatic": _build_verdict("pragmatic", gaps, health_metrics),
    }
    consensus = compute_consensus(verdicts, iteration_count=iteration_count)

    if consensus["decision"] == "ITERATE":
        research["iteration_count"] = iteration_count + 1
    else:
        research["iteration_count"] = iteration_count

    research["last_completed_stage"] = "1C"
    research["last_review_decision"] = consensus["decision"]

    payload = {
        "review_panel": {
            "generated_at": iso_now(),
            "reviewers": verdicts,
            "health": {
                "orphan_pages": len(health_metrics.get("orphan_pages", [])),
                "sparse_citation_pages": len(health_metrics.get("sparse_citation_pages", [])),
                "open_question_count": health_metrics.get("open_question_count", 0),
            },
            "consensus": consensus,
        }
    }
    dump_yaml(paths.reviews_dir / "review_verdicts.yaml", payload)

    append_log_entry(
        log_path=paths.wiki_v2_dir / "log.md",
        operation="review",
        title="Stage 1C fresh review panel",
        details=[
            f"decision: {consensus['decision']}",
            f"reason: {consensus['reason']}",
            f"proceed_votes: {consensus['proceed_votes']}",
            f"orphan_pages: {len(health_metrics.get('orphan_pages', []))}",
            f"sparse_citation_pages: {len(health_metrics.get('sparse_citation_pages', []))}",
        ],
    )

    save_manifest(paths, manifest)
    return {
        "decision": consensus["decision"],
        "reason": consensus["reason"],
        "proceed_votes": consensus["proceed_votes"],
        "iteration_count": research["iteration_count"],
        "requires_human_judgment": consensus["requires_human_judgment"],
        "orphan_pages": len(health_metrics.get("orphan_pages", [])),
        "sparse_citation_pages": len(health_metrics.get("sparse_citation_pages", [])),
    }
