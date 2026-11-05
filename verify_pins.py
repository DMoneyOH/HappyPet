#!/usr/bin/env python3
"""
verify_pins.py - did the pins Stage 3 fired actually reach their Pinterest boards?

post_pins.py counts a pin as fired when IFTTT's Maker webhook answers 2xx. That
means IFTTT accepted the event, not that Pinterest made the pin. On 2026-11-02
the "Best Cat Products" applet failed ("The Pinterest connection was temporarily
unavailable"), Stage 3 passed, and the pin was missing.

This script reads each board's public RSS feed some minutes after the fire and
reports a pin that is not there. pin-verify.yml runs it after pin.yml finishes.

READ-ONLY. It fires no webhook and changes no sentinel, ledger or queue file.
Its one output is a report in --out-dir, written only when something needs an
issue; pin-verify.yml turns that into a GitHub issue.

What it checks, and how:
  * Which pins to expect: the per-board sentinels post_pins.py writes
    (_pin_queue/.fired/<slug>.<event>.fired). Each holds the UTC time its webhook
    answered. A sentinel at or after --since (the Stage 3 run's start) is one
    pin to confirm. A board whose webhook failed has no sentinel, so it is not
    checked here: pin.yml's own alert covers it.
  * Where to look: BOARD_SLUG, the one place that maps an IFTTT event to a board.
    Feed: https://www.pinterest.com/<profile>/<board-slug>.rss
  * How a pin is recognised: the feed item's <title> holds the pin's
    DESCRIPTION text (not its title), <link> is the pin's own page, and <pubDate>
    is when Pinterest made it. So a pin counts as present only if an item's
    title matches the description post_pins.py sent AND its pubDate is at or
    after the fire time (minus SKEW). The date test is what stops an older pin
    with the same text from passing.
  * Matching text (same_pin_text): both sides are HTML-unescaped (repeatedly, for
    double escaping), stripped of tags, NFKC-normalised, case-folded, and
    reduced to words (punctuation, quotes and dashes dropped). The first
    COMPARE_MAX characters of the shorter side are then compared; they must be
    equal or at least RATIO similar. That absorbs truncation, escaping, curly
    quotes, an added ellipsis and a light rewording, while another post's text
    still fails.
  * Real feeds (captured 2026-11-05): item ORDER varies between reads, so every
    item is scanned and order is never assumed. Each feed carries one ghost item
    (empty title, `<pubDate>Invalid Date</pubDate>`); an item with no readable
    date is skipped, and never counts as a match or as "the feed has no dates".
    Feeds hold about 25 items; only pins fired in this run are expected, so an old
    pin dropping off the end changes nothing.
  * Three answers per pin, never a pass by default:
      found       a dated item matches.
      missing     the feed read fine, has dated items, and none matches.
      unchecked   the feed could not be read, was empty, or had no dates; or the
                  description is too short to identify a pin. A blocked or empty
                  feed is "could not check", never "missing" and never a pass.
    Anything not found is read once more after --retry-wait-minutes before it is
    reported, so feed lag does not raise an alarm.
  * Exit 0: every pin found (or none to check). 1: a pin is missing. 2: nothing
    is missing but a pin could not be checked (or the check itself crashed).

Run: python3 verify_pins.py --since 2026-11-05T12:00:00Z
Tests: test_verify_pins.py (no network in any of them).
"""

import argparse
import datetime as dt
import difflib
import html
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import namedtuple
from email.utils import parsedate_to_datetime
from pathlib import Path

REPO_DIR = Path(__file__).parent.resolve()
UTC = dt.timezone.utc

PROFILE = "happypetproductreviews"
FEED_URL = "https://www.pinterest.com/{profile}/{board}.rss"
MAX_FEED_BYTES = 2_000_000
FEED_TIMEOUT = 20

# IFTTT event (post_pins.JSON_EVENT's keys, the board identity) -> the board's slug
# in its Pinterest URL. The ONE place this mapping lives; test_verify_pins.py checks
# it covers every event post_pins.py can fire. Hand-checked 2026-11-05.
BOARD_SLUG = {
    "happypet_pin_cats":   "best-cat-products",
    "happypet_pin_dogs":   "best-dog-products",
    "happypet_pin_food":   "pet-food-treats-reviews",
    "happypet_pin_health": "pet-health-wellness",
    "happypet_pin_home":   "pet-home-lifestyle",
    "happypet_pin_toys":   "pet-toys-accessories",
}

# Pinterest's pin time against the runner's clock: a pin may be stamped a little
# before the sentinel's time, so an item that much earlier still counts.
SKEW = dt.timedelta(seconds=120)
MIN_MATCH = 20      # fewer characters than this identify no pin
COMPARE_MAX = 160
RATIO = 0.9

_SENTINEL = re.compile(r"^(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\.(?P<event>happypet_pin_[a-z0-9_]+)\.fired$")
_SINCE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")

Item = namedtuple("Item", "title pub link")


class FeedError(RuntimeError):
    """A feed read or parse failed. The message names the cause, never a URL."""


# --------------------------------------------------------------------------
# Text matching
# --------------------------------------------------------------------------
def norm(text):
    text = str(text or "")
    for _ in range(3):                      # &amp;amp; and the like
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = re.sub(r"<[^>]*>", " ", text)
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\W_]+", " ", text).strip()


def same_pin_text(sent, seen):
    a, b = norm(sent), norm(seen)
    k = min(len(a), len(b), COMPARE_MAX)
    if k < MIN_MATCH:
        return False
    a, b = a[:k], b[:k]
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= RATIO


# --------------------------------------------------------------------------
# Feeds
# --------------------------------------------------------------------------
def feed_url(board):
    return FEED_URL.format(profile=PROFILE, board=board)


def _pub(text):
    try:
        when = parsedate_to_datetime((text or "").strip())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None   # Pinterest's own "Invalid Date", or any other junk
    if when is None:
        return None
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


def parse_feed(data):
    """The feed's items, newest order not assumed. Raises FeedError for anything
    that is not an RSS document. A feed with no items is an empty list."""
    if re.search(rb"<!(?:doctype|entity)", data, re.I):
        raise FeedError("feed declares a DOCTYPE or entity and was refused")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise FeedError("response is not valid XML (a login or block page?)") from None
    if root.tag != "rss":
        raise FeedError("response is XML but not an RSS feed")
    return [Item((el.findtext("title") or "").strip(), _pub(el.findtext("pubDate")),
                 (el.findtext("link") or "").strip())
            for el in root.iter("item")]


def fetch_feed(board):
    """Read and parse one board's feed. Any failure is a FeedError naming the cause."""
    req = urllib.request.Request(feed_url(board), headers={
        "User-Agent": "HappyPetPinCheck/1.0 (+https://happypetproductreviews.com)",
        "Accept": "application/rss+xml, application/xml;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=FEED_TIMEOUT) as resp:
            data = resp.read(MAX_FEED_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise FeedError(f"HTTP {exc.code}") from None
    except Exception as exc:
        raise FeedError(type(exc).__name__) from None
    if len(data) > MAX_FEED_BYTES:
        raise FeedError("feed is larger than 2 MB")
    return parse_feed(data)


# --------------------------------------------------------------------------
# Which pins to expect
# --------------------------------------------------------------------------
def find_fired_pins(fired_dir, since):
    """One dict per board sentinel fired at or after `since`. Each sentinel holds
    the UTC time its webhook answered."""
    fired_dir = Path(fired_dir)
    pins = []
    if not fired_dir.is_dir():
        return pins
    for path in sorted(fired_dir.iterdir()):
        m = _SENTINEL.match(path.name)
        if not m:
            continue
        try:
            when = dt.datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            print(f"WARN: unreadable time in {path.name}, skipped")
            continue
        when = when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)
        if when >= since:
            pins.append({"slug": m["slug"], "event": m["event"], "board": BOARD_SLUG.get(m["event"]),
                         "fired_at": when, "description": None})
    return pins


def _queue_description(queue, slug):
    """The description in the slug's queue file (top folder, else sent/), or None."""
    import json
    for path in (queue / f"{slug}.json", queue / "sent" / f"{slug}.json"):
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("description")
        except (OSError, ValueError, AttributeError):
            continue
    return None


def repo_describer(repo_dir):
    """slug -> the description post_pins.py sent for it (the post's front-matter
    description, else the queue file's, capped as post_pins caps it), or None.
    Imported late: post_pins and generate_pin_images do work at import."""
    import post_pins
    captions = post_pins.load_pin_captions()
    queue = Path(repo_dir) / "_pin_queue"

    def describe(slug):
        caption = captions.get(slug)
        if caption is None:
            caption = _queue_description(queue, slug)
        return post_pins.cap_text(caption, post_pins.DESC_MAX) if caption else None
    return describe


# --------------------------------------------------------------------------
# Judging
# --------------------------------------------------------------------------
def evaluate_pin(pin, items):
    """(status, detail) for one pin against its board's feed items."""
    if pin["board"] is None:
        return "unchecked", f"no board is mapped for the event {pin['event']}"
    if len(norm(pin["description"])) < MIN_MATCH:
        return "unchecked", "the description sent for this pin is too short to identify it in a feed"
    if not items:
        return "unchecked", "the board's feed was empty"
    dated = [i for i in items if i.pub]
    if not dated:
        return "unchecked", "the board's feed items carry no readable dates"
    floor = pin["fired_at"] - SKEW
    for item in dated:
        if item.pub >= floor and same_pin_text(pin["description"], item.title):
            return "found", ""
    n, skipped = len(dated), len(items) - len(dated)
    newest = max(i.pub for i in dated)
    return "missing", (f"the feed ({n} dated item{'s' if n != 1 else ''}"
                       f"{f', {skipped} undated skipped' if skipped else ''}, newest {newest:%Y-%m-%d %H:%M} UTC) "
                       f"has no pin with that description posted after the fire")


def _permanent(pin):
    return pin["board"] is None or len(norm(pin["description"])) < MIN_MATCH


def _round(pins, read_board):
    feeds = {}
    results = []
    for pin in pins:
        board = pin["board"]
        if board is not None and board not in feeds:
            try:
                feeds[board] = read_board(board)
            except FeedError as exc:
                feeds[board] = exc
        feed = feeds.get(board)
        if isinstance(feed, FeedError):
            results.append({"pin": pin, "status": "unchecked", "detail": f"the feed could not be read ({feed})"})
        else:
            status, detail = evaluate_pin(pin, feed)
            results.append({"pin": pin, "status": status, "detail": detail})
    return results


def run_check(pins, read_board, sleep=time.sleep, retry_wait_seconds=900):
    """Judge every pin; read the ones not found once more after a further wait.
    `read_board(board)` returns parsed items or raises FeedError; any other
    exception propagates to the caller."""
    results = _round(pins, read_board)
    again = [r["pin"] for r in results if r["status"] != "found" and not _permanent(r["pin"])]
    if again and retry_wait_seconds > 0:
        sleep(retry_wait_seconds)
        second = {id(r["pin"]): r for r in _round(again, read_board)}
        results = [second.get(id(r["pin"]), r) for r in results]
    return results


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def _line(result):
    p = result["pin"]
    board = p["board"] or "(no board)"
    return (f"- `{p['slug']}` on board `{board}` (`{p['event']}`), fired {p['fired_at']:%Y-%m-%d %H:%M} UTC: "
            f"{result['detail']}" + (f" https://www.pinterest.com/{PROFILE}/{board}/" if p["board"] else ""))


def _sentinels(results):
    names = []
    for r in results:
        p = r["pin"]
        for name in (f"{p['slug']}.{p['event']}.fired", f"{p['slug']}.fired"):
            if f"`{name}`" not in names:
                names.append(f"`{name}`")
    return ", ".join(names)


def build_report(results):
    """None when every pin is found; else {'kind', 'slugs', 'body'}. The body is
    the issue text up to the footer pin-verify.yml adds."""
    missing = [r for r in results if r["status"] == "missing"]
    unchecked = [r for r in results if r["status"] == "unchecked"]
    if not missing and not unchecked:
        return None
    kind = "missing" if missing else "unchecked"
    slugs = ",".join(sorted({r["pin"]["slug"] for r in (missing or unchecked)}))
    lines = []
    if missing:
        lines += [f"The pin check read Pinterest's public board feeds after Stage 3 fired and found "
                  f"{len(missing)} pin(s) not on their boards. IFTTT accepted each webhook, so "
                  f"post_pins.py counted them as fired.", ""]
        lines += [_line(r) for r in missing]
        if unchecked:
            lines += ["", "Could not check:"] + [_line(r) for r in unchecked]
        lines += ["",
                  "Look at the board first: the check reads a public feed and can be wrong "
                  "(feed lag, text Pinterest changed).", "",
                  "This will not re-fire by itself. The ledger (`_pin_queue/.fired/.pinned.json`) and the "
                  "sentinels in `_pin_queue/.fired/` (" + _sentinels(missing) + ") all say the "
                  "pin went out, so pin.yml skips it, and a rerun with the same image is refused (exit 6). "
                  "To repost:",
                  "1. Check the IFTTT applet's activity for the failure.",
                  "2. In `_pin_queue/.fired/.pinned.json`, delete the board's entry under that slug's image. "
                  "If that leaves the image with no boards, delete the image key; then the slug key if it has "
                  "no images.",
                  "3. If the slug's queue file is in `_pin_queue/sent/`, move it back to `_pin_queue/` "
                  "(post_pins.py reads only that folder).",
                  "4. Commit, then run pin.yml with slugs set to the slug and force set to true. Boards "
                  "still in the ledger are refused, so only the board you removed fires.",
                  "5. Close this issue."]
    else:
        lines += [f"The pin check ran after Stage 3 fired but could not check {len(unchecked)} pin(s), so "
                  f"none of them is confirmed or ruled out. This is not a confirmed miss.", ""]
        lines += [_line(r) for r in unchecked]
        lines += ["",
                  "Open each board above and look for the pin. If Pinterest blocks the runner on every "
                  "run, this check protects nothing until the feed read has another route. Close this "
                  "issue once you have looked."]
    return {"kind": kind, "slugs": slugs, "body": "\n".join(lines)}


def write_report(out_dir, report):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "kind").write_text(report["kind"], encoding="utf-8")
    (out / "slugs").write_text(report["slugs"], encoding="utf-8")
    (out / "body.md").write_text(report["body"], encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _minutes(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("not a whole number") from None
    if not 0 <= n <= 30:
        raise argparse.ArgumentTypeError("must be 0 to 30")
    return n


def _since(value):
    if value == "":
        return None
    if not _SINCE.match(value):
        raise argparse.ArgumentTypeError("not an ISO UTC time like 2026-11-05T12:00:00Z")
    when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


def _parser():
    p = argparse.ArgumentParser(description="Confirm fired pins are on their Pinterest boards (read-only).")
    p.add_argument("--since", type=_since, default=None,
                   help="UTC start of the Stage 3 run; blank or absent = the last 3 hours")
    p.add_argument("--wait-minutes", type=_minutes, default=15,
                   help="wait this long after the fire before the first read (default 15)")
    p.add_argument("--retry-wait-minutes", type=_minutes, default=15,
                   help="wait this long before reading a pin that was not found again (default 15)")
    p.add_argument("--repo-dir", default=str(REPO_DIR))
    p.add_argument("--out-dir", default="pin-verify")
    return p


def _run(args, read_board, describe_factory, sleep, state):
    since = args.since or (dt.datetime.now(UTC) - dt.timedelta(hours=3))
    pins = find_fired_pins(Path(args.repo_dir) / "_pin_queue" / ".fired", since)
    state["pins"] = pins
    if not pins:
        print(f"No board sentinel at or after {since:%Y-%m-%d %H:%M:%S} UTC: nothing fired, nothing to check")
        return None
    print(f"{len(pins)} pin(s) to confirm: " + ", ".join(f"{p['slug']}@{p['board'] or p['event']}" for p in pins))
    describe = describe_factory(args.repo_dir)
    for p in pins:
        p["description"] = describe(p["slug"]) if describe else None
    if args.wait_minutes:
        sleep(args.wait_minutes * 60)
    results = run_check(pins, read_board, sleep=sleep, retry_wait_seconds=args.retry_wait_minutes * 60)
    for r in results:
        p = r["pin"]
        print(f"{r['status'].upper():9} {p['slug']} on {p['board'] or p['event']}"
              + (f" -- {r['detail']}" if r["detail"] else ""))
    return build_report(results)


def main(argv=None, *, read_board=None, describe=None, sleep=time.sleep):
    args = _parser().parse_args(argv)
    state = {"pins": []}
    try:
        report = _run(args, read_board or fetch_feed, describe or repo_describer, sleep, state)
    except Exception as exc:
        # The check must never read as a pass because it broke.
        slugs = ",".join(sorted({p["slug"] for p in state["pins"]}))
        report = {"kind": "unchecked", "slugs": slugs,
                  "body": f"The pin check stopped with an error ({type(exc).__name__}), so it could not "
                          f"check these pins. This is not a confirmed miss. The run log has the trace.\n"
                          f"Open the boards by hand: https://www.pinterest.com/{PROFILE}/"}
        import traceback
        traceback.print_exc()
    if report is None:
        return 0
    write_report(args.out_dir, report)
    return 1 if report["kind"] == "missing" else 2


if __name__ == "__main__":
    sys.exit(main())
