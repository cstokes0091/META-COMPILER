"""migrate-decision-log: Migrate a v1 Decision Log to the typed-IO + code-architecture schema.

The v2 Decision Log schema requires:
  - `agents_needed[].inputs` and `agents_needed[].outputs` as typed lists of
    `{name, modality}` (modality ∈ {document, code}) — replaces the legacy
    untyped `reads` / `writes`.
  - `code_architecture` as a top-level section for `algorithm`/`hybrid`
    projects (forbidden for `report`).

This module is the deterministic side of the LLM-driven migration. The
prompt-as-conductor at `.github/prompts/decision-log-migrate-v2.prompt.md`
walks the human/LLM through:
  1. Reading the existing log.
  2. Writing a `migration_request.yaml` (modality typings + revised reason).
  3. `--plan` (this CLI, mode=plan): reads the request and emits a typed
     proposal plus a code-architecture transcript skeleton.
  4. The LLM authors `code_architecture_blocks.md` for algorithm/hybrid
     projects (skipped for report).
  5. `--apply` (this CLI, mode=apply): reads the proposal + blocks and writes
     the v{N+1} Decision Log under the new schema, validating it.
  6. `audit-requirements` for traceability.

The hook `gate_migration_request` (.github/hooks/main.json) blocks `--apply`
unless the proposal exists.
"""
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
from ..utils import iso_now, read_text_safe, sha256_bytes
from ..validation import (
    CODE_ARCH_REQUIRED_PROJECT_TYPES,
    VALID_AGENT_MODALITIES,
    validate_decision_log,
)
from .elicit_stage import (
    DecisionBlockParseError,
    parse_decision_blocks,
)


# Default modality bucketing used when the proposal does not override.
# Any artifact name not present here defaults to `document` and is surfaced
# in the proposal's `unresolved_artifacts` list so the LLM/human override.
_DEFAULT_CODE_ARTIFACTS = {"code", "tests", "scaffold"}
_DEFAULT_DOCUMENT_ARTIFACTS = {
    "decision_log",
    "architecture",
    "code_architecture",
    "conventions",
    "requirements",
    "scope",
    "open_items",
    "agents_needed",
    "docs",
    "documentation",
    "agents",
    "report",
    "references",
    "transcript",
    "precheck_request",
    "postcheck_request",
    "precheck_verdict",
    "postcheck_verdict",
}


def _migration_runtime_dir(paths) -> Path:
    return paths.runtime_dir / "migration"


def _proposal_path(paths) -> Path:
    return _migration_runtime_dir(paths) / "proposal.yaml"


def _request_path(paths) -> Path:
    return _migration_runtime_dir(paths) / "migration_request.yaml"


def _code_arch_blocks_path(paths) -> Path:
    return _migration_runtime_dir(paths) / "code_architecture_blocks.md"


def _default_modality_for(name: str) -> tuple[str, bool]:
    """Return (modality, confident) for an artifact name.

    `confident` is False when the default falls back to 'document' without a
    real signal — those entries get surfaced as `unresolved_artifacts` in
    the proposal so the LLM/human can override.
    """
    lowered = (name or "").strip().lower()
    if lowered in _DEFAULT_CODE_ARTIFACTS:
        return ("code", True)
    if lowered in _DEFAULT_DOCUMENT_ARTIFACTS:
        return ("document", True)
    return ("document", False)


def _propose_typed_io(
    raw: list[Any], *, role: str, field: str, project_type: str
) -> tuple[list[dict[str, str]], list[str]]:
    proposed: list[dict[str, str]] = []
    unresolved: list[str] = []
    for item in raw or []:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            modality = item.get("modality")
            if not name:
                continue
            if modality in VALID_AGENT_MODALITIES:
                proposed.append({"name": name, "modality": modality})
                continue
        else:
            name = str(item).strip()
        if not name:
            continue
        modality, confident = _default_modality_for(name)
        if project_type == "report" and field == "outputs":
            modality = "document"
            confident = True
        proposed.append({"name": name, "modality": modality})
        if not confident:
            unresolved.append(f"{role}.{field}:{name}")
    return proposed, unresolved


# ---------------------------------------------------------------------------
# --plan
# ---------------------------------------------------------------------------


def run_migrate_decision_log_plan(
    artifacts_root: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    """Read the latest Decision Log + (optional) migration_request.yaml and
    write a typed proposal under runtime/migration/proposal.yaml.

    The proposal is heuristic — `--apply` does not blindly trust it, the LLM
    is expected to refine it based on `unresolved_artifacts` and on the
    code-architecture dialog before invoking `--apply`.
    """
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    latest = latest_decision_log_path(paths)
    if latest is None:
        raise RuntimeError(
            "No Decision Log found. Run `meta-compiler elicit-vision --start` / "
            "`--finalize` to author v1 first."
        )
    parent_version, parent_path = latest

    payload = load_yaml(parent_path) or {}
    root = payload.get("decision_log") or {}
    if not root:
        raise RuntimeError(f"Decision Log is empty: {parent_path}")

    meta = root.get("meta") or {}
    project_type = meta.get("project_type") or "algorithm"

    request = (
        load_yaml(_request_path(paths)) if _request_path(paths).exists() else {}
    ) or {}
    request_body = request.get("decision_log_migration_request") or {}
    overrides = request_body.get("modality_overrides") or {}

    agents_proposal: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for agent in root.get("agents_needed") or []:
        if not isinstance(agent, dict):
            continue
        role = str(agent.get("role") or "agent")
        # Apply overrides first (LLM/human-curated typings).
        override = overrides.get(role) or {}
        inputs_raw = override.get("inputs") or agent.get("inputs") or agent.get("reads") or []
        outputs_raw = override.get("outputs") or agent.get("outputs") or agent.get("writes") or []

        inputs_typed, in_unresolved = _propose_typed_io(
            inputs_raw, role=role, field="inputs", project_type=project_type
        )
        outputs_typed, out_unresolved = _propose_typed_io(
            outputs_raw, role=role, field="outputs", project_type=project_type
        )
        unresolved.extend(in_unresolved)
        unresolved.extend(out_unresolved)
        agents_proposal.append(
            {
                "role": role,
                "responsibility": agent.get("responsibility", ""),
                "inputs": inputs_typed,
                "outputs": outputs_typed,
                "key_constraints": list(agent.get("key_constraints") or []),
                "rationale": agent.get("rationale", ""),
                "citations": list(agent.get("citations") or []),
            }
        )

    needs_code_arch = project_type in CODE_ARCH_REQUIRED_PROJECT_TYPES
    has_code_arch = bool(root.get("code_architecture"))

    proposal = {
        "decision_log_migration_proposal": {
            "generated_at": iso_now(),
            "parent_version": parent_version,
            "new_version": parent_version + 1,
            "project_type": project_type,
            "reason_for_revision": (
                request_body.get("reason")
                or "schema migration: typed agent I/O + code_architecture"
            ),
            "agents_needed": agents_proposal,
            "unresolved_artifacts": sorted(set(unresolved)),
            "needs_code_architecture": needs_code_arch and not has_code_arch,
            "code_architecture_blocks_path": (
                str(_code_arch_blocks_path(paths).relative_to(paths.root).as_posix())
                if needs_code_arch
                else None
            ),
            "instructions": [
                "Review every agent's inputs/outputs modality below.",
                "Override anything in `unresolved_artifacts` by editing this file's "
                "agents_needed entries directly OR by re-authoring "
                "runtime/migration/migration_request.yaml with `modality_overrides:` "
                "and re-running --plan.",
                "For algorithm/hybrid projects without prior code_architecture, author "
                "the code-architecture decision blocks in "
                "runtime/migration/code_architecture_blocks.md (one Aspect=language "
                "and one Aspect=libraries block at minimum).",
                "Then run `meta-compiler migrate-decision-log --apply` to compile the "
                "new Decision Log.",
            ],
        }
    }

    _migration_runtime_dir(paths).mkdir(parents=True, exist_ok=True)
    dump_yaml(_proposal_path(paths), proposal)

    if needs_code_arch and not has_code_arch and not _code_arch_blocks_path(paths).exists():
        _code_arch_blocks_path(paths).write_text(
            _code_architecture_skeleton(parent_version, project_type),
            encoding="utf-8",
        )

    return {
        "status": "proposal_written",
        "parent_version": parent_version,
        "new_version": parent_version + 1,
        "project_type": project_type,
        "proposal_path": str(_proposal_path(paths).relative_to(paths.root).as_posix()),
        "code_architecture_blocks_path": (
            str(_code_arch_blocks_path(paths).relative_to(paths.root).as_posix())
            if needs_code_arch
            else None
        ),
        "unresolved_artifact_count": len(set(unresolved)),
        "next_step": (
            "Walk Step 4 of .github/prompts/decision-log-migrate-v2.prompt.md "
            "(code-architecture dialog), then `meta-compiler migrate-decision-log --apply`."
        ),
    }


def _code_architecture_skeleton(parent_version: int, project_type: str) -> str:
    return (
        "# Code Architecture Decision Blocks (migration v"
        f"{parent_version + 1})\n\n"
        f"_Author at least one Aspect=language and one Aspect=libraries block.\n"
        "Walk the `code-architecture` probes in .github/docs/stage-2-probes.md._\n\n"
        "## Decision Area: Code Architecture\n\n"
        "<!--\n"
        "Example:\n\n"
        "### Decision: language-choice\n"
        "- Section: code-architecture\n"
        "- Aspect: language\n"
        "- Choice: Python 3.11\n"
        "- Rationale: matches existing toolchain and team familiarity\n"
        "- Citations: (none)\n\n"
        "### Decision: numerical-libraries\n"
        "- Section: code-architecture\n"
        "- Aspect: libraries\n"
        "- Choice: numpy + pyarrow\n"
        "- Libraries:\n"
        "  - numpy: PSF math (>=1.26)\n"
        "  - pyarrow: columnar IO (>=15)\n"
        "- Rationale: stable and well-documented\n"
        "- Citations: (none)\n"
        "-->\n"
    )


# ---------------------------------------------------------------------------
# --apply
# ---------------------------------------------------------------------------


def run_migrate_decision_log_apply(
    artifacts_root: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    """Compile the proposal + code_architecture_blocks.md into a new
    Decision Log version and write it to disk."""
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    proposal = load_yaml(_proposal_path(paths))
    if not proposal:
        raise RuntimeError(
            "Migration proposal not found. Run "
            "`meta-compiler migrate-decision-log --plan` first."
        )
    proposal_body = proposal.get("decision_log_migration_proposal") or {}
    parent_version = proposal_body.get("parent_version")
    if not isinstance(parent_version, int):
        raise RuntimeError("Proposal missing parent_version.")

    parent_path = paths.decision_logs_dir / f"decision_log_v{parent_version}.yaml"
    if not parent_path.exists():
        raise RuntimeError(f"Parent Decision Log missing at {parent_path}.")
    parent = load_yaml(parent_path) or {}
    parent_root = parent.get("decision_log") or {}
    parent_meta = parent_root.get("meta") or {}
    project_type = parent_meta.get("project_type") or "algorithm"

    new_version = parent_version + 1

    # Validate agents proposal: every input/output must be {name, modality}
    # with a valid modality enum value.
    agents_proposed = proposal_body.get("agents_needed") or []
    new_agents: list[dict[str, Any]] = []
    issues: list[str] = []
    for idx, agent in enumerate(agents_proposed):
        if not isinstance(agent, dict):
            issues.append(f"agents_needed[{idx}]: not an object")
            continue
        for io_field in ("inputs", "outputs"):
            entries = agent.get(io_field) or []
            if not entries:
                issues.append(
                    f"agents_needed[{idx}] role={agent.get('role')!r}: "
                    f"{io_field} is empty — every agent must declare at least one"
                )
            for jdx, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    issues.append(
                        f"agents_needed[{idx}].{io_field}[{jdx}]: not an object"
                    )
                    continue
                if not entry.get("name"):
                    issues.append(
                        f"agents_needed[{idx}].{io_field}[{jdx}]: missing 'name'"
                    )
                if entry.get("modality") not in VALID_AGENT_MODALITIES:
                    issues.append(
                        f"agents_needed[{idx}].{io_field}[{jdx}]: modality "
                        f"{entry.get('modality')!r} not in "
                        f"{sorted(VALID_AGENT_MODALITIES)}"
                    )
        new_agents.append(
            {
                "role": agent.get("role", ""),
                "responsibility": agent.get("responsibility", ""),
                "inputs": [
                    dict(e) for e in agent.get("inputs", []) if isinstance(e, dict)
                ],
                "outputs": [
                    dict(e) for e in agent.get("outputs", []) if isinstance(e, dict)
                ],
                "key_constraints": list(agent.get("key_constraints") or []),
                "rationale": agent.get("rationale", ""),
                "citations": list(agent.get("citations") or []),
            }
        )

    if issues:
        raise RuntimeError(
            "Migration proposal failed validation. Fix the proposal and re-run --apply:\n"
            + "\n".join(f"  - {issue}" for issue in issues)
        )

    # Carry forward every other section as-is. code_architecture is special:
    # if the parent already had it (re-migration), carry forward; otherwise
    # parse from code_architecture_blocks.md.
    new_root: dict[str, Any] = {
        "meta": {
            "project_name": parent_meta.get("project_name", "META-COMPILER Project"),
            "project_type": project_type,
            "created": iso_now(),
            "version": new_version,
            "parent_version": parent_version,
            "reason_for_revision": proposal_body.get(
                "reason_for_revision",
                "schema migration: typed agent I/O + code_architecture",
            ),
            "problem_statement_hash": _problem_statement_hash(workspace_root),
            "wiki_version": parent_meta.get("wiki_version", ""),
            "use_case": parent_meta.get("use_case", ""),
        },
        "conventions": list(parent_root.get("conventions") or []),
        "architecture": list(parent_root.get("architecture") or []),
        "scope": dict(parent_root.get("scope") or {}),
        "requirements": list(parent_root.get("requirements") or []),
        "open_items": list(parent_root.get("open_items") or []),
        "agents_needed": new_agents,
    }

    if project_type in CODE_ARCH_REQUIRED_PROJECT_TYPES:
        existing_code_arch = parent_root.get("code_architecture") or []
        if existing_code_arch:
            new_root["code_architecture"] = list(existing_code_arch)
        else:
            blocks_path = _code_arch_blocks_path(paths)
            if not blocks_path.exists():
                raise RuntimeError(
                    f"{blocks_path.relative_to(paths.root).as_posix()} is missing. "
                    "Author code-architecture decision blocks (Step 4 of "
                    ".github/prompts/decision-log-migrate-v2.prompt.md) before --apply."
                )
            blocks_text = read_text_safe(blocks_path)
            try:
                blocks, errors = parse_decision_blocks(blocks_text)
            except DecisionBlockParseError as exc:
                raise RuntimeError(f"code-architecture blocks parse error: {exc}")
            if errors:
                raise RuntimeError(
                    "code-architecture blocks parse errors:\n"
                    + "\n".join(f"  - {err}" for err in errors)
                )
            ca_blocks = [b for b in blocks if b.section == "code-architecture"]
            if not ca_blocks:
                raise RuntimeError(
                    "No `Section: code-architecture` decision blocks found in "
                    f"{blocks_path.relative_to(paths.root).as_posix()}. At least one "
                    "Aspect=language and one Aspect=libraries block are required."
                )
            new_root["code_architecture"] = _compile_code_architecture_from_blocks(
                ca_blocks
            )

    compiled = {"decision_log": new_root}
    schema_issues = validate_decision_log(compiled)
    if schema_issues:
        raise RuntimeError(
            "Migrated Decision Log failed schema validation:\n"
            + "\n".join(f"  - {issue}" for issue in schema_issues)
        )

    out_path = paths.decision_logs_dir / f"decision_log_v{new_version}.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dump_yaml(out_path, compiled)

    # Update manifest
    manifest = load_manifest(paths)
    if manifest:
        wm = manifest["workspace_manifest"]
        decision_logs = wm.setdefault("decision_logs", [])
        decision_logs[:] = [
            row
            for row in decision_logs
            if not (isinstance(row, dict) and row.get("version") == new_version)
        ]
        decision_logs.append(
            {
                "version": new_version,
                "created": new_root["meta"]["created"],
                "parent_version": parent_version,
                "reason_for_revision": new_root["meta"]["reason_for_revision"],
                "use_case": new_root["meta"].get("use_case"),
                "scaffold_path": None,
            }
        )
        save_manifest(paths, manifest)

    return {
        "status": "migrated",
        "decision_log_path": str(out_path.relative_to(paths.root).as_posix()),
        "new_version": new_version,
        "parent_version": parent_version,
        "project_type": project_type,
        "next_step": (
            "Run `meta-compiler audit-requirements` to verify REQ traces still resolve, "
            "then re-run `meta-compiler scaffold` to regenerate Stage 3 outputs."
        ),
    }


def _compile_code_architecture_from_blocks(blocks) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for block in blocks:
        entry: dict[str, Any] = {
            "aspect": block.fields["aspect"],
            "choice": block.fields["choice"],
            "alternatives_rejected": [dict(alt) for alt in block.alternatives_rejected],
            "constraints_applied": list(block.fields.get("constraints_applied", [])),
            "citations": list(block.citations),
            "rationale": block.rationale,
        }
        libraries = block.fields.get("libraries")
        if isinstance(libraries, list) and libraries:
            entry["libraries"] = [dict(lib) for lib in libraries]
        module_layout = block.fields.get("module_layout")
        if isinstance(module_layout, str) and module_layout.strip():
            entry["module_layout"] = module_layout
        entries.append(entry)
    return entries


def _problem_statement_hash(workspace_root: Path) -> str:
    statement_path = workspace_root / "PROBLEM_STATEMENT.md"
    if not statement_path.exists():
        return sha256_bytes(b"")
    return sha256_bytes(read_text_safe(statement_path).encode("utf-8"))
