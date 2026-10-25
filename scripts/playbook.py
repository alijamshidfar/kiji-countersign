#!/usr/bin/env python3
"""Playbook per project: lessons from outcomes that grow and sharpen every day.

    python3 playbook.py reflect --project NAME [--days 1.2] [--seed]
    python3 playbook.py show    --project NAME
    python3 playbook.py apply   --project NAME (--all | --ids A1,A3) [--date YYYY-MM-DD]
    python3 playbook.py pending

After ACE (Agentic Context Engineering, ICLR 2026): a reviewer reads what happened and
proposes small changes; a curator merges them into a playbook with counters. The playbook is
never rewritten from scratch, because rewrites lose detail.

Signals: the project's session notes (Decided/Done/Open sections) and its review files (any
markdown a checker wrote: legal, finance, fact-check, code review). Counters (helpful/harmful)
update directly; new and retired lessons only after a person countersigns with `apply`. The
project's fixed tests (its gate) must be green, or the proposals are held: a guard against
a playbook that drifts.

Configure projects in ~/.config/countersign/config.json:
  "projects": {"reports": {"root": "~/work/reports", "desc": "market reports sold to companies",
               "notes": "~/notes/reports", "reviews": ["*/review/*.md"],
               "gate": [["python3", "tools/build.py", "--check"]], "generic": false}}
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import STATE, load_config  # noqa: E402
from runner import claude_json  # noqa: E402

ITEM = re.compile(r"^- \[(P\d{3}) \+(\d+)/−(\d+)\] (.+?)(?: \(source: (.+)\))?$")


def proj(cfg, name):
    pc = (cfg.get("projects") or {}).get(name)
    if not pc:
        sys.exit(f"no project '{name}' in the config")
    return dict(pc, root=Path(os.path.expanduser(pc["root"])))


def load(pc):
    p = pc["root"] / "PLAYBOOK.md"
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            m = ITEM.match(line.strip())
            if m:
                out.append({"id": m[1], "plus": int(m[2]), "minus": int(m[3]), "text": m[4].strip(), "src": (m[5] or "").strip()})
    return out


def save(name, pc, items, cap):
    items = sorted(items, key=lambda i: (-(i["plus"] - i["minus"]), i["id"]))
    lines = [f"# Playbook — {name}", "",
             "Lessons from outcomes: what went wrong or right in reviews and sessions. Read before working here.",
             f"At most {cap}. [id +helpful/−harmful]. Written by Countersign; do not edit by hand, propose instead",
             "(a person countersigns new and retired lessons; counters update every night).", ""]
    lines += [f"- [{i['id']} +{i['plus']}/−{i['minus']}] {i['text']}" + (f" (source: {i['src']})" if i["src"] else "")
              for i in items]
    (pc["root"] / "PLAYBOOK.md").write_text("\n".join(lines) + "\n")


def signals(pc, since):
    out = []
    nd = pc.get("notes")
    if nd:
        for note in sorted(Path(os.path.expanduser(nd)).rglob("*.md")):
            if note.name[:10] < since.isoformat():
                continue
            keep, sec = [], None
            for line in note.read_text(errors="replace").splitlines():
                if line.startswith("## "):
                    sec = line[3:].strip()
                elif sec in ("Decided", "Done", "Open") and line.startswith("- "):
                    keep.append(f"  - {line[2:].strip()[:300]}")
            if keep:
                out.append(f"NOTE {note.name}\n" + "\n".join(keep[:10]))
    for f in sorted(x for g in pc.get("reviews", []) for x in glob.glob(str(pc["root"] / g))):
        if datetime.date.fromtimestamp(os.path.getmtime(f)) >= since:
            out.append(f"REVIEW {os.path.relpath(f, pc['root'])}\n" + "\n".join(Path(f).read_text(errors="replace").splitlines()[:40])[:2500])
    return out


def gate(pc):
    for cmd in pc.get("gate", []):
        try:
            r = subprocess.run(cmd, cwd=pc["root"], capture_output=True, text=True, timeout=900)
        except Exception as e:
            return False, f"{' '.join(cmd)}: {e}"
        if r.returncode:
            return False, f"{' '.join(cmd)}: exit {r.returncode}"
    return True, "green" if pc.get("gate") else "no gate configured"


def render(name, prop):
    g = prop["gate"]
    out = [f"{name} {prop['date']}: {len(prop['add'])} new, {len(prop['retire'])} to retire. Tests: {g['why']}."]
    if not g["ok"]:
        out.append("Tests are red: proposals are held until they are green.")
    out += [f"- {x['pid']} NEW: {x['text']} (source: {x.get('source', '?')})" for x in prop["add"]]
    out += [f"- RETIRE {x['id']}: {x.get('why', '')}" for x in prop["retire"]]
    return "\n".join(out)


def reflect(a, cfg):
    pc = proj(cfg, a.project)
    pdir = STATE / "playbook" / a.project
    pdir.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today()
    sig = signals(pc, today - datetime.timedelta(days=a.days))
    if not sig:
        print("no new signals")
        return
    items = load(pc)
    maxadd = 15 if a.seed else 5
    prompt = (
        f"You are the reviewer in a self-improving system for the project {a.project} ({pc.get('desc', '')}). "
        "Below: the current playbook, and new signals (session notes and review files). Task:\n"
        f"1. Propose AT MOST {maxadd} new lessons: concrete, recurring patterns that make the next piece of work better. "
        "One imperative sentence each, no dates in the text. Not one-off mistakes, not what the playbook already says.\n"
        + ("Write lessons that apply to the NEXT engagement: no company, person or deal names, no figures from one case.\n"
           if pc.get("generic") else "")
        + "2. For existing lessons: 'helpful' = the signals show it was followed and held; 'harmful' = it misled or is "
        "contradicted; 'retire' = outdated.\nGive a source (file) for each. Reply ONLY with JSON: "
        '{"add":[{"text":"...","source":"file"}],"helpful":[{"id":"P001"}],"harmful":[{"id":"P002"}],'
        '"retire":[{"id":"P003","why":"..."}]}\n\nPLAYBOOK:\n'
        + ("\n".join(f"- {i['id']}: {i['text']}" for i in items) or "(empty)")
        + "\n\nSIGNALS:\n" + "\n\n".join(sig)[:120000])
    got = claude_json(cfg, prompt)
    ids = {i["id"] for i in items}
    for key, field in (("helpful", "plus"), ("harmful", "minus")):
        for h in got.get(key, []):
            for i in items:
                if i["id"] == h.get("id"):
                    i[field] += 1
    if items:
        save(a.project, pc, items, cfg["playbook_cap"])
    ok, why = gate(pc)
    f = pdir / f"proposals_{today.isoformat()}.json"
    old = json.loads(f.read_text()) if f.exists() else {"add": [], "retire": []}
    add = [x for x in got.get("add", [])[:maxadd] if x.get("text")]
    prop = {"date": today.isoformat(), "gate": {"ok": ok, "why": why},
            "add": old["add"] + [dict(x, pid=f"A{len(old['add']) + n + 1}") for n, x in enumerate(add)],
            "retire": old["retire"] + [x for x in got.get("retire", []) if x.get("id") in ids]}
    f.write_text(json.dumps(prop, ensure_ascii=False, indent=1))
    print(render(a.project, prop))


def apply(a, cfg):
    pc = proj(cfg, a.project)
    pdir = STATE / "playbook" / a.project
    files = [pdir / f"proposals_{a.date}.json"] if a.date else sorted(pdir.glob("proposals_*[0-9].json"))
    if not files or not files[-1].exists():
        sys.exit("no proposals")
    prop = json.loads(files[-1].read_text())
    if not prop["gate"]["ok"]:
        sys.exit(f"tests were red ({prop['gate']['why']}); run reflect again when they are green")
    want = None if a.all else set((a.ids or "").split(","))
    items = load(pc)
    added = 0
    for x in prop["add"]:
        if (want is None or x["pid"] in want) and not any(x["text"].lower() == i["text"].lower() for i in items):
            n = max([int(i["id"][1:]) for i in items] or [0]) + 1
            items.append({"id": f"P{n:03d}", "plus": 0, "minus": 0, "text": x["text"].strip(),
                          "src": f"{x.get('source', '')}; {prop['date']}".strip("; ")})
            added += 1
    retire = {x["id"] for x in prop["retire"]} if (a.all or (want and "retire" in want)) else set()
    items = [i for i in items if i["id"] not in retire]
    while len(items) > int(cfg["playbook_cap"]):
        items.remove(min(items, key=lambda i: (i["plus"] - i["minus"], -int(i["id"][1:]))))
    save(a.project, pc, items, cfg["playbook_cap"])
    files[-1].rename(files[-1].with_suffix(".applied.json"))
    print(f"{added} new, {len(retire)} retired; the playbook has {len(items)} lessons")


def show(a, cfg):
    for i in load(proj(cfg, a.project)):
        print(f"{i['id']} +{i['plus']}/−{i['minus']} {i['text']}")


def pending(a, cfg):
    parts = []
    for name in cfg.get("projects") or {}:
        for f in sorted((STATE / "playbook" / name).glob("proposals_*[0-9].json")):
            prop = json.loads(f.read_text())
            if prop["add"] or prop["retire"]:
                parts.append(render(name, prop))
    print("\n\n".join(parts) or "no pending proposals")


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(required=True)
    r = sub.add_parser("reflect"); r.add_argument("--project", required=True)
    r.add_argument("--days", type=float, default=1.2); r.add_argument("--seed", action="store_true"); r.set_defaults(f=reflect)
    s = sub.add_parser("show"); s.add_argument("--project", required=True); s.set_defaults(f=show)
    p = sub.add_parser("apply"); p.add_argument("--project", required=True); p.add_argument("--ids")
    p.add_argument("--all", action="store_true"); p.add_argument("--date"); p.set_defaults(f=apply)
    w = sub.add_parser("pending"); w.set_defaults(f=pending)
    a = ap.parse_args()
    a.f(a, cfg)


if __name__ == "__main__":
    main()
