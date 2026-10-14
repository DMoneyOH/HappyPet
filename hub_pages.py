#!/usr/bin/env python3
"""hub_pages.py -- give every topic with HUB_MIN published posts a hub page.

A hub is hubs/<topic>.md (layout: hub, permalink /<topic>/); the layout lists
the topic's posts itself, so a hub never needs editing when a post lands. What
needs a file is a topic reaching HUB_MIN posts for the first time: this writes
a minimal hub for it (title = the topic label, no intro) and leaves existing
hubs untouched. Copy for a new hub comes from Scarlett afterwards.

Not wired into any workflow. Run it by hand, or from publish.yml after a post
is dated (a CI change, so it needs its own approval).

usage: python hub_pages.py [--dry-run]
"""
import re
import sys
from collections import Counter
from pathlib import Path

import categories

REPO_DIR = Path(__file__).parent.resolve()
HUB_MIN = 3
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


def write_hub(slug: str, repo: Path = REPO_DIR) -> Path:
    label = categories.LABELS[slug]
    path = repo / "hubs" / f"{slug}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(f'---\nlayout: hub\nhub_topic: {slug}\npermalink: /{slug}/\n'
                    f'title: "{label}"\n---\n', encoding="utf-8")
    return path


def main(argv=None) -> int:
    dry = "--dry-run" in (argv if argv is not None else sys.argv[1:])
    todo = missing_hubs()
    for slug in todo:
        print(("would write" if dry else "wrote"), f"hubs/{slug}.md")
        if not dry:
            write_hub(slug)
    if not todo:
        print("every topic with", HUB_MIN, "or more posts has a hub")
    return 0


if __name__ == "__main__":
    sys.exit(main())
