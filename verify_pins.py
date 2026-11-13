#!/usr/bin/env python3
"""
verify_pins.py - did the pins Stage 3 fired actually reach their Pinterest boards?

post_pins.py counts a pin as fired when IFTTT's Maker webhook answers 2xx. That
means IFTTT accepted the event, not that Pinterest made the pin. On 2026-11-02
the "Best Cat Products" applet failed ("The Pinterest connection was temporarily
unavailable"), Stage 3 passed, and the pin was missing.

This script reads each board's public RSS feed and reports a pin that is not
there. pin-verify.yml runs it on an hourly cron schedule. It is a sweep over
whatever fired recently, not a step that follows one Stage 3 run, so no trigger
has to "fire after" another workflow, and it looks at pins whatever way Stage 3
ended, provided the sentinels reached main (see LIMITS).

READ-ONLY. It fires no webhook and changes no sentinel, ledger or queue file.
Its one output is a report in --out-dir, written only when something needs an
issue; pin-verify.yml turns that into a GitHub issue.

What it checks, and how:
  * Which pins to expect: the per-board sentinels post_pins.py writes
    (_pin_queue/.fired/<slug>.<event>.fired). Each holds the UTC time its webhook
    answered. A sentinel fired within the window (default the last 24 hours, or
    from --since, never more than 24 hours back) is one pin to confirm. A pin
    younger than --settle-minutes (default 30) is not due yet and is left to the
    next sweep, which gives Pinterest time to catch up. A sweep therefore examines
    a pin while its age is 30 minutes to 24 hours. The cron asks for hourly runs,
    but GitHub ran them every 7 to 9 hours (November 2026), which a 210-minute
    window did not bridge. Any gap up to 23.5 hours between actual sweeps is
    bridged; a longer one can leave a pin unchecked. The cost: a pin is read
    again by every sweep for a day, and a closed but unfixed miss reopens for
    that long.
    A sentinel whose time cannot be read, or is more than an hour in the future,
    is "could not check", never skipped.
  * LIMITS, stated plainly. A board whose webhook failed has no sentinel, so it is
    not checked here: pin.yml's own alert covers it. A fire whose sentinels never
    reached main (the runner died after firing and before pin.yml's commit) is
    invisible to this check, which reads main. pin.yml now alerts on a cancelled or
    timed-out run, with or without sentinels, but that alert is not this check. The
    only other net is publish.yml's stale `.pending-slugs` issue: it opens at the
    first publish run more than 24 hours later (publish runs Mon and Thu, so up to
    about 4 days), and a pin.yml run started by hand with typed slugs has no
    `.pending-slugs` file, so no net at all. slot-watchdog.yml opens an issue when
    no run of pin-verify.yml was created in the 12 hours before a Mon/Thu slot
    check. It shares GitHub's scheduler with this check, so it cannot catch a total
    scheduler outage, and it looks only twice a week.
  * ACCEPTED, not fixed (each makes noise or loses a report, none hides a miss
    except where said): a duplicate issue if the Director closes one between
    sweeps, or if sweeps straddle midnight (the title carries the Eastern date);
    a false "missing" or "could not check" from a blocked or stale second read,
    from a stale feed, or from a post whose description was edited after the fire;
    a pin that appears in the feed with a blank title reads as missing; a cancel
    during the retry wait loses its report, so that run opens only the generic
    "could not check" issue.
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
    Feeds hold about 25 items; only pins fired in the window are expected, so an
    old pin dropping off the end changes nothing.
  * Three answers per pin, never a pass by default:
      found       a dated item matches.
      missing     the feed read fine, has dated items, and none matches.
      unchecked   the feed could not be read, was empty, had no dates, or none of
                  its dated items carries text long enough to match; or the
                  description is too short to identify a pin; or the sentinel's
                  time is unreadable or in the future.
                  A blocked or empty feed is "could not check", never "missing"
                  and never a pass.
    Anything not found is read once more (always; after --retry-wait-minutes when
    that is above 0) before it is reported, so feed lag does not raise an alarm.
    One board failing in an unexpected way is "could not check" for that board
    only; it never downgrades a miss found on another board.
  * Exit 0: every pin found (or none to check). 1: a pin is missing. 2: nothing
    is missing but a pin could not be checked (or the check itself crashed).

Run: python3 verify_pins.py [--since 2026-11-05T12:00:00Z] [--settle-minutes 30]
Tests: test_verify_pins.py (no network in any of them).
A GitHub runner read all six board feeds on 2026-11-05 (18 of 18 requests, HTTP 200,
no login), in a temporary test job that has since been deleted.
"""

import argparse
import datetime as dt
import difflib
import hashlib
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
USER_AGENT = "Mozilla/5.0"
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

DEFAULT_WINDOW = dt.timedelta(hours=24)      # bridges a 23.5 h gap; GitHub runs the hourly cron every 7-9 h
FUTURE_TOLERANCE = dt.timedelta(hours=1)  # a sentinel time further ahead than this is not a real fire time
MAX_LOOKBACK = dt.timedelta(hours=24)    # an older --since risks a post edited after its fire
MAX_WAIT_MINUTES = 30                    # the longest --settle/--retry-wait the CLI accepts
DEFAULT_SETTLE_MINUTES = 30              # the one hand-timed feed lag was under 3 minutes
DEFAULT_RETRY_WAIT_MINUTES = 15
TITLE_SLUGS_MAX = 3                      # GitHub caps a title at 256 characters

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
    # The one header a GitHub runner was measured to read all six feeds with (18 of 18,
    # 2026-11-05). Nothing else is sent: no Accept, since the test did not use one.
    req = urllib.request.Request(feed_url(board), headers={"User-Agent": USER_AGENT})
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
def _fire_time(path):
    """The UTC time a sentinel holds, or None when it cannot be read."""
    try:
        when = dt.datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


def find_fired_pins(fired_dir, since, now=None):
    """One dict per board sentinel fired at or after `since`. Each sentinel holds
    the UTC time its webhook answered. A sentinel whose time cannot be read, or
    (given `now`) lies more than FUTURE_TOLERANCE ahead of it, is returned with
    fired_at None, so it is reported as "could not check" and is never silently
    dropped or treated as always recent."""
    fired_dir = Path(fired_dir)
    pins = []
    if not fired_dir.is_dir():
        return pins
    for path in sorted(fired_dir.iterdir()):
        m = _SENTINEL.match(path.name)
        if not m:
            continue
        when = _fire_time(path)
        if when is not None and now is not None and when > now + FUTURE_TOLERANCE:
            when = None
        if when is None:
            print(f"WARN: unreadable or future fire time in {path.name}")
        if when is None or when >= since:
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
    if pin["fired_at"] is None:
        return "unchecked", "the sentinel's fire time could not be read"
    if len(norm(pin["description"])) < MIN_MATCH:
        return "unchecked", "the description sent for this pin is too short to identify it in a feed"
    if not items:
        return "unchecked", "the board's feed was empty"
    dated = [i for i in items if i.pub]
    if not dated:
        return "unchecked", "the board's feed items carry no readable dates"
    if not any(len(norm(i.title)) >= MIN_MATCH for i in dated):
        return "unchecked", "none of the board's dated feed items carries text long enough to match"
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
    return (pin["board"] is None or pin["fired_at"] is None
            or len(norm(pin["description"])) < MIN_MATCH)


def _round(pins, read_board):
    feeds = {}
    results = []
    for pin in pins:
        if _permanent(pin):   # cannot be judged whatever the feed says: no point reading it
            status, detail = evaluate_pin(pin, [])
            results.append({"pin": pin, "status": status, "detail": detail})
            continue
        board = pin["board"]
        if board not in feeds:
            try:
                feeds[board] = read_board(board)
            except FeedError as exc:
                feeds[board] = exc
            except Exception as exc:   # one board's surprise must not hide a miss on another
                feeds[board] = FeedError(f"unexpected {type(exc).__name__}")
        feed = feeds.get(board)
        if isinstance(feed, FeedError):
            results.append({"pin": pin, "status": "unchecked", "detail": f"the feed could not be read ({feed})"})
        else:
            status, detail = evaluate_pin(pin, feed)
            results.append({"pin": pin, "status": status, "detail": detail})
    return results


def run_check(pins, read_board, sleep=time.sleep, retry_wait_seconds=900):
    """Judge every pin; read the ones not found once more, after a further wait
    when `retry_wait_seconds` is above 0 (a wait of 0 still re-reads).
    `read_board(board)` returns parsed items or raises; whatever it raises is
    that board's "could not check", never a crash of the whole check."""
    results = _round(pins, read_board)
    again = [r["pin"] for r in results if r["status"] != "found" and not _permanent(r["pin"])]
    if again:
        if retry_wait_seconds > 0:
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
    fired = f"fired {p['fired_at']:%Y-%m-%d %H:%M} UTC" if p["fired_at"] else "fire time unreadable"
    return (f"- `{p['slug']}` on board `{board}` (`{p['event']}`), {fired}: "
            f"{result['detail']}" + (f" https://www.pinterest.com/{PROFILE}/{board}/" if p["board"] else ""))


def _sentinels(results):
    names = []
    for r in results:
        p = r["pin"]
        for name in (f"{p['slug']}.{p['event']}.fired", f"{p['slug']}.fired"):
            if f"`{name}`" not in names:
                names.append(f"`{name}`")
    return ", ".join(names)


def title_slugs(pins):
    """The tail of an issue title for these pins: the first few slugs, a count of
    the rest, and a short hash of the exact (slug, board) set. The cap keeps a big
    batch under GitHub's 256 characters (the body lists every pin). The hash keeps
    the dedupe by title from hiding a different miss behind an open issue about
    another one: the same set always gives the same title, a different set does not."""
    pins = list(pins)
    slugs = sorted({p["slug"] for p in pins})
    key = ";".join(sorted({f"{p['slug']}|{p['event']}" for p in pins}))
    short = hashlib.sha1(key.encode("utf-8")).hexdigest()[:6]
    shown = ",".join(slugs[:TITLE_SLUGS_MAX])
    more = f" (+{len(slugs) - TITLE_SLUGS_MAX} more)" if len(slugs) > TITLE_SLUGS_MAX else ""
    return f"{shown}{more} #{short}"


def _slug_files(missing, where):
    slugs = sorted({r["pin"]["slug"] for r in missing})
    return " and ".join(f"`_pin_queue/{where}{s}.json`" for s in slugs)


def _repost_steps(missing):
    """How to put a missing pin back. Each claim was checked by driving
    post_pins.py's own main(): see TestRecoverySteps in test_verify_pins.py."""
    s = "s" if len({r["pin"]["slug"] for r in missing}) > 1 else ""
    return [
        "This will not re-fire by itself. The ledger (`_pin_queue/.fired/.pinned.json`) and the "
        "sentinels in `_pin_queue/.fired/` (" + _sentinels(missing) + ") all say the pin went out, so "
        "pin.yml skips it. To repost:",
        "1. Check the IFTTT applet's activity for the failure.",
        "2. In `_pin_queue/.fired/.pinned.json`, delete the missing board's entry under the slug's image. "
        "If that leaves the image with no boards, delete the image key; then the slug key if it has no images.",
        f"3. If the queue file is in `_pin_queue/sent/` ({_slug_files(missing, 'sent/')}), move it back to "
        "`_pin_queue/`. Left there, a rerun looks green (\"nothing to do\") while nothing fires. With the "
        "file back but the ledger entry still there, post_pins.py refuses the repeat (exit 6).",
        f"4. Commit the change{s} and push to `main`. pin.yml runs from `main`, so a change that is not "
        "there changes nothing.",
        f"5. Run pin.yml with slugs set to the slug{s} and force set to true. Boards still in the "
        "ledger are refused. Boards missing from it fire: the one you removed, and any that never fired "
        "in a partial fire.",
        "6. The next sweep checks the repost. Close this issue.",
    ]


def _repair_note(unchecked):
    """A sentinel with an unreadable or future time never goes away by itself."""
    bad = [r["pin"] for r in unchecked if r["pin"]["fired_at"] is None]
    if not bad:
        return []
    names = ", ".join(f"`_pin_queue/.fired/{p['slug']}.{p['event']}.fired`" for p in bad)
    return ["", f"Repair {names}: its time is unreadable or in the future. It stays in the repo and every "
                f"sweep reads it again, so this issue reopens until it holds a real UTC time or is deleted."]


def build_report(results):
    """None when every pin is found; else {'kind', 'slugs', 'body'}. `slugs` is
    the capped title list. The body is the issue text up to the footer
    pin-verify.yml adds."""
    missing = [r for r in results if r["status"] == "missing"]
    unchecked = [r for r in results if r["status"] == "unchecked"]
    if not missing and not unchecked:
        return None
    kind = "missing" if missing else "unchecked"
    slugs = title_slugs(r["pin"] for r in (missing or unchecked))
    lines = []
    if missing:
        lines += [f"The pin check read Pinterest's public board feeds and found "
                  f"{len(missing)} pin(s) not on their boards. IFTTT accepted each webhook, so "
                  f"post_pins.py counted them as fired.", ""]
        lines += [_line(r) for r in missing]
        if unchecked:
            lines += ["", "Could not check:"] + [_line(r) for r in unchecked] + _repair_note(unchecked)
        lines += ["",
                  "Look at the board first: the check reads a public feed and can be wrong "
                  "(feed lag, text Pinterest changed, or the post's description edited after the fire).", ""]
        lines += _repost_steps(missing)
    else:
        lines += [f"The pin check could not check {len(unchecked)} pin(s), so "
                  f"none of them is confirmed or ruled out. This is not a confirmed miss.", ""]
        lines += [_line(r) for r in unchecked] + _repair_note(unchecked)
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
    if not 0 <= n <= MAX_WAIT_MINUTES:
        raise argparse.ArgumentTypeError(f"must be 0 to {MAX_WAIT_MINUTES}")
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
                   help="UTC time to look from, at most 24 hours back; blank or absent = the last 24 hours")
    p.add_argument("--settle-minutes", type=_minutes, default=DEFAULT_SETTLE_MINUTES,
                   help="a pin fired more recently than this is not due yet and is left to the next "
                        f"sweep (default {DEFAULT_SETTLE_MINUTES})")
    p.add_argument("--retry-wait-minutes", type=_minutes, default=DEFAULT_RETRY_WAIT_MINUTES,
                   help="wait this long before reading a pin that was not found again; 0 re-reads at once "
                        f"(default {DEFAULT_RETRY_WAIT_MINUTES})")
    p.add_argument("--repo-dir", default=str(REPO_DIR))
    p.add_argument("--out-dir", default="LOGS/pin-verify")
    return p


def _due(pins, now, settle):
    """(due, not_due): a pin younger than `settle` waits for the next sweep. A pin
    with an unreadable time is always due, so it cannot hide."""
    due = [p for p in pins if p["fired_at"] is None or now - p["fired_at"] >= settle]
    return due, [p for p in pins if p not in due]


def _run(args, read_board, describe_factory, sleep, state, now):
    since = args.since or (now - DEFAULT_WINDOW)
    pins = find_fired_pins(Path(args.repo_dir) / "_pin_queue" / ".fired", since, now)
    due, waiting = _due(pins, now, dt.timedelta(minutes=args.settle_minutes))
    state["pins"] = due
    for p in waiting:
        print(f"NOT DUE   {p['slug']} on {p['board'] or p['event']}: fired {p['fired_at']:%H:%M:%S} UTC, "
              f"younger than {args.settle_minutes} min, left to the next sweep")
    if not due:
        print(f"No pin due for a check since {since:%Y-%m-%d %H:%M:%S} UTC: nothing to do")
        return None
    print(f"{len(due)} pin(s) to confirm: " + ", ".join(f"{p['slug']}@{p['board'] or p['event']}" for p in due))
    describe = describe_factory(args.repo_dir)
    for p in due:
        p["description"] = describe(p["slug"]) if describe else None
    results = run_check(due, read_board, sleep=sleep, retry_wait_seconds=args.retry_wait_minutes * 60)
    for r in results:
        p = r["pin"]
        print(f"{r['status'].upper():9} {p['slug']} on {p['board'] or p['event']}"
              + (f" -- {r['detail']}" if r["detail"] else ""))
    return build_report(results)


def main(argv=None, *, read_board=None, describe=None, sleep=time.sleep, now=None):
    parser = _parser()
    args = parser.parse_args(argv)
    now = now or dt.datetime.now(UTC)
    if args.since and args.since < now - MAX_LOOKBACK:
        parser.error(f"--since is more than {MAX_LOOKBACK.days * 24} hours back: a post edited after its "
                     f"fire would read as a missing pin")
    state = {"pins": []}
    try:
        report = _run(args, read_board or fetch_feed, describe or repo_describer, sleep, state, now)
    except Exception as exc:
        # The check must never read as a pass because it broke.
        slugs = title_slugs(state["pins"])
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
