---
name: agts-research-run
description: Create and operate a Research Meta-AGTS run with durable branches, worktrees, eval attempts, shared memory, and optional local AGTS use by workers.
disable-model-invocation: true
allowed-tools: Read Write Edit Grep Glob Bash
---

Run AGTS Research from a config:

```bash
python -m agts.cli research start -c $ARGUMENTS
```

Then inspect:

```bash
python -m agts.cli research status <run_dir>
```

From a branch worktree, submit attempts:

```bash
./agts-research eval -m "short attempt description"
```

Run meta-controller steps:

```bash
python -m agts.cli research step <run_dir>
```
