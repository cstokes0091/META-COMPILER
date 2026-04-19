# Meta-Compiler Hooks

Source spec: `docs/superpowers/specs/2026-04-18-hooks-and-stage2-reentry-design.md`.

## What hooks are

VSCode Copilot hooks intercept lifecycle events (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStart/Stop, PreCompact) and can inject context, auto-fire commands, deny tool invocations, or block session end. Meta-compiler uses them to enforce pipeline-ordering discipline that was previously informal.

## Where hooks live

- **Workspace hooks:** `.github/hooks/main.json`. Apply to every session.
- **Agent-scoped hooks:** declared in each `.github/agents/*.agent.md` frontmatter under `hooks:`. Require `chat.useCustomAgentHooks: true` in `.vscode/settings.json`.
- **Shared machinery:** every hook entry invokes `python3 .github/hooks/bin/meta_hook.py <check-name>`. One module, stdlib-only, fully unit-tested under `.github/hooks/bin/tests/`.

## Checks

| Check | Event | Purpose |
|---|---|---|
| `inject_state` | SessionStart, UserPromptSubmit | Workspace stage summary + next action into context. |
| `gate_cli` | PreToolUse (Bash) | Denies `meta-compiler X` when manifest state precondition fails. |
| `gate_artifact_writes` | PreToolUse (Write/Edit) | Denies writes to `decision-logs/*.yaml` and `seeds/**`. |
| `gate_reentry_request` | PreToolUse (Bash) | Denies `stage2-reentry` without `reentry_request.yaml`. **Non-overridable.** |
| `capture_output` | PostToolUse (Bash) | Injects `meta-compiler` stdout as `additionalContext`. |
| `user_prompt_submit_dispatch` | UserPromptSubmit | Auto-fires CLI chain on `/stage-*-*` slash commands. |
| `subagent_stop_dispatch` | SubagentStop | Auto-fires post-subagent validation chain. |
| `nudge_finalize` | Stop | Blocks session end during mid-flight Stage 2. |
| `gate_orchestrator_mode_{preflight,postflight}` | Agent PreToolUse | Deny agent run without input artifact. |
| `require_verdict_{preflight,postflight}` | Agent SubagentStop | Block agent stop without verdict. |
| `gate_ingest_workplan` | Agent PreToolUse | Deny ingest-orchestrator fan-out without work_plan.yaml. |
| `require_ingest_report` / `require_handoff` | Agent SubagentStop | Block stop without output artifact. |
| `validate_findings_schema` | Agent PostToolUse (Write) | Deny malformed findings JSON at write time. Polymorphic on `source_type` — accepts doc findings (`citation_id` + `concepts`), code findings (`file_metadata` + line-anchored `symbols[]`), or legacy `{source_id, findings[]}` shape. |
| `validate_repo_map_schema` | Agent PostToolUse (Write) | Deny malformed RepoMap YAML under `runtime/ingest/repo_map/` at write time. Requires `repo_name`, `commit_sha`, `languages[]`, and non-empty `priority_files[{path,rank,reason}]`. Wired from `repo-mapper.agent.md` frontmatter. |

## Override mechanisms

### Per-call env flag

Set `META_COMPILER_SKIP_HOOK=1` in the tool call env. Honored by `gate_cli`, `gate_artifact_writes`, and the auto-fire chain runner's child invocations. **Not honored by** `gate_reentry_request`, `gate_orchestrator_mode_*`, `require_verdict_*`, `validate_findings_schema`, or `validate_repo_map_schema`.

### Code ingestion interaction

- `add-code-seed` and `bind-code-seed` pass `gate_cli` from any post-init stage (`0`/`1a`/`1b`/`1c`/`2`).
- `git clone` inside `add-code-seed` runs via `subprocess.run` (not the VSCode Write tool), so `gate_artifact_writes` never sees it — the clone populates `seeds/code/<name>/` without tripping the seeds-immutability gate.
- Per-file `code-reader` subagents inherit the same `validate_findings_schema` hook as `seed-reader`; the polymorphic dispatcher applies code-specific checks when `source_type: "code"` or `file_metadata` is present.

### Config file

`.github/hooks/overrides.json` (gitignored). Time-bounded kill-switch:

```json
{
  "disable_checks": ["nudge_finalize"],
  "disable_until": "2026-05-01T00:00:00Z",
  "reason": "...",
  "approved_by": "human"
}
```

Listed checks short-circuit with an override `systemMessage`. Expired entries are ignored.

## Audit log

`workspace-artifacts/runtime/hook_audit.log`. One JSON line per event. Cleared only by `meta-compiler clean-workspace --target-stage 0`.

## Testing hooks

```bash
pytest .github/hooks/bin/tests/ -v
```

Runs unit tests for every check against fixture hook-input JSON. No VSCode required.

## Debugging

Set `META_COMPILER_HOOK_TEST=1` to suppress audit writes. Invoke `meta_hook.py <check>` directly with a JSON payload on stdin to reproduce denials:

```bash
echo '{"hookEventName":"PreToolUse","tool_input":{"command":"meta-compiler scaffold"}}' | python3 .github/hooks/bin/meta_hook.py gate_cli
```
