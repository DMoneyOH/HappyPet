#!/usr/bin/env python3
"""
test_verify_pins.py -- verify_pins.py and .github/workflows/pin-verify.yml.

post_pins.py counts a pin as fired when IFTTT's Maker webhook answers 2xx. That
says IFTTT accepted the event, not that Pinterest made the pin: on 2026-11-02
the "Best Cat Products" applet failed ("The Pinterest connection was
temporarily unavailable"), Stage 3 still passed, and the pin was missing.
verify_pins.py reads each board's public RSS feed some minutes after the fire
and reports a pin that is not there.

Kept out of test_pipeline.py on purpose (several branches edit it at once); it
borrows that file's alert-issue harness so the new alert step is held to the
same rules as every other one. No test reaches a network: urlopen is patched
to raise for the whole module, and every feed is a literal built here.

The feed shape in these fixtures (an RSS 2.0 item whose <title> is the pin's
description and whose <pubDate> is the pin's creation time) comes from a hand
check of the live feeds on 2026-11-05, not from a captured sample.

Run: python3 -m pytest test_verify_pins.py -q
"""

import datetime as dt
import email.utils
import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

import post_pins  # noqa: E402
import verify_pins as vp  # noqa: E402
from test_pipeline import (WORKFLOWS, _AlertIssueStep, _named_step,  # noqa: E402
                           _workflow_permissions)

UTC = dt.timezone.utc
FIRED = dt.datetime(2026, 11, 2, 12, 29, 20, tzinfo=UTC)
CATS, FOOD = "best-cat-products", "pet-food-treats-reviews"
CAT_DESC = ("Picky cats finished every lick of these puree treats, and the tube "
            "keeps the mess off the counter. Our tested pick for the treat jar.")
OTHER_DESC = ("A walk-in crate that folds flat for the car and still holds a "
              "heavy chewer. Here is how it held up over three weeks of use.")
GUARD = "no test may reach the network"


def rss(items):
    """items: (title, datetime|None). The XML is escaped once, as a feed is."""
    body = ""
    for title, when in items:
        date = (f"<pubDate>{email.utils.format_datetime(when, usegmt=True)}</pubDate>"
                if when else "")
        body += (f"<item><title>{escape(title)}</title>"
                 f"<link>https://www.pinterest.com/pin/1/</link>{date}</item>")
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            f"<title>Board</title>{body}</channel></rss>").encode()


def feed_of(*items):
    return vp.parse_feed(rss(items))


def pin(slug="best-lickable-cat-treats", event="happypet_pin_cats", desc=CAT_DESC, fired=FIRED):
    return {"slug": slug, "event": event, "board": vp.BOARD_SLUG.get(event),
            "fired_at": fired, "description": desc}


class _NoNetwork(unittest.TestCase):
    def setUp(self):
        patcher = patch("urllib.request.urlopen", side_effect=AssertionError(GUARD))
        patcher.start()
        self.addCleanup(patcher.stop)


class TestBoardMap(unittest.TestCase):
    def test_every_event_post_pins_can_fire_has_a_board(self):
        self.assertEqual(sorted(vp.BOARD_SLUG), sorted(post_pins.JSON_EVENT))

    def test_the_boards_are_the_six_hand_checked_slugs(self):
        # Literals from the 2026-11-05 hand check, not computed from the map.
        self.assertEqual(vp.BOARD_SLUG, {
            "happypet_pin_cats": "best-cat-products",
            "happypet_pin_dogs": "best-dog-products",
            "happypet_pin_food": "pet-food-treats-reviews",
            "happypet_pin_health": "pet-health-wellness",
            "happypet_pin_home": "pet-home-lifestyle",
            "happypet_pin_toys": "pet-toys-accessories",
        })

    def test_the_feed_url(self):
        self.assertEqual(vp.feed_url("best-cat-products"),
                         "https://www.pinterest.com/happypetproductreviews/best-cat-products.rss")


class TestNormalizeAndMatch(unittest.TestCase):
    def test_identical_text_matches(self):
        self.assertTrue(vp.same_pin_text(CAT_DESC, CAT_DESC))

    def test_a_feed_that_truncates_still_matches(self):
        self.assertTrue(vp.same_pin_text(CAT_DESC, CAT_DESC[:60]))
        self.assertTrue(vp.same_pin_text(CAT_DESC, CAT_DESC[:60] + "..."))
        self.assertTrue(vp.same_pin_text(CAT_DESC, CAT_DESC[:60] + "…"))

    def test_escaping_tags_quotes_and_dashes_do_not_matter(self):
        sent = 'Dogs & cats: "fresh" food — it\'s the pick, 5 reasons why it wins.'
        for seen in ("Dogs &amp; cats: &quot;fresh&quot; food &#8212; it&#39;s the pick, 5 reasons why it wins.",
                     "Dogs &amp;amp; cats: &amp;quot;fresh&amp;quot; food - it&amp;#39;s the pick, 5 reasons why it wins.",
                     "<b>Dogs</b> &amp; cats: “fresh” food – it’s the pick, 5 reasons why it wins.",
                     "  DOGS & CATS:   fresh food it's the pick 5 reasons why it wins  "):
            with self.subTest(seen=seen):
                self.assertTrue(vp.same_pin_text(sent, seen))

    def test_markup_around_the_text_does_not_matter(self):
        wrapped = ('<a href="https://example.com/some/long/path?x=1"><span class="pin-text">'
                   + CAT_DESC + "</span></a>")
        self.assertTrue(vp.same_pin_text(CAT_DESC, wrapped))
        self.assertTrue(vp.same_pin_text(CAT_DESC, wrapped.replace("<", "&lt;").replace(">", "&gt;")))

    def test_a_light_rewording_matches(self):
        self.assertTrue(vp.same_pin_text(CAT_DESC, CAT_DESC.replace("Picky", "Fussy").replace("tube", "tubes")))

    def test_another_posts_description_does_not_match(self):
        self.assertFalse(vp.same_pin_text(CAT_DESC, OTHER_DESC))

    def test_a_shared_opening_alone_does_not_match(self):
        sent = "The best dog crates of the year, tested for weeks " + "a" * 80 + " first ending"
        seen = "The best dog crates of the year, tested for weeks " + "z" * 80 + " other ending"
        self.assertFalse(vp.same_pin_text(sent, seen))

    def test_text_too_short_to_identify_a_pin_never_matches(self):
        self.assertFalse(vp.same_pin_text("Great food", "Great food"))
        self.assertFalse(vp.same_pin_text("", ""))
        self.assertFalse(vp.same_pin_text(CAT_DESC, ""))


class TestParseFeed(unittest.TestCase):
    def test_items_carry_title_and_date(self):
        items = feed_of((CAT_DESC, FIRED), ("no date here", None))
        self.assertEqual([i.title for i in items], [CAT_DESC, "no date here"])
        self.assertEqual(items[0].pub, FIRED)
        self.assertIsNone(items[1].pub)

    def test_a_feed_with_no_items_is_an_empty_list_not_an_error(self):
        self.assertEqual(feed_of(), [])

    def test_a_feed_is_xml_or_an_error(self):
        for label, data in (("html login page", b"<html><body>Log in to Pinterest</body></html>"),
                            ("plain text", b"rate limited"),
                            ("empty", b""),
                            ("xml but not rss", b"<feed><entry/></feed>")):
            with self.subTest(case=label):
                with self.assertRaises(vp.FeedError):
                    vp.parse_feed(data)

    def test_entity_declarations_are_refused(self):
        bomb = (b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]>'
                b"<rss><channel><item><title>&a;</title></item></channel></rss>")
        with self.assertRaises(vp.FeedError):
            vp.parse_feed(bomb)

    def test_a_date_with_no_zone_is_read_as_utc_and_junk_is_none(self):
        data = (b"<rss><channel><item><title>a</title><pubDate>Mon, 02 Nov 2026 12:30:00</pubDate></item>"
                b"<item><title>b</title><pubDate>yesterday-ish</pubDate></item></channel></rss>")
        a, b = vp.parse_feed(data)
        self.assertEqual(a.pub, dt.datetime(2026, 11, 2, 12, 30, tzinfo=UTC))
        self.assertIsNone(b.pub)


class TestFetchFeed(unittest.TestCase):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_a_good_response_is_parsed(self):
        with patch("urllib.request.urlopen", return_value=self._Resp(rss([(CAT_DESC, FIRED)]))) as op:
            items = vp.fetch_feed(CATS)
        self.assertEqual(op.call_args[0][0].full_url, vp.feed_url(CATS))
        self.assertEqual(items[0].title, CAT_DESC)

    def test_a_blocked_or_broken_read_is_a_feed_error_naming_the_cause(self):
        cases = (("HTTP 403", urllib.error.HTTPError("u", 403, "Forbidden", {}, None)),
                 ("HTTP 429", urllib.error.HTTPError("u", 429, "Too Many", {}, None)),
                 ("URLError", urllib.error.URLError("dns")),
                 ("TimeoutError", TimeoutError()))
        for cause, exc in cases:
            with self.subTest(cause=cause), patch("urllib.request.urlopen", side_effect=exc):
                with self.assertRaises(vp.FeedError) as ctx:
                    vp.fetch_feed(CATS)
                self.assertIn(cause, str(ctx.exception))

    def test_an_oversized_response_is_refused(self):
        # Valid XML even when cut at the limit, so only the size check can refuse it.
        big = rss([(CAT_DESC, FIRED)]) + b" " * (vp.MAX_FEED_BYTES + 10)
        with patch("urllib.request.urlopen", return_value=self._Resp(big)):
            with self.assertRaises(vp.FeedError):
                vp.fetch_feed(CATS)


class TestEvaluatePin(unittest.TestCase):
    def test_a_matching_item_after_the_fire_is_found(self):
        status, _ = vp.evaluate_pin(pin(), feed_of((CAT_DESC, FIRED + dt.timedelta(minutes=3))))
        self.assertEqual(status, "found")

    def test_an_old_pin_with_the_same_description_is_not_a_pass(self):
        """A re-posted or earlier pin carries the same text; only the date tells."""
        old = feed_of((CAT_DESC, FIRED - dt.timedelta(days=7)), (OTHER_DESC, FIRED + dt.timedelta(minutes=2)))
        status, detail = vp.evaluate_pin(pin(), old)
        self.assertEqual(status, "missing")
        self.assertIn("no pin with that description", detail)

    def test_clock_skew_up_to_two_minutes_still_counts(self):
        for secs, want in ((-90, "found"), (-119, "found"), (-130, "missing"), (-600, "missing")):
            with self.subTest(offset=secs):
                status, _ = vp.evaluate_pin(pin(), feed_of((CAT_DESC, FIRED + dt.timedelta(seconds=secs))))
                self.assertEqual(status, want)

    def test_an_absent_pin_is_missing_when_the_feed_demonstrably_works(self):
        feed = feed_of((OTHER_DESC, dt.datetime(2026, 10, 26, 12, 0, tzinfo=UTC)))
        status, detail = vp.evaluate_pin(pin(), feed)
        self.assertEqual(status, "missing")
        self.assertIn("1 dated item", detail)

    def test_an_empty_feed_is_could_not_check_not_missing(self):
        status, detail = vp.evaluate_pin(pin(), feed_of())
        self.assertEqual(status, "unchecked")
        self.assertIn("empty", detail)

    def test_a_feed_with_no_readable_dates_is_could_not_check(self):
        status, _ = vp.evaluate_pin(pin(), feed_of((CAT_DESC, None)))
        self.assertEqual(status, "unchecked")

    def test_a_description_too_short_to_match_is_could_not_check(self):
        for desc in ("", None, "Great"):
            with self.subTest(desc=desc):
                status, _ = vp.evaluate_pin(pin(desc=desc), feed_of((CAT_DESC, FIRED)))
                self.assertEqual(status, "unchecked")

    def test_a_board_with_no_mapping_is_could_not_check(self):
        status, detail = vp.evaluate_pin(pin(event="happypet_pin_birds"), feed_of((CAT_DESC, FIRED)))
        self.assertEqual(status, "unchecked")
        self.assertIn("happypet_pin_birds", detail)


class _Feeds:
    """read_board stand-in: board -> list of per-call answers (bytes or a
    FeedError). The last answer repeats. Counts calls per board."""

    def __init__(self, **by_board):
        self.answers = {b.replace("_", "-"): v for b, v in by_board.items()}
        self.calls = {}

    def __call__(self, board):
        self.calls[board] = self.calls.get(board, 0) + 1
        seq = self.answers[board]
        ans = seq[min(self.calls[board], len(seq)) - 1]
        if isinstance(ans, Exception):
            raise ans
        return vp.parse_feed(ans)


class TestRunCheck(_NoNetwork):
    RETRY = 900

    def run_check(self, pins, feeds):
        slept = []
        results = vp.run_check(pins, feeds, sleep=slept.append, retry_wait_seconds=self.RETRY)
        return results, slept

    def statuses(self, results):
        return {(r["pin"]["slug"], r["pin"]["board"]): r["status"] for r in results}

    def food_ok(self, desc=OTHER_DESC):
        return rss([(desc, FIRED + dt.timedelta(minutes=3))])

    def test_a_healthy_run_is_silent(self):
        pins = [pin(), pin(event="happypet_pin_food")]
        feeds = _Feeds(**{CATS: [rss([(CAT_DESC, FIRED + dt.timedelta(minutes=2))])],
                          FOOD: [rss([(CAT_DESC, FIRED + dt.timedelta(minutes=3))])]})
        results, slept = self.run_check(pins, feeds)
        self.assertEqual({r["status"] for r in results}, {"found"})
        self.assertIsNone(vp.build_report(results))
        self.assertEqual(slept, [], "nothing missing, so no retry wait")
        self.assertEqual(feeds.calls, {CATS: 1, FOOD: 1})

    def test_2026_11_02_food_present_cat_absent_alerts_on_cat_only(self):
        pins = [pin(), pin(event="happypet_pin_food")]
        feeds = _Feeds(**{CATS: [rss([(OTHER_DESC, dt.datetime(2026, 10, 26, 12, 0, tzinfo=UTC))])],
                          FOOD: [rss([(CAT_DESC, FIRED + dt.timedelta(minutes=1))])]})
        results, _ = self.run_check(pins, feeds)
        self.assertEqual(self.statuses(results),
                         {("best-lickable-cat-treats", CATS): "missing",
                          ("best-lickable-cat-treats", FOOD): "found"})
        report = vp.build_report(results)
        self.assertEqual(report["kind"], "missing")
        self.assertEqual(report["slugs"], "best-lickable-cat-treats")
        self.assertIn(CATS, report["body"])
        self.assertNotIn(FOOD, report["body"])

    def test_a_miss_is_rechecked_once_after_a_further_wait(self):
        feeds = _Feeds(**{CATS: [rss([]), rss([(CAT_DESC, FIRED + dt.timedelta(minutes=17))])]})
        results, slept = self.run_check([pin()], feeds)
        self.assertEqual(slept, [self.RETRY])
        self.assertEqual(feeds.calls[CATS], 2)
        self.assertEqual(results[0]["status"], "found", "a pin that arrives late is not an alert")
        self.assertIsNone(vp.build_report(results))

    def test_a_pin_still_absent_after_the_retry_is_missing_and_checked_only_twice(self):
        feeds = _Feeds(**{CATS: [rss([(OTHER_DESC, FIRED)])]})
        results, slept = self.run_check([pin()], feeds)
        self.assertEqual(slept, [self.RETRY])
        self.assertEqual(feeds.calls[CATS], 2)
        self.assertEqual(results[0]["status"], "missing")

    def test_only_the_unresolved_pins_are_read_again(self):
        feeds = _Feeds(**{CATS: [rss([])], FOOD: [rss([(CAT_DESC, FIRED)])]})
        self.run_check([pin(), pin(event="happypet_pin_food")], feeds)
        self.assertEqual(feeds.calls, {CATS: 2, FOOD: 1})

    def test_a_blocked_feed_says_could_not_check_never_missing_never_a_pass(self):
        feeds = _Feeds(**{CATS: [vp.FeedError("HTTP 403")]})
        results, slept = self.run_check([pin()], feeds)
        self.assertEqual(results[0]["status"], "unchecked")
        self.assertEqual(slept, [self.RETRY], "a read that failed is retried once too")
        report = vp.build_report(results)
        self.assertEqual(report["kind"], "unchecked")
        self.assertIn("could not check", report["body"].lower())
        self.assertIn("HTTP 403", report["body"])
        self.assertNotIn("missing", report["body"].lower())
        self.assertIn("not a confirmed miss", report["body"])

    def test_a_feed_that_recovers_on_the_retry_is_judged_normally(self):
        feeds = _Feeds(**{CATS: [vp.FeedError("HTTP 429"),
                                 rss([(CAT_DESC, FIRED + dt.timedelta(minutes=16))])]})
        results, _ = self.run_check([pin()], feeds)
        self.assertEqual(results[0]["status"], "found")

    def test_an_empty_feed_is_could_not_check(self):
        results, _ = self.run_check([pin()], _Feeds(**{CATS: [rss([])]}))
        self.assertEqual(results[0]["status"], "unchecked")
        self.assertEqual(vp.build_report(results)["kind"], "unchecked")

    def test_a_miss_outranks_a_could_not_check_and_both_are_named(self):
        feeds = _Feeds(**{CATS: [rss([(OTHER_DESC, FIRED)])], FOOD: [vp.FeedError("HTTP 403")]})
        results, _ = self.run_check([pin(), pin(event="happypet_pin_food")], feeds)
        report = vp.build_report(results)
        self.assertEqual(report["kind"], "missing")
        self.assertIn(CATS, report["body"])
        self.assertIn(FOOD, report["body"])
        self.assertIn("HTTP 403", report["body"])

    def test_one_feed_read_serves_every_pin_on_that_board(self):
        a, b = pin(slug="best-a"), pin(slug="best-b", desc=OTHER_DESC)
        feeds = _Feeds(**{CATS: [rss([(CAT_DESC, FIRED), (OTHER_DESC, FIRED)])]})
        results, _ = self.run_check([a, b], feeds)
        self.assertEqual({r["status"] for r in results}, {"found"})
        self.assertEqual(feeds.calls[CATS], 1)

    def test_the_description_on_another_board_does_not_count(self):
        feeds = _Feeds(**{CATS: [rss([(OTHER_DESC, FIRED)])], FOOD: [rss([(CAT_DESC, FIRED)])]})
        results, _ = self.run_check([pin()], feeds)   # the pin belongs on the cat board
        self.assertEqual(results[0]["status"], "missing")


class TestReport(unittest.TestCase):
    def missing(self):
        return [{"pin": pin(), "status": "missing", "detail": "the cat feed has 12 items"}]

    def test_the_body_says_it_will_not_re_fire_and_how_to_repost(self):
        body = vp.build_report(self.missing())["body"]
        for needle in ("best-lickable-cat-treats", CATS, "happypet_pin_cats",
                       "will not re-fire by itself", "_pin_queue/.fired/.pinned.json",
                       "best-lickable-cat-treats.happypet_pin_cats.fired",
                       "best-lickable-cat-treats.fired", "_pin_queue/sent/",
                       "force", "pin.yml", "https://www.pinterest.com/happypetproductreviews/best-cat-products/"):
            with self.subTest(needle=needle):
                self.assertIn(needle, body)

    def test_the_fire_time_is_in_utc(self):
        self.assertIn("2026-11-02 12:29 UTC", vp.build_report(self.missing())["body"])

    def test_found_pins_are_not_in_the_report(self):
        results = self.missing() + [{"pin": pin(event="happypet_pin_food"), "status": "found", "detail": ""}]
        self.assertNotIn(FOOD, vp.build_report(results)["body"])
        self.assertEqual(vp.build_report(results)["slugs"], "best-lickable-cat-treats")

    def test_slugs_are_sorted_and_unique(self):
        results = [{"pin": pin(slug=s, event=e), "status": "missing", "detail": "x"}
                   for s in ("best-b", "best-a") for e in ("happypet_pin_cats", "happypet_pin_food")]
        self.assertEqual(vp.build_report(results)["slugs"], "best-a,best-b")


class TestFindFiredPins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def sentinel(self, name, when):
        (self.dir / name).write_text(when.isoformat() if hasattr(when, "isoformat") else when, encoding="utf-8")

    def test_only_board_sentinels_fired_since_the_run_started_count(self):
        since = dt.datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
        self.sentinel("best-new.happypet_pin_cats.fired", since + dt.timedelta(minutes=12))
        self.sentinel("best-new.happypet_pin_food.fired", since + dt.timedelta(minutes=13))
        self.sentinel("best-new.fired", since + dt.timedelta(minutes=13))            # all-events marker
        self.sentinel("best-old.happypet_pin_cats.fired", since - dt.timedelta(days=3))
        (self.dir / ".pinned.json").write_text("{}", encoding="utf-8")
        found = vp.find_fired_pins(self.dir, since)
        self.assertEqual(sorted((p["slug"], p["event"]) for p in found),
                         [("best-new", "happypet_pin_cats"), ("best-new", "happypet_pin_food")])
        self.assertEqual(found[0]["fired_at"].tzinfo, UTC)

    def test_a_sentinel_exactly_at_the_start_counts(self):
        since = dt.datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
        self.sentinel("best-new.happypet_pin_cats.fired", since)
        self.assertEqual(len(vp.find_fired_pins(self.dir, since)), 1)

    def test_an_unparseable_sentinel_is_skipped_not_fatal(self):
        since = dt.datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
        self.sentinel("best-new.happypet_pin_cats.fired", "not a time")
        self.assertEqual(vp.find_fired_pins(self.dir, since), [])

    def test_an_unknown_board_event_is_returned_so_it_can_be_reported(self):
        since = dt.datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
        self.sentinel("best-new.happypet_pin_birds.fired", since + dt.timedelta(minutes=1))
        (found,) = vp.find_fired_pins(self.dir, since)
        self.assertIsNone(found["board"])

    def test_a_missing_directory_is_no_pins(self):
        self.assertEqual(vp.find_fired_pins(self.dir / "nope", dt.datetime(2026, 1, 1, tzinfo=UTC)), [])


def _tree_digest(root):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


class TestMain(_NoNetwork):
    """main() end to end against a temp repo: sentinels in, report files out."""

    SINCE = "2026-11-02T12:20:00Z"

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        fired = self.repo / "_pin_queue" / ".fired"
        fired.mkdir(parents=True)
        (self.repo / "_pin_queue" / "sent").mkdir()
        slug = "best-lickable-cat-treats"
        for event in ("happypet_pin_cats", "happypet_pin_food"):
            (fired / f"{slug}.{event}.fired").write_text(FIRED.isoformat(), encoding="utf-8")
        (fired / f"{slug}.fired").write_text(FIRED.isoformat(), encoding="utf-8")
        (fired / ".pinned.json").write_text(json.dumps(
            {slug: {"example.com/a.jpg": {"happypet_pin_cats": "2026-11-02",
                                          "happypet_pin_food": "2026-11-02"}}}), encoding="utf-8")
        (self.repo / "_pin_queue" / "sent" / f"{slug}.json").write_text("{}", encoding="utf-8")
        self.out = self.repo / "pin-verify"
        self.slept = []

    def go(self, feeds, since=SINCE, describe=lambda slug: CAT_DESC, extra=()):
        return vp.main(["--since", since, "--repo-dir", str(self.repo), "--out-dir", str(self.out),
                        "--wait-minutes", "15", "--retry-wait-minutes", "15", *extra],
                       read_board=feeds, describe=lambda r: describe, sleep=self.slept.append)

    def test_the_cat_failure_exits_1_and_writes_the_report_files(self):
        feeds = _Feeds(**{CATS: [rss([(OTHER_DESC, dt.datetime(2026, 10, 26, tzinfo=UTC))])],
                          FOOD: [rss([(CAT_DESC, FIRED + dt.timedelta(minutes=1))])]})
        before = _tree_digest(self.repo / "_pin_queue")
        self.assertEqual(self.go(feeds), 1)
        self.assertEqual((self.out / "kind").read_text(encoding="utf-8"), "missing")
        self.assertEqual((self.out / "slugs").read_text(encoding="utf-8"), "best-lickable-cat-treats")
        self.assertIn(CATS, (self.out / "body.md").read_text(encoding="utf-8"))
        self.assertEqual(self.slept, [900, 900], "the initial wait, then one retry wait")
        self.assertEqual(_tree_digest(self.repo / "_pin_queue"), before,
                         "the check must not touch a sentinel, the ledger or a queue file")

    def test_a_healthy_run_exits_0_and_writes_nothing(self):
        feeds = _Feeds(**{CATS: [rss([(CAT_DESC, FIRED + dt.timedelta(minutes=2))])],
                          FOOD: [rss([(CAT_DESC, FIRED + dt.timedelta(minutes=2))])]})
        before = _tree_digest(self.repo / "_pin_queue")
        self.assertEqual(self.go(feeds), 0)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.slept, [900])
        self.assertEqual(_tree_digest(self.repo / "_pin_queue"), before)

    def test_a_blocked_feed_exits_2_and_says_could_not_check(self):
        feeds = _Feeds(**{CATS: [vp.FeedError("HTTP 403")], FOOD: [vp.FeedError("HTTP 403")]})
        self.assertEqual(self.go(feeds), 2)
        self.assertEqual((self.out / "kind").read_text(encoding="utf-8"), "unchecked")

    def test_nothing_fired_since_the_run_started_means_no_wait_and_no_feed_read(self):
        def never(board):
            raise AssertionError("a feed was read although nothing fired")
        self.assertEqual(self.go(never, since="2026-11-03T00:00:00Z"), 0)
        self.assertEqual(self.slept, [])

    def test_a_pin_with_no_description_is_could_not_check(self):
        feeds = _Feeds(**{CATS: [rss([])], FOOD: [rss([])]})
        self.assertEqual(self.go(feeds, describe=None), 2)

    def test_a_crash_inside_the_check_is_could_not_check_not_a_pass(self):
        def boom(board):
            raise RuntimeError("unexpected")
        self.assertEqual(self.go(boom), 2)
        self.assertEqual((self.out / "kind").read_text(encoding="utf-8"), "unchecked")
        self.assertIn("RuntimeError", (self.out / "body.md").read_text(encoding="utf-8"))

    def test_hostile_arguments_are_refused_not_run(self):
        for extra in (["--wait-minutes", "-5"], ["--wait-minutes", "999"], ["--retry-wait-minutes", "x"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                vp.main(["--since", self.SINCE, "--repo-dir", str(self.repo), *extra],
                        read_board=_Feeds(), describe=lambda r: (lambda s: ""), sleep=self.slept.append)
        with self.assertRaises(SystemExit):
            vp.main(["--since", "2026-11-02; rm -rf /", "--repo-dir", str(self.repo)],
                    read_board=_Feeds(), describe=lambda r: (lambda s: ""), sleep=self.slept.append)


# ---------------------------------------------------------------------------
# A real capture
# ---------------------------------------------------------------------------
REAL_FEED = REPO / "_test_fixtures" / "pinterest_feed_best_dog_products_2026-11-05.xml"
# What post_pins.py sent for best-dog-winter-coats on 2026-11-05, and when its dogs
# webhook answered (the sentinel's own time). Literals, not read from the repo.
WINTER_DESC = "Waterproof, reversible and under $27: a dog winter coat that keeps cold, wet walks on track."
WINTER_FIRED = dt.datetime(2026, 11, 5, 12, 12, 42, 663749, tzinfo=UTC)
WINTER_PIN_TIME = dt.datetime(2026, 11, 5, 12, 12, 43, tzinfo=UTC)
GHOST = (b"<item><title></title><link>https://www.pinterest.com/pin/AlZA7KdS/</link>"
         b"<description>&lt;a href=&quot;https://www.pinterest.com/pin/AlZA7KdS/&quot;&gt;"
         b"&lt;img src=&quot;&quot;&gt;&lt;/a&gt;</description><pubDate>Invalid Date</pubDate>"
         b"<guid>https://www.pinterest.com/pin/AlZA7KdS/</guid></item>")


def real_items():
    return vp.parse_feed(REAL_FEED.read_bytes())


def winter_pin(**kw):
    return {**pin(slug="best-dog-winter-coats", event="happypet_pin_dogs", desc=WINTER_DESC,
                  fired=WINTER_FIRED), **kw}


class TestRealFeed(_NoNetwork):
    """Held against bytes Pinterest served, not a feed built from a description of one."""

    def test_the_capture_parses_with_its_one_ghost_item(self):
        items = real_items()
        self.assertEqual(len(items), 26)
        undated = [i for i in items if i.pub is None]
        self.assertEqual(len(undated), 1)
        self.assertEqual(undated[0].title, "")
        self.assertTrue(undated[0].link.endswith("/pin/AlZA7KdS/"), undated[0].link)
        self.assertEqual(max(i.pub for i in items if i.pub), WINTER_PIN_TIME)

    def test_the_real_pin_is_found(self):
        self.assertEqual(vp.evaluate_pin(winter_pin(), real_items()), ("found", ""))

    def test_a_real_run_through_run_check_is_silent(self):
        results = vp.run_check([winter_pin()], lambda board: real_items(),
                               sleep=lambda s: self.fail("no retry expected"))
        self.assertEqual([r["status"] for r in results], ["found"])
        self.assertIsNone(vp.build_report(results))

    def test_another_posts_description_is_missing_from_the_real_feed(self):
        status, detail = vp.evaluate_pin(winter_pin(description=CAT_DESC), real_items())
        self.assertEqual(status, "missing")
        self.assertIn("25 dated items, 1 undated skipped", detail)
        self.assertIn("newest 2026-11-05 12:12 UTC", detail)

    def test_a_fire_after_the_real_pin_does_not_find_it(self):
        later = WINTER_FIRED + dt.timedelta(minutes=8)
        self.assertEqual(vp.evaluate_pin(winter_pin(fired_at=later), real_items())[0], "missing")

    def test_an_older_real_pin_does_not_count_for_a_new_fire(self):
        """The 2026-10-29 advent pin is in the feed; fired today it is not today's pin."""
        advent = "Advent countdown treats your small dog will wait by the shelf for, rawhide-free and budget friendly."
        self.assertIn(advent, [i.title for i in real_items()])
        self.assertEqual(vp.evaluate_pin(winter_pin(description=advent), real_items())[0], "missing")
        old_fire = dt.datetime(2026, 10, 29, 12, 30, 30, tzinfo=UTC)
        self.assertEqual(
            vp.evaluate_pin(winter_pin(description=advent, fired_at=old_fire), real_items())[0], "found")

    def test_the_real_feed_is_found_whatever_the_item_order(self):
        """Pinterest reorders a feed between reads; the newest pin was first on 17
        of 24 reads and a random older item otherwise."""
        import random
        items = real_items()
        orders = [list(reversed(items)),
                  sorted(items, key=lambda i: (i.pub is not None, i.pub or WINTER_PIN_TIME))]
        for seed in range(30):
            shuffled = list(items)
            random.Random(seed).shuffle(shuffled)
            orders.append(shuffled)
        for n, order in enumerate(orders):
            with self.subTest(order=n):
                self.assertEqual(vp.evaluate_pin(winter_pin(), order)[0], "found")
                status, detail = vp.evaluate_pin(winter_pin(description=CAT_DESC), order)
                self.assertEqual(status, "missing")
                self.assertIn("newest 2026-11-05 12:12 UTC", detail)

    def test_the_pin_is_found_wherever_the_cut_falls_when_old_pins_drop_off(self):
        """If the feed is capped, older pins fall off the end. Only this run's pins
        are expected, so any number of the newest items still finds them."""
        dated = sorted((i for i in real_items() if i.pub), key=lambda i: i.pub, reverse=True)
        for keep in (1, 2, 5, 13, 24, 25):
            with self.subTest(keep=keep):
                self.assertEqual(vp.evaluate_pin(winter_pin(), dated[:keep])[0], "found")

    def test_main_end_to_end_on_the_real_feed_exits_0_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            fired = repo / "_pin_queue" / ".fired"
            fired.mkdir(parents=True)
            (fired / "best-dog-winter-coats.happypet_pin_dogs.fired").write_text(
                WINTER_FIRED.isoformat(), encoding="utf-8")
            out = repo / "pin-verify"
            rc = vp.main(["--since", "2026-11-05T12:00:00Z", "--repo-dir", str(repo), "--out-dir", str(out),
                          "--wait-minutes", "0", "--retry-wait-minutes", "0"],
                         read_board=lambda board: real_items(),
                         describe=lambda r: (lambda slug: WINTER_DESC), sleep=lambda s: None)
            self.assertEqual(rc, 0)
            self.assertFalse(out.exists())


class TestGhostAndUndatedItems(_NoNetwork):
    GOOD = (CAT_DESC, FIRED + dt.timedelta(minutes=2))

    @staticmethod
    def feed_with(*parts):
        return vp.parse_feed(b'<rss version="2.0"><channel>' + b"".join(parts) + b"</channel></rss>")

    @staticmethod
    def real_item(title, when):
        return (f"<item><title>{escape(title)}</title><pubDate>{email.utils.format_datetime(when, usegmt=True)}"
                f"</pubDate></item>").encode()

    def test_a_ghost_beside_a_real_pin_does_not_hide_it_or_crash(self):
        good = self.real_item(*self.GOOD)
        for ghost_first, order in ((True, (GHOST, good)), (False, (good, GHOST))):
            with self.subTest(ghost_first=ghost_first):
                self.assertEqual(vp.evaluate_pin(pin(), self.feed_with(*order))[0], "found")

    def test_a_ghost_beside_other_dated_pins_is_missing_not_no_dates(self):
        other = self.real_item(OTHER_DESC, FIRED + dt.timedelta(minutes=1))
        status, detail = vp.evaluate_pin(pin(), self.feed_with(GHOST, other))
        self.assertEqual(status, "missing")
        self.assertIn("1 dated item, 1 undated skipped", detail)

    def test_a_feed_of_only_a_ghost_is_could_not_check(self):
        status, detail = vp.evaluate_pin(pin(), self.feed_with(GHOST))
        self.assertEqual(status, "unchecked")
        self.assertIn("no readable dates", detail)

    def test_many_undated_items_do_not_stop_a_dated_match(self):
        undated = [f"<item><title>{OTHER_DESC} {n}</title></item>".encode() for n in range(10)]
        feed = self.feed_with(*undated, self.real_item(*self.GOOD))
        self.assertEqual(vp.evaluate_pin(pin(), feed)[0], "found")

    def test_an_undated_item_with_the_right_text_cannot_prove_the_pin(self):
        """No date means no proof it is this run's pin rather than an older one."""
        for junk in ("Invalid Date", "", "yesterday", "Thu, 99 Foo 2026 12:00:00 GMT",
                     "Thu, 05 Nov 99999 12:12:43 GMT", "Thu, 05 Nov 2026 25:61:61 GMT"):
            with self.subTest(pubdate=junk):
                undated = f"<item><title>{escape(CAT_DESC)}</title><pubDate>{junk}</pubDate></item>".encode()
                dated = self.real_item(OTHER_DESC, FIRED + dt.timedelta(minutes=1))
                self.assertEqual(vp.evaluate_pin(pin(), self.feed_with(undated, dated))[0], "missing")

    def test_junk_dates_read_as_none_and_never_raise(self):
        for junk in ("Invalid Date", "", "   ", "NaN", "0", "Thu, 99 Foo 2026 12:00:00 GMT",
                     "Thu, 05 Nov 99999 12:12:43 GMT", "Thu, 05 Nov 2026 25:61:61 GMT", None):
            with self.subTest(junk=junk):
                self.assertIsNone(vp._pub(junk))

    def test_items_with_no_title_or_no_pubdate_element_parse(self):
        items = self.feed_with(b"<item><link>https://x/</link></item>", b"<item><title/></item>")
        self.assertEqual([(i.title, i.pub) for i in items], [("", None), ("", None)])


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------
class TestPinVerifyWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (WORKFLOWS / "pin-verify.yml").read_text(encoding="utf-8")
        cls.pin_yml = (WORKFLOWS / "pin.yml").read_text(encoding="utf-8")

    def test_it_waits_on_pin_ymls_own_name(self):
        """A renamed pin.yml would silently turn the check off."""
        name = re.search(r"^name: (.+)$", self.pin_yml, re.M).group(1).strip()
        self.assertEqual(re.findall(r"^    workflows: \[\"(.+)\"\]$", self.text, re.M), [name])
        self.assertIn("\n  workflow_run:\n", self.text)
        self.assertIn("    types: [completed]\n", self.text)
        self.assertIn("\n  workflow_dispatch:\n", self.text)

    def test_it_runs_after_a_run_that_pinned_or_failed_and_never_after_a_cancel(self):
        self.assertIn("github.event.workflow_run.conclusion == 'success'", self.text)
        self.assertIn("github.event.workflow_run.conclusion == 'failure'", self.text)
        self.assertNotIn("cancelled", self.text.replace("never after a cancel", ""))

    def test_the_token_can_read_the_repo_and_open_issues_only(self):
        self.assertEqual(_workflow_permissions(self.text), ({"contents": "read", "issues": "write"}, 1))

    def test_it_reads_main_with_no_stored_credentials(self):
        self.assertIn("uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4\n", self.text)
        self.assertIn("          ref: main\n", self.text)
        self.assertIn("          persist-credentials: false\n", self.text)

    def test_it_never_writes_or_fires_anything(self):
        for needle in ("git push", "git commit", "git add", "IFTTT", "maker.ifttt.com", "post_pins.py",
                       "workflow run", "gh pr ", "secrets.GH", "contents: write"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, self.text)
        self.assertEqual(re.findall(r"secrets\.\w+", self.text), ["secrets.GITHUB_TOKEN"])

    def test_the_timeout_covers_both_waits_with_room(self):
        minutes = int(re.search(r"timeout-minutes: (\d+)", self.text).group(1))
        self.assertGreaterEqual(minutes, 15 + 15 + 10)
        self.assertLessEqual(minutes, 60)

    def test_the_check_step_gets_its_inputs_through_env_not_script_text(self):
        head, script = _named_step(self.text, "Check each pin is on its board")
        self.assertNotIn("${{", script)
        self.assertIn("verify_pins.py", script)
        self.assertIn("github.event.workflow_run.run_started_at", head)
        self.assertIn("continue-on-error: true", head)

    def test_a_check_that_fails_turns_the_run_red_and_the_alert_runs_on_any_failure(self):
        self.assertRegex(self.text, r"name: Fail the run if the check found a problem\n\s+if: steps\.check\.outcome == 'failure'")
        head, _ = _named_step(self.text, "Open an issue")
        self.assertIn("if: failure()", head)

    def test_the_default_waits_match_what_the_workflow_documents(self):
        self.assertRegex(self.text, r"wait_minutes:[\s\S]*?default: '15'")
        self.assertIn("WAIT_MINUTES: ${{ inputs.wait_minutes || '15' }}", self.text)


class _PinVerifyIssue(_AlertIssueStep):
    WORKFLOW = "pin-verify.yml"
    STEP = "Open an issue"
    KIND = BODY = SLUGS = None

    def prepare(self, tmp):
        out = Path(tmp) / "pin-verify"
        out.mkdir()
        (out / "kind").write_text(self.KIND, encoding="utf-8")
        (out / "slugs").write_text(self.SLUGS, encoding="utf-8")
        (out / "body.md").write_text(self.BODY, encoding="utf-8")


class TestPinMissingIssue(_PinVerifyIssue, unittest.TestCase):
    TITLE_RE = r"^⚠️ Pin missing from its board on \d{4}-\d{2}-\d{2}: best-a,best-b$"
    KIND, SLUGS = "missing", "best-a,best-b"
    BODY = "Detail line from the checker."

    def test_the_issue_carries_the_checkers_text(self):
        _, body = self.opened()
        self.assertTrue(body.startswith("Detail line from the checker."), body)

    def test_slugs_are_data_not_shell(self):
        hostile = "best-a,$(touch pwned),`touch pwned2`;touch pwned3"
        with tempfile.TemporaryDirectory() as d:
            from test_pipeline import _run_alert_step
            out = Path(d) / "pin-verify"
            out.mkdir()
            (out / "kind").write_text("missing", encoding="utf-8")
            (out / "slugs").write_text(hostile, encoding="utf-8")
            (out / "body.md").write_text("x", encoding="utf-8")
            proc, calls = _run_alert_step(self, self.script, {"RUN_URL": self.RUN_URL}, d)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["gh_args", "pin-verify"])
        self.assertTrue(self.arg(self.creates(calls)[0], "--title").endswith(": " + hostile))


class TestPinCheckCouldNotRunIssue(_PinVerifyIssue, unittest.TestCase):
    TITLE_RE = r"^⚠️ Pin check could not run on \d{4}-\d{2}-\d{2}: best-a$"
    KIND, SLUGS = "unchecked", "best-a"
    BODY = "Could not check."


class TestPinCheckDiedBeforeReportIssue(_PinVerifyIssue, unittest.TestCase):
    """A setup step failed, so no report files exist: still one honest issue."""
    TITLE_RE = r"^⚠️ Pin check could not run on \d{4}-\d{2}-\d{2}: unknown slugs$"
    KIND = SLUGS = BODY = ""

    def prepare(self, tmp):
        pass

    def test_the_body_says_nothing_was_confirmed(self):
        _, body = self.opened()
        self.assertIn("No pin was confirmed or ruled out", body)


if __name__ == "__main__":
    unittest.main()
