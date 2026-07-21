#!/usr/bin/env python3
"""Plumbing CLI for the internal Claude Stage-1 routine. Exposes the deterministic
pieces the agent needs (prompt building, the authoritative gate, staging) over a
thin command surface. No model calls happen here -- the agent supplies the write,
review, and rewrite text using Claude models."""
import argparse, json, sys
from pathlib import Path

# Windows: when stdout/stderr are piped (subprocess capture, as in
# test_stage1_cli.py), Python can default to the system ANSI codepage
# (cp1252) instead of UTF-8. The prompts printed here contain literal
# U+2014/U+2013 (em/en dash) and other non-cp1252 characters, so force
# UTF-8 explicitly rather than crash on print().
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

import generate_posts as gp


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def cmd_next_topic(args) -> int:
    products = gp.load_products()
    used = gp.build_used_slugs()
    picked = gp.select_next_topic(products, used)
    if picked is None:
        print(json.dumps({}))
        return 0
    slug, product = picked
    writer = gp.build_writer_inputs(slug, product)
    print(json.dumps({
        "slug": slug, "title": writer["title"], "keyword": writer["keyword"],
        "fmt": writer["fmt"], "species": writer["species"],
        "affiliate_url": writer["affiliate_url"],
        "system": writer["system"], "user": writer["user"],
    }))
    return 0


def cmd_review_prompt(args) -> int:
    body = _read(args.body)
    print(gp.make_review_prompt(args.title, args.keyword, body))
    return 0


def cmd_gate(args) -> int:
    body = _read(args.body)
    scorecard = json.loads(_read(args.scorecard))
    # Scrub first, then gate on the SCRUBBED body (spec 4.1). scrub_typography
    # deterministically converts em/en dashes to human punctuation, so a fixable
    # em dash is auto-corrected and never causes a hold -- the article passes and
    # the scrubbed text is what staging writes (cmd_stage re-scrubs identically).
    # The em-dash hard-check inside authoritative_gate is a backstop for the rare
    # case a real em dash survives scrubbing.
    scrubbed = gp.scrub_typography(body)
    passed, flags = gp.authoritative_gate(scorecard, scrubbed)
    print(json.dumps({"passed": passed, "flags": flags, "scrubbed_body": scrubbed}))
    return 0


def cmd_rewrite_prompt(args) -> int:
    body = _read(args.body)
    instructions = _read(args.instructions)
    print(gp.make_rewrite_prompt(args.title, args.keyword, body, instructions,
                                 affiliate_url=args.affiliate_url,
                                 product_name=args.product_name))
    return 0


def cmd_stage(args) -> int:
    body = gp.scrub_typography(_read(args.body))
    products = gp.load_products()
    product = products.get(args.slug)
    if product is None:
        print(f"ERROR: unknown slug {args.slug!r}", file=sys.stderr)
        return 2
    pin_desc = gp.clean_pin_desc(args.pin_desc or f"{product['title']} - reviews and buying guide.",
                                 product.get("species", "dog"))
    try:
        gp.validate_output("review", body, args.slug,
                           affiliate_url=product.get("affiliate_url", ""))
    except gp.GenerationStageError as e:
        print(f"ERROR: content contract failed: {e}", file=sys.stderr)
        return 3
    out = gp.stage_article(args.slug, product, body, pin_desc)
    print(json.dumps({"draft_path": str(out["draft_path"]),
                      "pin_queue_path": str(out["pin_queue_path"])}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stage1_cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("next-topic").set_defaults(func=cmd_next_topic)

    rp = sub.add_parser("review-prompt")
    rp.add_argument("--body", required=True); rp.add_argument("--title", required=True)
    rp.add_argument("--keyword", required=True); rp.set_defaults(func=cmd_review_prompt)

    g = sub.add_parser("gate")
    g.add_argument("--body", required=True); g.add_argument("--scorecard", required=True)
    g.set_defaults(func=cmd_gate)

    rw = sub.add_parser("rewrite-prompt")
    rw.add_argument("--body", required=True); rw.add_argument("--instructions", required=True)
    rw.add_argument("--title", default=""); rw.add_argument("--keyword", default="")
    rw.add_argument("--affiliate-url", dest="affiliate_url", default="")
    rw.add_argument("--product-name", dest="product_name", default="")
    rw.set_defaults(func=cmd_rewrite_prompt)

    st = sub.add_parser("stage")
    st.add_argument("--slug", required=True); st.add_argument("--body", required=True)
    st.add_argument("--pin-desc", dest="pin_desc", default="")
    st.set_defaults(func=cmd_stage)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
