---
name: bulk-reader
description: >
  Shunt large file reads off the frontier. Use when a hook blocked Read, when
  a question spans large files, or the user says bulk-read / shunt.
---

File bodies go to MiniMax via a script. This session keeps the script's stdout.

0. If large Reads are not being denied, run `python3 ~/.grok/plugins/shunt/scripts/install-user-hook` once, then retry.
1. Collect the question and the absolute paths.
2. Run, via bash:

```
python3 ~/.grok/plugins/shunt/scripts/bulk-read --question "<question>" --paths <path> [path...]
```

If that path is missing, use the `scripts/bulk-read` next to the plugin's `hooks/check-read.py` (the deny reason prints it).
3. Return the stdout bullets. Do not Read or cat the files.
4. Follow-up on the same files: run the same command again with a new `--question` and the same `--paths`.

Targeted edit after understanding: `read_file` with `offset` and `limit` on the section you will change (`limit` ≤ 120).
