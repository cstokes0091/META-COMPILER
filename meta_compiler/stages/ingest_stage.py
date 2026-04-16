"""ingest command: Prepare seeds for full-fidelity extraction by seed-reader subagents.

Deterministic pre-work only. Enumerates seeds by scope (all|new), pre-extracts
non-plaintext seeds via scripts/read_document.py, computes citation IDs, and
writes a work plan YAML that the ingest-orchestrator agent fans out against.

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


def _preextract_binary(seed: Path, target: Path, workspace_root: Path) -> tuple[bool, str]:
    if target.exists():
        return True, "cached"
    target.parent.mkdir(parents=True, exist_ok=True)
    script_path = workspace_root / "scripts" / "read_document.py"
    if not script_path.exists():
        return False, f"read_document.py not found at {script_path}"
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
