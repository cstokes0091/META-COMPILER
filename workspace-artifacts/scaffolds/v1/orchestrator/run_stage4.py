from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import yaml


SCAFFOLD_ROOT = Path(__file__).resolve().parents[1]
PROJECT_TYPE = 'hybrid'
DECISION_LOG_VERSION = 1
REQUIREMENT_IDS = ['REQ-001', 'REQ-002']
CITATION_IDS = ['src-decision-seed', 'src-sample-seed']


def _load_generated_module():
    module_path = SCAFFOLD_ROOT / 'code' / 'main.py'
    if not module_path.exists():
        return None
    spec = importlib.util.spec_from_file_location('generated_stage4_main', module_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + '\n', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='Run the scaffold Stage 4 orchestrator.')
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    deliverables: list[dict[str, str]] = []
    execution_notes: list[str] = []

    if PROJECT_TYPE in {'algorithm', 'hybrid'}:
        generated_module = _load_generated_module()
        workflow_state = 'not-run'
        if generated_module is not None:
            runner = getattr(generated_module, 'run_workflow', None)
            if callable(runner):
                runner()
                workflow_state = 'run_workflow_executed'
        algorithm_output = output_dir / 'algorithm_output.md'
        _write_text(
            algorithm_output,
            '\n'.join([
                '# Algorithm Output',
                '',
                f'- Decision Log Version: v{DECISION_LOG_VERSION}',
                f'- Workflow state: {workflow_state}',
                f'- Requirement IDs: {', '.join(REQUIREMENT_IDS)}',
                f'- Citation IDs: {', '.join(CITATION_IDS) if CITATION_IDS else 'None'}',
                '',
                'This artifact is the executable handoff produced by the scaffold orchestrator.',
            ])
        )
        deliverables.append({'kind': 'algorithm-output', 'path': str(algorithm_output)})
        execution_notes.append(workflow_state)

    if PROJECT_TYPE in {'report', 'hybrid'}:
        outline_path = SCAFFOLD_ROOT / 'report' / 'OUTLINE.md'
        draft_path = SCAFFOLD_ROOT / 'report' / 'DRAFT.md'
        outline = outline_path.read_text(encoding='utf-8') if outline_path.exists() else '# Missing Outline\n'
        draft = draft_path.read_text(encoding='utf-8') if draft_path.exists() else '# Missing Draft\n'
        report_output = output_dir / 'report_output.md'
        _write_text(report_output, '\n'.join(['# Report Output', '', outline.strip(), '', draft.strip()]))
        deliverables.append({'kind': 'report-output', 'path': str(report_output)})
        execution_notes.append('report_artifacts_compiled')

    summary_path = output_dir / 'final_product_summary.md'
    _write_text(
        summary_path,
        '\n'.join([
            '# Final Product Summary',
            '',
            f'- Project type: {PROJECT_TYPE}',
            f'- Decision Log Version: v{DECISION_LOG_VERSION}',
            f'- Requirement IDs: {', '.join(REQUIREMENT_IDS)}',
            f'- Execution notes: {', '.join(execution_notes) if execution_notes else 'none'}',
        ])
    )
    deliverables.append({'kind': 'final-product-summary', 'path': str(summary_path)})

    manifest = {
        'final_output': {
            'decision_log_version': DECISION_LOG_VERSION,
            'project_type': PROJECT_TYPE,
            'deliverables': deliverables,
            'requirement_ids': REQUIREMENT_IDS,
            'citation_ids': CITATION_IDS,
            'execution_notes': execution_notes,
        }
    }
    with (output_dir / 'FINAL_OUTPUT_MANIFEST.yaml').open('w', encoding='utf-8') as handle:
        yaml.safe_dump(manifest, handle, sort_keys=False, allow_unicode=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
