from pathlib import Path

from meta_compiler.stages.init_stage import _source_customizations_dir, _source_prompts_dir, run_meta_init


def _template_prompt_names() -> list[str]:
    return sorted(path.name for path in _source_prompts_dir().glob("*.prompt.md"))


def _template_customization_paths() -> list[str]:
    source_dir = _source_customizations_dir()
    return sorted(str(path.relative_to(source_dir)) for path in source_dir.rglob("*") if path.is_file())


def test_meta_init_provisions_prompt_templates(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    artifacts_root = workspace_root / "workspace-artifacts"

    result = run_meta_init(
        workspace_root=workspace_root,
        artifacts_root=artifacts_root,
        project_name="Test Project",
        problem_domain="Test Domain",
        project_type="algorithm",
    )

    prompt_dir = workspace_root / "prompts"
    customization_dir = workspace_root / ".github"
    expected_names = _template_prompt_names()
    expected_customization_paths = _template_customization_paths()

    assert prompt_dir.exists()
    assert sorted(path.name for path in prompt_dir.glob("*.prompt.md")) == expected_names
    assert result["prompt_dir"] == str(prompt_dir)
    assert result["prompt_count"] == len(expected_names)
    assert customization_dir.exists()
    assert sorted(
        str(path.relative_to(customization_dir))
        for path in customization_dir.rglob("*")
        if path.is_file()
    ) == expected_customization_paths
    assert result["customization_dir"] == str(customization_dir)


def test_meta_init_prompt_overwrite_respects_force(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    artifacts_root = workspace_root / "workspace-artifacts"

    run_meta_init(
        workspace_root=workspace_root,
        artifacts_root=artifacts_root,
        project_name="Test Project",
        problem_domain="Test Domain",
        project_type="hybrid",
    )

    prompt_name = "stage-1a-breadth.prompt.md"
    workspace_prompt = workspace_root / "prompts" / prompt_name
    template_prompt = _source_prompts_dir() / prompt_name
    customization_name = "agents/stage-1a2-orchestrator.agent.md"
    workspace_customization = workspace_root / ".github" / customization_name
    template_customization = _source_customizations_dir() / customization_name

    workspace_prompt.write_text("customized prompt", encoding="utf-8")
    workspace_customization.write_text("customized customization", encoding="utf-8")

    run_meta_init(
        workspace_root=workspace_root,
        artifacts_root=artifacts_root,
        project_name="Test Project",
        problem_domain="Test Domain",
        project_type="hybrid",
        force=False,
    )
    assert workspace_prompt.read_text(encoding="utf-8") == "customized prompt"
    assert workspace_customization.read_text(encoding="utf-8") == "customized customization"

    run_meta_init(
        workspace_root=workspace_root,
        artifacts_root=artifacts_root,
        project_name="Test Project",
        problem_domain="Test Domain",
        project_type="hybrid",
        force=True,
    )
    assert workspace_prompt.read_text(encoding="utf-8") == template_prompt.read_text(encoding="utf-8")
    assert workspace_customization.read_text(encoding="utf-8") == template_customization.read_text(encoding="utf-8")
