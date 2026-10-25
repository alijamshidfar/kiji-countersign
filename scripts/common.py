"""Shared helpers for Countersign: configuration, memory discovery, embeddings, headless Claude.

Everything here is optional at runtime. No Ollama: recall falls back to keyword matching and
the duplicate check is skipped. No `claude` on PATH: the weekly proposals say so and stop.
Nothing ever writes to the memory folder; the tools only read it and write reports elsewhere.
"""
import hashlib
import json
import math
import os
import re
import unicodedata
import urllib.request
from pathlib import Path

HOME = Path.home()
STATE = Path(os.environ.get("COUNTERSIGN_HOME", HOME / ".countersign"))

DEFAULTS = {
    # Folder that holds MEMORY.md and the memory files. Empty: derived from the working
    # directory the way Claude Code does (~/.claude/projects/<cwd with / as ->/memory).
    "memory_dir": "",
    # Project roots that carry a PLAYBOOK.md, each with its fixed tests (the gate) and the
    # review files the reviewer learns from.
    "projects": {},
    # Optional client groups: words that name a client, and the memory index and playbook
    # projects that belong to it. When a prompt names a client, other clients stay out.
    "groups": {},
    # Optional folder of per-session notes (markdown with "## Decided" / "## Done" sections)
    # that the weekly capture reads. Empty: capture is skipped.
    "notes_dir": "",
    "ollama_url": "http://localhost:11434",
    "embed_model": "bge-m3",
    "claude_cmd": "claude",
    "claude_model": "sonnet",
    # Shell command that receives the weekly report on stdin, e.g. "mail -s Countersign me@x".
    "notify_command": "",
    "stale_days": 45,
    "instructions_cap": 200,
    "playbook_cap": 40,
    "recall_top": 3,
}


# A project's own .countersign.json may only set these. Anything that runs a command or reaches
# the network (notify_command, project gates, claude_cmd, ollama_url) comes from the user's own
# config only: a cloned repository must never be able to make Countersign execute something.
PROJECT_SAFE = {"memory_dir", "groups", "stopwords", "recall_top", "stale_days", "instructions_cap", "notes_dir"}


def load_config(cwd=None):
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads((HOME / ".config/countersign/config.json").read_text()))
    except (OSError, ValueError):
        pass
    try:
        local = json.loads((Path(cwd or os.getcwd()) / ".countersign.json").read_text())
        cfg.update({k: v for k, v in local.items() if k in PROJECT_SAFE})
    except (OSError, ValueError, AttributeError):
        pass
    return cfg


def memory_dir(cfg, cwd=None):
    if cfg.get("memory_dir"):
        return Path(os.path.expanduser(cfg["memory_dir"]))
    cwd = str(Path(cwd or os.getcwd()).resolve())
    return HOME / ".claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd) / "memory"


def is_index(p):
    return p.name == "MEMORY.md" or p.name.startswith("index-")


def memories(mem):
    return [p for p in sorted(mem.glob("*.md")) if not is_index(p)]


def frontmatter(text):
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    head, fm = text[3:end], {}
    for key in ("name", "description", "type"):
        m = re.search(rf"^\s*{key}:\s*(.+)$", head, re.M)
        if m:
            fm[key] = m.group(1).strip().strip('"')
    return fm


def words(text, stop=()):
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return {w for w in re.split(r"[^a-z0-9]+", t) if len(w) >= 3 and w not in stop}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def embed(cfg, texts, timeout=60):
    """List of vectors, or None when Ollama or the model is not there."""
    try:
        req = urllib.request.Request(cfg["ollama_url"].rstrip("/") + "/api/embed",
                                     data=json.dumps({"model": cfg["embed_model"], "input": texts}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["embeddings"]
    except Exception:
        return None


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def headless(hook):
    """True when this hook fires inside a headless run (a script calling `claude -p`).
    Claude Code writes the entrypoint into the session transcript; no environment is read."""
    try:
        with open(hook.get("transcript_path") or "", errors="replace") as f:
            for _ in range(30):
                line = f.readline()
                if not line:
                    break
                if '"entrypoint":"sdk' in line.replace(" ", ""):
                    return True
    except (OSError, TypeError):
        pass
    return False

