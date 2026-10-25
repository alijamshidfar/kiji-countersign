"""Running things for Countersign: headless Claude and the optional notify command.

Kept apart from common.py, which holds the only network call (local Ollama embeddings).
"""
import json
import os
import shlex
import shutil
import subprocess

from common import STATE


def claude_json(cfg, prompt, timeout=900):
    """Headless Claude with no tools and no MCP servers. Returns a dict, or raises."""
    exe = shutil.which(cfg["claude_cmd"]) or os.path.expanduser(cfg["claude_cmd"])
    if not os.path.exists(exe):
        raise RuntimeError(f"`{cfg['claude_cmd']}` not found; set claude_cmd in the config")
    STATE.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([exe, "-p", "--model", cfg["claude_model"], "--output-format", "text",
                        "--permission-mode", "dontAsk", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                        "--disallowedTools", "Bash,Write,Edit,Read,Glob,Grep,WebFetch,WebSearch,Agent,NotebookEdit"],
                       input=prompt, capture_output=True, text=True, timeout=timeout, cwd=str(STATE))
    raw = r.stdout.strip()
    return json.loads(raw[raw.find("{"):raw.rfind("}") + 1] or "{}")


def notify(cfg, subject, body):
    cmd = cfg.get("notify_command")
    if cmd:
        subprocess.run(shlex.split(cmd), input=f"{subject}\n\n{body}", text=True, timeout=60)
