#!/usr/bin/env python3
"""refill_cli.py -- the deterministic half of the local refill session.

The session (a headless Claude run driving SiteStripe in Chrome) only picks topics
and reads products off Amazon pages. It writes them to one plan file; every
decision about what may enter products.json lives here, offline:

  context --out ctx.json [--batch N]   what the session needs: the 22 topics, the
                                       Pinterest boards, every slug already taken
  seed    --plan plan.json             validate each planned topic, append the
                                       valid ones as NEEDS_* placeholders
  resolve --plan plan.json --base b.json
                                       fill each placeholder this run seeded (not
                                       in b.json) from the plan's product, Chewy
                                       enrichment OFF; drop the ones left unfilled
  check   --base base.json             every seed and resolve rule again, on every
                                       entry not in base.json, plus the auto-merge
                                       gate's refill rule run locally

Plan file: {"abort": null | "<reason>", "topics": [{topic, title, keyword, species,
category, topical_sheet, amazon_search_query, product: null | {name, asin, image,
price, stars, runners_up?, upc?}}]}. Plan content is untrusted.

What the gate re-checks on GitHub (automerge_gate.evaluate_refill) is narrower than
`check`: the PR shape and CI, products.json the only file, no NEEDS_/REVIEW marker,
existing entries unchanged and in order, and for each new entry its ASIN shape and
uniqueness, the canonical affiliate link, the m.media-amazon.com image and a null or
chewy.sjv.io chewy_url. It does NOT re-check slug collisions, category, species,
topical_sheet, the text fields, price, stars or upc, and it allows a chewy.sjv.io
link. Those rules live only here, so `check` must pass before any push.

No network, no git: the wrapper (run-refill.ps1) owns branches, pushes and the PR.
Every command takes --products to work on a scratch copy instead of the repo file.
Exit: 0 ok; nonzero when nothing usable came out, the check found problems, or a
file could not be read.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from json_io import CorruptJSONError, atomic_write_json, read_json
import automerge_gate
import categories
import generate_posts as gp
import refill_products as rp

SLUG_RE = re.compile(r"best-[a-z0-9]+(?:-[a-z0-9]+)*")
SLUG_MAX = 60
TEXT_MAX = 200
SPECIES = ("dog", "cat", "both")
PRICE_RE = re.compile(r"[0-9]{1,5}(?:\.[0-9]{2})?")
UPC_RE = re.compile(r"[0-9]{12,14}")
TOPIC_FIELDS = ("topic", "title", "keyword", "species", "category",
                "topical_sheet", "amazon_search_query")
# Every field build_entry writes: a new entry missing one did not come through seed.
ENTRY_FIELDS = tuple(rp.build_entry({k: "x" for k in TOPIC_FIELDS}))
CHEWY_FIELDS = ("chewy_url", "chewy_price", "chewy_stock", "chewy_rating")
REPO_DIR = Path(__file__).parent.resolve()


def _bare(slug: str) -> str:
    return slug.removeprefix("best-")


def taken_slugs(products: list) -> set:
    """Every slug that is spoken for, in both forms (with and without best-):
    dated posts and drafts, queued entries, and pin-queue files (pending, sent,
    and .fired sentinels)."""
    slugs = set(gp.build_used_slugs())
    slugs |= {e.get("topic") for e in products if isinstance(e.get("topic"), str)}
    pin_dir = REPO_DIR / "_pin_queue"
    for sub in (pin_dir, pin_dir / "sent", pin_dir / ".fired"):
        if sub.is_dir():
            slugs |= {p.name.split(".", 1)[0] for p in sub.iterdir()
                      if p.is_file() and not p.name.startswith(".")}
    return slugs | {_bare(s) for s in slugs}


def _text(name: str, value) -> str | None:
    return automerge_gate.text_problem(name, value, TEXT_MAX)


def topic_problems(t, taken: set) -> list:
    """Problems with one planned topic; empty = it may be seeded."""
    if not isinstance(t, dict):
        return ["not an object"]
    bad = []
    slug = t.get("topic")
    if not (isinstance(slug, str) and SLUG_RE.fullmatch(slug) and len(slug) <= SLUG_MAX):
        bad.append("topic is not a best-<slug>")
    elif slug in taken or _bare(slug) in taken:
        bad.append("topic collides with a published, drafted, queued or pinned slug")
    for f in ("title", "keyword", "amazon_search_query"):
        if p := _text(f, t.get(f)):
            bad.append(p)
    if t.get("species") not in SPECIES:
        bad.append("species is not dog/cat/both")
    # The 22 bare topics only. rp.VALID_CATEGORIES still accepts the legacy names
    # for old branches; a new entry must never use one.
    if t.get("category") not in categories.TOPICS:
        bad.append("category is not one of the topics in _data/categories.json")
    if t.get("topical_sheet") not in rp.VALID_SHEETS:
        bad.append("topical_sheet is not a category board")
    return bad


def product_problems(p) -> list:
    """Problems with one product read off Amazon; empty = it may fill a placeholder."""
    if not isinstance(p, dict):
        return ["product is not an object"]
    bad = []
    if not rp.validate_candidate(p):
        bad.append("failed validate_candidate (ASIN shape, image host, sponsored name)")
    elif not automerge_gate.AMAZON_IMAGE.fullmatch(p["image"]):
        bad.append("image URL is not the gate's exact m.media-amazon.com form")
    if why := _text("name", p.get("name")):
        bad.append(why)
    if not (isinstance(p.get("price"), str) and PRICE_RE.fullmatch(p["price"])):
        bad.append("price is not a plain decimal string")
    stars = p.get("stars")
    if isinstance(stars, bool) or not isinstance(stars, (int, float)) or not 0 <= stars <= 5:
        bad.append("stars is not a number from 0 to 5")
    if p.get("runners_up") is not None and (why := _text("runners_up", p["runners_up"])):
        bad.append(why)
    if p.get("upc") is not None and not (isinstance(p["upc"], str) and UPC_RE.fullmatch(p["upc"])):
        bad.append("upc is not 12-14 digits")
    return bad


def new_entry_problems(e: dict, taken: set) -> list:
    """Every seed and resolve rule, applied to one entry as it sits in products.json.
    `taken` must not contain the entry's own slug."""
    bad = []
    missing = [k for k in ENTRY_FIELDS if k not in e]
    if missing:
        bad.append("missing field(s) " + ", ".join(missing))
    bad += topic_problems({k: e.get(k) for k in TOPIC_FIELDS}, taken)
    if e.get("format") != "roundup":
        bad.append("format is not roundup")
    product = {k: e.get(k) for k in ("name", "asin", "image", "price", "stars")}
    if e.get("runners_up"):          # build_entry writes "" when there are none
        product["runners_up"] = e["runners_up"]
    if "upc" in e:
        product["upc"] = e["upc"]
    bad += product_problems(product)
    # Locally, Chewy data comes only from the GTIN path, never from refill.
    bad += [f"{k} is set; refill never adds Chewy data" for k in CHEWY_FIELDS
            if e.get(k) is not None]
    return bad


def load_products(path, label: str) -> list:
    """products.json as a list, or a clean SystemExit naming the file."""
    try:
        data = read_json(path)
    except (OSError, CorruptJSONError) as exc:
        raise SystemExit(f"{label} unreadable: {exc}") from None
    if not isinstance(data, list):
        raise SystemExit(f"{label} {path} is missing or not a JSON list")
    return data


def load_plan(path: Path) -> dict:
    try:
        # utf-8-sig: Windows PowerShell 5.1 writes a BOM with -Encoding utf8.
        plan = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"plan unreadable: {exc}") from None
    if not isinstance(plan, dict) or not isinstance(plan.get("topics"), list):
        raise SystemExit("plan is not an object with a topics list")
    return plan


def cmd_context(args) -> int:
    products = load_products(args.products, "products")
    ctx = {
        "batch": args.batch,
        "categories": list(categories.TOPICS),
        "topical_sheets": list(rp.VALID_SHEETS),
        "species": list(SPECIES),
        "queued": [{"topic": e.get("topic"), "category": e.get("category"),
                    "species": e.get("species")} for e in products],
        "taken_slugs": sorted(s for s in taken_slugs(products) if s.startswith("best-")),
    }
    Path(args.out).write_text(json.dumps(ctx, indent=2) + "\n", encoding="utf-8")
    print(f"context: {len(ctx['taken_slugs'])} taken slugs, batch {args.batch}")
    return 0


def cmd_seed(args) -> int:
    plan = load_plan(Path(args.plan))
    if plan.get("abort"):
        print(f"ABORTED by the session: {str(plan['abort'])[:300]!r}")
        return 1
    products = load_products(args.products, "products")
    taken = taken_slugs(products)
    seeded = []
    for t in plan["topics"][:args.max]:
        bad = topic_problems(t, taken)
        name = t.get("topic") if isinstance(t, dict) else None
        if bad:
            print(f"REJECTED topic {name!r}: {'; '.join(bad)}")
            continue
        products.append(rp.build_entry({k: t[k] for k in TOPIC_FIELDS}))
        taken |= {name, _bare(name)}
        seeded.append(name)
    if not seeded:
        print("seeded nothing")
        return 1
    atomic_write_json(Path(args.products), products, trailing_newline=True)
    print(f"seeded {len(seeded)}: {', '.join(seeded)}")
    return 0


def cmd_resolve(args) -> int:
    plan = load_plan(Path(args.plan))
    path = Path(args.products)
    products = load_products(path, "products")
    planned = {t.get("topic"): t.get("product") for t in plan["topics"] if isinstance(t, dict)}
    # Only entries this run seeded: anything already on the branch point is never
    # filled or dropped here, so a plan naming a queued topic cannot touch it.
    base_topics = {e.get("topic") for e in load_products(args.base, "base")
                   if isinstance(e, dict)}
    filled, dropped = [], []
    for entry in products:
        topic = entry.get("topic")
        if topic in base_topics or topic not in planned \
                or not automerge_gate.is_placeholder(entry):
            continue
        product = planned[topic]
        bad = product_problems(product) if product is not None else ["no product in the plan"]
        if bad:
            dropped.append({"topic": topic, "reasons": bad})
            continue
        resolved = {k: product[k] for k in ("name", "asin", "image", "price", "stars")}
        for k in ("runners_up", "upc"):
            if product.get(k):
                resolved[k] = product[k]
        rp.apply_resolution(entry, resolved, enrich_chewy=False)
        filled.append(topic)
    gone = {d["topic"] for d in dropped}
    products = [e for e in products if e.get("topic") not in gone]
    atomic_write_json(path, products, trailing_newline=True)
    if args.result:
        Path(args.result).write_text(json.dumps({"filled": filled, "dropped": dropped},
                                                indent=2) + "\n", encoding="utf-8")
    for d in dropped:
        print(f"DROPPED {d['topic']!r}: {'; '.join(d['reasons'])}")
    print(f"filled {len(filled)}, dropped {len(dropped)}")
    return 0 if filled else 1


def cmd_check(args) -> int:
    head = load_products(args.products, "products")
    base = load_products(args.base, "base")
    problems = automerge_gate.refill_products_problems(json.dumps(head), json.dumps(base))
    base_topics = {e.get("topic") for e in base if isinstance(e, dict)}
    taken = taken_slugs(base)
    for e in head:
        if not isinstance(e, dict) or e.get("topic") in base_topics:
            continue
        name = e.get("topic")
        problems += [f"entry {name!r}: {m}" for m in new_entry_problems(e, taken)]
        if isinstance(name, str):
            taken |= {name, _bare(name)}   # a second entry with this slug collides
    for p in problems:
        print(f"PROBLEM {p}")
    print("check: PASS" if not problems else f"check: HOLD ({len(problems)} problem(s))")
    return 0 if not problems else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="refill_cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--products", default=str(rp.PRODUCTS_PATH),
                    help="products.json to read/write (default: the repo's)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_context = sub.add_parser("context")
    p_context.add_argument("--out", required=True)
    p_context.add_argument("--batch", type=int, default=6)
    p_context.set_defaults(func=cmd_context)
    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--plan", required=True)
    p_seed.add_argument("--max", type=int, default=10)
    p_seed.set_defaults(func=cmd_seed)
    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("--plan", required=True)
    p_resolve.add_argument("--base", required=True)
    p_resolve.add_argument("--result", help="write {filled, dropped} JSON here (outside the repo)")
    p_resolve.set_defaults(func=cmd_resolve)
    p_check = sub.add_parser("check")
    p_check.add_argument("--base", required=True)
    p_check.set_defaults(func=cmd_check)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
