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

# Empty output buckets that Stage 3 creates under scaffolds/v{N}/ per project
# type. Buckets stay empty at Stage 3 — Stage 4 populates them. The
# capability graph is project-type-neutral; only the layout here depends on it.
_SCAFFOLD_SUBDIRS: dict[str, frozenset[str]] = {
    "algorithm": frozenset({"code", "tests"}),
    "report": frozenset({"report", "references"}),
    "hybrid": frozenset({"code", "tests", "report", "references"}),
    "workflow": frozenset({"inbox", "outbox", "state", "kb_brief", "tests"}),
}


def project_type_choices() -> list[str]:
    """Sorted list, suitable for argparse `choices=`."""
    return sorted(VALID_PROJECT_TYPES)


def requires_code_architecture(project_type: str) -> bool:
    return project_type in CODE_ARCH_REQUIRED_PROJECT_TYPES


def requires_workflow_config(project_type: str) -> bool:
    return project_type in WORKFLOW_CONFIG_REQUIRED_PROJECT_TYPES


def scaffold_subdirs_for(project_type: str) -> frozenset[str]:
    """Return the set of empty output-bucket subdirectories Stage 3 creates.

    Stage 4 fills these. For unknown project types (should never happen —
    argparse clamps the choice), returns the empty set so the bootstrap
    stage doesn't create anything speculative.
    """
    return _SCAFFOLD_SUBDIRS.get(project_type, frozenset())
