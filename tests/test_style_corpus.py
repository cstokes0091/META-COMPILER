"""Tests for run_tag_seed and run_wiki_build_style_corpus."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import dump_yaml, load_yaml
from meta_compiler.stages.style_corpus_stage import (
    run_tag_seed,
    run_wiki_build_style_corpus,
)


def _seed_workspace(tmp_path: Path) -> Path:
    artifacts_root = tmp_path / "ws" / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)
    dump_yaml(
        paths.source_bindings_path,
        {
            "bindings": {
                "seeds/personal_writing.md": {
                    "citation_id": "src-personal",
                    "sha256": "a" * 64,
                    "first_seen": "2026-04-01T00:00:00+00:00",
                    "last_seen": "2026-04-01T00:00:00+00:00",
                },
                "seeds/external_paper.md": {
                    "citation_id": "src-external",
                    "sha256": "b" * 64,
                    "first_seen": "2026-04-01T00:00:00+00:00",
                    "last_seen": "2026-04-01T00:00:00+00:00",
                },
            },
            "code_bindings": {},
        },
    )
    return artifacts_root


def _write_findings(paths, citation_id: str, quotes: list[str]) -> None:
    payload = {
        "citation_id": citation_id,
        "seed_path": f"seeds/{citation_id}.md",
        "file_hash": "x" * 64,
        "extracted_at": "2026-04-01T00:00:00+00:00",
        "extractor": "test",
        "concepts": [],
        "quotes": [{"text": q, "locator": {"page": 1}} for q in quotes],
        "equations": [],
        "claims": [],
        "tables_figures": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {},
        "document_metadata": {},
    }
    findings_path = paths.findings_dir / f"{citation_id}.json"
    findings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_tag_seed_persists_author_role(tmp_path):
    artifacts_root = _seed_workspace(tmp_path)
    result = run_tag_seed(
        artifacts_root=artifacts_root,
        seed_path="seeds/personal_writing.md",
        author_role="user_authored",
    )
    assert result["status"] == "tagged"
    payload = load_yaml(build_paths(artifacts_root).source_bindings_path)
    assert (
        payload["bindings"]["seeds/personal_writing.md"]["author_role"]
        == "user_authored"
    )


def test_tag_seed_rejects_unknown_path(tmp_path):
    artifacts_root = _seed_workspace(tmp_path)
    with pytest.raises(LookupError):
        run_tag_seed(
            artifacts_root=artifacts_root,
            seed_path="seeds/nope.md",
            author_role="user_authored",
        )


def test_tag_seed_rejects_invalid_role(tmp_path):
    artifacts_root = _seed_workspace(tmp_path)
    with pytest.raises(ValueError):
        run_tag_seed(
            artifacts_root=artifacts_root,
            seed_path="seeds/personal_writing.md",
            author_role="weird",
        )


def test_build_style_corpus_skips_external_authors(tmp_path):
    artifacts_root = _seed_workspace(tmp_path)
    paths = build_paths(artifacts_root)
    _write_findings(paths, "src-external", ["External quote that should not appear."])
    # Even without tagging anything user_authored, the corpus is still emitted
    # but with zero quotes.
    result = run_wiki_build_style_corpus(artifacts_root=artifacts_root)
    assert result["status"] == "ok"
    assert result["quote_count"] == 0
    text = (paths.wiki_dir / "style" / "style_corpus.md").read_text()
    assert "External quote" not in text


def test_build_style_corpus_collects_user_authored(tmp_path):
    artifacts_root = _seed_workspace(tmp_path)
    paths = build_paths(artifacts_root)
    _write_findings(
        paths,
        "src-personal",
        [
            "However, the answer is rarely simple; instead we balance trade-offs.",
            "Therefore the conclusion follows from the prior analysis.",
        ],
    )
    _write_findings(paths, "src-external", ["External quote, do not surface."])
    run_tag_seed(
        artifacts_root=artifacts_root,
        seed_path="seeds/personal_writing.md",
        author_role="user_authored",
    )
    result = run_wiki_build_style_corpus(artifacts_root=artifacts_root)
    assert result["status"] == "ok"
    assert result["citation_count"] == 1
    assert result["quote_count"] >= 2
    text = (paths.wiki_dir / "style" / "style_corpus.md").read_text()
    assert "src-personal" in text
    assert "External quote" not in text
    # Lexical signature picks up at least one transition word.
    assert "however" in text or "therefore" in text
