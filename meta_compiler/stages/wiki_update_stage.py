"""`wiki-update` convenience wrapper.

Runs `ingest --scope new` followed by `research-breadth` so the operator
can refresh the wiki index after dropping new seeds with a single
command. The two underlying commands remain separately invocable; this
just chains them.

Behaviour:

* If no new seeds need extraction (every seed already has findings), the
  ingest preflight reports `work_items=0` and we proceed straight to
  `research-breadth`, which rebuilds the index/log and re-enriches
  concept pages from the current findings.
* If new seeds DO need extraction, the ingest preflight has written a
  work plan but the LLM-driven `ingest-orchestrator` must run before
  findings exist. We halt with a remediation message instead of running
  `research-breadth` on stale data. The operator invokes the orchestrator
  and re-runs `wiki-update` once findings have landed.
* Pass ``force=True`` (CLI: ``--force``) to skip the
  orchestrator-pending check and run `research-breadth` regardless.
  Useful for "re-render the index without re-extracting" workflows.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .breadth_stage import run_research_breadth
from .ingest_stage import run_ingest


def run_wiki_update(
    artifacts_root: Path,
    workspace_root: Path,
    *,
    scope: str = "new",
    force: bool = False,
) -> dict[str, Any]:
    """Refresh the wiki by chaining ingest preflight + research-breadth.

    Parameters
    ----------
    scope:
        Passed through to ``run_ingest``. Default ``"new"`` skips seeds
        already covered by the findings index.
    force:
        When True, run ``research-breadth`` even if the ingest preflight
        produced new work items pending extraction.
    """
    ingest_result = run_ingest(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
        scope=scope,
    )

    pending_work = int(ingest_result.get("work_items") or 0)
    pending_repo_maps = int(ingest_result.get("repo_map_items") or 0)
    pending = pending_work > 0 or pending_repo_maps > 0

    if pending and not force:
        return {
            "status": "ingest_pending_orchestrator",
            "ingest": ingest_result,
            "instruction": (
                "New seeds detected. Invoke @ingest-orchestrator to extract "
                "findings (it reads "
                f"{ingest_result.get('work_plan_path')}), then re-run "
                "`meta-compiler wiki-update` to refresh the wiki index. "
                "Pass --force to refresh the index from existing findings "
                "without waiting for extraction."
            ),
        }

    breadth_result = run_research_breadth(
        artifacts_root=artifacts_root,
        workspace_root=workspace_root,
    )

    return {
        "status": "updated",
        "ingest": ingest_result,
        "breadth": breadth_result,
        "forced": bool(force and pending),
    }
