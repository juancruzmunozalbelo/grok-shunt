# shunt

A [Grok Build](https://x.ai) plugin that keeps large file bodies and boilerplate generation off the **frontier** model.

Inspired by [Portal by Spotify cut my Claude Code token usage by 90%](https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90). Same idea, no Portal: a `PreToolUse` hook blocks untargeted large reads, and skills spawn a cheaper **worker** subagent whose files never enter the parent context.

The worker's **last message** does enter the parent. The bulk-reader agent is instructed to return short bullets, not file dumps.

## Install

```bash
grok plugin install juan-munoz_scann/grok-shunt --trust
```

Then enable it (`plugins` stay off until listed):

```toml
# ~/.grok/config.toml
[plugins]
enabled = ["shunt"]
```

Start a new Grok session. Trust is automatic for plugins under `~/.grok/plugins/` after install with `--trust`.

## What it does

| Layer | Role |
| --- | --- |
| Hook `hooks/check-read.py` | Denies `read_file` without `offset`/`limit` when the file is over `SHUNT_MIN_LINES` (default 350). Denies `cat` / `less` / `more` of a large file. Pipes, `head`, `tail`, and targeted reads pass. Subagent sessions pass, so the worker can still read. |
| Skill `bulk-reader` | Tells the frontier to `spawn_subagent` with `subagent_type: shunt:bulk-reader`. |
| Agent `bulk-reader` | Read-only worker. Structured bullets only. |
| Skill + agent `code-writer` | Boilerplate from a **required** reference file. Writes to disk. Parent should not Read the result. |

Not shunted: edits, debugging, architecture. After a bulk-read, change a section with `read_file` `offset`/`limit`.

## Worker model

Agents default to `grok-4.5` so the plugin works with stock Grok. That model is **not** cheaper per token than grok-4.6; it only drops `xhigh` and keeps the corpus out of the parent window.

To send the worker to a model you already pay less for (or have free), pin it in *your* config. Example with MiniMax M3 — **you supply the key**, this repo never ships one:

```toml
[model.minimax-m3]
model = "MiniMax-M3"
base_url = "https://api.minimax.io/v1"
name = "MiniMax M3"
api_backend = "chat_completions"
env_key = "MINIMAX_API_KEY"
context_window = 1000000
temperature = 0.2
supports_backend_search = false

[subagents.models]
"shunt:bulk-reader" = "minimax-m3"
"shunt:code-writer" = "minimax-m3"
```

```bash
export MINIMAX_API_KEY=...   # Token Plan or pay-as-you-go; never commit this
```

Any OpenAI-compatible endpoint works the same way. Do not run `mmx agent setup` for Grok if you want grok-4.6 to stay the session default — that wizard rewrites the default model.

## Env

| Variable | Default | Meaning |
| --- | --- | --- |
| `SHUNT_MIN_LINES` | `350` | Untargeted reads of files with more lines are denied. |
| `SHUNT_DISABLE` | unset | Set to `1` to allow every read (hook still runs, then allows). |

## Disable / uninstall

```bash
# leave installed but inert
# ~/.grok/config.toml — drop "shunt" from [plugins].enabled

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
