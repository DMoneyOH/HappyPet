"""Topic categories, loaded from _data/categories.json -- the single definition
Jekyll (site.data.categories) and the pipeline both read. Edit the JSON, not
this file."""
import json
import re
from pathlib import Path

_TOPICS = json.loads(
    (Path(__file__).resolve().parent / "_data" / "categories.json")
    .read_text(encoding="utf-8"))["topics"]

TOPICS      = tuple(t["slug"] for t in _TOPICS)
LABELS      = {t["slug"]: t["label"] for t in _TOPICS}
CTAS        = {t["slug"]: t["cta"] for t in _TOPICS}
USES        = {t["slug"]: t["use"] for t in _TOPICS}

_CATEGORY = re.compile(r"^categories:\s*\[\s*['\"]?([^\]'\",\s]+)", re.M)


def post_category(md: Path):
    """A post's FIRST `categories:` value, quotes stripped, or None. The reader
    of a post's topic: generate_posts, hub_pages, push_pins_to_sheets and the
    tests use it. indexnow.url_for reads every category itself, because
    Jekyll's permalink joins them all."""
    m = _CATEGORY.search(Path(md).read_text(encoding="utf-8"))
    return m.group(1) if m else None
