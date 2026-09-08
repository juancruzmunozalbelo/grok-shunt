# shunt

A [Grok Build](https://x.ai) plugin that keeps large file bodies and boilerplate off the **frontier** model (grok-4.6).

Same idea as [Portal by Spotify](https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90): a hook blocks untargeted large reads, then a **script** sends the files to a cheap worker over HTTP. MiniMax never gets your disk. The frontier only sees the script's stdout (bullets, or `wrote /path`).

This is not Portal and it does not claim 90% savings.

## Install

```bash
grok plugin install juancruzmunozalbelo/grok-shunt --trust
```

Enable it (`plugins` stay off until listed):

```toml
# ~/.grok/config.toml
[plugins]
enabled = ["shunt"]
```

Grok 1.0.13 discovers plugin hooks but does not run them as PreToolUse. One extra command writes the user hook (pointing at the plugin symlink):

```bash
python3 ~/.grok/plugins/shunt/scripts/install-user-hook
```

Or in a Grok session: `/shunt-enable`. Start a new session after that.

You also need a MiniMax key (not shipped):

```bash
export MINIMAX_API_KEY=...    # or put it in ~/.grok/minimax.env (mode 600)
```

Any OpenAI-compatible endpoint works via `SHUNT_BASE_URL` / `SHUNT_MODEL`.

## What it does

| Layer | Role |
| --- | --- |
| Hook `hooks/check-read.py` | Denies untargeted `read_file` of a large file (over `SHUNT_MIN_LINES` or `SHUNT_MIN_BYTES`). Denies `cat` / `head` / `tail` / `less` / `more` / `grep` / `rg`, including `cat f && true` and multi-file `cat`. Denies the `grep` tool on a large **file** (directories pass). Denies `python3 -c` that names a large file. Pipes pass. `bulk-read` / `code-write` pass. Targeted `read_file` passes only when `limit` is set and `limit ≤ SHUNT_MAX_LIMIT`. |
| `scripts/bulk-read` | Packs files into an XML prompt, POSTs to MiniMax, prints bullets on stdout. Usage on stderr. |
| `scripts/code-write` | Requires `--reference`. MiniMax returns code; **this script** writes `--target` after stripping fences. Stdout is `wrote <path>` only. |
| Skills | Tell the frontier to run those scripts after a deny. |

Follow-up: run `bulk-read` again with the same `--paths` and a new `--question`. Each call is one shot.

Not shunted: edits, debugging, architecture. After a bulk-read, change a section with `read_file` `offset`/`limit` (`limit` ≤ 120).

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

There are no plugin subagents. MiniMax is only reached over HTTP.

## Env

| Variable | Default | Meaning |
| --- | --- | --- |
| `SHUNT_MIN_LINES` | `350` | Untargeted reads of files with more lines are denied. |
| `SHUNT_MIN_BYTES` | `65536` | Untargeted reads of files bigger than this are denied (catches one-line blobs). |
| `SHUNT_MAX_LIMIT` | `120` | Max `read_file` `limit` that still counts as targeted. |
| `SHUNT_DISABLE` | unset | Only `1` or `true` allow every read. `0` / `false` do not disable. |
| `MINIMAX_API_KEY` | — | Worker key. Also read from `~/.grok/minimax.env`. |
| `SHUNT_MODEL` | `MiniMax-M3` | OpenAI-compat model name. |
| `SHUNT_BASE_URL` | `https://api.minimax.io/v1` | Chat completions base. |
| `SHUNT_TIMEOUT_SECONDS` | `120` | Worker HTTP timeout. |
| `SHUNT_MAX_BYTES` | `8000000` | Max packed file bytes per `bulk-read` call; split larger jobs. |

## Disable / uninstall

```bash
# ~/.grok/config.toml — drop "shunt" from [plugins].enabled
rm ~/.grok/hooks/shunt.json
grok plugin uninstall shunt --confirm
```

## Develop

```bash
python3 -m unittest discover -s tests -v
python3 scripts/bench_tokens.py --lines 800
```

`bench_tokens.py` runs two isolated `grok -p` sessions (shunt on vs `SHUNT_DISABLE=1`). Isolation is whether `def f50` landed in the parent. Frontier token delta is informational; MiniMax usage is HTTP, not Grok `modelUsage`.

Hook fail-open: invalid JSON or a missing file does not block. Only an explicit deny JSON blocks.

## License

MIT. The Spotify article is theirs; this plugin is a separate implementation for Grok.
