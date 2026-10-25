---
description: Let the reviewer read recent notes and reviews for a project and propose playbook lessons
argument-hint: <project> [days]
---

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/playbook.py" reflect --project $1 --days ${2:-7}` and show the proposals with their ids and sources. If the tests were red, say so: the proposals are held. Write nothing into the playbook until the user countersigns; then use `apply` as /countersign:review describes.
