#!/usr/bin/env python3
"""UserPromptSubmit hook: show Claude the memories and playbook lessons that match the message.

Memory only helps if it is read. MEMORY.md gives one line per memory and the rest is read
only when Claude remembers to look. This hook searches every memory file and every playbook
lesson for the prompt and hands Claude the best matches as extra context.

Scoring: local embeddings (Ollama, optional) plus a boost for rare words the prompt shares
with a memory's name or description; without Ollama, the rare-word match alone. A match
needs a shared rare word, or a very close embedding. Prints nothing when unsure: no output
beats a wrong hint. Skips headless runs (scripts calling `claude -p`) and short prompts.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import STATE, cosine, embed, frontmatter, headless, load_config, memories, memory_dir, sha, words  # noqa: E402

MIN_PROMPT = 12
MIN_SCORE = 0.64      # embedding + boost needed when a rare word is shared
COS_ALONE = 0.75      # embedding alone, no shared rare word
RARE_BOOST = 0.16
RARE_MAX = 5          # a word in at most this many memories counts as rare
KEYWORD_ONLY_MIN = 2  # without embeddings: at least this many shared rare words

# Common words carry no topic. Extend with your own language in the config ("stopwords").
STOP = set("""the and for with from that this have has not can will what how when where all any you your
are was were but into about our its his her they them then than also just more very fix check make
please could would should need want""".split())


def describe(fm, text):
    return (fm or {}).get("description") or text.strip().splitlines()[0][:200]


def main():
    try:
        hook = json.load(sys.stdin)
    except Exception:
        return
    if headless(hook):
        return
    prompt = hook.get("prompt", "")
    if len(prompt.strip()) < MIN_PROMPT:
        return
    cfg = load_config(hook.get("cwd"))
    stop = STOP | set(cfg.get("stopwords", []))
    mem = memory_dir(cfg, hook.get("cwd"))

    docs = {}          # key -> {text, label, path, owner}
    for p in memories(mem):
        t = p.read_text(errors="replace")
        fm = frontmatter(t)
        docs[p.name] = {"text": t[:2000], "label": describe(fm, t), "path": str(p),
                        "keys": words(p.stem + " " + describe(fm, t), stop)}
    for proj, pc in (cfg.get("projects") or {}).items():
        pb = Path(os.path.expanduser(pc.get("root", ""))) / "PLAYBOOK.md"
        if not pb.exists():
            continue
        for line in pb.read_text(errors="replace").splitlines():
            m = re.match(r"^- \[(P\d{3}) [^\]]*\] (.+?)(?: \(source: .+\))?$", line.strip())
            if m:
                docs[f"{proj}/PLAYBOOK {m[1]}"] = {"text": m[2], "label": m[2], "path": str(pb),
                                                   "keys": words(m[2], stop), "project": proj}

    # Client walls: which group owns each doc; which groups the prompt names.
    owner, named, asked = {}, set(), words(prompt, stop)
    for g, gc in (cfg.get("groups") or {}).items():
        ix = mem / gc.get("index", f"index-{g}.md")
        if ix.exists():
            for target in re.findall(r"\]\(([^)]+\.md)\)", ix.read_text(errors="replace")):
                owner[os.path.basename(target)] = g
        for k, d in docs.items():
            if d.get("project") in gc.get("playbooks", []):
                owner[k] = g
        if asked & {w.lower() for w in gc.get("words", [])}:
            named.add(g)

    df = {}
    for d in docs.values():
        for w in d["keys"]:
            df[w] = df.get(w, 0) + 1

    # Embeddings, cached by content hash.
    cache_file = STATE / "recall-cache.json"
    try:
        cache = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        cache = {}
    todo = [k for k, d in docs.items() if cache.get(k, {}).get("h") != sha(d["text"])]
    if todo:
        vecs = embed(cfg, [f"search_document: {docs[k]['text']}" for k in todo], timeout=60)
        if vecs:
            for k, v in zip(todo, vecs):
                cache[k] = {"h": sha(docs[k]["text"]), "v": [round(x, 5) for x in v]}
            STATE.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({k: v for k, v in cache.items() if k in docs}))
    qv = embed(cfg, [f"search_query: {prompt[:1000]}"], timeout=3)
    qv = qv[0] if qv else None

    scored = []
    for k, d in docs.items():
        if named and owner.get(k) and owner[k] not in named:
            continue
        rare = [w for w in asked & d["keys"] if df[w] <= RARE_MAX]
        if qv and k in cache:
            cos = cosine(qv, cache[k]["v"])
            score = cos + RARE_BOOST * min(len(rare), 2)
            if (rare and score >= MIN_SCORE) or cos >= COS_ALONE:
                scored.append((score, k))
        elif len(rare) >= KEYWORD_ONLY_MIN:
            scored.append((len(rare), k))
    scored.sort(reverse=True)
    hits = scored[:int(cfg.get("recall_top", 3))]
    if not hits:
        return
    lines = ["Memories that may matter for this message (hints only: open the file before relying on one, "
             "and never carry one client's facts into another's work):"]
    for s, k in hits:
        lines.append(f"- {k}: {docs[k]['label'][:220]} — {docs[k]['path']}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
