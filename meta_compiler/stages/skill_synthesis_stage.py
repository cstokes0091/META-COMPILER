"""Stage 3.3 — skill synthesis.

Renders one SKILL.md per capability under scaffolds/v{N}/skills/{name}/,
plus scaffolds/v{N}/skills/INDEX.md. Body content is drawn from the cited
findings' quotes/claims — NO placeholder slots. Every `## ` section must
have non-empty content (enforced by validate_scaffold in Commit 7; we
produce non-empty sections here by construction).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import build_paths
from ..findings_loader import FindingRecord, build_finding_index, load_all_findings
from ..io import dump_yaml, load_yaml, render_frontmatter
from ..schemas import (
    Capability,
    CapabilityGraph,
    Contract,
    ContractManifest,
    FindingRef,
    SkillFrontmatter,
    SkillIndex,
    SkillIndexEntry,
)
from ..utils import iso_now
from ._decision_log_utils import resolve_decision_log


MAX_INVARIANTS_IN_BODY = 10
MAX_EVIDENCE_IN_BODY = 8
MAX_QUOTE_LENGTH = 280  # trimmed before embedding in SKILL.md


def run_skill_synthesis(
    artifacts_root: Path,
    decision_log_version: int | None = None,
) -> dict[str, Any]:
    paths = build_paths(artifacts_root)
    version, _, _payload = resolve_decision_log(paths, decision_log_version)

    scaffold_root = paths.scaffolds_dir / f"v{version}"
    capabilities_path = scaffold_root / "capabilities.yaml"
    contracts_manifest_path = scaffold_root / "contracts" / "_manifest.yaml"
    if not capabilities_path.exists():
        raise RuntimeError(f"capabilities.yaml missing at {capabilities_path}. Run compile-capabilities.")
    if not contracts_manifest_path.exists():
        raise RuntimeError(f"contracts/_manifest.yaml missing at {contracts_manifest_path}. Run extract-contracts.")

    graph = CapabilityGraph.model_validate(
        (load_yaml(capabilities_path) or {}).get("capability_graph") or {}
    )
    manifest = ContractManifest.model_validate(
        (load_yaml(contracts_manifest_path) or {}).get("contract_manifest") or {}
    )
    contracts_by_id = _load_contracts(scaffold_root, manifest)

    findings = load_all_findings(paths)
    finding_index = build_finding_index(findings)

    skills_dir = scaffold_root / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    index_entries: list[SkillIndexEntry] = []
    skill_count = 0

    cap_by_name = {cap.name: cap for cap in graph.capabilities}
    for cap in graph.capabilities:
        contract = contracts_by_id.get(cap.io_contract_ref)
        if contract is None:
            raise RuntimeError(
                f"Capability {cap.name} references unknown contract {cap.io_contract_ref}. "
                "Run extract-contracts before skill synthesis."
            )
        finding_refs = _resolve_finding_refs(cap, finding_index, contract)
        contract_refs = _collect_contract_refs(cap, cap_by_name, contracts_by_id)
        frontmatter_model = SkillFrontmatter(
            name=cap.name,
            description=cap.description,
            triggers=list(cap.when_to_use),
            required_finding_ids=list(cap.required_finding_ids),
            contract_refs=contract_refs,
            verification_hooks=list(cap.verification_hook_ids),
            findings=finding_refs,
        )
        body = _render_skill_body(cap, contract, finding_refs, finding_index)
        skill_dir = skills_dir / cap.name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        _write_skill(skill_path, frontmatter_model, body)
        skill_count += 1
        index_entries.append(SkillIndexEntry(
            capability_name=cap.name,
            trigger_keywords=_flatten_keywords(cap.when_to_use),
            skill_path=f"skills/{cap.name}/SKILL.md",
            contract_refs=contract_refs,
            composes=list(cap.composes),
        ))

    index = SkillIndex(
        generated_at=iso_now(),
        decision_log_version=version,
        entries=index_entries,
    )
    _write_index(skills_dir / "INDEX.md", index)

    return {
        "stage": "skill-synthesis",
        "decision_log_version": version,
        "skills_dir": str(skills_dir),
        "skill_count": skill_count,
        "index_path": str(skills_dir / "INDEX.md"),
    }


def _load_contracts(scaffold_root: Path, manifest: ContractManifest) -> dict[str, Contract]:
    out: dict[str, Contract] = {}
    for entry in manifest.entries:
        path = scaffold_root / entry.path
        if not path.exists():
            raise RuntimeError(f"contract file missing: {path}")
        payload = load_yaml(path) or {}
        out[entry.contract_id] = Contract.model_validate(payload.get("contract") or {})
    return out


def _resolve_finding_refs(
    cap: Capability,
    finding_index: dict[str, FindingRecord],
    contract: Contract,
) -> list[FindingRef]:
    """Build the skill frontmatter's `findings` list.

    Prefers records from the finding index (real findings); falls back to
    reusing the contract's required_findings for bootstrap mode where
    capability.required_finding_ids are citation IDs (no file_hash) and
    thus don't appear in the index.
    """
    refs: list[FindingRef] = []
    for fid in cap.required_finding_ids:
        rec = finding_index.get(fid)
        if rec is not None:
            quote = _best_quote(rec)
            refs.append(FindingRef(
                finding_id=rec.finding_id,
                citation_id=rec.citation_id,
                seed_path=rec.seed_path or f"seeds/{rec.citation_id}.md",
                quote=quote,
                locator={"file_hash": rec.file_hash[:12] if rec.file_hash else ""},
            ))
            continue
        # Bootstrap fallback: fid is a citation ID.
        fallback = next(
            (f for f in contract.required_findings if f.citation_id == fid or f.finding_id == fid),
            None,
        )
        if fallback is not None:
            refs.append(fallback)
            continue
        refs.append(FindingRef(
            finding_id=fid,
            citation_id=fid,
            seed_path=f"seeds/{fid}.md",
            locator={"stage": "bootstrap"},
        ))
    return refs


def _best_quote(rec: FindingRecord) -> str | None:
    for quote in rec.quotes:
        text = str(quote.get("text") or "").strip()
        if text:
            return _truncate(text, MAX_QUOTE_LENGTH)
    for claim in rec.claims:
        statement = str(claim.get("statement") or "").strip()
        if statement:
            return _truncate(statement, MAX_QUOTE_LENGTH)
    return None


def _collect_contract_refs(
    cap: Capability,
    cap_by_name: dict[str, Capability],
    contracts_by_id: dict[str, Contract],
) -> list[str]:
    refs: list[str] = [cap.io_contract_ref]
    for composed_name in cap.composes:
        composed = cap_by_name.get(composed_name)
        if composed is None:
            continue
        if composed.io_contract_ref not in refs:
            refs.append(composed.io_contract_ref)
    # Every ref must be present in the contract library.
    return [r for r in refs if r in contracts_by_id] or refs


def _render_skill_body(
    cap: Capability,
    contract: Contract,
    findings: list[FindingRef],
    finding_index: dict[str, FindingRecord],
) -> str:
    lines: list[str] = [
        f"# Skill: {cap.description}",
        "",
        "## Goal",
        cap.description,
        "",
        "## Procedure",
    ]
    procedure = _render_procedure(cap, contract)
    lines.extend(procedure)
    lines.extend([
        "",
        "## Inputs and Outputs",
        f"See `contracts/{contract.contract_id}.yaml`. Inputs: "
        + ", ".join(f"`{f.name}` ({f.modality})" for f in contract.inputs)
        + "; Outputs: "
        + ", ".join(f"`{f.name}` ({f.modality})" for f in contract.outputs)
        + ".",
        "",
        "## Invariants",
    ])
    lines.extend(_render_invariants(contract))
    lines.extend([
        "",
        "## Evidence",
    ])
    lines.extend(_render_evidence(findings, finding_index))
    lines.append("")
    return "\n".join(lines)


def _render_procedure(cap: Capability, contract: Contract) -> list[str]:
    steps: list[str] = []
    steps.append(
        f"1. Load inputs per `contracts/{contract.contract_id}.yaml` "
        "(see the Inputs and Outputs section)."
    )
    steps.append(
        "2. Apply the capability with the domain vocabulary "
        f"drawn from the cited findings: {', '.join(cap.citation_ids)}."
    )
    if cap.composes:
        steps.append(
            "3. Invoke composed skills where their triggers match the sub-task: "
            + ", ".join(f"`{n}`" for n in cap.composes) + "."
        )
    next_step = 4 if cap.composes else 3
    steps.append(
        f"{next_step}. Produce outputs per the contract and annotate every claim "
        "with its citation ID."
    )
    steps.append(
        f"{next_step + 1}. Run verification hooks: "
        + ", ".join(f"`{h}`" for h in cap.verification_hook_ids)
        + "."
    )
    return steps


def _render_invariants(contract: Contract) -> list[str]:
    out: list[str] = []
    for inv in contract.invariants[:MAX_INVARIANTS_IN_BODY]:
        citation_hint = ""
        if contract.required_findings:
            first = contract.required_findings[0]
            citation_hint = f" <{first.citation_id}>"
        out.append(f"- {inv}{citation_hint}")
    if len(contract.invariants) > MAX_INVARIANTS_IN_BODY:
        out.append(
            f"- *(… {len(contract.invariants) - MAX_INVARIANTS_IN_BODY} more in the contract file)*"
        )
    return out


def _render_evidence(findings: list[FindingRef], finding_index: dict[str, FindingRecord]) -> list[str]:
    out: list[str] = []
    for ref in findings[:MAX_EVIDENCE_IN_BODY]:
        rec = finding_index.get(ref.finding_id)
        if rec is not None:
            quote = _best_quote(rec) or "(no quote)"
            locator_desc = _summarize_locator(ref, rec)
            out.append(
                f"- `{ref.citation_id}` @ `{ref.seed_path}` {locator_desc}: \"{quote}\""
            )
        else:
            # Bootstrap: no record available; still produce non-empty line.
            out.append(
                f"- `{ref.citation_id}` @ `{ref.seed_path}`: referenced in decision log "
                "(findings not yet ingested)."
            )
    if not out:
        # Degenerate guard — should not happen because required_finding_ids is
        # non-empty, but belt-and-suspenders to keep the section non-stub.
        out.append("- (capability has no cited evidence; this is a bug — see capabilities.yaml)")
    return out


def _summarize_locator(ref: FindingRef, rec: FindingRecord) -> str:
    # Try to pick a representative locator from the rec's first quote/claim.
    for quote in rec.quotes:
        locator = quote.get("locator")
        if isinstance(locator, dict):
            return _format_locator(locator)
    for claim in rec.claims:
        locator = claim.get("locator")
        if isinstance(locator, dict):
            return _format_locator(locator)
    return _format_locator(ref.locator)


def _format_locator(locator: dict[str, Any]) -> str:
    if not locator:
        return ""
    parts = [f"{k}={v}" for k, v in locator.items() if v is not None and v != ""]
    if not parts:
        return ""
    return "(" + ", ".join(parts) + ")"


def _flatten_keywords(triggers: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in triggers:
        for token in _split_trigger(t):
            if token and token not in seen:
                seen.add(token)
                out.append(token)
    return out or ["unknown"]


def _split_trigger(trigger: str) -> list[str]:
    current: list[str] = []
    buf = []
    for ch in trigger.lower():
        if ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                current.append("".join(buf))
                buf = []
    if buf:
        current.append("".join(buf))
    return current


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _write_skill(path: Path, frontmatter_model: SkillFrontmatter, body: str) -> None:
    fm_dict = frontmatter_model.model_dump(mode="json")
    content = "---\n" + render_frontmatter(fm_dict) + "\n---\n" + body
    path.write_text(content, encoding="utf-8")


def _write_index(path: Path, index: SkillIndex) -> None:
    fm_block = render_frontmatter({"skill_index": index.model_dump(mode="json")})
    lines = [
        "---",
        fm_block.rstrip(),
        "---",
        "",
        "# Skill Index",
        "",
        "| Capability | Triggers | Skill | Contracts |",
        "| --- | --- | --- | --- |",
    ]
    for entry in index.entries:
        trig = "; ".join(entry.trigger_keywords)
        contracts = ", ".join(f"`{c}`" for c in entry.contract_refs)
        lines.append(
            f"| `{entry.capability_name}` | {trig} | [SKILL]({entry.capability_name}/SKILL.md) | {contracts} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
