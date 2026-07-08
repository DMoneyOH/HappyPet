#!/usr/bin/env python3
"""
manual_resolve.py -- apply a manually-found Amazon product to a products.json
placeholder entry.

Amazon resolution in refill_products.py is currently blocked (anonymous
scrape returning 0/21 resolved as of 2026-07-07 -- see
docs/superpowers/specs/2026-07-07-refill-manual-resolve-design.md). This
script takes a product found via a live, logged-in browser session and
applies it through refill_products.py's existing validation and Chewy
enrichment -- no Amazon or Chewy network code is duplicated here.

Usage:
    python3 manual_resolve.py --topic best-automatic-litter-box \
      --name "PETLIBRO Automatic Self-Cleaning Litter Box" \
      --asin B0ABCD1234 \
      --image https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg \
      --price 249.99 --stars 4.5 \
      --runners-up "Litter-Robot 4; PetSafe ScoopFree" \
      --upc 810189030893

Exits non-zero and writes nothing if:
  - --topic doesn't match an existing products.json entry
  - the matched entry is not currently a NEEDS_ASIN/NEEDS_IMAGE placeholder
  - the candidate fails refill_products.validate_candidate() (bad ASIN shape,
    wrong image host, or a "Sponsored"-prefixed name)
"""
import argparse
import json

import refill_products as rp


def find_placeholder(products: list, topic: str) -> dict:
    entry = next((e for e in products if e.get("topic") == topic), None)
    if entry is None:
        raise SystemExit(f"no products.json entry with topic '{topic}'")
    if entry.get("asin") != "NEEDS_ASIN" and entry.get("image") != "NEEDS_IMAGE":
        raise SystemExit(
            f"'{topic}' is not a NEEDS_ASIN/NEEDS_IMAGE placeholder "
            f"(asin={entry.get('asin')!r}, image={entry.get('image')!r})")
    return entry


def main(argv: list | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--asin", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--price", required=True)
    parser.add_argument("--stars", required=True, type=float)
    parser.add_argument("--runners-up", dest="runners_up", default=None)
    parser.add_argument("--upc", default=None,
                        help="Amazon UPC/GTIN, if visible in the product's "
                             "'Product information' section -- enables an "
                             "exact-match fast path in Chewy enrichment")
    args = parser.parse_args(argv)

    products = rp.load_products()
    entry = find_placeholder(products, args.topic)

    card = {"name": args.name, "asin": args.asin, "image": args.image}
    if not rp.validate_candidate(card):
        raise SystemExit(
            f"REJECTED '{args.topic}': failed validate_candidate "
            f"(bad ASIN shape, wrong image host, or sponsored-prefixed name)")

    resolved = {
        "name": args.name, "asin": args.asin, "image": args.image,
        "price": args.price, "stars": args.stars,
    }
    if args.runners_up:
        resolved["runners_up"] = args.runners_up
    if args.upc:
        resolved["upc"] = args.upc

    rp.apply_resolution(entry, resolved)
    rp.PRODUCTS_PATH.write_text(json.dumps(products, indent=2) + "\n")
    print(f"APPLIED '{args.topic}': {args.name} ({args.asin})")


if __name__ == "__main__":
    main()
