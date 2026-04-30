"""Stage 3.1 — capability compile.

Parses a decision log + wiki findings into a CapabilityGraph and writes
`workspace-artifacts/scaffolds/v{N}/capabilities.yaml`. No project-type
defaults: the graph is entirely determined by what the Decision Log said
and which findings support each row's citations.

One capability is produced per:
- requirements[*] row (primary — every Stage 2 REQ-NNN becomes a capability)
- architecture[*] row (adds infrastructure capabilities for system components)
- conventions[*] row (adds style/policy capabilities as contracts-in-waiting)

Each capability's `required_finding_ids` are the finding IDs whose citation_id
appears in the source row's `citations` list. In the bootstrap case
(no findings on disk, decision_log_version == 1), citation IDs themselves
are used as placeholder finding IDs — the bootstrap exception is documented
in the plan's validation check #4.

`io_contract_ref` is a placeholder `contract-{capability_name}` at this stage;
Commit 4's contract extract stage populates the real contract library and
rewrites these references when needed.

`verification_hook_ids` is a placeholder `ver-{capability_name}-001`; the
workspace bootstrap stage emits the actual verification/{hook_id}_spec.yaml
acceptance specs (machine-readable Gherkin/example-IO; the Stage 4
implementer translates each scenario into work/<cap>/tests/test_acceptance.py
at step 0 of the work loop).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import ArtifactPaths, build_paths
from ..findings_loader import (
    FindingRecord,
    concept_vocabulary,
    decision_log_vocabulary,
    load_all_findings,
    trigger_content_tokens,
)
from ..io import dump_yaml, load_yaml
from ..schemas import Capability, CapabilityGraph, VerificationType
from ..utils import iso_now, slugify
from ..validation import _is_generic_trigger
from ._decision_log_utils import as_string_list, ordered_unique, resolve_decision_log


MAX_TRIGGERS_PER_CAPABILITY = 6
MAX_DESCRIPTION_LENGTH = 240


def run_capability_compile(
    artifacts_root: Path,
    decision_log_version: int | None = None,
    *,
    allow_empty_findings: bool = False,
) -> dict[str, Any]:
    """Compile decision_log + findings into capabilities.yaml.

    Raises RuntimeError if findings are empty, decision_log_version > 1,
    and `allow_empty_findings` is False — the v1 bootstrap path is the only
    case where an empty findings dir is acceptable.
    """
    paths = build_paths(artifacts_root)
    version, decision_log_path, payload = resolve_decision_log(paths, decision_log_version)
    root = payload["decision_log"]

    findings = load_all_findings(paths)
    if not findings and version > 1 and not allow_empty_findings:
        raise RuntimeError(
            f"wiki/findings/ is empty but decision_log v{version} is not v1. "
            "Only v1 is allowed to bootstrap without findings — run `meta-compiler ingest` "
            "(or pass allow_empty_findings=True for testing)."
        )

    plan_extract = _load_plan_extract(paths, version)
    capabilities = _extract_capabilities(
        root, findings, version, plan_extract=plan_extract
    )
    if plan_extract is None:
        capabilities = _merge_compositions(capabilities)

    graph = CapabilityGraph(
        generated_at=iso_now(),
        decision_log_version=version,
        project_type=str(root.get("meta", {}).get("project_type") or "algorithm"),
        capabilities=capabilities,
    )

    scaffold_root = paths.scaffolds_dir / f"v{version}"
    scaffold_root.mkdir(parents=True, exist_ok=True)
    output_path = scaffold_root / "capabilities.yaml"
    dump_yaml(
        output_path,
        {"capability_graph": graph.model_dump(mode="json")},
    )

    return {
        "stage": "capability-compile",
        "decision_log_version": version,
        "decision_log_path": str(decision_log_path),
        "capabilities_path": str(output_path),
        "capability_count": len(capabilities),
        "findings_considered": len(findings),
    }


def _load_plan_extract(paths: ArtifactPaths, version: int) -> dict[str, Any] | None:
    """Load `decision-logs/plan_extract_v{N}.yaml` when present.

    Returns the inner `plan_extract` dict, or None when the file is missing
    (legacy/bootstrap path).
    """
    extract_path = paths.plan_extract_path(version)
    if not extract_path.exists():
        return None
    payload = load_yaml(extract_path) or {}
    inner = payload.get("plan_extract")
    if not isinstance(inner, dict):
        return None
    return inner


def _extract_capabilities(
    root: dict[str, Any],
    findings: list[FindingRecord],
    decision_log_version: int,
    *,
    plan_extract: dict[str, Any] | None = None,
) -> list[Capability]:
    findings_by_citation = _index_findings_by_citation(findings)
    vocab_primary = concept_vocabulary(findings)
    vocab_bootstrap = decision_log_vocabulary({"decision_log": root}) if not findings else None
    bootstrap_mode = not findings

    if plan_extract is not None:
        return _capabilities_from_plan_extract(
            plan_extract,
            root=root,
            findings_by_citation=findings_by_citation,
            vocab_primary=vocab_primary,
            vocab_bootstrap=vocab_bootstrap,
            decision_log_version=decision_log_version,
            bootstrap_mode=bootstrap_mode,
        )

    capabilities: list[Capability] = []
    used_names: set[str] = set()

    requirements = root.get("requirements") or []
    for row in requirements:
        if not isinstance(row, dict):
            continue
        cap = _capability_from_requirement(
            row,
            findings_by_citation,
            vocab_primary,
            vocab_bootstrap,
            decision_log_version,
            used_names,
            bootstrap_mode,
        )
        if cap is not None:
            capabilities.append(cap)

    requirement_ids_all = [cap.requirement_ids[0] for cap in capabilities if cap.requirement_ids]

    for row in root.get("architecture") or []:
        if not isinstance(row, dict):
            continue
        cap = _capability_from_architecture(
            row,
            findings_by_citation,
            vocab_primary,
            vocab_bootstrap,
            decision_log_version,
            used_names,
            requirement_ids_all,
            bootstrap_mode,
        )
        if cap is not None:
            capabilities.append(cap)

    for row in root.get("conventions") or []:
        if not isinstance(row, dict):
            continue
        cap = _capability_from_convention(
            row,
            findings_by_citation,
            vocab_primary,
            vocab_bootstrap,
            decision_log_version,
            used_names,
            requirement_ids_all,
            bootstrap_mode,
        )
        if cap is not None:
            capabilities.append(cap)

    for row in root.get("code_architecture") or []:
        if not isinstance(row, dict):
            continue
        cap = _capability_from_code_architecture(
            row,
            findings_by_citation,
            vocab_primary,
            vocab_bootstrap,
            decision_log_version,
            used_names,
            requirement_ids_all,
            bootstrap_mode,
        )
        if cap is not None:
            capabilities.append(cap)

    if not capabilities:
        raise RuntimeError(
            "Decision log produced zero capabilities. "
            "At minimum, one `requirements[]` row is required."
        )
    return capabilities


def _index_findings_by_citation(records: list[FindingRecord]) -> dict[str, list[FindingRecord]]:
    out: dict[str, list[FindingRecord]] = {}
    for rec in records:
        out.setdefault(rec.citation_id, []).append(rec)
    return out


def _capabilities_from_plan_extract(
    plan_extract: dict[str, Any],
    *,
    root: dict[str, Any],
    findings_by_citation: dict[str, list[FindingRecord]],
    vocab_primary: set[str],
    vocab_bootstrap: set[str] | None,
    decision_log_version: int,
    bootstrap_mode: bool,
) -> list[Capability]:
    """Plan-driven capability compile.

    Reads each entry in `plan_extract.capabilities` and constructs a
    Capability whose `requirement_ids` / `constraint_ids` come from the
    plan (N-to-M with REQs/CONs), citations are the union of REQ + CON
    citations, and `verification_required` propagates from the plan.
    """
    requirements_by_id = {
        str(row.get("id")): row
        for row in root.get("requirements") or []
        if isinstance(row, dict) and row.get("id")
    }
    constraints_by_id = {
        str(row.get("id")): row
        for row in root.get("constraints") or []
        if isinstance(row, dict) and row.get("id")
    }

    capabilities: list[Capability] = []
    used_names: set[str] = set()
    plan_capabilities = plan_extract.get("capabilities") or []
    for entry in plan_capabilities:
        if not isinstance(entry, dict):
            continue
        cap = _capability_from_plan_entry(
            entry,
            requirements_by_id=requirements_by_id,
            constraints_by_id=constraints_by_id,
            findings_by_citation=findings_by_citation,
            vocab_primary=vocab_primary,
            vocab_bootstrap=vocab_bootstrap,
            decision_log_version=decision_log_version,
            used_names=used_names,
            bootstrap_mode=bootstrap_mode,
        )
        if cap is not None:
            capabilities.append(cap)
    if not capabilities:
        raise RuntimeError(
            "Plan extract produced zero capabilities. "
            "Re-run `meta-compiler plan-implementation --finalize` to refresh "
            "decision-logs/plan_extract_v*.yaml."
        )
    return capabilities


def _capability_from_plan_entry(
    entry: dict[str, Any],
    *,
    requirements_by_id: dict[str, dict[str, Any]],
    constraints_by_id: dict[str, dict[str, Any]],
    findings_by_citation: dict[str, list[FindingRecord]],
    vocab_primary: set[str],
    vocab_bootstrap: set[str] | None,
    decision_log_version: int,
    used_names: set[str],
    bootstrap_mode: bool,
) -> Capability | None:
    raw_name = str(entry.get("name") or "").strip()
    description = str(entry.get("description") or "").strip()
    if not raw_name or not description:
        return None
    requirement_ids = [
        str(rid) for rid in entry.get("requirement_ids") or [] if isinstance(rid, str)
    ]
    constraint_ids = [
        str(cid) for cid in entry.get("constraint_ids") or [] if isinstance(cid, str)
    ]
    verification_required = bool(entry.get("verification_required", True))
    composes = [
        str(c) for c in entry.get("composes") or [] if isinstance(c, str)
    ]
    explicit_triggers = _string_list(entry.get("explicit_triggers"))
    implementation_steps = _string_list(entry.get("implementation_steps"))
    acceptance_criteria = _string_list(entry.get("acceptance_criteria"))
    evidence_refs = _string_list(entry.get("evidence_refs"))
    phase = _optional_string(entry.get("phase"))
    objective = _optional_string(entry.get("objective"))
    rationale = _optional_string(entry.get("rationale"))
    parallelizable = (
        entry.get("parallelizable")
        if isinstance(entry.get("parallelizable"), bool)
        else None
    )

    # v2.1 fields (Change A → Change B propagation):
    dispatch_kind = entry.get("dispatch_kind")
    if dispatch_kind not in ("hitl", "afk"):
        dispatch_kind = None
    user_story = _optional_string(entry.get("user_story"))
    the_problem = _optional_string(entry.get("the_problem"))
    the_fix = _optional_string(entry.get("the_fix"))
    deletion_test = _optional_string(entry.get("deletion_test"))
    anti_patterns = _string_list(entry.get("anti_patterns"))
    out_of_scope = _string_list(entry.get("out_of_scope"))
    acceptance_spec_raw = entry.get("acceptance_spec")
    acceptance_spec = (
        acceptance_spec_raw if isinstance(acceptance_spec_raw, dict) else None
    )

    # Citations are the union of every referenced REQ + CON's citations.
    citations: list[str] = []
    for rid in requirement_ids:
        row = requirements_by_id.get(rid)
        if isinstance(row, dict):
            citations.extend(as_string_list(row.get("citations", [])))
    for cid in constraint_ids:
        row = constraints_by_id.get(cid)
        if isinstance(row, dict):
            citations.extend(as_string_list(row.get("citations", [])))
    citations = ordered_unique(citations)

    name = _unique_name(raw_name, used_names)
    description_truncated = _truncate(description, MAX_DESCRIPTION_LENGTH)

    findings_for_row = [
        rec for cid in citations for rec in findings_by_citation.get(cid, [])
    ]
    if findings_for_row:
        required_finding_ids = ordered_unique(
            [rec.finding_id for rec in findings_for_row]
        )
    elif bootstrap_mode and citations:
        required_finding_ids = list(citations)
    else:
        required_finding_ids = []

    if not citations:
        raise RuntimeError(
            f"Capability {name!r}: every plan capability must trace to >=1 "
            "citation via its REQ_ids or CON_ids. The planner should refuse "
            "to emit floating capabilities — add citations to the underlying "
            "REQ/CON rows or merge this entry into a cap that already has them."
        )

    triggers = _triggers_from_plan(
        explicit_triggers,
        vocab_primary=vocab_primary,
        vocab_bootstrap=vocab_bootstrap,
    )
    if not triggers:
        try:
            triggers = _derive_triggers(
                description_truncated,
                findings_for_row,
                vocab_primary,
                vocab_bootstrap,
                {"description": description_truncated, "objective": objective or ""},
            )
        except RuntimeError:
            triggers = []
    if not triggers:
        triggers = [description_truncated]

    if not required_finding_ids:
        if bootstrap_mode:
            required_finding_ids = list(citations)
        elif verification_required:
            raise RuntimeError(
                f"Capability {name!r}: verification_required=True and "
                f"citations {citations} resolve to zero findings. Run "
                "ingest first or mark the plan entry "
                "verification_required=false."
            )
        else:
            # Policy-only cap whose CON citations didn't yield findings
            # (the citation_id appears in the index but has no extracted
            # findings yet). Fall back to the citation IDs themselves so
            # downstream stages have something to display.
            required_finding_ids = list(citations)

    verification_hook_ids = [f"ver-{name}-001"]

    verification_type = _infer_verification_type_from_plan(entry, requirements_by_id)

    return Capability(
        name=name,
        description=description_truncated,
        when_to_use=triggers,
        required_finding_ids=required_finding_ids,
        io_contract_ref=f"contract-{name}",
        verification_type=verification_type,
        verification_hook_ids=verification_hook_ids,
        requirement_ids=requirement_ids,
        constraint_ids=constraint_ids,
        citation_ids=citations,
        composes=composes,
        verification_required=verification_required,
        phase=phase,
        objective=objective,
        implementation_steps=implementation_steps,
        acceptance_criteria=acceptance_criteria,
        explicit_triggers=explicit_triggers,
        evidence_refs=evidence_refs or required_finding_ids,
        parallelizable=parallelizable,
        rationale=rationale,
        dispatch_kind=dispatch_kind,
        acceptance_spec=acceptance_spec,
        user_story=user_story,
        the_problem=the_problem,
        the_fix=the_fix,
        anti_patterns=anti_patterns,
        out_of_scope=out_of_scope,
        deletion_test=deletion_test,
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, str) and item.strip()]


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _triggers_from_plan(
    explicit_triggers: list[str],
    *,
    vocab_primary: set[str],
    vocab_bootstrap: set[str] | None,
) -> list[str]:
    triggers: list[str] = []
    seen: set[str] = set()
    effective_vocab = vocab_primary or vocab_bootstrap or set()
    for trigger in explicit_triggers:
        candidate = trigger.strip().rstrip(":").strip()
        if not candidate:
            continue
        normalized = candidate.lower()
        if normalized in seen:
            continue
        if _is_generic_trigger(
            candidate,
            vocab=vocab_primary,
            bootstrap_vocab=vocab_bootstrap,
        ):
            tokens = trigger_content_tokens(candidate)
            tokens_in_vocab = tokens & effective_vocab if effective_vocab else tokens
            if not tokens_in_vocab:
                continue
            candidate = " ".join(sorted(tokens_in_vocab))
            normalized = candidate.lower()
            if normalized in seen:
                continue
        triggers.append(candidate)
        seen.add(normalized)
        if len(triggers) >= MAX_TRIGGERS_PER_CAPABILITY:
            break
    return triggers


def _infer_verification_type_from_plan(
    entry: dict[str, Any],
    requirements_by_id: dict[str, dict[str, Any]],
) -> VerificationType:
    """Pick a verification type for a planner-driven capability.

    If the plan covers any REQ-NNN with a `verification` hint, prefer that.
    Otherwise default to unit_test for verification-required capabilities and
    static_lint for non-verification capabilities (a sensible placeholder
    even though no stub is generated).
    """
    for rid in entry.get("requirement_ids") or []:
        row = requirements_by_id.get(str(rid))
        if isinstance(row, dict) and row.get("verification"):
            return _infer_verification_type(row.get("verification"))
    if entry.get("verification_required") is False:
        return VerificationType.static_lint
    return VerificationType.unit_test


def _capability_from_requirement(
    row: dict[str, Any],
    findings_by_citation: dict[str, list[FindingRecord]],
    vocab_primary: set[str],
    vocab_bootstrap: set[str] | None,
    decision_log_version: int,
    used_names: set[str],
    bootstrap_mode: bool,
) -> Capability | None:
    req_id = str(row.get("id") or "").strip()
    description = str(row.get("description") or "").strip()
    citations = as_string_list(row.get("citations", []))
    if not req_id or not description or not citations:
        return None

    name = _unique_name(f"req-{req_id.lower()}-{slugify(description)[:40]}", used_names)
    verification_type = _infer_verification_type(row.get("verification"))

    return _build_capability(
        name=name,
        description=_truncate(description, MAX_DESCRIPTION_LENGTH),
        source_row=row,
        citations=citations,
        findings_by_citation=findings_by_citation,
        vocab_primary=vocab_primary,
        vocab_bootstrap=vocab_bootstrap,
        decision_log_version=decision_log_version,
        requirement_ids=[req_id],
        verification_type=verification_type,
        bootstrap_mode=bootstrap_mode,
    )


def _capability_from_architecture(
    row: dict[str, Any],
    findings_by_citation: dict[str, list[FindingRecord]],
    vocab_primary: set[str],
    vocab_bootstrap: set[str] | None,
    decision_log_version: int,
    used_names: set[str],
    fallback_requirements: list[str],
    bootstrap_mode: bool,
) -> Capability | None:
    component = str(row.get("component") or "").strip()
    approach = str(row.get("approach") or "").strip()
    citations = as_string_list(row.get("citations", []))
    if not component or not citations:
        return None

    name = _unique_name(f"arch-{slugify(component)}", used_names)
    description = _truncate(
        f"{component}: {approach}".strip().rstrip(":"),
        MAX_DESCRIPTION_LENGTH,
    ) or component

    return _build_capability(
        name=name,
        description=description,
        source_row=row,
        citations=citations,
        findings_by_citation=findings_by_citation,
        vocab_primary=vocab_primary,
        vocab_bootstrap=vocab_bootstrap,
        decision_log_version=decision_log_version,
        requirement_ids=fallback_requirements or ["REQ-000"],
        verification_type=VerificationType.contract_fixture,
        bootstrap_mode=bootstrap_mode,
    )


def _capability_from_convention(
    row: dict[str, Any],
    findings_by_citation: dict[str, list[FindingRecord]],
    vocab_primary: set[str],
    vocab_bootstrap: set[str] | None,
    decision_log_version: int,
    used_names: set[str],
    fallback_requirements: list[str],
    bootstrap_mode: bool,
) -> Capability | None:
    convention_name = str(row.get("name") or "").strip()
    choice = str(row.get("choice") or "").strip()
    citations = as_string_list(row.get("citations", []))
    if not convention_name or not citations:
        return None

    name = _unique_name(f"convention-{slugify(convention_name)}", used_names)
    description = _truncate(
        f"{convention_name}: {choice}".strip().rstrip(":"),
        MAX_DESCRIPTION_LENGTH,
    ) or convention_name

    return _build_capability(
        name=name,
        description=description,
        source_row=row,
        citations=citations,
        findings_by_citation=findings_by_citation,
        vocab_primary=vocab_primary,
        vocab_bootstrap=vocab_bootstrap,
        decision_log_version=decision_log_version,
        requirement_ids=fallback_requirements or ["REQ-000"],
        verification_type=VerificationType.static_lint,
        bootstrap_mode=bootstrap_mode,
    )


def _capability_from_code_architecture(
    row: dict[str, Any],
    findings_by_citation: dict[str, list[FindingRecord]],
    vocab_primary: set[str],
    vocab_bootstrap: set[str] | None,
    decision_log_version: int,
    used_names: set[str],
    fallback_requirements: list[str],
    bootstrap_mode: bool,
) -> Capability | None:
    aspect = str(row.get("aspect") or row.get("component") or "").strip()
    choice = str(row.get("choice") or row.get("approach") or "").strip()
    citations = as_string_list(row.get("citations", []))
    if not aspect or not citations:
        return None

    name = _unique_name(f"code-arch-{slugify(aspect)}", used_names)
    description = _truncate(
        f"{aspect}: {choice}".strip().rstrip(":"),
        MAX_DESCRIPTION_LENGTH,
    ) or aspect

    return _build_capability(
        name=name,
        description=description,
        source_row=row,
        citations=citations,
        findings_by_citation=findings_by_citation,
        vocab_primary=vocab_primary,
        vocab_bootstrap=vocab_bootstrap,
        decision_log_version=decision_log_version,
        requirement_ids=fallback_requirements or ["REQ-000"],
        verification_type=VerificationType.contract_fixture,
        bootstrap_mode=bootstrap_mode,
    )


def _build_capability(
    *,
    name: str,
    description: str,
    source_row: dict[str, Any],
    citations: list[str],
    findings_by_citation: dict[str, list[FindingRecord]],
    vocab_primary: set[str],
    vocab_bootstrap: set[str] | None,
    decision_log_version: int,
    requirement_ids: list[str],
    verification_type: VerificationType,
    bootstrap_mode: bool,
) -> Capability:
    findings_for_row = [
        rec
        for cid in citations
        for rec in findings_by_citation.get(cid, [])
    ]
    if findings_for_row:
        required_finding_ids = ordered_unique([rec.finding_id for rec in findings_for_row])
    elif bootstrap_mode:
        # Globally empty findings (v1 bootstrap or --allow-empty-findings).
        # Use the citation IDs themselves as placeholder finding refs; the
        # scaffold validator's bootstrap branch resolves them against
        # wiki/citations/index.yaml instead of wiki/findings/.
        required_finding_ids = list(citations)
    else:
        raise RuntimeError(
            f"Capability {name}: citations {citations} resolve to zero findings "
            f"although wiki/findings/ is non-empty. Ensure ingest covers these citations "
            "or remove them from the source row."
        )

    triggers = _derive_triggers(
        description,
        findings_for_row,
        vocab_primary,
        vocab_bootstrap,
        source_row,
    )

    return Capability(
        name=name,
        description=description,
        when_to_use=triggers,
        required_finding_ids=required_finding_ids,
        io_contract_ref=f"contract-{name}",
        verification_type=verification_type,
        verification_hook_ids=[f"ver-{name}-001"],
        requirement_ids=requirement_ids,
        citation_ids=citations,
        composes=[],
    )


def _derive_triggers(
    description: str,
    findings_for_row: list[FindingRecord],
    vocab_primary: set[str],
    vocab_bootstrap: set[str] | None,
    source_row: dict[str, Any],
) -> list[str]:
    # Start with the description itself; trim to content tokens we can present
    # as a trigger phrase. Then enrich with concept names from the cited findings.
    seeds: list[str] = [description]

    # Candidate concept-name triggers: rank by whether they're represented in
    # the row-specific findings and whether tokens overlap with primary vocab.
    concept_names: list[str] = []
    for rec in findings_for_row:
        for concept in rec.concepts:
            name = str(concept.get("name") or "").strip()
            if name:
                concept_names.append(name)

    seeds.extend(concept_names)

    triggers: list[str] = []
    seen: set[str] = set()
    effective_vocab = vocab_primary or vocab_bootstrap or set()
    for seed in seeds:
        seed_clean = seed.strip().rstrip(":").strip()
        if not seed_clean:
            continue
        normalized = seed_clean.lower()
        if normalized in seen:
            continue
        if _is_generic_trigger(
            seed_clean,
            vocab=vocab_primary,
            bootstrap_vocab=vocab_bootstrap,
        ):
            # Try rebuilding the trigger from content-tokens only — preserves
            # domain nouns, drops stop-words. Fall back to skipping if still
            # empty after stripping.
            tokens = trigger_content_tokens(seed_clean)
            tokens_in_vocab = tokens & effective_vocab if effective_vocab else tokens
            if not tokens_in_vocab:
                continue
            rebuilt = " ".join(sorted(tokens_in_vocab))
            if rebuilt in seen:
                continue
            triggers.append(rebuilt)
            seen.add(rebuilt)
        else:
            triggers.append(seed_clean)
            seen.add(normalized)
        if len(triggers) >= MAX_TRIGGERS_PER_CAPABILITY:
            break

    if not triggers:
        # Last-resort: derive a single token trigger from the row citations +
        # description content-tokens. If every token is generic we raise so
        # the caller can't produce an invalid Capability — the Pydantic model
        # rejects empty when_to_use anyway, but raising here gives a clearer
        # message.
        tokens = trigger_content_tokens(description)
        if effective_vocab:
            tokens &= effective_vocab
        if not tokens:
            raise RuntimeError(
                f"Could not derive a non-generic trigger for capability "
                f"(description={description!r}). The row's citations do not "
                "appear in any finding's concept names and the description "
                "contains only stop-words."
            )
        triggers = [" ".join(sorted(tokens))]
    return triggers


def _infer_verification_type(verification_hint: Any) -> VerificationType:
    text = str(verification_hint or "").lower()
    if not text:
        return VerificationType.unit_test
    if "numeric" in text or "numerical" in text:
        return VerificationType.numerical
    if "regression" in text:
        return VerificationType.regression
    if "lint" in text or "style" in text:
        return VerificationType.static_lint
    if "review" in text or "human" in text:
        return VerificationType.human_review
    if "fixture" in text or "contract" in text:
        return VerificationType.contract_fixture
    return VerificationType.unit_test


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _unique_name(candidate: str, used: set[str]) -> str:
    base = candidate.strip().strip("-") or "capability"
    base = base[:80]
    name = base
    counter = 2
    while name in used:
        name = f"{base}-{counter}"
        counter += 1
    used.add(name)
    return name


def _merge_compositions(capabilities: list[Capability]) -> list[Capability]:
    """Populate `composes` by matching output modalities to input modalities.

    This is a minimal stub: two capabilities A and B compose if they share a
    requirement_id. Commit 4's contract extract stage will replace this with
    a proper IO-shape-based analysis once contracts exist.
    """
    by_req: dict[str, list[Capability]] = {}
    for cap in capabilities:
        for req in cap.requirement_ids:
            by_req.setdefault(req, []).append(cap)

    updated: list[Capability] = []
    for cap in capabilities:
        siblings: list[str] = []
        for req in cap.requirement_ids:
            for other in by_req.get(req, []):
                if other.name != cap.name and other.name not in siblings:
                    siblings.append(other.name)
        if siblings == cap.composes:
            updated.append(cap)
            continue
        updated.append(cap.model_copy(update={"composes": siblings}))
    return updated
