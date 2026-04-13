from pathlib import Path

from meta_compiler.validation import validate_scaffold


def _write_agent(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                f"# Agent Spec: {path.stem}",
                "",
                "## Decisions Embedded",
                "- Architecture: core-component -> constrained approach",
                "",
                "## Requirement Trace",
                "- REQ-001: requirement description",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_validate_scaffold_hybrid_happy_path(tmp_path: Path):
    root = tmp_path / "v1"
    (root / "agents").mkdir(parents=True)
    (root / "docs" / "skills").mkdir(parents=True)
    (root / "docs" / "instructions").mkdir(parents=True)
    (root / "requirements").mkdir(parents=True)
    (root / "code").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "report").mkdir(parents=True)
    (root / "references").mkdir(parents=True)

    (root / "ARCHITECTURE.md").write_text("# ARCHITECTURE\n", encoding="utf-8")
    (root / "CONVENTIONS.md").write_text("# CONVENTIONS\n", encoding="utf-8")
    (root / "REQUIREMENTS_TRACED.md").write_text("# REQUIREMENTS_TRACED\n", encoding="utf-8")
    (root / "SCAFFOLD_MANIFEST.yaml").write_text(
        "\n".join(
            [
                "scaffold:",
                "  project_type: hybrid",
                "  agent_count: 6",
                "",
            ]
        ),
        encoding="utf-8",
    )

    for name in [
        "scaffold-generator.md",
        "math-conventions-agent.md",
        "scope-reduction-agent.md",
        "citation-manager-agent.md",
        "style-conventions-agent.md",
        "narrative-structure-agent.md",
    ]:
        _write_agent(root / "agents" / name)

    (root / "docs" / "skills" / "core-scaffold-skill.md").write_text(
        "# Skill\n",
        encoding="utf-8",
    )
    (root / "docs" / "instructions" / "execution-instructions.md").write_text(
        "# Instructions\n",
        encoding="utf-8",
    )
    (root / "docs" / "instructions" / "decision-trace-instructions.md").write_text(
        "# Instructions\n",
        encoding="utf-8",
    )

    (root / "requirements" / "REQ_TRACE_MATRIX.md").write_text("# Trace\n", encoding="utf-8")

    (root / "code" / "__init__.py").write_text("\n", encoding="utf-8")
    (root / "code" / "main.py").write_text("REQUIREMENT_IDS = ['REQ-001']\n", encoding="utf-8")
    (root / "code" / "README.md").write_text("# Code\n", encoding="utf-8")
    (root / "tests" / "test_requirements_trace.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    (root / "report" / "OUTLINE.md").write_text("# Outline\n", encoding="utf-8")
    (root / "report" / "DRAFT.md").write_text("# Draft\n", encoding="utf-8")
    (root / "references" / "CITATION_STYLE.md").write_text("# Citation Style\n", encoding="utf-8")
    (root / "references" / "SOURCES.yaml").write_text("sources: []\n", encoding="utf-8")

    issues = validate_scaffold(root)
    assert issues == []


def test_validate_scaffold_flags_missing_skills(tmp_path: Path):
    root = tmp_path / "v1"
    (root / "agents").mkdir(parents=True)
    (root / "docs" / "instructions").mkdir(parents=True)
    (root / "requirements").mkdir(parents=True)

    (root / "ARCHITECTURE.md").write_text("# ARCHITECTURE\n", encoding="utf-8")
    (root / "CONVENTIONS.md").write_text("# CONVENTIONS\n", encoding="utf-8")
    (root / "REQUIREMENTS_TRACED.md").write_text("# REQUIREMENTS_TRACED\n", encoding="utf-8")
    (root / "SCAFFOLD_MANIFEST.yaml").write_text(
        "\n".join(
            [
                "scaffold:",
                "  project_type: algorithm",
                "  agent_count: 3",
                "",
            ]
        ),
        encoding="utf-8",
    )

    for name in [
        "scaffold-generator.md",
        "math-conventions-agent.md",
        "scope-reduction-agent.md",
    ]:
        _write_agent(root / "agents" / name)

    (root / "docs" / "instructions" / "execution-instructions.md").write_text(
        "# Instructions\n",
        encoding="utf-8",
    )
    (root / "docs" / "instructions" / "decision-trace-instructions.md").write_text(
        "# Instructions\n",
        encoding="utf-8",
    )
    (root / "requirements" / "REQ_TRACE_MATRIX.md").write_text("# Trace\n", encoding="utf-8")

    issues = validate_scaffold(root)
    assert any("docs/skills" in issue for issue in issues)
