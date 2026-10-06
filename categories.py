"""Topic categories, loaded from _data/categories.json -- the single definition
Jekyll (site.data.categories) and the pipeline both read. Edit the JSON, not
this file."""
import json
from pathlib import Path

_TOPICS = json.loads(
    (Path(__file__).resolve().parent / "_data" / "categories.json")
    .read_text(encoding="utf-8"))["topics"]

TOPICS      = tuple(t["slug"] for t in _TOPICS)
LABELS      = {t["slug"]: t["label"] for t in _TOPICS}
CTAS        = {t["slug"]: t["cta"] for t in _TOPICS}
USES        = {t["slug"]: t["use"] for t in _TOPICS}
CONSUMABLE  = frozenset(t["slug"] for t in _TOPICS if t["consumable"])
HARD_GOODS  = frozenset(TOPICS) - CONSUMABLE
