from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _assert_contains_all(text: str, phrases: list[str]) -> None:
    lowered = text.lower()
    missing = [phrase for phrase in phrases if phrase.lower() not in lowered]
    assert not missing, f"missing expected phrases: {missing}"


def test_grill_me_skill_discovers_stage2_dialog_workflows():
    skill = _read(".github/skills/grill-me/SKILL.md")
    _assert_contains_all(
        skill,
        [
            "Stage 2 vision elicitation",
            "Stage 2 re-entry",
            "probe-driven decision dialogs",
            "decision blocks",
        ],
    )


def test_stage2_dialog_prompt_requires_grill_me_discipline_before_blocks():
    prompt = _read(".github/prompts/stage-2-dialog.prompt.md")
    _assert_contains_all(
        prompt,
        [
            "### Grill-me discipline",
            "Apply the `grill-me` skill during Step 3",
            "Ask one focused question at a time",
            "recommended answer",
            "explore those artifacts",
            "Only write a decision block",
            "at least 4 section probes",
        ],
    )


def test_stage2_reentry_prompt_scopes_grill_me_to_revised_sections():
    prompt = _read(".github/prompts/stage2-reentry.prompt.md")
    _assert_contains_all(
        prompt,
        [
            "Apply the `grill-me` skill while re-ingesting the problem space",
            "Apply the `grill-me` skill to the revised sections only",
            "ask one focused question at a time",
            "recommended answer",
            "explore artifacts before asking the human",
            "For unchanged sections, do not grill the human again",
        ],
    )


def test_stage2_orchestrator_postflight_audits_grill_me_discipline():
    agent = _read(".github/agents/stage2-orchestrator.agent.md")
    _assert_contains_all(
        agent,
        [
            "Grill-me discipline (dialog depth check)",
            "one focused question at a time",
            "recommended answers or researched options",
            "artifact exploration before asking the human",
            "name: grill_me_discipline",
            "verdict: REVISE",
        ],
    )
