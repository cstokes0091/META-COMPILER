"""Parse each .github/agents/*.agent.md frontmatter; validate any hooks:
block against the known check names in meta_hook.py."""
import re
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO / ".github" / "agents"
HOOK_SCRIPT = REPO / ".github" / "hooks" / "bin" / "meta_hook.py"


def _known_checks() -> set[str]:
    text = HOOK_SCRIPT.read_text(encoding="utf-8")
    return set(re.findall(r'@register\("([^"]+)"\)', text))


def _parse_frontmatter(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    _, fm, _ = text.split("---", 2)
    return yaml.safe_load(fm) or {}


@pytest.mark.parametrize("agent_path", sorted(AGENTS_DIR.glob("*.agent.md")))
def test_agent_hooks_reference_known_checks(agent_path):
    fm = _parse_frontmatter(agent_path)
    if fm is None or "hooks" not in fm:
        pytest.skip(f"{agent_path.name} has no hooks block")
    known = _known_checks()
    for event, entries in (fm["hooks"] or {}).items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            cmd = entry.get("command", "") if isinstance(entry, dict) else ""
            # Extract the last token of the python3 meta_hook.py <check> command
            m = re.search(r"meta_hook\.py\s+(\S+)", cmd)
            if not m:
                continue
            check = m.group(1)
            # Strip any argparse junk
            check = check.split()[0]
            assert check in known, (
                f"{agent_path.name} event={event} references unknown check '{check}'. "
                f"Known: {sorted(known)}"
            )
