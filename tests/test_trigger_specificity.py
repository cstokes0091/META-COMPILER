"""Trigger-specificity helper tests.

Commit 2: exercises the pure-function helpers in meta_compiler.findings_loader
and meta_compiler.validation._is_generic_trigger against synthesized
FindingRecord fixtures. Nothing in the scaffold or CLI path consumes them
yet; wiring lands in Commit 3 (capability_compile_stage) and Commit 7
(validate_scaffold rewrite).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from meta_compiler.artifacts import build_paths
from meta_compiler.findings_loader import (
    GENERIC_TRIGGER_STOPWORDS,
    FindingRecord,
    build_finding_index,
    concept_vocabulary,
    decision_log_vocabulary,
    load_all_findings,
    trigger_content_tokens,
)
from meta_compiler.validation import _is_generic_trigger


def _doc_finding(tmp_path: Path, citation_id: str, file_hash: str, *, concepts: list[dict], claims: list[dict] | None = None) -> Path:
    payload = {
        "citation_id": citation_id,
        "seed_path": f"seeds/{citation_id}.md",
        "file_hash": file_hash,
        "extracted_at": "2026-04-22T00:00:00+00:00",
        "extractor": "test",
        "document_metadata": {"title": citation_id},
        "concepts": concepts,
        "quotes": [],
        "equations": [],
        "claims": claims or [],
        "tables_figures": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {},
    }
    path = tmp_path / f"wiki/findings/{citation_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _code_finding(tmp_path: Path, citation_id: str, file_hash: str, *, concepts: list[dict]) -> Path:
    payload = {
        "source_type": "code",
        "citation_id": citation_id,
        "seed_path": f"seeds/code/{citation_id}.py",
        "file_hash": file_hash,
        "extracted_at": "2026-04-22T00:00:00+00:00",
        "extractor": "test",
        "file_metadata": {"language": "python", "loc": 50},
        "concepts": concepts,
        "symbols": [],
        "claims": [],
        "quotes": [],
        "dependencies": [],
        "call_edges": [],
        "relationships": [],
        "open_questions": [],
        "extraction_stats": {},
    }
    path = tmp_path / f"wiki/findings/{citation_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestTokenize:
    def test_trigger_content_tokens_strips_stopwords(self):
        assert trigger_content_tokens("use when implementing") == set()

    def test_trigger_content_tokens_keeps_domain_words(self):
        assert "schema" in trigger_content_tokens("validate decision log schema")

    def test_trigger_content_tokens_handles_punctuation(self):
        assert trigger_content_tokens("validate decision-log schema") >= {"decision", "log", "schema"}

    def test_stopwords_contains_use_when(self):
        assert "use" in GENERIC_TRIGGER_STOPWORDS
        assert "when" in GENERIC_TRIGGER_STOPWORDS


class TestFindingsLoader:
    def test_load_all_findings_doc(self, tmp_path):
        _doc_finding(tmp_path, "src-a", "hash-abcdef123456", concepts=[{"name": "Schema"}])
        paths = build_paths(tmp_path)
        records = load_all_findings(paths)
        assert len(records) == 1
        rec = records[0]
        assert rec.finding_id == "src-a#hash-abcdef1"  # first 12 chars of "hash-abcdef123456"
        assert rec.citation_id == "src-a"
        assert rec.source_type == "document"
        assert rec.concepts[0]["name"] == "Schema"

    def test_load_all_findings_code(self, tmp_path):
        _code_finding(tmp_path, "src-repo-main", "codehash00001111", concepts=[{"name": "Orchestrator"}])
        paths = build_paths(tmp_path)
        records = load_all_findings(paths)
        assert len(records) == 1
        assert records[0].source_type == "code"

    def test_load_all_findings_skips_missing_required(self, tmp_path):
        bad = tmp_path / "wiki/findings/bad.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text(json.dumps({"citation_id": "src-x"}), encoding="utf-8")  # no file_hash
        paths = build_paths(tmp_path)
        assert load_all_findings(paths) == []

    def test_load_all_findings_handles_legacy_shape(self, tmp_path):
        legacy = tmp_path / "wiki/findings/legacy.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({
            "source_id": "src-legacy",
            "file_hash": "legacyhash00",
            "findings": [{
                "citation_id": "src-legacy",
                "seed_path": "seeds/legacy.md",
                "concepts": [{"name": "Thing"}],
            }],
        }), encoding="utf-8")
        paths = build_paths(tmp_path)
        records = load_all_findings(paths)
        assert len(records) == 1
        assert records[0].citation_id == "src-legacy"

    def test_load_all_findings_empty_dir(self, tmp_path):
        (tmp_path / "wiki/findings").mkdir(parents=True)
        paths = build_paths(tmp_path)
        assert load_all_findings(paths) == []

    def test_load_all_findings_missing_dir(self, tmp_path):
        paths = build_paths(tmp_path)
        assert load_all_findings(paths) == []

    def test_build_finding_index(self):
        rec = FindingRecord(
            finding_id="src-a#hash",
            citation_id="src-a",
            file_hash="hash",
            seed_path="seeds/a.md",
            source_type="document",
        )
        idx = build_finding_index([rec])
        assert idx == {"src-a#hash": rec}


class TestConceptVocabulary:
    def test_draws_tokens_from_concept_names(self, tmp_path):
        _doc_finding(
            tmp_path,
            "src-a",
            "h1" * 8,
            concepts=[{"name": "Decision-Log Schema"}, {"name": "Citation Traceability"}],
        )
        vocab = concept_vocabulary(load_all_findings(build_paths(tmp_path)))
        assert {"decision", "log", "schema", "citation", "traceability"} <= vocab

    def test_draws_tokens_from_aliases(self, tmp_path):
        _doc_finding(
            tmp_path,
            "src-a",
            "h1" * 8,
            concepts=[{"name": "Orchestrator", "aliases": ["Conductor"]}],
        )
        vocab = concept_vocabulary(load_all_findings(build_paths(tmp_path)))
        assert "conductor" in vocab

    def test_draws_tokens_from_claim_statements(self, tmp_path):
        _doc_finding(
            tmp_path,
            "src-a",
            "h1" * 8,
            concepts=[{"name": "Schema"}],
            claims=[{"statement": "Every requirement has a citation", "locator": {"page": 1}}],
        )
        vocab = concept_vocabulary(load_all_findings(build_paths(tmp_path)))
        assert "requirement" in vocab
        assert "citation" in vocab

    def test_empty_when_no_records(self):
        assert concept_vocabulary([]) == set()


class TestDecisionLogVocabulary:
    def test_draws_from_architecture_component(self):
        decision_log = {
            "decision_log": {
                "architecture": [
                    {"component": "workflow-orchestrator", "approach": "fan-out"},
                ],
            },
        }
        vocab = decision_log_vocabulary(decision_log)
        assert {"workflow", "orchestrator", "fan", "out"} <= vocab

    def test_draws_from_convention_name(self):
        decision_log = {
            "decision_log": {
                "conventions": [
                    {"name": "Citation IDs", "choice": "src- prefix", "rationale": "uniform"},
                ],
            },
        }
        vocab = decision_log_vocabulary(decision_log)
        assert {"citation", "ids", "src", "prefix", "uniform"} <= vocab

    def test_draws_from_requirement_description(self):
        decision_log = {
            "decision_log": {
                "requirements": [
                    {"id": "REQ-001", "description": "Scaffold the capability graph"},
                ],
            },
        }
        vocab = decision_log_vocabulary(decision_log)
        assert {"scaffold", "capability", "graph"} <= vocab

    def test_empty_for_empty_decision_log(self):
        assert decision_log_vocabulary({}) == set()


class TestIsGenericTrigger:
    def test_pure_stopwords_is_generic(self):
        assert _is_generic_trigger("use when implementing", vocab={"schema"})

    def test_empty_trigger_is_generic(self):
        assert _is_generic_trigger("", vocab={"schema"})

    def test_domain_token_matches_vocab(self):
        assert not _is_generic_trigger("validate schema rows", vocab={"schema"})

    def test_no_domain_overlap_is_generic(self):
        assert _is_generic_trigger("fetch weather forecast", vocab={"schema", "citation"})

    def test_bootstrap_vocab_used_when_primary_empty(self):
        assert not _is_generic_trigger(
            "orchestrate workflow",
            vocab=set(),
            bootstrap_vocab={"workflow", "orchestrator"},
        )

    def test_bootstrap_vocab_rejects_non_matching(self):
        assert _is_generic_trigger(
            "run the thing",
            vocab=set(),
            bootstrap_vocab={"workflow", "orchestrator"},
        )

    def test_no_vocab_accepts_any_nonstopword(self):
        # Fallback: when neither vocabulary is available, stop-word stripping
        # is the only check we can do.
        assert not _is_generic_trigger("fetch weather forecast", vocab=set())

    def test_no_vocab_rejects_pure_stopwords(self):
        assert _is_generic_trigger("use when executing the task", vocab=set())
