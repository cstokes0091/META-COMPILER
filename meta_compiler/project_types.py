"""Single source of truth for project-type values.

Every CLI argparse `choices=`, every validator's allowed-set, and every
scaffold/stage branch that switches on project type imports from this module
so adding a 5th type remains a single-edit change.
"""
from __future__ import annotations


VALID_PROJECT_TYPES: frozenset[str] = frozenset(
    {"algorithm", "report", "hybrid", "workflow"}
)

# Project types that REQUIRE a `code_architecture` block in the Decision Log.
CODE_ARCH_REQUIRED_PROJECT_TYPES: frozenset[str] = frozenset({"algorithm", "hybrid"})

# Project types that REQUIRE a `workflow_config` block in the Decision Log.
WORKFLOW_CONFIG_REQUIRED_PROJECT_TYPES: frozenset[str] = frozenset({"workflow"})

# Suffix appended to the project name when deriving the wiki Atlas name.
WIKI_NAME_SUFFIX: dict[str, str] = {
    "algorithm": "Build Atlas",
    "report": "Research Atlas",
    "hybrid": "Project Atlas",
    "workflow": "Workflow Atlas",
}

# Stage 4 registry checks: the canonical agent role kinds each project type
# must include at least one of. Stage 4 finalize raises if missing.
MIN_OUTPUT_KINDS_BY_TYPE: dict[str, frozenset[str]] = {
    "algorithm": frozenset({"code"}),
    "report": frozenset({"document"}),
    "hybrid": frozenset({"code", "document"}),
    "workflow": frozenset({"tracked_doc", "comment_reply"}),
}


def project_type_choices() -> list[str]:
    """Sorted list, suitable for argparse `choices=`."""
    return sorted(VALID_PROJECT_TYPES)


def requires_code_architecture(project_type: str) -> bool:
    return project_type in CODE_ARCH_REQUIRED_PROJECT_TYPES


def requires_workflow_config(project_type: str) -> bool:
    return project_type in WORKFLOW_CONFIG_REQUIRED_PROJECT_TYPES
