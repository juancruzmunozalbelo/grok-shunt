---
name: bulk-reader
description: >
  Shunt large file reads off the frontier. Use when a hook blocked Read, when
  a question spans large files, or the user says bulk-read / shunt.
---

The file bodies stay in a worker subagent. The parent keeps a short bullet report.

1. Collect the question and the paths (from the user, or from the hook deny reason).
2. Call `spawn_subagent` with:
   - `subagent_type`: `shunt:bulk-reader`
   - `description`: a 3–5 word label
   - `prompt`: the question, then a list of absolute paths
3. Return the worker's bullets to the user. Do not Read the files into this session.
4. Follow-up on the same files: `resume_from` that subagent id. Do not re-Read.

Targeted edit after understanding: `read_file` with `offset` and `limit` on the section you will change. Do not spawn for that window.

If `shunt:bulk-reader` is missing, install and enable the shunt plugin.
