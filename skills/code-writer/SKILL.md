---
name: code-writer
description: >
  Shunt boilerplate generation off the frontier. Use when writing tests, config
  stubs, or types that match an existing reference, or the user says code-write / shunt.
---

The generated file is written by a worker. This session must not Read it back.

Required: a spec, a reference file that already exists, a target path.

1. Call `spawn_subagent` with:
   - `subagent_type`: `shunt:code-writer`
   - `prompt`: spec, absolute reference path, absolute target path
2. When it returns, tell the user the target path.
3. Do not Read the target into this session unless the user asks to review it.

Do not use this for edits to existing logic, debugging, or architecture. Those stay on the frontier.

If `shunt:code-writer` is missing, install and enable the shunt plugin.
