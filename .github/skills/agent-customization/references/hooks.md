# [Hooks (.json)](https://code.visualstudio.com/docs/copilot/customization/hooks)

Deterministic lifecycle automation for agent sessions. Use hooks to enforce policy, automate validation, and inject runtime context.

## Locations

| Path | Scope |
|------|-------|
| `.github/hooks/*.json` | Workspace |
| `.claude/settings.local.json` | Workspace local |
| `.claude/settings.json` | Workspace |
| `~/.claude/settings.json` | User profile |

## Hook Events

| Event | Trigger |
|-------|---------|
| `SessionStart` | First prompt of a new agent session |
| `UserPromptSubmit` | User submits a prompt |
| `PreToolUse` | Before tool invocation |
| `PostToolUse` | After successful tool invocation |
| `PreCompact` | Before context compaction |
| `SubagentStart` | Subagent starts |
| `SubagentStop` | Subagent ends |
| `Stop` | Agent session ends |

## Configuration Format

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "type": "command",
        "command": "./scripts/validate-tool.sh",
        "timeout": 15
      }
    ]
  }
}
```

## Hooks vs Other Customizations

| Primitive | Behavior |
|-----------|----------|
| Instructions / Prompts / Skills / Agents | Guidance |
| Hooks | Runtime enforcement and deterministic automation |

## Core Principles

1. Keep hooks small and auditable
2. Validate and sanitize hook inputs
3. Avoid hardcoded secrets
4. Prefer workspace hooks for team policy