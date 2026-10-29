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


def _product_for(slug: str, required: bool = False):
    """(entry, None) for a known slug, (None, None) when no slug was given and
    none is required, and (None, 2) after printing the error for an unknown
    slug or a missing required one."""
    if not slug:
        if required:
            print("ERROR: --slug is required and was empty", file=sys.stderr)
            return None, 2
        return None, None
    product = gp.load_products().get(slug)
    if product is None:
        print(f"ERROR: unknown slug {slug!r}", file=sys.stderr)
        return None, 2
    return product, None


def cmd_review_prompt(args) -> int:
    body = _read(args.body)
    title, keyword, verified = args.title, args.keyword, ""
    product, err = _product_for(args.slug)
    if err:
        return err
    if product is not None:
        title = title or product.get("title", "")
        keyword = keyword or product.get("keyword", "")
        verified = gp.build_verified_facts(product)
    print(gp.make_review_prompt(
        title, keyword, body, verified,
        sources=gp.feedback_sources(product)))
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
    product, err = _product_for(args.slug)
    if err:
        return err
    # Buyer feedback is judged against the entry's customer summary; with no
    # --slug there is none, so every customer attribution is flagged.
    passed, flags = gp.authoritative_gate(scorecard, scrubbed,
                                          gp.feedback_sources(product))
    product_name = ""
    # A link to a product with no record is read off the body here too, when the
    # slug is given. authoritative_gate itself cannot do it -- its signature
    # carries no product context, and the check is meaningless without the
    # entry's own link to compare against. Staging holds the article regardless
    # (validate_output + stage_article); flagging it here is what lets the
    # rewrite pass fix it instead of hitting the hold.
    if product is not None:
        product_name = product.get("name", "")
        for link in gp.find_unbacked_affiliate_links(
                scrubbed, product.get("affiliate_url", "")):
            passed = False
            flags.append(f"unbacked_affiliate_link_in_body={link!r}")
        # Alternative picks, same reasoning as the link check above: staging
        # holds either way, and a flag HERE is what lets the rewrite pass fix
        # it instead of hitting the hold. This is the agent-driven path's share
        # of the work fact_check_alternatives does on the scheduled path --
        # that stage never ran here, so an article could be written, gated,
        # staged and published without one pass over its alternative sections.
        if product.get("format") == "roundup":
            runners_up = product.get("runners_up", "")
            for name in gp.find_unlisted_alternatives(scrubbed, runners_up):
                passed = False
                flags.append(f"unlisted_alternative_pick={name!r}")
            for figure in gp.find_unsourced_alternative_figures(scrubbed, runners_up):
                passed = False
                flags.append(f"unsourced_figure_on_alternative={figure!r}")
    # The answer-first lead and question block, same reasoning again: staging
    # holds without them, and a flag here gives the rewrite pass the chance to
    # add them. The top-pick half needs --slug for the product name.
    for problem in gp.find_question_block_problems(scrubbed, product_name):
        passed = False
        flags.append(f"question_block: {problem}")
    # The Pinterest description the routine wrote beside the body
    # (LOOP/pindesc.txt), judged as stage judges it, so the rewrite pass can
    # fix it here instead of hitting the stage hold.
    pin_path = Path(args.pin_desc_file) if args.pin_desc_file else \
        Path(args.body).with_name("pindesc.txt")
    if pin_path.is_file():
        pin_desc = gp.clean_pin_desc(_read(pin_path),
                                     (product or {}).get("species", "dog"))
        for claim in gp.find_firsthand_claims(pin_desc):
            passed = False
            flags.append(f"pin_desc: firsthand claim {claim!r}")
        for claim in gp.find_unsourced_buyer_feedback(pin_desc):
            passed = False
            flags.append(f"pin_desc: buyer feedback {claim!r}")
    print(json.dumps({"passed": passed, "flags": flags, "scrubbed_body": scrubbed}))
    return 0


def cmd_rewrite_prompt(args) -> int:
    body = _read(args.body)
    instructions = _read(args.instructions)
    product, err = _product_for(args.slug)
    if err:
        return err
    entry = product or {}
    # --slug means what it means on review-prompt: the entry fills every field
    # a flag leaves empty, and supplies the customer summary.
    print(gp.make_rewrite_prompt(
        args.title or entry.get("title", ""), args.keyword or entry.get("keyword", ""),
        body, instructions,
        affiliate_url=args.affiliate_url or entry.get("affiliate_url", ""),
        product_name=args.product_name or entry.get("name", ""),
        sources=gp.feedback_sources(product)))
    return 0


def cmd_stage(args) -> int:
    body = gp.scrub_typography(_read(args.body))
    product, err = _product_for(args.slug, required=True)
    if err:
        return err
    pin_desc = gp.clean_pin_desc(args.pin_desc or f"{product['title']} - reviews and buying guide.",
                                 product.get("species", "dog"))
    try:
        gp.validate_output(
            "review", body, args.slug,
            affiliate_url=product.get("affiliate_url", ""),
            runners_up=(product.get("runners_up", "")
                        if product.get("format") == "roundup" else None),
            product_name=product.get("name", ""),
            sources=gp.feedback_sources(product))
        # The pin description becomes the published `description` and the
        # Pinterest text, which travels without the post's date: no buyer feedback.
        gp.assert_no_unsourced_buyer_feedback("pin_desc", pin_desc, args.slug)
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
    rp.add_argument("--body", required=True)
    rp.add_argument("--slug", default="",
                    help="products.json entry: fills empty --title/--keyword, adds its "
                         "verified facts and its Amazon customer summary")
    rp.add_argument("--title", default=""); rp.add_argument("--keyword", default="")
    rp.set_defaults(func=cmd_review_prompt)

    g = sub.add_parser("gate")
    g.add_argument("--body", required=True); g.add_argument("--scorecard", required=True)
    g.add_argument("--slug", default="",
                   help="products.json entry: supplies its link, alternatives, product "
                        "name and Amazon customer summary; without it every "
                        "buyer-feedback line is flagged")
    g.add_argument("--pin-desc-file", dest="pin_desc_file", default="",
                   help="Pinterest description to check (default: pindesc.txt "
                        "beside --body, when it exists)")
    g.set_defaults(func=cmd_gate)

    rw = sub.add_parser("rewrite-prompt")
    rw.add_argument("--body", required=True); rw.add_argument("--instructions", required=True)
    rw.add_argument("--title", default=""); rw.add_argument("--keyword", default="")
    rw.add_argument("--affiliate-url", dest="affiliate_url", default="")
    rw.add_argument("--product-name", dest="product_name", default="")
    rw.add_argument("--slug", default="",
                    help="products.json entry: fills empty --title/--keyword/"
                         "--affiliate-url/--product-name and adds its Amazon "
                         "customer summary (same meaning as on review-prompt)")
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
