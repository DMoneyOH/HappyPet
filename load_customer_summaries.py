#!/usr/bin/env python3
"""Copy Amazon "Customers say" summaries into products.json.

The summaries file is read off each product page in a signed-in browser and
saved as JSON of this shape:

    {"read_on": "YYYY-MM-DD",
     "products": {"<ASIN>": {"summary": "<page text>", ...}, ...}}

For every products.json entry whose ASIN is in the file, this writes
`customer_summary` (cleaned with generate_posts.clean_customer_summary) and
`customer_summary_read_on`. An empty summary in the file removes both fields,
because the newest reading found no summary to cite. An ASIN missing from the
file leaves its entry untouched. Re-run it after every fresh pull:

    python load_customer_summaries.py path/to/customer-summaries.json

The summary text is untrusted page content. It is stored as data and reaches a
prompt only inside the delimited <customer_summary> block.
"""
import argparse
import datetime
import sys
from pathlib import Path

from generate_posts import clean_customer_summary
from json_io import atomic_write_json, read_json

PRODUCTS_PATH = Path(__file__).resolve().parent / "products.json"


def apply_summaries(products: list, summaries: dict) -> dict:
    """Update `products` in place from a parsed summaries file. Returns counts.
    Raises ValueError when the file's read_on is not an ISO date, since every
    summary the site cites must carry the date it was read."""
    read_on = summaries.get("read_on")
    try:
        datetime.date.fromisoformat(read_on)
    except (TypeError, ValueError):
        raise ValueError(f"read_on is {read_on!r}, not an ISO date (YYYY-MM-DD)") from None
    by_asin = summaries.get("products")
    if not isinstance(by_asin, dict):
        raise ValueError("the summaries file has no 'products' object keyed by ASIN")
    counts = {"loaded": 0, "cleared": 0, "missing": 0}
    for entry in products:
        record = by_asin.get(entry.get("asin") or "")
        if not isinstance(record, dict):
            counts["missing"] += 1
            continue
        summary = clean_customer_summary(record.get("summary"))
        if summary:
            entry["customer_summary"] = summary
            entry["customer_summary_read_on"] = read_on
            counts["loaded"] += 1
        else:
            entry.pop("customer_summary", None)
            entry.pop("customer_summary_read_on", None)
            counts["cleared"] += 1
    return counts


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("summaries", help="path to the customer-summaries JSON file")
    p.add_argument("--products", default=str(PRODUCTS_PATH))
    args = p.parse_args(argv)
    products_path = Path(args.products)
    products = read_json(products_path)
    summaries = read_json(args.summaries)
    if not isinstance(products, list) or not isinstance(summaries, dict):
        print("ERROR: products.json must be a list and the summaries file an object",
              file=sys.stderr)
        return 2
    before = [dict(e) for e in products]
    try:
        counts = apply_summaries(products, summaries)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if products != before:
        trailing = products_path.read_text(encoding="utf-8").endswith("\n")
        atomic_write_json(products_path, products, trailing_newline=trailing)
    print(f"{counts['loaded']} loaded, {counts['cleared']} cleared (empty summary), "
          f"{counts['missing']} not in the file; products.json "
          f"{'updated' if products != before else 'unchanged'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
