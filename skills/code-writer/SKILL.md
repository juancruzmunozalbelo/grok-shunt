---
name: code-writer
description: >
  Shunt boilerplate generation off the frontier. Use when writing tests, config
  stubs, or types that match an existing reference, or the user says code-write / shunt.
---

MiniMax generates the file. The script writes `--target` on this machine. Do not Read the target back.

Required: a spec, an existing reference file, a target path.

```
python3 ~/.grok/plugins/shunt/scripts/code-write --spec "<spec>" --reference <abs-ref> --target <abs-target>
```

Stdout is `wrote <path>`. Tell the user that path.

Do not use this for edits to existing logic, debugging, or architecture. Those stay on the frontier.
