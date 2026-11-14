---
name: cross-repo-read
description: Read a second local checkout without copying it into the run's workspace.
pulls_tools: sandbox
---

# Cross-repo read

Use when a task needs a file that lives in another checkout on this machine.

1. Confirm the path exists and is a directory before running anything.
2. Read with `ls` and the file reader; never write.
3. Quote the exact path you read in your result, so the owner can check it.
