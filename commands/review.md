---
description: Show what Countersign wants you to look at: the latest memory report and pending playbook proposals
---

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/playbook.py" pending` and read `~/.countersign/latest.md`.

Show the user, briefly: the items to look at from the memory report (index, paths, duplicates, suspicious text, proposed new memories) and the pending playbook proposals with their ids. Then ask which ones to countersign. Change nothing until the user says which.

When the user countersigns:
- Memory items: make the change in the memory folder yourself (one fact per file, an index line per file).
- Playbook proposals: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/playbook.py" apply --project <name> --ids <ids>` (or `--all`).
- An item the user says is not an error: add a line to `~/.countersign/ignore.txt` starting with that item's text, so it is not flagged again.
