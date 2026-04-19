"""ingest command: Prepare seeds for full-fidelity extraction by seed-reader subagents.

Deterministic pre-work only. Enumerates seeds by scope (all|new), pre-extracts
PDF seeds via scripts/pdf_to_text.py and other non-plaintext seeds via
scripts/read_document.py, computes citation IDs, and writes a work plan YAML
that the ingest-orchestrator agent fans out against.

The orchestrator is responsible for the LLM-driven fan-out, findings JSON
persistence, findings index updates, and ingest_report.yaml emission.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..artifacts import (
    ArtifactPaths,
    build_paths,
    ensure_layout,
    list_seed_files,
)
from ..io import dump_yaml, load_yaml
from ..utils import iso_now, sha256_file, slugify


BINARY_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx"}


def _load_findings_index(paths: ArtifactPaths) -> dict[str, Any]:
    if not paths.findings_index_path.exists():
        return {
            "findings_index": {
                "version": 1,
                "last_updated": "",
                "processed_seeds": [],
            }
        }
    raw = load_yaml(paths.findings_index_path)
    if not raw or not isinstance(raw, dict):
        return {
            "findings_index": {
                "version": 1,
                "last_updated": "",
                "processed_seeds": [],
            }
        }
    raw.setdefault("findings_index", {"version": 1, "last_updated": "", "processed_seeds": []})
    raw["findings_index"].setdefault("processed_seeds", [])
    return raw


def _known_hashes_in_findings(index: dict[str, Any]) -> set[str]:
    processed = index.get("findings_index", {}).get("processed_seeds", [])
    hashes: set[str] = set()
    for entry in processed:
        if isinstance(entry, dict):
            fh = entry.get("file_hash")
            if isinstance(fh, str):
                hashes.add(fh)
    return hashes


def _load_citation_index(paths: ArtifactPaths) -> dict[str, Any]:
    if not paths.citations_index_path.exists():
        return {"citations": {}}
    raw = load_yaml(paths.citations_index_path)
    if not raw or not isinstance(raw, dict):
        return {"citations": {}}
    raw.setdefault("citations", {})
    return raw


def _citation_id_by_hash(citation_index: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    citations = citation_index.get("citations", {})
    if not isinstance(citations, dict):
        return mapping
    for cid, entry in citations.items():
        if not isinstance(entry, dict):
            continue
        fh = (entry.get("metadata") or {}).get("file_hash")
        if isinstance(fh, str) and fh:
            mapping[fh] = cid
    return mapping


def _mint_citation_id(seed_stem: str, existing_ids: set[str]) -> str:
    slug = slugify(seed_stem)[:50] or "seed"
    candidate = f"src-{slug}"
    if candidate not in existing_ids:
        return candidate
    suffix = 2
    while f"{candidate}-{suffix}" in existing_ids:
        suffix += 1
    return f"{candidate}-{suffix}"


def _preextract_script_path(workspace_root: Path, suffix: str) -> tuple[Path, str]:
    script_name = "pdf_to_text.py" if suffix == ".pdf" else "read_document.py"
    return workspace_root / "scripts" / script_name, script_name


def _preextract_binary(seed: Path, target: Path, workspace_root: Path) -> tuple[bool, str]:
    if target.exists():
        return True, "cached"
    target.parent.mkdir(parents=True, exist_ok=True)
    script_path, script_name = _preextract_script_path(workspace_root, seed.suffix.lower())
    if not script_path.exists():
        return False, f"{script_name} not found at {script_path}"
    try:
        subprocess.run(
            [
                sys.executable,
                str(script_path),
                str(seed),
                "--output",
                str(target),
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
        return False, f"extraction failed: {stderr.strip()[:200]}"
    return True, "extracted"


def run_ingest(
    artifacts_root: Path,
    workspace_root: Path,
    scope: str = "new",
) -> dict[str, Any]:
    """Prepare the work plan for the ingest-orchestrator agent.

    Parameters
    ----------
    artifacts_root : Path
        Path to the workspace-artifacts directory.
    workspace_root : Path
        Root directory of the workspace (used to find scripts/).
    scope : str
        "all" (every seed) or "new" (seeds not in findings index).
    """
    if scope not in {"all", "new"}:
        raise ValueError(f"scope must be 'all' or 'new', got {scope!r}")

    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    seeds = list_seed_files(paths)
    if not seeds:
        return {
            "status": "no_seeds",
            "scope": scope,
            "work_items": 0,
            "work_plan_path": None,
        }

    findings_index = _load_findings_index(paths)
    known_hashes = _known_hashes_in_findings(findings_index)

    citation_index = _load_citation_index(paths)
    hash_to_cid = _citation_id_by_hash(citation_index)
    existing_ids = set(citation_index.get("citations", {}).keys())

    preextract_root = paths.runtime_dir / "ingest"
    preextract_root.mkdir(parents=True, exist_ok=True)

    work_items: list[dict[str, Any]] = []
    skipped_existing = 0
    preextract_failures: list[dict[str, Any]] = []

    for seed in seeds:
        file_hash = sha256_file(seed)
        if scope == "new" and file_hash in known_hashes:
            skipped_existing += 1
            continue

        citation_id = hash_to_cid.get(file_hash) or _mint_citation_id(seed.stem, existing_ids)
        existing_ids.add(citation_id)
        hash_to_cid[file_hash] = citation_id

        relative_seed = seed.relative_to(paths.root).as_posix()
        extracted_path: str | None = None

        if seed.suffix.lower() in BINARY_EXTENSIONS:
            target = preextract_root / f"{citation_id}.md"
            ok, note = _preextract_binary(seed, target, workspace_root)
            if ok:
                extracted_path = str(target.relative_to(paths.root).as_posix())
            else:
                preextract_failures.append({
                    "seed_path": relative_seed,
                    "citation_id": citation_id,
                    "reason": note,
                })
                continue

        work_items.append({
            "citation_id": citation_id,
            "seed_path": relative_seed,
            "file_hash": file_hash,
            "extracted_path": extracted_path,
            "suffix": seed.suffix.lower(),
            "size_bytes": seed.stat().st_size,
        })

    work_plan = {
        "work_plan": {
            "version": 1,
            "generated_at": iso_now(),
            "scope": scope,
            "artifacts_root": str(paths.root),
            "findings_dir": str(paths.findings_dir.relative_to(paths.root).as_posix()),
            "findings_index_path": str(paths.findings_index_path.relative_to(paths.root).as_posix()),
            "work_items": work_items,
            "preextract_failures": preextract_failures,
            "counts": {
                "seeds_total": len(seeds),
                "work_items": len(work_items),
                "skipped_already_extracted": skipped_existing,
                "preextract_failures": len(preextract_failures),
            },
        }
    }

    plan_path = preextract_root / "work_plan.yaml"
    dump_yaml(plan_path, work_plan)

    return {
        "status": "ready_for_orchestrator",
        "scope": scope,
        "work_items": len(work_items),
        "skipped_already_extracted": skipped_existing,
        "preextract_failures": len(preextract_failures),
        "work_plan_path": str(plan_path.relative_to(paths.root).as_posix()),
        "findings_dir": str(paths.findings_dir.relative_to(paths.root).as_posix()),
        "findings_index_path": str(paths.findings_index_path.relative_to(paths.root).as_posix()),
        "instruction": (
            "Invoke the ingest-orchestrator agent. It will read the work plan, "
            "fan out seed-reader subagents, write one JSON per seed to findings/, "
            "update findings/index.yaml, and emit wiki/reports/ingest_report.yaml."
        ),
    }


REQUIRED_FINDINGS_FIELDS = {
    "citation_id",
    "seed_path",
    "file_hash",
    "extracted_at",
    "extractor",
    "document_metadata",
    "concepts",
    "quotes",
    "equations",
    "claims",
    "tables_figures",
    "relationships",
    "open_questions",
    "extraction_stats",
}


def validate_findings_file(path: Path) -> list[str]:
    """Return a list of schema violations for one findings JSON file."""
    issues: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path.name}: invalid JSON ({exc})"]

    if not isinstance(payload, dict):
        return [f"{path.name}: top-level must be an object"]

    missing = REQUIRED_FINDINGS_FIELDS - payload.keys()
    if missing:
        issues.append(f"{path.name}: missing required fields: {sorted(missing)}")

    for field in ("concepts", "quotes", "equations", "claims", "tables_figures", "relationships", "open_questions"):
        if field in payload and not isinstance(payload[field], list):
            issues.append(f"{path.name}: {field!r} must be a list")

    for idx, quote in enumerate(payload.get("quotes", []) or []):
        if not isinstance(quote, dict):
            issues.append(f"{path.name}: quotes[{idx}] must be an object")
            continue
        if not quote.get("text"):
            issues.append(f"{path.name}: quotes[{idx}].text is empty")
        locator = quote.get("locator")
        if not isinstance(locator, dict) or not (locator.get("page") or locator.get("section")):
            issues.append(f"{path.name}: quotes[{idx}].locator must include page or section")

    for idx, claim in enumerate(payload.get("claims", []) or []):
        if not isinstance(claim, dict):
            issues.append(f"{path.name}: claims[{idx}] must be an object")
            continue
        if not claim.get("statement"):
            issues.append(f"{path.name}: claims[{idx}].statement is empty")
        locator = claim.get("locator")
        if not isinstance(locator, dict) or not (locator.get("page") or locator.get("section")):
            issues.append(f"{path.name}: claims[{idx}].locator must include page or section")

    for idx, equation in enumerate(payload.get("equations", []) or []):
        if not isinstance(equation, dict):
            issues.append(f"{path.name}: equations[{idx}] must be an object")
            continue
        locator = equation.get("locator")
        if not isinstance(locator, dict) or not (locator.get("page") or locator.get("section")):
            issues.append(f"{path.name}: equations[{idx}].locator must include page or section")

    stats = payload.get("extraction_stats")
    if isinstance(stats, dict):
        completeness = stats.get("completeness")
        if completeness not in {"full", "partial"}:
            issues.append(f"{path.name}: extraction_stats.completeness must be 'full' or 'partial'")

    return issues


def validate_all_findings(artifacts_root: Path) -> dict[str, Any]:
    """Validate every findings JSON under workspace-artifacts/wiki/findings/."""
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    results: list[dict[str, Any]] = []
    total_issues = 0
    for findings_file in sorted(paths.findings_dir.glob("*.json")):
        file_issues = validate_findings_file(findings_file)
        results.append({
            "path": str(findings_file.relative_to(paths.root).as_posix()),
            "issue_count": len(file_issues),
            "issues": file_issues,
        })
        total_issues += len(file_issues)

    return {
        "findings_scanned": len(results),
        "total_issues": total_issues,
        "per_file": results,
    }


# ---------------------------------------------------------------------------
# Phase A: ingest preflight + postflight (prompt-as-conductor bookends)
#
# The CLI does the mechanical preflight/postflight bookkeeping; the
# `ingest-orchestrator` agent's `mode=preflight` / `mode=postflight` modes
# do the semantic judgment. Mirrors the Stage 2 hardening pattern.
# ---------------------------------------------------------------------------


def _check(name: str, result: str, evidence: str = "", remediation: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "result": result}
    if evidence:
        entry["evidence"] = evidence
    if remediation:
        entry["remediation"] = remediation
    return entry


def _ingest_preflight_checks(
    paths: ArtifactPaths,
    workspace_root: Path,
    scope: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Mechanical preflight checks for ingest. Returns (checks, blocking_reasons)."""
    checks: list[dict[str, Any]] = []
    blocking: list[str] = []

    seeds = list_seed_files(paths)
    if not seeds:
        checks.append(
            _check(
                "seeds_present",
                "FAIL",
                evidence=f"no files in {paths.seeds_dir.relative_to(paths.root).as_posix()}",
                remediation=(
                    "Add seed documents to workspace-artifacts/seeds/ before "
                    "running the ingest-orchestrator."
                ),
            )
        )
        blocking.append("no seeds present")
    else:
        checks.append(
            _check(
                "seeds_present",
                "PASS",
                evidence=f"{len(seeds)} seed(s) tracked",
            )
        )

    pdf_script = workspace_root / "scripts" / "pdf_to_text.py"
    doc_script = workspace_root / "scripts" / "read_document.py"
    binary_seeds = [s for s in seeds if s.suffix.lower() in BINARY_EXTENSIONS]
    needs_pdf = any(s.suffix.lower() == ".pdf" for s in binary_seeds)
    needs_doc = any(s.suffix.lower() in {".docx", ".xlsx", ".pptx"} for s in binary_seeds)

    if needs_pdf and not pdf_script.exists():
        checks.append(
            _check(
                "pdf_to_text_script_present",
                "FAIL",
                evidence=f"missing {pdf_script}",
                remediation="Restore scripts/pdf_to_text.py before ingesting PDF seeds.",
            )
        )
        blocking.append("pdf_to_text.py missing but PDF seeds present")
    elif needs_pdf:
        checks.append(_check("pdf_to_text_script_present", "PASS"))

    if needs_doc and not doc_script.exists():
        checks.append(
            _check(
                "read_document_script_present",
                "FAIL",
                evidence=f"missing {doc_script}",
                remediation="Restore scripts/read_document.py before ingesting DOCX/XLSX/PPTX seeds.",
            )
        )
        blocking.append("read_document.py missing but binary seeds present")
    elif needs_doc:
        checks.append(_check("read_document_script_present", "PASS"))

    work_plan_path = paths.ingest_runtime_dir / "work_plan.yaml"
    if not work_plan_path.exists():
        checks.append(
            _check(
                "work_plan_present",
                "FAIL",
                evidence=f"missing {work_plan_path.relative_to(paths.root).as_posix()}",
                remediation=(
                    f"Run `meta-compiler ingest --scope {scope}` to write the work plan first."
                ),
            )
        )
        blocking.append("work_plan.yaml missing — run `meta-compiler ingest` first")
    else:
        plan = load_yaml(work_plan_path) or {}
        body = plan.get("work_plan") or {}
        work_items = body.get("work_items") or []
        preextract_failures = body.get("preextract_failures") or []
        plan_scope = body.get("scope")
        if plan_scope != scope:
            checks.append(
                _check(
                    "work_plan_scope_matches",
                    "FAIL",
                    evidence=f"work plan scope is {plan_scope!r}, requested {scope!r}",
                    remediation=f"Re-run `meta-compiler ingest --scope {scope}`.",
                )
            )
            blocking.append("work plan scope mismatch")
        else:
            checks.append(
                _check(
                    "work_plan_scope_matches",
                    "PASS",
                    evidence=f"scope={plan_scope}, work_items={len(work_items)}",
                )
            )
        if preextract_failures:
            checks.append(
                _check(
                    "preextract_clean",
                    "FAIL",
                    evidence=f"{len(preextract_failures)} pre-extraction failure(s)",
                    remediation=(
                        "Inspect work_plan.yaml→preextract_failures and fix the seed "
                        "or its extraction script before fan-out."
                    ),
                )
            )
            blocking.append(f"{len(preextract_failures)} pre-extraction failures recorded")
        else:
            checks.append(_check("preextract_clean", "PASS"))

    return checks, blocking


def run_ingest_precheck(
    artifacts_root: Path,
    workspace_root: Path,
    scope: str = "new",
) -> dict[str, Any]:
    """Stage 1A ingest Step 2 — write the precheck request for the orchestrator.

    Mechanical only. The agent reads the request and renders a semantic
    PROCEED|BLOCK verdict in `precheck_verdict.yaml`. Aborts on any FAIL
    so the orchestrator never fans out against a broken work plan.
    """
    if scope not in {"all", "new"}:
        raise ValueError(f"scope must be 'all' or 'new', got {scope!r}")

    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    checks, blocking = _ingest_preflight_checks(paths, workspace_root, scope)
    generated_at = iso_now()

    payload = {
        "ingest_precheck_request": {
            "generated_at": generated_at,
            "scope": scope,
            "inputs": {
                "work_plan": str(
                    (paths.ingest_runtime_dir / "work_plan.yaml")
                    .relative_to(paths.root)
                    .as_posix()
                ),
                "seeds_dir": str(paths.seeds_dir.relative_to(paths.root).as_posix()),
                "findings_index": str(
                    paths.findings_index_path.relative_to(paths.root).as_posix()
                ),
                "problem_statement": str(
                    (workspace_root / "PROBLEM_STATEMENT.md").as_posix()
                ),
            },
            "mechanical_checks": checks,
            "verdict_output_path": str(
                paths.ingest_precheck_verdict_path.relative_to(paths.root).as_posix()
            ),
        }
    }
    dump_yaml(paths.ingest_precheck_request_path, payload)

    if blocking:
        blocking_lines = "\n".join(f"  - {reason}" for reason in blocking)
        raise RuntimeError(
            "Ingest preflight blocked. Failing checks:\n"
            f"{blocking_lines}\n"
            f"See {paths.ingest_precheck_request_path.relative_to(paths.root).as_posix()} "
            "for full evidence."
        )

    return {
        "status": "ready_for_orchestrator",
        "scope": scope,
        "precheck_request_path": str(
            paths.ingest_precheck_request_path.relative_to(paths.root).as_posix()
        ),
        "verdict_output_path": str(
            paths.ingest_precheck_verdict_path.relative_to(paths.root).as_posix()
        ),
        "checks": checks,
        "instruction": (
            "Invoke @ingest-orchestrator mode=preflight next; it writes "
            f"{paths.ingest_precheck_verdict_path.name} with verdict PROCEED|BLOCK."
        ),
    }


def _ingest_postflight_checks(
    paths: ArtifactPaths,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Mechanical postflight checks. Returns (checks, blocking_reasons)."""
    checks: list[dict[str, Any]] = []
    blocking: list[str] = []

    if not paths.ingest_report_path.exists():
        checks.append(
            _check(
                "ingest_report_present",
                "FAIL",
                evidence=f"missing {paths.ingest_report_path.relative_to(paths.root).as_posix()}",
                remediation=(
                    "ingest-orchestrator must write ingest_report.yaml before "
                    "postflight. Re-run the orchestrator if it stopped early."
                ),
            )
        )
        blocking.append("ingest_report.yaml missing — orchestrator did not finish")
    else:
        checks.append(_check("ingest_report_present", "PASS"))

    findings = sorted(paths.findings_dir.glob("*.json"))
    checks.append(
        _check(
            "findings_files_present",
            "PASS" if findings else "FAIL",
            evidence=f"{len(findings)} findings JSON file(s) on disk",
            remediation=(
                "No findings on disk — the orchestrator never persisted any output."
                if not findings
                else ""
            ),
        )
    )
    if not findings:
        blocking.append("no findings JSON files on disk")

    findings_validation = validate_all_findings(paths.root)
    if findings_validation["total_issues"] > 0:
        checks.append(
            _check(
                "findings_schema_valid",
                "FAIL",
                evidence=(
                    f"{findings_validation['total_issues']} schema issue(s) across "
                    f"{findings_validation['findings_scanned']} file(s)"
                ),
                remediation=(
                    "Re-run failing seed-readers; or run "
                    "`meta-compiler ingest-validate` for the per-file detail."
                ),
            )
        )
        blocking.append(f"{findings_validation['total_issues']} findings schema issues")
    else:
        checks.append(
            _check(
                "findings_schema_valid",
                "PASS",
                evidence=f"all {findings_validation['findings_scanned']} files schema-valid",
            )
        )

    return checks, blocking


def run_ingest_postcheck(
    artifacts_root: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    """Stage 1A ingest Step 5 — write the postcheck request for the orchestrator.

    Mechanical only. The agent reads the request, spot-verifies a sample of
    quotes against pre-extracted text, and writes
    `postcheck_verdict.yaml` with `verdict: PROCEED | REVISE`. Aborts on any
    mechanical FAIL so the postflight semantic audit never runs against a
    broken set of findings.
    """
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    checks, blocking = _ingest_postflight_checks(paths)
    generated_at = iso_now()

    payload = {
        "ingest_postcheck_request": {
            "generated_at": generated_at,
            "inputs": {
                "ingest_report": str(
                    paths.ingest_report_path.relative_to(paths.root).as_posix()
                ),
                "findings_dir": str(
                    paths.findings_dir.relative_to(paths.root).as_posix()
                ),
                "findings_index": str(
                    paths.findings_index_path.relative_to(paths.root).as_posix()
                ),
                "work_plan": str(
                    (paths.ingest_runtime_dir / "work_plan.yaml")
                    .relative_to(paths.root)
                    .as_posix()
                ),
            },
            "mechanical_checks": checks,
            "verdict_output_path": str(
                paths.ingest_postcheck_verdict_path.relative_to(paths.root).as_posix()
            ),
        }
    }
    dump_yaml(paths.ingest_postcheck_request_path, payload)

    if blocking:
        blocking_lines = "\n".join(f"  - {reason}" for reason in blocking)
        raise RuntimeError(
            "Ingest postflight blocked. Failing checks:\n"
            f"{blocking_lines}\n"
            f"See {paths.ingest_postcheck_request_path.relative_to(paths.root).as_posix()} "
            "for full evidence."
        )

    return {
        "status": "ready_for_orchestrator",
        "postcheck_request_path": str(
            paths.ingest_postcheck_request_path.relative_to(paths.root).as_posix()
        ),
        "verdict_output_path": str(
            paths.ingest_postcheck_verdict_path.relative_to(paths.root).as_posix()
        ),
        "checks": checks,
        "instruction": (
            "Invoke @ingest-orchestrator mode=postflight next; it writes "
            f"{paths.ingest_postcheck_verdict_path.name} with verdict PROCEED|REVISE."
        ),
    }
