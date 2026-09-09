---
name: bulk-reader
description: >
  After a shunt: deny, run the bulk-read bash command from the deny text.
  Do not Read this skill. Do not Read or cat the file. Also when the user
  says bulk-read / shunt, or a question spans large files.
---

File bodies go to MiniMax via a script. This session keeps the script's stdout.
Do not Read this skill file; the deny reason already has the command.

0. If large Reads are not being denied, run `python3 ~/.grok/plugins/shunt/scripts/install-user-hook` once, then retry.
1. After a `shunt:` deny, run the `python3 …/bulk-read` command from that deny as the next tool call. You may replace `--question`.
2. Or, without a deny:

```
python3 ~/.grok/plugins/shunt/scripts/bulk-read --question "<question>" --paths <path> [path...]
```

If that path is missing, use the `scripts/bulk-read` next to the plugin's `hooks/check-read.py`.
3. Return the stdout bullets. Do not Read or cat the files.
4. Follow-up on the same files: run the same command again with a new `--question` and the same `--paths`.

Targeted edit after understanding: `read_file` with `offset` and `limit` on the section you will change (`limit` ≤ 120).
