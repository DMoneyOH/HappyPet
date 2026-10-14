#!/usr/bin/env python3
"""hub_pages.py -- give every topic with HUB_MIN published posts a hub page.

A hub is hubs/<topic>.md (layout: hub, permalink /<topic>/); the layout lists
the topic's posts itself, so a hub never needs editing when a post lands. What
needs a file is a topic reaching HUB_MIN posts for the first time: this writes
its hub and leaves existing hubs untouched.

A hub's copy (title, description, intro, how_we_choose) comes from
_data/hub_copy.json, the one source for every topic: each hub file is exactly
hub_text() of its entry, and the tests hold it there. A topic that needs a hub
but has no complete copy is an error: nothing is written and the exit code is
1, never a hub without copy.

publish.yml runs this after it dates the day's posts (Director-approved
2026-10-14), so the publish that takes a topic to HUB_MIN posts commits the
topic's hub in the same commit. A copy error fails that publish before its
commit, so nothing is pushed.

usage: python hub_pages.py [--dry-run]
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

import categories

REPO_DIR = Path(__file__).parent.resolve()
HUB_MIN = 3
COPY_FIELDS = ("title", "description", "intro", "how_we_choose")
_DATED = re.compile(r"\d{4}-\d{2}-\d{2}-.+\.md")


def topic_counts(repo: Path = REPO_DIR) -> Counter:
    """Published (dated) posts per topic, by each post's first category."""
    counts = Counter()
    for md in (repo / "_posts").glob("*.md"):
        if _DATED.fullmatch(md.name):
            topic = categories.post_category(md)
            if topic:
                counts[topic] += 1
    return counts


def missing_hubs(repo: Path = REPO_DIR) -> list:
    """Topics with at least HUB_MIN posts and no hubs/<topic>.md, in topic order."""
    counts = topic_counts(repo)
    return [t for t in categories.TOPICS
            if counts[t] >= HUB_MIN and not (repo / "hubs" / f"{t}.md").exists()]


def load_copy(repo: Path = REPO_DIR) -> dict:
    return json.loads((repo / "_data" / "hub_copy.json").read_text(encoding="utf-8"))


def copy_problems(slug: str, copy: dict) -> list:
    """Why `slug` cannot get a hub from `copy`; empty = it can."""
    entry = copy.get(slug)
    if not isinstance(entry, dict):
        return [f"{slug}: no entry in _data/hub_copy.json"]
    return [f"{slug}: {f} is missing or empty in _data/hub_copy.json" for f in COPY_FIELDS
            if not (isinstance(entry.get(f), str) and entry[f].strip())]


def hub_text(slug: str, copy: dict) -> str:
    """The hub file for `slug`. Each copy value is written as a JSON string,
    which is also a valid YAML double-quoted scalar."""
    lines = ["---", "layout: hub", f"hub_topic: {slug}", f"permalink: /{slug}/"]
    lines += [f"{f}: {json.dumps(copy[slug][f], ensure_ascii=False)}" for f in COPY_FIELDS]
    return "\n".join(lines + ["---", ""])


def write_hub(slug: str, copy: dict, repo: Path = REPO_DIR) -> Path:
    path = repo / "hubs" / f"{slug}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(hub_text(slug, copy), encoding="utf-8")
    return path


def main(argv=None, repo: Path = REPO_DIR) -> int:
    dry = "--dry-run" in (argv if argv is not None else sys.argv[1:])
    todo = missing_hubs(repo)
    if not todo:
        print("every topic with", HUB_MIN, "or more posts has a hub")
        return 0
    copy = load_copy(repo)
    problems = [p for slug in todo for p in copy_problems(slug, copy)]
    if problems:
        for p in problems:
            print(f"ERROR {p}", file=sys.stderr)
        print("ERROR wrote no hub: a hub ships only with its copy. Add the topic's "
              "title, description, intro and how_we_choose to _data/hub_copy.json.",
              file=sys.stderr)
        return 1
    for slug in todo:
        print(("would write" if dry else "wrote"), f"hubs/{slug}.md")
        if not dry:
            write_hub(slug, copy, repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
