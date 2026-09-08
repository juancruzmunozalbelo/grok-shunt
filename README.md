# shunt

A [Grok Build](https://x.ai) plugin that keeps large file bodies and boilerplate off the **frontier** model (grok-4.6).

Same idea as [Portal by Spotify](https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90): a hook blocks untargeted large reads, then a **script** sends the files to a cheap worker over HTTP. MiniMax never gets your disk. The frontier only sees the script's stdout (bullets, or `wrote /path`).

This is not Portal and it does not claim 90% savings.

## Install

```bash
grok plugin install juancruzmunozalbelo/grok-shunt --trust
```

Enable it:

```toml
# ~/.grok/config.toml
[plugins]
enabled = ["shunt"]
```

Grok 1.0.13 discovers plugin hooks but does not run them as PreToolUse. Point a **user** hook at the plugin symlink (not a hashed install dir):

```bash
python3 - <<'PY'
from pathlib import Path
import json
home = Path.home()
path = home / ".grok" / "hooks" / "shunt.json"
path.parent.mkdir(parents=True, exist_ok=True)
cmd = str(home / ".grok" / "plugins" / "shunt" / "hooks" / "check-read.py")
path.write_text(json.dumps({
    "hooks": {"PreToolUse": [{"matcher": "read_file|Read|run_terminal_command|Bash",
        "hooks": [{"type": "command", "command": cmd, "timeout": 10}]}]}
}, indent=2) + "\n")
print("wrote", path)
print("command", cmd)
PY
```

Start a new Grok session.

You also need a MiniMax key (not shipped):

```bash
export MINIMAX_API_KEY=...    # or put it in ~/.grok/minimax.env (mode 600)
```

Any OpenAI-compatible endpoint works via `SHUNT_BASE_URL` / `SHUNT_MODEL`.

## What it does

| Layer | Role |
| --- | --- |
| Hook `hooks/check-read.py` | Denies `read_file` without `offset`/`limit` when the file is over `SHUNT_MIN_LINES` (default 350). Denies `cat` / `less` / `more` of a large file. Pipes, `head`, `tail`, and targeted reads pass. |
| `scripts/bulk-read` | Packs files into an XML prompt, POSTs to MiniMax, prints bullets on stdout. Usage on stderr. |
| `scripts/code-write` | Requires `--reference`. MiniMax returns code; **this script** writes `--target` after stripping fences. Stdout is `wrote <path>` only. |
| Skills | Tell the frontier to run those scripts after a deny, not `spawn_subagent`. |

Follow-up: run `bulk-read` again with the same `--paths` and a new `--question`. Each call is one shot.

Not shunted: edits, debugging, architecture. After a bulk-read, change a section with `read_file` `offset`/`limit`.

## Worker

Default: `MiniMax-M3` at `https://api.minimax.io/v1`. The model only sees text you packed. It has no `search_replace`, no bash, no `$HOME`.

```bash
python3 ~/.grok/plugins/shunt/scripts/bulk-read \
  --question "What does this service do?" \
  --paths src/Service.java src/Handler.java

python3 ~/.grok/plugins/shunt/scripts/code-write \
  --spec "Write tests for UserService" \
  --reference tests/OrderTest.java \
  --target tests/UserTest.java
```

The leftover `agents/` subagents still exist if you spawn them, but that puts MiniMax on your machine with tools. Prefer the scripts.

## Env

| Variable | Default | Meaning |
| --- | --- | --- |
| `SHUNT_MIN_LINES` | `350` | Untargeted reads of files with more lines are denied. |
| `SHUNT_DISABLE` | unset | Set to `1` to allow every read. |
| `MINIMAX_API_KEY` | — | Worker key. Also read from `~/.grok/minimax.env`. |
| `SHUNT_MODEL` | `MiniMax-M3` | OpenAI-compat model name. |
| `SHUNT_BASE_URL` | `https://api.minimax.io/v1` | Chat completions base. |
| `SHUNT_TIMEOUT_SECONDS` | `120` | Worker HTTP timeout. |
| `SHUNT_MAX_BYTES` | `8000000` | Max packed file bytes per call; split larger jobs. |

## Disable / uninstall

```bash
# ~/.grok/config.toml — drop "shunt" from [plugins].enabled
rm ~/.grok/hooks/shunt.json
grok plugin uninstall shunt --confirm
```

## Develop

```bash
python3 -m unittest discover -s tests -v
grok plugin validate .
```

Hook fail-open: invalid JSON or a missing file does not block. Only an explicit deny JSON blocks.

## License

MIT. The Spotify article is theirs; this plugin is a separate implementation for Grok.
