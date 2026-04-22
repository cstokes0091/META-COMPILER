"""Operator voice calibration: scan findings + bindings for `author_role:
user_authored` content and emit a style corpus the workflow scaffold's
response-author agent can use to mimic the operator's tone.

Two CLI entry points:

* ``run_tag_seed`` — set ``author_role`` on an existing entry in
  ``source_bindings.yaml`` (`bindings:` for doc seeds, `code_bindings:` for
  code repos). Idempotent.

* ``run_wiki_build_style_corpus`` — walk every ``findings/*.json`` whose
  doc-level ``author_role`` is ``user_authored`` (resolved from the
  bindings entry), pull verbatim quotes + sentence-opener patterns + a
  cheap lexical signature, and write
  ``workspace-artifacts/wiki/style/style_corpus.md``.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..artifacts import ArtifactPaths, build_paths, ensure_layout, load_manifest, save_manifest
from ..io import dump_yaml, load_yaml
from ..utils import iso_now


VALID_AUTHOR_ROLES = {"external", "user_authored"}
USER_AUTHORED = "user_authored"

_TRANSITION_WORDS = {
    "however", "therefore", "moreover", "consequently", "nevertheless",
    "furthermore", "instead", "meanwhile", "specifically", "notably",
    "additionally", "ultimately", "alternatively", "similarly", "conversely",
}


def run_tag_seed(
    artifacts_root: Path,
    seed_path: str,
    author_role: str,
) -> dict[str, Any]:
    if author_role not in VALID_AUTHOR_ROLES:
        raise ValueError(
            f"author_role must be one of {sorted(VALID_AUTHOR_ROLES)}, got {author_role!r}"
        )
    paths = build_paths(artifacts_root)
    if not paths.source_bindings_path.exists():
        raise FileNotFoundError(
            "source_bindings.yaml is missing — run `meta-compiler meta-init` first."
        )
    payload = load_yaml(paths.source_bindings_path) or {}
    bindings = payload.setdefault("bindings", {}) if isinstance(payload, dict) else {}
    code_bindings = (
        payload.setdefault("code_bindings", {}) if isinstance(payload, dict) else {}
    )

    target = None
    bucket: dict[str, Any] | None = None
    bucket_name = ""
    for key in (seed_path, seed_path.rstrip("/") + "/"):
        if key in bindings:
            target = bindings[key]
            bucket = bindings
            bucket_name = "bindings"
            seed_path = key
            break
        if key in code_bindings:
            target = code_bindings[key]
            bucket = code_bindings
            bucket_name = "code_bindings"
            seed_path = key
            break
    if target is None or bucket is None:
        raise LookupError(
            f"seed path {seed_path!r} not found in bindings or code_bindings — "
            "register it via add-code-seed/bind-code-seed or `meta-compiler ingest` first."
        )

    target["author_role"] = author_role
    bucket[seed_path] = target
    dump_yaml(paths.source_bindings_path, payload)
    return {
        "status": "tagged",
        "bucket": bucket_name,
        "path": seed_path,
        "author_role": author_role,
    }


# ---------------------------------------------------------------------------
# Style corpus build
# ---------------------------------------------------------------------------


def _author_role_for_citation(payload: dict[str, Any]) -> dict[str, str]:
    """Return citation_id -> author_role from source_bindings.yaml."""
    out: dict[str, str] = {}
    bindings = payload.get("bindings") if isinstance(payload, dict) else None
    if isinstance(bindings, dict):
        for row in bindings.values():
            if not isinstance(row, dict):
                continue
            cid = row.get("citation_id")
            role = row.get("author_role")
            if isinstance(cid, str) and isinstance(role, str):
                out[cid] = role
    code_bindings = payload.get("code_bindings") if isinstance(payload, dict) else None
    if isinstance(code_bindings, dict):
        for row in code_bindings.values():
            if not isinstance(row, dict):
                continue
            cid = row.get("citation_id")
            role = row.get("author_role")
            if isinstance(cid, str) and isinstance(role, str):
                out[cid] = role
    return out


def _user_authored_findings(paths: ArtifactPaths) -> list[tuple[Path, dict[str, Any]]]:
    bindings_payload = load_yaml(paths.source_bindings_path) or {}
    role_for_cid = _author_role_for_citation(bindings_payload)
    if not paths.findings_dir.exists():
        return []
    out: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(paths.findings_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        # Allow either an explicit doc-level author_role (forward-compat) or
        # fall back to the role recorded for this citation in bindings.
        role = payload.get("author_role")
        if role not in VALID_AUTHOR_ROLES:
            cid = payload.get("citation_id")
            role = role_for_cid.get(cid) if isinstance(cid, str) else None
        if role == USER_AUTHORED:
            out.append((path, payload))
    return out


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")


def _harvest_quotes(payload: dict[str, Any], cid: str) -> list[dict[str, str]]:
    """Pull short verbatim quotes from findings.quotes[] (doc) or .definition (concepts)."""
    out: list[dict[str, str]] = []
    for q in payload.get("quotes") or []:
        if not isinstance(q, dict):
            continue
        text = (q.get("text") or "").strip()
        if not text or len(text) > 240:
            continue
        out.append({"text": text, "citation": cid})
    for concept in payload.get("concepts") or []:
        if not isinstance(concept, dict):
            continue
        definition = (concept.get("definition") or "").strip()
        if 30 <= len(definition) <= 240:
            out.append({"text": definition, "citation": cid})
    return out


def _harvest_sentence_openers(text: str) -> list[str]:
    openers: list[str] = []
    for sentence in _SENTENCE_RE.split(text):
        words = sentence.strip().split()
        if len(words) >= 3:
            openers.append(" ".join(words[:3]))
    return openers


def _lexical_signature(quotes: list[dict[str, str]]) -> dict[str, Any]:
    all_text = " ".join(q["text"] for q in quotes)
    if not all_text:
        return {
            "top_unigrams": [],
            "avg_sentence_length": 0,
            "preferred_transitions": [],
        }
    tokens = [t.lower() for t in _TOKEN_RE.findall(all_text)]
    unigrams = Counter(t for t in tokens if len(t) > 4 and t not in _TRANSITION_WORDS)
    sentences = [s.strip() for s in _SENTENCE_RE.split(all_text) if s.strip()]
    avg_len = (
        round(sum(len(s.split()) for s in sentences) / max(1, len(sentences)), 1)
        if sentences
        else 0
    )
    transitions = Counter(t for t in tokens if t in _TRANSITION_WORDS)
    return {
        "top_unigrams": [w for w, _ in unigrams.most_common(15)],
        "avg_sentence_length": avg_len,
        "preferred_transitions": [w for w, _ in transitions.most_common(8)],
    }


def run_wiki_build_style_corpus(
    artifacts_root: Path,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    del workspace_root  # signature parity
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    findings = _user_authored_findings(paths)

    quotes: list[dict[str, str]] = []
    sentence_openers: Counter[str] = Counter()
    citations: set[str] = set()
    for _path, payload in findings:
        cid = str(payload.get("citation_id") or "src-unknown")
        citations.add(cid)
        quotes.extend(_harvest_quotes(payload, cid))
        for q in payload.get("quotes") or []:
            if isinstance(q, dict) and isinstance(q.get("text"), str):
                for opener in _harvest_sentence_openers(q["text"]):
                    sentence_openers[opener] += 1

    signature = _lexical_signature(quotes)
    generated_at = iso_now()

    style_dir = paths.wiki_dir / "style"
    style_dir.mkdir(parents=True, exist_ok=True)
    output = style_dir / "style_corpus.md"

    lines = [
        "# Style Corpus",
        "",
        f"Generated: {generated_at}",
        f"Source citations: {sorted(citations) if citations else '(none)'}",
        "",
        "## Verbatim Quotes",
    ]
    if quotes:
        for q in quotes[:60]:
            lines.append(f'- "{q["text"]}" — [{q["citation"]}]')
    else:
        lines.append("- (no user_authored quotes available — register at least one seed with --author-role user_authored)")

    lines.extend(["", "## Paraphrasable Patterns"])
    if sentence_openers:
        for opener, count in sentence_openers.most_common(20):
            lines.append(f"- `{opener}…` ({count})")
    else:
        lines.append("- (no patterns harvested)")

    lines.extend(
        [
            "",
            "## Lexical Signature",
            f"- top_unigrams: {signature['top_unigrams']}",
            f"- avg_sentence_length: {signature['avg_sentence_length']}",
            f"- preferred_transitions: {signature['preferred_transitions']}",
            "",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")

    # Record path in manifest so other stages can find it.
    manifest = load_manifest(paths)
    if manifest:
        wiki_block = manifest["workspace_manifest"].setdefault("wiki", {})
        wiki_block["style_corpus_path"] = output.relative_to(paths.root).as_posix()
        save_manifest(paths, manifest)

    return {
        "status": "ok",
        "style_corpus_path": output.relative_to(paths.root).as_posix(),
        "quote_count": len(quotes),
        "citation_count": len(citations),
        "user_authored_findings": len(findings),
    }
