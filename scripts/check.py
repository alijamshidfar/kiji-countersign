#!/usr/bin/env python3
"""Memory check: what in Claude's memory is broken, stale, duplicated or suspicious.

    python3 check.py                 # mechanical checks, report to ~/.countersign/latest.md
    python3 check.py --weekly        # also proposals (contradictions, merges, new memories)
    python3 check.py --daily --brief # SessionStart hook: at most once a day, one line

Memory only grows. Nobody tidies it, so facts go stale, paths stop existing and the same
thing ends up in two files. This finds them. It NEVER edits memory: it proposes, a person
countersigns, and a session writes the change in.

Mechanical (free, local): frontmatter; index lines both ways (MEMORY.md and index-*.md);
paths and launchd/systemd units named in memories that no longer exist; project memories
whose newest date is old; files changed since the last run; text that tries to steer the
agent instead of describing something; instruction files (CLAUDE.md) over the cap;
near-duplicates (local embeddings, when Ollama is there).

Weekly (headless Claude, no tools): up to five contradictions, merges or stale files from
the memory descriptions, and up to eight new or updated memories from the week's session
notes (if a notes folder is configured).
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (STATE, cosine, embed, frontmatter, headless, is_index, load_config,  # noqa: E402
                    memories, memory_dir, sha)
from runner import claude_json, notify  # noqa: E402

TYPES = {"user", "feedback", "project", "reference"}
INJECTION = re.compile(
    r"(?i)ignore (all |any )?(previous|prior|above)|disregard (the|all|your)|system prompt|you are now|"
    r"new instructions|pre-?authori[sz]ed|do not tell the user")
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
DATE_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
DATE_TEXT = re.compile(r"\b(\d{1,2}) (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w* (20\d\d)\b", re.I)
PATH = re.compile(r"(?<![\w/])(~/[^\s`'\")\]>,;]+|/(?:Users|home)/[^\s`'\")\]>,;]+)")
INDEX_LINK = re.compile(r"\]\(([^)]+\.md)\)")
TITLES = [("suspect", "Text that tries to steer the agent"), ("index", "Index"), ("format", "Format"),
          ("paths", "Paths that do not exist"), ("instructions", "Instruction files over the cap"),
          ("capture", "Proposed new memories (from the week's sessions)"),
          ("llm", "Proposals (contradictions, duplicates, stale)"), ("dups", "Likely duplicates (similar content)"),
          ("stale", "Old project memories"), ("changed", "Changed since the last run")]
HOME_CLAUDE = Path.home() / ".claude/CLAUDE.md"
NEEDS_YOU = ("suspect", "index", "format", "paths", "instructions", "capture", "llm", "dups")


def newest_date(text):
    best = None
    for y, m, d in DATE_ISO.findall(text):
        try:
            best = max(best or datetime.date.min, datetime.date(int(y), int(m), int(d)))
        except ValueError:
            pass
    for d, mon, y in DATE_TEXT.findall(text):
        try:
            best = max(best or datetime.date.min, datetime.date(int(y), MONTHS[mon[:3].lower()], int(d)))
        except (ValueError, KeyError):
            pass
    return best


def check(cfg, mem, weekly):
    today = datetime.date.today()
    state_file = STATE / "check-state.json"
    try:
        state = json.loads(state_file.read_text())
    except (OSError, ValueError):
        state = {}
    files = sorted(mem.glob("*.md"))
    mems = memories(mem)
    texts = {p.name: p.read_text(errors="replace") for p in files}
    found = {k: [] for k, _ in TITLES}

    for p in mems:
        fm = frontmatter(texts[p.name])
        if fm is None:
            found["format"].append(f"{p.name}: no frontmatter")
        elif any(k not in fm for k in ("name", "description", "type")):
            found["format"].append(f"{p.name}: missing " + ", ".join(k for k in ("name", "description", "type") if k not in fm))
        elif fm["type"] not in TYPES:
            found["format"].append(f"{p.name}: unknown type '{fm['type']}'")

    linked = set()
    for ix in (p for p in files if is_index(p)):
        for target in INDEX_LINK.findall(texts[ix.name]):
            name = os.path.basename(target)
            linked.add(name)
            if not (mem / name).exists():
                found["index"].append(f"{ix.name} points to {name}, which does not exist")
    for p in mems:
        if p.name not in linked:
            found["index"].append(f"{p.name} has no index line")
    n_main = texts.get("MEMORY.md", "").count("\n") + 1
    if n_main >= 170:
        found["index"].append(f"MEMORY.md has {n_main} lines; Claude Code reads about 200")

    for p in mems:
        for raw in set(PATH.findall(texts[p.name])):
            path = raw.rstrip(".:*`")
            if any(c in path for c in "<>*…{}$"):
                continue
            full = os.path.expanduser(path)
            if not Path(full).exists() and not glob.glob(glob.escape(full) + "*"):
                found["paths"].append(f"{p.name}: {path}")

    for p in mems:
        fm = frontmatter(texts[p.name]) or {}
        d = newest_date(texts[p.name])
        if fm.get("type") == "project" and d and (today - d).days > int(cfg["stale_days"]):
            found["stale"].append(f"{p.name}: newest date {d.isoformat()} ({(today - d).days} days)")

    hashes = {p.name: sha(texts[p.name]) for p in mems}
    old = state.get("hashes", {})
    for n, h in hashes.items():
        if old and old.get(n) != h:
            found["changed"].append(f"{n} ({'new' if n not in old else 'changed'})")
    found["changed"] += [f"{n} (removed)" for n in set(old) - set(hashes)]
    for p in mems:
        for line in texts[p.name].splitlines():
            if INJECTION.search(line):
                found["suspect"].append(f"{p.name}: {line.strip()[:140]}")

    roots = [Path(os.path.expanduser(pc.get("root", ""))) for pc in (cfg.get("projects") or {}).values()]
    instr = {str(r / "CLAUDE.md") for r in roots if (r / "CLAUDE.md").exists()}
    if HOME_CLAUDE.exists():
        instr.add(str(HOME_CLAUDE))
    for md in sorted(instr):
        n = Path(md).read_text(errors="replace").count("\n")
        if n > int(cfg["instructions_cap"]):
            found["instructions"].append(f"{md}: {n} lines (cap {cfg['instructions_cap']})")

    cache = state.get("embed", {})
    todo = [p for p in mems if cache.get(p.name, {}).get("h") != hashes[p.name]]
    vecs = embed(cfg, [texts[p.name][:2000] for p in todo]) if todo else []
    if vecs is not None:
        for p, v in zip(todo, vecs or []):
            cache[p.name] = {"h": hashes[p.name], "v": [round(x, 5) for x in v]}
        names = [p.name for p in mems if p.name in cache]
        pairs = []
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                s = cosine(cache[a]["v"], cache[b]["v"])
                if s >= 0.90:
                    pairs.append((s, f"{a} ~ {b} ({s:.2f})"))
        found["dups"] = [t for _, t in sorted(pairs, reverse=True)[:10]]

    if weekly:
        found["llm"] = propose(cfg, mems, texts)
        found["capture"] = capture(cfg, mems, texts, today)

    ignore = []
    try:
        ignore = [ln.split("#")[0].strip() for ln in (STATE / "ignore.txt").read_text().splitlines()]
    except OSError:
        pass
    ignore = [r for r in ignore if r]
    for k in found:
        found[k] = [x for x in found[k] if not any(x.startswith(r) for r in ignore)]

    STATE.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"hashes": hashes, "embed": {k: v for k, v in cache.items() if k in hashes},
                                      "last_run": today.isoformat()}))
    return found, len(mems), n_main



def propose(cfg, mems, texts):
    lines = [f"- {p.name} [{(frontmatter(texts[p.name]) or {}).get('type', '?')}]: "
             f"{(frontmatter(texts[p.name]) or {}).get('description', '')[:220]}" for p in mems]
    prompt = ("Below is a list of memory files (file name, type, description) for an AI assistant. "
              f"Today: {datetime.date.today().isoformat()}. Find AT MOST five problems. Both files in a pair must be "
              "about the SAME thing (same company, tool or rule); never pair different subjects. Problems: two files "
              "that contradict each other, two that say the same thing and should be merged, or one that is clearly "
              "stale (a date that has passed, something said to be temporary). Reply ONLY with JSON: "
              '{"items":[{"files":["a.md","b.md"],"kind":"contradiction|duplicate|stale","why":"at most 20 words"}]}. '
              "Do not invent problems; an empty list is fine.\n\n" + "\n".join(lines))
    try:
        items = claude_json(cfg, prompt).get("items", [])[:5]
    except Exception as e:
        return [f"(proposals could not be written: {e})"]
    known = {p.name for p in mems}
    return [f"{' + '.join(f for f in it.get('files', []) if f in known)}: {it.get('kind', '?')} — {it.get('why', '')[:200]}"
            for it in items if any(f in known for f in it.get("files", []))]


def capture(cfg, mems, texts, today):
    nd = cfg.get("notes_dir")
    if not nd:
        return []
    since = (today - datetime.timedelta(days=7)).isoformat()
    items = []
    for note in sorted(Path(os.path.expanduser(nd)).rglob("*.md")):
        if note.name[:10] < since:
            continue
        keep, sec = [], None
        for line in note.read_text(errors="replace").splitlines():
            if line.startswith("## "):
                sec = line[3:].strip()
            elif sec in ("Decided", "Done") and line.startswith("- "):
                keep.append(f"  [{sec}] {line[2:].strip()[:300]}")
        if keep:
            items.append(f"NOTE {note.relative_to(Path(os.path.expanduser(nd)))}\n" + "\n".join(keep[:12]))
    if not items:
        return []
    have = "\n".join(f"- {p.name}: {(frontmatter(texts[p.name]) or {}).get('description', '')[:200]}" for p in mems)
    prompt = ("You help an AI assistant remember between sessions. Below: memories that exist, and decisions and "
              "changes from this week's session notes. Find AT MOST eight things worth a memory: lasting decisions, "
              "rules for how the person wants the assistant to work, new tools, services or places to find again. NOT: "
              "one-off tasks, things already in a memory, things the code shows, passwords or keys. One proposal is "
              "about ONE project and never mixes clients. If an existing memory only needs updating, give its file and "
              'update=true. Reply ONLY with JSON: {"items":[{"file":"kebab-name.md","update":false,"type":'
              '"user|feedback|project|reference","line":"the memory in one sentence, with a date","source":"note"}]}.'
              "\n\nMEMORIES:\n" + have + "\n\nNOTES:\n" + "\n\n".join(items))
    try:
        got = claude_json(cfg, prompt).get("items", [])[:8]
    except Exception as e:
        return [f"(new-memory proposals could not be written: {e})"]
    known = {p.name for p in mems}
    out = []
    for it in got:
        f = os.path.basename(str(it.get("file", "")))
        if not f.endswith(".md") or not it.get("line") or (f in known and not it.get("update")):
            continue
        out.append(f"{'update' if f in known else 'new'} {f} [{it.get('type', '?')}]: {str(it['line'])[:260]} "
                   f"(from {str(it.get('source', '?'))[:80]})")
    return out


def report(found, n, n_main):
    issues = sum(len(found[k]) for k in NEEDS_YOU)
    out = [f"Countersign memory check {datetime.date.today().isoformat()}: {n} memories, MEMORY.md {n_main} lines, "
           f"{issues} to look at.", "Nothing was changed. Countersign what you want fixed, and a session writes it in."]
    for k, title in TITLES:
        if found[k]:
            out += ["", f"{title} ({len(found[k])}):"] + [f"- {x}" for x in found[k][:15]]
    return "\n".join(out), issues


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--daily", action="store_true", help="skip if already run today (for a SessionStart hook)")
    ap.add_argument("--brief", action="store_true", help="print one line only")
    ap.add_argument("--cwd")
    a = ap.parse_args()
    cwd, hook = a.cwd, {}
    if not cwd and not sys.stdin.isatty():
        try:
            hook = json.load(sys.stdin)
            cwd = hook.get("cwd")
        except Exception:
            hook = {}
    if a.daily and headless(hook):
        return
    cfg = load_config(cwd)
    mem = memory_dir(cfg, cwd)
    if not mem.exists():
        if not a.brief:
            print(f"no memory folder at {mem}")
        return
    if a.daily:
        try:
            if json.loads((STATE / "check-state.json").read_text()).get("last_run") == datetime.date.today().isoformat():
                return
        except (OSError, ValueError):
            pass
    found, n, n_main = check(cfg, mem, a.weekly)
    body, issues = report(found, n, n_main)
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "latest.md").write_text(body + "\n")
    (STATE / f"report_{datetime.date.today().isoformat()}.md").write_text(body + "\n")
    if a.brief:
        if issues:
            print(f"Countersign: {issues} memory item(s) to look at — /countersign:review")
    else:
        print(body)
    if a.weekly and issues:
        notify(cfg, f"Countersign: {issues} to countersign", body)


if __name__ == "__main__":
    main()
