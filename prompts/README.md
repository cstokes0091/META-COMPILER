# prompts/ — generated mirror

The canonical source for stage prompts is **`.github/prompts/`** at the
repository root. The files in *this* directory (`prompts/`) are a generated
mirror, kept in sync so that LLM runtimes which do not read from `.github/`
(e.g., non-Copilot CLI tools) can still load the same prompt content.

When you edit a prompt:

1. Edit the `.github/prompts/<name>.prompt.md` version.
2. Copy the result to `prompts/<name>.prompt.md`.

When `meta-compiler meta-init` provisions a new workspace, it reads from
`.github/prompts/` and writes to **both** `prompts/` and `.github/prompts/`
in the target workspace. The two stay byte-identical.

If you need to do bulk sync from `.github/prompts/` to `prompts/`:

```bash
cp .github/prompts/*.prompt.md prompts/
```
