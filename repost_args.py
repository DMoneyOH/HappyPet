#!/usr/bin/env python3
"""
repost_args.py
Checks the repost list typed into the fb-repost workflow before anything else runs.

The list arrives in the REPOST_LIST environment variable (never on a command line
or in the workflow's shell text): "slug YYYY-MM-DD, slug YYYY-MM-DD, ...". Each
pair must meet the rules push_pins_to_sheets.py --repost enforces on its own
arguments: the slug matches that file's _SLUG_RE (imported, so the two cannot
drift) and the date is a real calendar date. The date form is stricter than the
script's, which also accepts 20261022 and 2026-W43-3, so the workflow takes
only YYYY-MM-DD. Whether a date is after today and whether a slug is published
and not queued within 7 days of it stay with --repost, which reads the sheet.

On success, prints one "slug date" line per pair. On a bad entry, prints one
::error:: line and exits 1.
"""

import datetime as _dt
import os
import re
import sys

from push_pins_to_sheets import _SLUG_RE

_DATE_RE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')


def parse_repost_list(text: str) -> list:
    """Return [(slug, 'YYYY-MM-DD'), ...] or raise ValueError naming the bad entry."""
    if not text or not text.strip():
        raise ValueError('the repost list is empty')
    pairs = []
    for entry in text.split(','):
        parts = entry.split()
        if len(parts) != 2:
            raise ValueError(f'{entry.strip()!r} is not "slug YYYY-MM-DD"')
        slug, day = parts
        if not _SLUG_RE.fullmatch(slug):
            raise ValueError(f'{slug!r} is not a post slug')
        if not _DATE_RE.fullmatch(day):
            raise ValueError(f'{day!r} is not a YYYY-MM-DD date')
        try:
            _dt.date.fromisoformat(day)
        except ValueError:
            raise ValueError(f'{day!r} is not a calendar date') from None
        pairs.append((slug, day))
    return pairs


def main() -> int:
    try:
        pairs = parse_repost_list(os.environ.get('REPOST_LIST', ''))
    except ValueError as exc:
        print(f'::error::Bad repost list: {exc}', file=sys.stderr)
        return 1
    for slug, day in pairs:
        print(f'{slug} {day}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
