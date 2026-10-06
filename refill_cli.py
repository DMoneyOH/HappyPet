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
  check   --base base.json             the auto-merge gate's own refill rule run
                                       locally against the branch point, plus
                                       validate_product on every new entry

Plan file: {"abort": null | "<reason>", "topics": [{topic, title, keyword, species,
category, topical_sheet, amazon_search_query, product: null | {name, asin, image,
price, stars, runners_up?, upc?}}]}. Plan content is untrusted: every field is
checked here, and the gate checks the result again on GitHub.

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

from json_io import atomic_write_json, read_json
import automerge_gate
import categories
import generate_posts as gp
import refill_products as rp

SLUG_RE = re.compile(r"best-[a-z0-9]+(?:-[a-z0-9]+)*")
SLUG_MAX = 60
TEXT_MAX = 200
# Titles reach pin JSON, Sheets and IFTTT; automerge_gate.pin_problems refuses these.
TEXT_FORBIDDEN = frozenset('"`\\<>')
SPECIES = ("dog", "cat", "both")
PRICE_RE = re.compile(r"[0-9]{1,5}(?:\.[0-9]{2})?")
UPC_RE = re.compile(r"[0-9]{12,14}")
TOPIC_FIELDS = ("topic", "title", "keyword", "species", "category",
                "topical_sheet", "amazon_search_query")
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


def _text_problem(name: str, value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return f"{name} is empty"
    if len(value) > TEXT_MAX or not value.isprintable() or value != value.strip():
        return f"{name} is too long, has control characters or edge whitespace"
    if TEXT_FORBIDDEN & set(value):
        return f"{name} has a quote, backtick, backslash or angle bracket"
    return None


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
        if p := _text_problem(f, t.get(f)):
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
    elif not automerge_gate._AMAZON_IMAGE.fullmatch(p["image"]):
        bad.append("image URL is not the gate's exact m.media-amazon.com form")
    if why := _text_problem("name", p.get("name")):
        bad.append(why)
    if not (isinstance(p.get("price"), str) and PRICE_RE.fullmatch(p["price"])):
        bad.append("price is not a plain decimal string")
    stars = p.get("stars")
    if isinstance(stars, bool) or not isinstance(stars, (int, float)) or not 0 <= stars <= 5:
        bad.append("stars is not a number from 0 to 5")
    if p.get("runners_up") is not None and (why := _text_problem("runners_up", p["runners_up"])):
        bad.append(why)
    if p.get("upc") is not None and not (isinstance(p["upc"], str) and UPC_RE.fullmatch(p["upc"])):
        bad.append("upc is not 12-14 digits")
    return bad


def load_plan(path: Path) -> dict:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"plan unreadable: {exc}") from None
    if not isinstance(plan, dict) or not isinstance(plan.get("topics"), list):
        raise SystemExit("plan is not an object with a topics list")
    return plan


def cmd_context(args) -> int:
    products = read_json(args.products, default=[])
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
    products = read_json(args.products, default=[])
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
    products = read_json(path, default=[])
    planned = {t.get("topic"): t.get("product") for t in plan["topics"] if isinstance(t, dict)}
    # Only entries this run seeded: anything already on the branch point is never
    # filled or dropped here, so a plan naming a queued topic cannot touch it.
    base_topics = {e.get("topic") for e in read_json(args.base, default=[]) if isinstance(e, dict)}
    filled, dropped = [], []
    for entry in products:
        topic = entry.get("topic")
        if topic in base_topics or topic not in planned \
                or not automerge_gate._is_placeholder(entry):
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
    head_text = Path(args.products).read_text(encoding="utf-8")
    base_text = Path(args.base).read_text(encoding="utf-8")
    problems = automerge_gate.refill_products_problems(head_text, base_text)
    base_topics = {e.get("topic") for e in json.loads(base_text) if isinstance(e, dict)}
    for e in json.loads(head_text):
        if isinstance(e, dict) and e.get("topic") not in base_topics:
            problems += [f"entry {e.get('topic')!r}: {m}"
                         for m in gp.validate_product(e.get("topic"), e)]
            if e.get("category") not in categories.TOPICS:
                problems.append(f"entry {e.get('topic')!r}: category is not a bare topic")
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
    c = sub.add_parser("context"); c.add_argument("--out", required=True)
    c.add_argument("--batch", type=int, default=6); c.set_defaults(func=cmd_context)
    s = sub.add_parser("seed"); s.add_argument("--plan", required=True)
    s.add_argument("--max", type=int, default=10); s.set_defaults(func=cmd_seed)
    r = sub.add_parser("resolve"); r.add_argument("--plan", required=True)
    r.add_argument("--base", required=True)
    r.add_argument("--result", help="write {filled, dropped} JSON here (outside the repo)")
    r.set_defaults(func=cmd_resolve)
    k = sub.add_parser("check"); k.add_argument("--base", required=True)
    k.set_defaults(func=cmd_check)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
