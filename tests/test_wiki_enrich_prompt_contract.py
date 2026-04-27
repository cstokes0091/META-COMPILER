from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _assert_contains_all(text: str, phrases: list[str]) -> None:
    lowered = text.lower()
    missing = [phrase for phrase in phrases if phrase.lower() not in lowered]
    assert not missing, f"missing expected phrases: {missing}"


def test_wiki_enrich_prompt_runs_full_semantic_pipeline():
    prompt = _read(".github/prompts/wiki-enrich.prompt.md")
    _assert_contains_all(
        prompt,
        [
            "name: wiki-enrich",
            "agent: wiki-enrichment-orchestrator",
            "meta-compiler wiki-update --scope {scope}",
            "@ingest-orchestrator mode=preflight",
            "@ingest-orchestrator mode=fanout scope={scope}",
            "@ingest-orchestrator mode=postflight",
            "meta-compiler ingest-validate",
            "meta-compiler research-breadth",
            "meta-compiler wiki-reconcile-concepts --version 2",
            "concept-reconciler",
            "meta-compiler wiki-apply-reconciliation --version 2",
            "meta-compiler wiki-cross-source-synthesize --version 2",
            "cross-source-synthesizer",
            "meta-compiler wiki-apply-cross-source-synthesis --version 2",
            "meta-compiler wiki-link --version 2",
            "No-op is success",
            "never trigger it automatically",
        ],
    )


def test_wiki_enrichment_orchestrator_agent_has_required_boundary():
    agent = _read(".github/agents/wiki-enrichment-orchestrator.agent.md")
    _assert_contains_all(
        agent,
        [
            "name: wiki-enrichment-orchestrator",
            "tools: [read, search, edit, execute, agent, todo]",
            "agents: [ingest-orchestrator, concept-reconciler, cross-source-synthesizer]",
            "Do not directly edit files under `workspace-artifacts/wiki/v2/pages/`",
            "full guarded ingest sequence",
            "up to 4 in parallel",
            "wiki-link --version 2",
            "Do not trigger Stage 2 re-entry automatically",
        ],
    )
