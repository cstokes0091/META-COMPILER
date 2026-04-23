"""Stage 3 composer — `meta-compiler scaffold`.

As of Commit 8 of the Stage-3 rearchitecture, `run_scaffold` is a thin
composer that invokes the four producers in order:

  1. capability_compile_stage  — parses decision log + findings into
                                 scaffolds/v{N}/capabilities.yaml.
  2. contract_extract_stage    — derives contracts from IO shapes + findings,
                                 rewrites capabilities.yaml with real refs.
  3. skill_synthesis_stage     — renders skills/{name}/SKILL.md + INDEX.md.
  4. workspace_bootstrap_stage — installs verification harness, manifests,
                                 output buckets; asserts repo-level palette.

All domain logic has moved into the four stage modules. The 23 hand-rolled
_write_* helpers, the hardcoded `_canonical_agents` roster, the
`AGENT_REGISTRY.yaml` emitter, and the embedded `run_stage4.py` source
previously here were deleted in this commit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .capability_compile_stage import run_capability_compile
from .contract_extract_stage import run_contract_extract
from .skill_synthesis_stage import run_skill_synthesis
from .workspace_bootstrap_stage import run_workspace_bootstrap


def run_scaffold(
    artifacts_root: Path,
    decision_log_version: int | None = None,
) -> dict[str, Any]:
    """Compose the four post-dialogue stages.

    Returns a merged result dict keyed by stage name. Each sub-stage can
    also be invoked independently via its own CLI subcommand for debugging
    (compile-capabilities, extract-contracts, synthesize-skills,
    workspace-bootstrap).
    """
    compile_result = run_capability_compile(
        artifacts_root=artifacts_root,
        decision_log_version=decision_log_version,
    )
    version = compile_result["decision_log_version"]
    contract_result = run_contract_extract(
        artifacts_root=artifacts_root,
        decision_log_version=version,
    )
    skill_result = run_skill_synthesis(
        artifacts_root=artifacts_root,
        decision_log_version=version,
    )
    bootstrap_result = run_workspace_bootstrap(
        artifacts_root=artifacts_root,
        decision_log_version=version,
    )

    return {
        "stage": "scaffold",
        "decision_log_version": version,
        "capability_compile": compile_result,
        "contract_extract": contract_result,
        "skill_synthesis": skill_result,
        "workspace_bootstrap": bootstrap_result,
        "capability_count": compile_result.get("capability_count"),
        "contract_count": contract_result.get("contract_count"),
        "skill_count": skill_result.get("skill_count"),
        "verification_hook_count": bootstrap_result.get("verification_hook_count"),
    }
