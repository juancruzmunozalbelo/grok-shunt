---
name: bulk-reader
description: >
  After a shunt omitted-dump, use those bullets. Do not Read this skill or
  the file. Also when the user says bulk-read / shunt, or a question spans
  large files.
---

File bodies go to MiniMax via a PostToolUse hook. The Read/cat result the
model sees is already bullets (`shunt: omitted …`). Do not Read the file back.

0. If dumps still land in the parent, run `python3 ~/.grok/plugins/shunt/scripts/install-user-hook` once (needs PostToolUse), then a new session.
1. If a result starts with `shunt: omitted`, answer from those bullets.
2. Follow-up on the same files without a dump:

```
python3 ~/.grok/plugins/shunt/scripts/bulk-read --question "<question>" --paths <path> [path...]
```

Targeted edit: `read_file` with `offset` and `limit` (`limit` ≤ 120).
