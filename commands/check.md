---
description: Run the Countersign memory check now (add "weekly" for proposals from a headless Claude)
argument-hint: [weekly]
---

If the argument is "weekly", run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check.py" --weekly`, otherwise `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check.py"`. Show the user the summary line and the sections that need them, in a few short lines. Change nothing; offer /countersign:review to act on it.
