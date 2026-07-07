#!/usr/bin/env python3
"""
pseudo_chewy_check.py -- ONE-OFF, throwaway script (not part of the pipeline).
Runs the real chewy_lookup.lookup() against candidate product names for
still-unresolved products.json entries, to see how many would actually
match on Chewy before spending time on live Amazon resolution for them.
Never merged to main -- lives only on a scratch branch / CI run.
"""
import json
from chewy_lookup import lookup

TOPICS = [
    "best-elevated-dog-beds",
    "best-gps-dog-trackers",
    "best-slow-feeder-dog-bowls",
    "best-dog-pools",
    "best-cat-dental-treats",
    "best-dog-ramps",
    "best-cat-puzzle-feeders",
    "best-dog-training-treats",
]

products = json.loads(open("products.json", encoding="utf-8").read())
by_topic = {p["topic"]: p for p in products}

print(f"{'topic':32s} {'status':10s} {'price':8s} name")
print("-" * 100)
for t in TOPICS:
    p = by_topic[t]
    name = p["name"]
    r = lookup(name)
    url = r.get("chewy_url")
    if url is None:
        status = "NO_CREDS"
    elif str(url).startswith("REVIEW"):
        status = "REVIEW"
    else:
        status = "MATCH"
    price = r.get("chewy_price") or "-"
    print(f"{t:32s} {status:10s} {str(price):8s} {name[:60]}")
