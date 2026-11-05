#!/usr/bin/env python3
"""
test_verify_pins.py -- verify_pins.py and .github/workflows/pin-verify.yml.

post_pins.py counts a pin as fired when IFTTT's Maker webhook answers 2xx. That
says IFTTT accepted the event, not that Pinterest made the pin: on 2026-11-02
the "Best Cat Products" applet failed ("The Pinterest connection was
temporarily unavailable"), Stage 3 still passed, and the pin was missing.
verify_pins.py reads each board's public RSS feed on an hourly schedule and
reports a pin that is not there.

Kept out of test_pipeline.py on purpose (several branches edit it at once); it
borrows that file's alert-issue harness so the new alert step is held to the
same rules as every other one. No test reaches a network: urlopen is patched
to raise for the whole module.

Most fixtures are built here from the feed shape seen on 2026-11-05 (an RSS 2.0
item whose <title> is the pin's description and whose <pubDate> is the pin's
creation time). TestRealFeed holds the checker against a real capture of the
best-dog-products feed taken that day, byte for byte.

Run: python3 -m pytest test_verify_pins.py -q
"""

import datetime as dt
import email.utils
import hashlib
import os
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
from test_pipeline import (GH_CALL_END, WORKFLOWS, _AlertIssueStep,  # noqa: E402
                           _bash_for_workflow_steps, _named_step, _run_alert_step,
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

    def test_the_request_carries_only_the_user_agent_a_runner_was_tested_with(self):
        """A GitHub runner read all six feeds 18 of 18 times with `Mozilla/5.0` and
        nothing else; no custom Accept is sent."""
        with patch("urllib.request.urlopen", return_value=self._Resp(rss([(CAT_DESC, FIRED)]))) as op:
            vp.fetch_feed(CATS)
        self.assertEqual(op.call_args[0][0].header_items(), [("User-agent", "Mozilla/5.0")])
        self.assertEqual(vp.USER_AGENT, "Mozilla/5.0")

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

    def test_a_feed_whose_dated_items_all_have_blank_titles_is_could_not_check(self):
        """Text moved out of <title> is a format change, not a missing pin."""
        blank = feed_of(("", FIRED + dt.timedelta(minutes=1)), ("   ", FIRED + dt.timedelta(minutes=2)),
                        ("<b></b>", FIRED + dt.timedelta(minutes=3)))
        status, detail = vp.evaluate_pin(pin(), blank)
        self.assertEqual(status, "unchecked")
        self.assertIn("long enough to match", detail)

    def test_a_feed_whose_dated_items_are_all_shorter_than_a_match_is_could_not_check(self):
        """Titles under MIN_MATCH characters cannot identify a pin, so a feed of nothing
        else tells us nothing; one title of MIN_MATCH characters is enough to judge."""
        when = FIRED + dt.timedelta(minutes=1)
        short = feed_of(("x" * (vp.MIN_MATCH - 1), when), ("Great food", when))
        status, detail = vp.evaluate_pin(pin(), short)
        self.assertEqual(status, "unchecked")
        self.assertIn("long enough to match", detail)
        enough = feed_of(("x" * vp.MIN_MATCH, when), ("Great food", when))
        self.assertEqual(vp.evaluate_pin(pin(), enough)[0], "missing")

    def test_text_only_on_undated_items_is_still_could_not_check(self):
        feed = vp.parse_feed(
            b"<rss><channel><item><title>" + CAT_DESC.encode() + b"</title></item>"
            b"<item><title></title><pubDate>Mon, 02 Nov 2026 12:30:00 GMT</pubDate></item></channel></rss>")
        self.assertEqual(vp.evaluate_pin(pin(), feed)[0], "unchecked")

    def test_one_blank_title_among_dated_items_changes_nothing(self):
        """The real capture has a blank-titled ghost; one blank dated item must not
        turn a found pin or a missing pin into anything else."""
        when = FIRED + dt.timedelta(minutes=2)
        self.assertEqual(vp.evaluate_pin(pin(), feed_of(("", when), (CAT_DESC, when)))[0], "found")
        self.assertEqual(vp.evaluate_pin(pin(), feed_of(("", when), (OTHER_DESC, when)))[0], "missing")

    def test_an_unreadable_fire_time_is_could_not_check(self):
        status, detail = vp.evaluate_pin(pin(fired=None), feed_of((CAT_DESC, FIRED)))
        self.assertEqual(status, "unchecked")
        self.assertIn("fire time could not be read", detail)

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
        self.assertEqual(report["slugs"], "best-lickable-cat-treats #b5dce3")
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

    def test_a_retry_wait_of_zero_still_re_reads_and_does_not_sleep(self):
        feeds = _Feeds(**{CATS: [rss([]), rss([(CAT_DESC, FIRED + dt.timedelta(minutes=1))])]})
        slept = []
        results = vp.run_check([pin()], feeds, sleep=slept.append, retry_wait_seconds=0)
        self.assertEqual(slept, [])
        self.assertEqual(feeds.calls[CATS], 2, "a miss is always read a second time")
        self.assertEqual(results[0]["status"], "found")

    def test_an_unexpected_error_on_one_board_does_not_hide_a_miss_on_another(self):
        def read(board):
            if board == FOOD:
                raise RuntimeError("boom")
            return feed_of((OTHER_DESC, FIRED))
        results, _ = self.run_check([pin(), pin(event="happypet_pin_food")], read)
        self.assertEqual(self.statuses(results), {("best-lickable-cat-treats", CATS): "missing",
                                                  ("best-lickable-cat-treats", FOOD): "unchecked"})
        report = vp.build_report(results)
        self.assertEqual(report["kind"], "missing")
        self.assertIn("unexpected RuntimeError", report["body"])

    def test_an_unreadable_fire_time_is_could_not_check_and_not_retried(self):
        feeds = _Feeds(**{CATS: [rss([(CAT_DESC, FIRED)])]})
        results, slept = self.run_check([pin(fired=None)], feeds)
        self.assertEqual(results[0]["status"], "unchecked")
        self.assertEqual(slept, [])
        self.assertIn("fire time unreadable", vp.build_report(results)["body"])
        self.assertEqual(vp.build_report(results)["kind"], "unchecked")


class TestReport(unittest.TestCase):
    def missing(self):
        return [{"pin": pin(), "status": "missing", "detail": "the cat feed has 12 items"}]

    def test_the_body_names_the_pin_and_the_board(self):
        body = vp.build_report(self.missing())["body"]
        for needle in ("best-lickable-cat-treats", CATS, "happypet_pin_cats",
                       "https://www.pinterest.com/happypetproductreviews/best-cat-products/"):
            with self.subTest(needle=needle):
                self.assertIn(needle, body)

    def test_the_repost_steps_are_exactly_these(self):
        """The steps as a person will read them. TestRecoverySteps proves each one
        against post_pins.py's own main()."""
        lines = vp.build_report(self.missing())["body"].splitlines()
        steps = [ln for ln in lines if re.match(r"^\d\. ", ln)]
        self.assertEqual(steps, [
            "1. Check the IFTTT applet's activity for the failure.",
            "2. In `_pin_queue/.fired/.pinned.json`, delete the missing board's entry under the slug's image. "
            "If that leaves the image with no boards, delete the image key; then the slug key if it has no images.",
            "3. If the queue file is in `_pin_queue/sent/` (`_pin_queue/sent/best-lickable-cat-treats.json`), "
            "move it back to `_pin_queue/`. Left there, a rerun looks green (\"nothing to do\") while nothing "
            "fires. With the file back but the ledger entry still there, post_pins.py refuses the repeat "
            "(exit 6).",
            "4. Commit the change and push to `main`. pin.yml runs from `main`, so a change that is not "
            "there changes nothing.",
            "5. Run pin.yml with slugs set to the slug and force set to true. Boards still in the ledger "
            "are refused. Boards missing from it fire: the one you removed, and any that never fired in a "
            "partial fire.",
            "6. The next hourly sweep checks the repost. Close this issue.",
        ])
        (intro,) = [ln for ln in lines if ln.startswith("This will not re-fire by itself.")]
        self.assertEqual(intro,
                         "This will not re-fire by itself. The ledger (`_pin_queue/.fired/.pinned.json`) and the "
                         "sentinels in `_pin_queue/.fired/` (`best-lickable-cat-treats.happypet_pin_cats.fired`, "
                         "`best-lickable-cat-treats.fired`) all say the pin went out, so pin.yml skips it. "
                         "To repost:")

    def test_several_slugs_name_every_queue_file_and_pluralize_the_steps(self):
        results = [{"pin": pin(slug=s), "status": "missing", "detail": "x"} for s in ("best-b", "best-a")]
        body = vp.build_report(results)["body"]
        self.assertIn("3. If the queue file is in `_pin_queue/sent/` (`_pin_queue/sent/best-a.json` and "
                      "`_pin_queue/sent/best-b.json`), move it back", body)
        self.assertIn("4. Commit the changes and push", body)
        self.assertIn("slugs set to the slugs and force", body)

    def test_the_title_slug_list_is_capped_and_the_body_still_lists_every_pin(self):
        slugs = [f"best-{c}-{'x' * 38}" for c in "abcdefgh"]
        results = [{"pin": pin(slug=s), "status": "missing", "detail": "x"} for s in slugs]
        report = vp.build_report(results)
        self.assertEqual(report["slugs"], ",".join(slugs[:3]) + " (+5 more) #a942a2")
        self.assertLess(len("⚠️ Pin missing from its board on 2026-11-05: " + report["slugs"]), 256)
        for s in slugs:
            self.assertIn(f"`{s}`", report["body"])

    def test_the_title_list_is_stable_whatever_the_order(self):
        a = [{"pin": pin(slug=s), "status": "missing", "detail": "x"} for s in ("best-b", "best-c", "best-a", "best-d")]
        self.assertEqual(vp.build_report(a)["slugs"], "best-a,best-b,best-c (+1 more) #e18bc5")
        self.assertEqual(vp.build_report(list(reversed(a)))["slugs"], vp.build_report(a)["slugs"])

    def test_the_fire_time_is_in_utc(self):
        self.assertIn("2026-11-02 12:29 UTC", vp.build_report(self.missing())["body"])

    def test_found_pins_are_not_in_the_report(self):
        results = self.missing() + [{"pin": pin(event="happypet_pin_food"), "status": "found", "detail": ""}]
        self.assertNotIn(FOOD, vp.build_report(results)["body"])
        self.assertEqual(vp.build_report(results)["slugs"], "best-lickable-cat-treats #b5dce3")

    def test_a_different_set_of_misses_gets_a_different_title_so_dedupe_cannot_hide_it(self):
        """Literals from the sha1 of the sorted slug|event pairs. Same slug on another
        board, another slug on the same board, and a bigger set are all new titles."""
        cat = [{"pin": pin(), "status": "missing", "detail": "x"}]
        food = [{"pin": pin(event="happypet_pin_food"), "status": "missing", "detail": "x"}]
        both = cat + food
        titles = [vp.build_report(r)["slugs"] for r in (cat, food, both)]
        self.assertEqual(titles[:2], ["best-lickable-cat-treats #b5dce3", "best-lickable-cat-treats #1d6949"])
        self.assertEqual(len(set(titles)), 3)
        self.assertEqual(vp.build_report(list(reversed(both)))["slugs"], titles[2], "order does not matter")

    def test_slugs_are_sorted_and_unique(self):
        results = [{"pin": pin(slug=s, event=e), "status": "missing", "detail": "x"}
                   for s in ("best-b", "best-a") for e in ("happypet_pin_cats", "happypet_pin_food")]
        self.assertEqual(vp.build_report(results)["slugs"], "best-a,best-b #aeadb2")


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

    def test_an_unreadable_sentinel_time_fails_closed_it_is_returned_not_dropped(self):
        since = dt.datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
        for n, text in enumerate(("not a time", "", "   ", "2026-13-45T99:00:00")):
            with self.subTest(text=text):
                self.sentinel(f"best-bad{n}.happypet_pin_cats.fired", text)
        found = vp.find_fired_pins(self.dir, since)
        self.assertEqual(sorted(p["slug"] for p in found), ["best-bad0", "best-bad1", "best-bad2", "best-bad3"])
        self.assertTrue(all(p["fired_at"] is None for p in found))

    def test_a_far_future_sentinel_time_is_unreadable_and_a_slightly_future_one_is_not(self):
        now = dt.datetime(2026, 11, 5, 13, 0, tzinfo=UTC)
        since = now - dt.timedelta(hours=3)
        self.sentinel("best-far.happypet_pin_cats.fired", (now + dt.timedelta(hours=1, minutes=1)).isoformat())
        self.sentinel("best-skew.happypet_pin_cats.fired", (now + dt.timedelta(minutes=59)).isoformat())
        self.sentinel("best-year.happypet_pin_cats.fired", "2099-01-01T00:00:00+00:00")
        found = {p["slug"]: p["fired_at"] for p in vp.find_fired_pins(self.dir, since, now)}
        self.assertEqual(sorted(found), ["best-far", "best-skew", "best-year"])
        self.assertIsNone(found["best-far"])
        self.assertIsNone(found["best-year"])
        self.assertEqual(found["best-skew"], now + dt.timedelta(minutes=59))

    def test_without_a_now_a_future_time_is_left_alone(self):
        since = dt.datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
        self.sentinel("best-year.happypet_pin_cats.fired", "2099-01-01T00:00:00+00:00")
        (found,) = vp.find_fired_pins(self.dir, since)
        self.assertEqual(found["fired_at"].year, 2099)

    def test_a_sentinel_with_a_zone_other_than_utc_is_converted(self):
        since = dt.datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
        self.sentinel("best-new.happypet_pin_cats.fired", "2026-11-05T08:12:42.663749-04:00")
        (found,) = vp.find_fired_pins(self.dir, since)
        self.assertEqual(found["fired_at"], dt.datetime(2026, 11, 5, 12, 12, 42, 663749, tzinfo=UTC))

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
        self.out = self.repo / "LOGS" / "pin-verify"
        self.slept = []

    NOW = FIRED + dt.timedelta(hours=1)   # a pin fired 60 minutes ago: due

    def go(self, feeds, now=NOW, since=None, describe=lambda slug: CAT_DESC, extra=()):
        argv = ["--repo-dir", str(self.repo), "--out-dir", str(self.out), "--retry-wait-minutes", "15", *extra]
        if since is not None:
            argv += ["--since", since]
        return vp.main(argv, read_board=feeds, describe=lambda r: describe, sleep=self.slept.append, now=now)

    def test_the_cat_failure_exits_1_and_writes_the_report_files(self):
        feeds = _Feeds(**{CATS: [rss([(OTHER_DESC, dt.datetime(2026, 10, 26, tzinfo=UTC))])],
                          FOOD: [rss([(CAT_DESC, FIRED + dt.timedelta(minutes=1))])]})
        before = _tree_digest(self.repo / "_pin_queue")
        self.assertEqual(self.go(feeds), 1)
        self.assertEqual((self.out / "kind").read_text(encoding="utf-8"), "missing")
        self.assertEqual((self.out / "slugs").read_text(encoding="utf-8"), "best-lickable-cat-treats #b5dce3")
        self.assertIn(CATS, (self.out / "body.md").read_text(encoding="utf-8"))
        self.assertEqual(self.slept, [900], "one retry wait, and no wait before the first read")
        self.assertEqual(_tree_digest(self.repo / "_pin_queue"), before,
                         "the check must not touch a sentinel, the ledger or a queue file")

    def test_a_healthy_run_exits_0_and_writes_nothing(self):
        feeds = _Feeds(**{CATS: [rss([(CAT_DESC, FIRED + dt.timedelta(minutes=2))])],
                          FOOD: [rss([(CAT_DESC, FIRED + dt.timedelta(minutes=2))])]})
        before = _tree_digest(self.repo / "_pin_queue")
        self.assertEqual(self.go(feeds), 0)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.slept, [], "a healthy check never sleeps")
        self.assertEqual(_tree_digest(self.repo / "_pin_queue"), before)

    def test_a_blocked_feed_exits_2_and_says_could_not_check(self):
        feeds = _Feeds(**{CATS: [vp.FeedError("HTTP 403")], FOOD: [vp.FeedError("HTTP 403")]})
        self.assertEqual(self.go(feeds), 2)
        self.assertEqual((self.out / "kind").read_text(encoding="utf-8"), "unchecked")

    @staticmethod
    def never(board):
        raise AssertionError("a feed was read although no pin was due")

    def test_nothing_fired_in_the_window_means_no_wait_and_no_feed_read(self):
        self.assertEqual(self.go(self.never, now=FIRED + dt.timedelta(hours=4)), 0)
        self.assertEqual(self.slept, [])

    def test_the_default_window_is_210_minutes(self):
        self.assertEqual(self.go(self.never, now=FIRED + dt.timedelta(minutes=210, seconds=1)), 0)
        feeds = _Feeds(**{CATS: [rss([(CAT_DESC, FIRED)])], FOOD: [rss([(CAT_DESC, FIRED)])]})
        self.assertEqual(self.go(feeds, now=FIRED + dt.timedelta(minutes=209)), 0)
        self.assertEqual(feeds.calls, {CATS: 1, FOOD: 1}, "inside the window, the pins were read")

    def test_a_pin_younger_than_the_settle_time_is_left_to_the_next_sweep(self):
        self.assertEqual(self.go(self.never, now=FIRED + dt.timedelta(minutes=29, seconds=59)), 0)
        feeds = _Feeds(**{CATS: [rss([(CAT_DESC, FIRED)])], FOOD: [rss([(CAT_DESC, FIRED)])]})
        self.assertEqual(self.go(feeds, now=FIRED + dt.timedelta(minutes=30)), 0)
        self.assertEqual(feeds.calls, {CATS: 1, FOOD: 1}, "at exactly 30 minutes the pins are due")

    def test_the_settle_time_is_an_argument(self):
        feeds = _Feeds(**{CATS: [rss([(CAT_DESC, FIRED)])], FOOD: [rss([(CAT_DESC, FIRED)])]})
        self.assertEqual(self.go(feeds, now=FIRED + dt.timedelta(minutes=2), extra=["--settle-minutes", "0"]), 0)
        self.assertEqual(feeds.calls, {CATS: 1, FOOD: 1})
        self.assertEqual(self.go(self.never, now=FIRED + dt.timedelta(minutes=2),
                                 extra=["--settle-minutes", "5"]), 0)

    def test_an_explicit_since_widens_the_window_but_not_past_24_hours(self):
        feeds = _Feeds(**{CATS: [rss([(CAT_DESC, FIRED)])], FOOD: [rss([(CAT_DESC, FIRED)])]})
        later = FIRED + dt.timedelta(hours=23)
        self.assertEqual(self.go(feeds, now=later, since="2026-11-02T12:00:00Z"), 0)
        self.assertEqual(feeds.calls, {CATS: 1, FOOD: 1})
        with self.assertRaises(SystemExit):
            self.go(feeds, now=dt.datetime(2026, 11, 3, 12, 20, 1, tzinfo=UTC), since="2026-11-02T12:20:00Z")

    def test_a_blank_since_means_the_default_window(self):
        feeds = _Feeds(**{CATS: [rss([(CAT_DESC, FIRED)])], FOOD: [rss([(CAT_DESC, FIRED)])]})
        self.assertEqual(self.go(feeds, since=""), 0)
        self.assertEqual(feeds.calls, {CATS: 1, FOOD: 1})

    def test_an_unreadable_sentinel_time_is_reported_even_outside_the_window(self):
        (self.repo / "_pin_queue" / ".fired" / "best-bad.happypet_pin_cats.fired").write_text("junk", encoding="utf-8")
        self.assertEqual(self.go(_Feeds(), now=FIRED + dt.timedelta(days=9)), 2)
        body = (self.out / "body.md").read_text(encoding="utf-8")
        self.assertIn("best-bad", body)
        self.assertIn("fire time unreadable", body)
        self.assertIn("the sentinel's fire time could not be read", body, "judged on its own reason, not a feed error")

    def test_a_far_future_sentinel_is_reported_with_the_repair_note_in_every_sweep(self):
        (self.repo / "_pin_queue" / ".fired" / "best-year.happypet_pin_toys.fired").write_text(
            "2099-01-01T00:00:00+00:00", encoding="utf-8")
        for _ in range(2):
            self.assertEqual(self.go(_Feeds()), 2, "it reopens every sweep until repaired")
            body = (self.out / "body.md").read_text(encoding="utf-8")
            self.assertIn("Repair `_pin_queue/.fired/best-year.happypet_pin_toys.fired`", body)
            self.assertIn("reopens", body)

    def test_a_pin_with_no_description_is_could_not_check(self):
        feeds = _Feeds(**{CATS: [rss([])], FOOD: [rss([])]})
        self.assertEqual(self.go(feeds, describe=None), 2)

    def test_an_error_reading_boards_is_could_not_check_not_a_pass(self):
        def boom(board):
            raise RuntimeError("unexpected")
        self.assertEqual(self.go(boom), 2)
        self.assertEqual((self.out / "kind").read_text(encoding="utf-8"), "unchecked")
        self.assertIn("unexpected RuntimeError", (self.out / "body.md").read_text(encoding="utf-8"))

    def test_a_crash_outside_the_board_reads_is_could_not_check_not_a_pass(self):
        def boom(repo_dir):
            raise OSError("cannot read the posts")
        rc = vp.main(["--repo-dir", str(self.repo), "--out-dir", str(self.out)], read_board=self.never,
                     describe=boom, sleep=self.slept.append, now=self.NOW)
        self.assertEqual(rc, 2)
        self.assertEqual((self.out / "kind").read_text(encoding="utf-8"), "unchecked")
        self.assertIn("OSError", (self.out / "body.md").read_text(encoding="utf-8"))
        self.assertRegex((self.out / "slugs").read_text(encoding="utf-8"),
                         r"^best-lickable-cat-treats #[0-9a-f]{6}$")

    def test_hostile_arguments_are_refused_not_run(self):
        for extra in (["--settle-minutes", "-5"], ["--settle-minutes", "31"], ["--retry-wait-minutes", "x"],
                      ["--retry-wait-minutes", "999"], ["--wait-minutes", "15"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                vp.main(["--repo-dir", str(self.repo), *extra],
                        read_board=_Feeds(), describe=lambda r: (lambda s: ""), sleep=self.slept.append, now=self.NOW)
        with self.assertRaises(SystemExit):
            vp.main(["--since", "2026-11-02; rm -rf /", "--repo-dir", str(self.repo)],
                    read_board=_Feeds(), describe=lambda r: (lambda s: ""), sleep=self.slept.append, now=self.NOW)


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
                          "--retry-wait-minutes", "0"],
                         read_board=lambda board: real_items(),
                         describe=lambda r: (lambda slug: WINTER_DESC), sleep=lambda s: None,
                         now=WINTER_FIRED + dt.timedelta(minutes=50))
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

    def test_it_runs_on_a_schedule_and_by_hand_and_waits_on_no_other_workflow(self):
        """A trigger that fires after another workflow cannot be tested before merge,
        and if it never fired nothing would say so. The schedule has no such gap, so
        no workflow_run trigger and no job condition on another run's conclusion."""
        self.assertEqual(re.findall(r"^    - cron: '([^']*)'$", self.text, re.M), ["23 * * * *"])
        self.assertIn("\n  schedule:\n", self.text)
        self.assertIn("\n  workflow_dispatch:\n", self.text)
        self.assertNotIn("workflow_run", self.text.replace("after another workflow", ""))
        self.assertNotIn("\n    if:", self.text, "the job runs unconditionally")

    def test_the_schedule_is_hourly_and_off_the_top_of_the_hour(self):
        minute, hour, *rest = re.search(r"- cron: '([^']*)'", self.text).group(1).split()
        self.assertEqual((hour, rest), ("*", ["*", "*", "*"]))
        self.assertNotIn(int(minute), (0, 15, 30, 45), "scheduled runs are delayed most at round times")

    @staticmethod
    def _examined(fire_minute, sweeps):
        """Is a pin fired at `fire_minute` past the hour examined by any of `sweeps`
        (actual run times, in minutes from the top of the hour of the first)?"""
        settle = vp.DEFAULT_SETTLE_MINUTES
        window = vp.DEFAULT_WINDOW.total_seconds() / 60
        return any(settle <= when - fire_minute < window for when in sweeps)

    def test_a_pin_is_examined_through_one_skipped_run_and_up_to_59_minutes_of_delay(self):
        """Hourly sweeps nominally at :23, each examining pins aged settle..window. Any
        one sweep skipped, every other sweep on time or up to 59 minutes late, any
        fire minute: still examined at least once."""
        import itertools
        for fire_minute in range(60):
            for skipped in range(6):
                for delays in itertools.product((0, 59), repeat=6):
                    sweeps = [60 * k + 23 + delays[k] for k in range(6) if k != skipped]
                    self.assertTrue(self._examined(fire_minute, sweeps),
                                    (fire_minute, skipped, delays))

    def test_two_skipped_runs_in_a_row_plus_delay_can_leave_a_pin_unchecked(self):
        """The documented limit, shown to be real: the window is 180 minutes past the
        settle time, so a 180-minute gap between actual sweeps is the most it bridges."""
        self.assertEqual(vp.DEFAULT_WINDOW - dt.timedelta(minutes=vp.DEFAULT_SETTLE_MINUTES),
                         dt.timedelta(minutes=180))
        sweeps = [23, 203 + 59, 263 + 59]     # sweeps 1 and 2 skipped, the next one late
        self.assertFalse(self._examined(0, sweeps))
        self.assertTrue(self._examined(0, [23, 203, 263]), "two skipped runs on time are bridged exactly")

    def test_on_time_a_pin_is_examined_by_three_sweeps(self):
        for fire_minute in range(60):
            with self.subTest(fire_minute=fire_minute):
                sweeps = [60 * k + 23 for k in range(8)]
                n = sum(1 for s in sweeps if self._examined(fire_minute, [s]))
                self.assertEqual(n, 3)

    def test_there_is_no_concurrency_group_that_could_drop_a_pending_run(self):
        self.assertNotIn("concurrency:", self.text.split("\njobs:")[0].split("\non:")[1].split("permissions:")[1])
        self.assertEqual(re.findall(r"^\s*concurrency:", self.text, re.M), [])

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


    def test_the_timeout_covers_the_longest_wait_the_inputs_allow_with_room(self):
        """The only wait is the retry wait (at most MAX_WAIT_MINUTES); the settle time
        skips a young pin instead of sleeping. Room for 6 feed reads, twice."""
        minutes = int(re.search(r"timeout-minutes: (\d+)", self.text).group(1))
        self.assertGreaterEqual(minutes, vp.MAX_WAIT_MINUTES + 15)
        self.assertLessEqual(minutes, 60)
        self.assertEqual(self.text.count(f"0 to {vp.MAX_WAIT_MINUTES}"), 2, "both wait inputs say their range")

    def test_the_defaults_in_the_workflow_are_the_scripts_defaults(self):
        self.assertIn(f"SETTLE_MINUTES: ${{{{ inputs.settle_minutes || '{vp.DEFAULT_SETTLE_MINUTES}' }}}}", self.text)
        self.assertIn(f"RETRY_WAIT_MINUTES: ${{{{ inputs.retry_wait_minutes || '{vp.DEFAULT_RETRY_WAIT_MINUTES}' }}}}",
                      self.text)
        self.assertRegex(self.text, rf"settle_minutes:[\s\S]*?default: '{vp.DEFAULT_SETTLE_MINUTES}'")
        self.assertRegex(self.text, rf"retry_wait_minutes:[\s\S]*?default: '{vp.DEFAULT_RETRY_WAIT_MINUTES}'")

    def test_a_check_that_fails_turns_the_run_red(self):
        self.assertRegex(self.text, r"name: Fail the run if the check found a problem\n\s+if: steps\.check\.outcome == 'failure'")

    def test_the_alert_runs_when_the_run_fails_is_cancelled_or_times_out(self):
        head, _ = _named_step(self.text, "Open an issue")
        self.assertIn("        if: failure() || cancelled()\n", head)

    def test_the_output_folder_is_ignored_by_git_and_the_workflow_and_script_agree(self):
        _, script = _named_step(self.text, "Check each pin is on its board")
        (out_dir,) = re.findall(r"--out-dir (\S+)", script)
        self.assertEqual(out_dir, vp._parser().get_default("out_dir"))
        self.assertTrue(out_dir.startswith("LOGS/"), out_dir)
        self.assertIn("LOGS/", (REPO / ".gitignore").read_text(encoding="utf-8").splitlines())
        alert = _named_step(self.text, "Open an issue")[1]
        self.assertEqual(set(re.findall(r"cat (\S+?)/(?:kind|slugs|body\.md)", alert)), {out_dir})

    def test_the_inputs_reach_the_script_as_arguments(self):
        """Run the check step's own script in bash with python3 replaced by a
        recorder, then hand what it recorded to the script's real argument parser."""
        bash, why = _bash_for_workflow_steps()
        if not bash:
            self.skipTest(why)
        import subprocess
        _, script = _named_step(self.text, "Check each pin is on its board")
        for since in ("2026-11-05T12:00:00Z", ""):
            with self.subTest(since=since), tempfile.TemporaryDirectory() as d:
                out = Path(d) / "args"
                shim = f'python3() {{ printf "%s\\n" "$@" > "{out.as_posix()}"; }}\n'
                env = dict(os.environ, SINCE=since, SETTLE_MINUTES="7", RETRY_WAIT_MINUTES="3")
                proc = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", shim + script],
                                      cwd=d, capture_output=True, text=True, env=env)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                argv = out.read_text(encoding="utf-8").split("\n")[:-1]
                self.assertEqual(argv, ["verify_pins.py", "--since", since, "--settle-minutes", "7",
                                        "--retry-wait-minutes", "3", "--out-dir", "LOGS/pin-verify"])
                args = vp._parser().parse_args(argv[1:])
                self.assertEqual((args.settle_minutes, args.retry_wait_minutes), (7, 3))
                self.assertEqual(args.since, vp._since(since))


class _PinVerifyIssue(_AlertIssueStep):
    WORKFLOW = "pin-verify.yml"
    STEP = "Open an issue"
    KIND = BODY = SLUGS = None

    def prepare(self, tmp):
        out = Path(tmp) / "LOGS" / "pin-verify"
        out.mkdir(parents=True)
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
            out = Path(d) / "LOGS" / "pin-verify"
            out.mkdir(parents=True)
            (out / "kind").write_text("missing", encoding="utf-8")
            (out / "slugs").write_text(hostile, encoding="utf-8")
            (out / "body.md").write_text("x", encoding="utf-8")
            proc, calls = _run_alert_step(self, self.script, {"RUN_URL": self.RUN_URL}, d)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["LOGS", "gh_args"])
        self.assertTrue(self.arg(self.creates(calls)[0], "--title").endswith(": " + hostile))

    def test_a_big_batch_still_gets_a_title_under_github_s_limit(self):
        results = [{"pin": pin(slug=f"best-{c}-{'x' * 38}"), "status": "missing", "detail": "x"} for c in "abcdefgh"]
        report = vp.build_report(results)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "LOGS" / "pin-verify"
            out.mkdir(parents=True)
            (out / "kind").write_text(report["kind"], encoding="utf-8")
            (out / "slugs").write_text(report["slugs"], encoding="utf-8")
            (out / "body.md").write_text(report["body"], encoding="utf-8")
            proc, calls = _run_alert_step(self, self.script, {"RUN_URL": self.RUN_URL}, d)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        title = self.arg(self.creates(calls)[0], "--title")
        self.assertLess(len(title), 256, len(title))
        self.assertIn("(+5 more)", title)


class TestPinCheckCouldNotCheckIssue(_PinVerifyIssue, unittest.TestCase):
    TITLE_RE = r"^⚠️ Could not check pins on \d{4}-\d{2}-\d{2}: best-a$"
    KIND, SLUGS = "unchecked", "best-a"
    BODY = "Could not check."


class TestPinCheckDiedBeforeReportIssue(_PinVerifyIssue, unittest.TestCase):
    """A setup step failed, or the run was cancelled or timed out, so no report
    files exist: still one honest issue."""
    TITLE_RE = r"^⚠️ Could not check pins on \d{4}-\d{2}-\d{2}: unknown slugs$"
    KIND = SLUGS = BODY = ""

    def prepare(self, tmp):
        pass

    def test_the_body_says_nothing_was_confirmed_and_names_the_ways_it_stops(self):
        _, body = self.opened()
        self.assertIn("No pin was confirmed or ruled out", body)
        self.assertIn("cancelled or timed out", body)


# ---------------------------------------------------------------------------
# The description the check looks for, and the steps in the issue
# ---------------------------------------------------------------------------
def image_key(url):
    return post_pins.pin_ledger.image_key(url)


class _PostPinsRepo(unittest.TestCase):
    """A scratch repo dir post_pins.main() can run in, with every network and git
    path stubbed, as test_pin_failure_exit.py does."""

    SLUG = "best-lickable-cat-treats"
    IMAGE = "https://happypetproductreviews.com/assets/images/pins/best-lickable-cat-treats.jpg"
    QUEUE_DESC = "Queue file description for the lickable treats pin, used only without a post."

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.queue = self.repo / "_pin_queue"
        (self.queue / "sent").mkdir(parents=True)
        (self.queue / ".fired").mkdir()
        self.posts = self.repo / "_posts"
        self.posts.mkdir()

    def queue_json(self):
        return json.dumps({"slug": self.SLUG, "title": "Lickable cat treats", "article_url": "https://x/a/",
                           "description": self.QUEUE_DESC, "image_url": self.IMAGE, "species": "cat",
                           "topical_sheet": "HAPPYPET_SHEET_ID_FOOD"})

    def write_post(self, description):
        text = f'---\ntitle: "Best"\ndescription: "{description}"\n---\n'
        (self.posts / f"2026-10-05-{self.SLUG}.md").write_bytes(text.encode("utf-8"))

    def run_main(self, *argv):
        """(exit code, [(event, payload), ...]) from post_pins.main() in this repo."""
        import generate_pin_images
        from unittest.mock import MagicMock
        fired = []

        def fire(event, payload, maker_key):
            fired.append((event, payload))
            return True

        with patch.object(post_pins, "REPO_DIR", self.repo), \
             patch.object(post_pins, "LOG_PATH", self.repo / "test.log"), \
             patch.object(post_pins, "brain_get_secret", return_value="k"), \
             patch.object(post_pins, "check_url_live", return_value=True), \
             patch.object(post_pins, "check_image_has_content", return_value=True), \
             patch.object(post_pins.time, "sleep", lambda *_: None), \
             patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
             patch.object(post_pins, "fire_webhook", side_effect=fire), \
             patch.object(generate_pin_images, "POSTS_DIR", self.posts), \
             patch.object(sys, "argv", ["post_pins.py", *argv]):
            try:
                post_pins.main()
                code = 0
            except SystemExit as exc:
                code = 0 if exc.code is None else exc.code
        return code, fired


class TestRepoDescriber(_PostPinsRepo, _NoNetwork):
    """repo_describer must return what post_pins.py actually sends."""

    def describer(self):
        import generate_pin_images
        with patch.object(generate_pin_images, "POSTS_DIR", self.posts):
            return vp.repo_describer(str(self.repo))

    def sent_by_post_pins(self):
        """The description post_pins.main() puts in the webhook payload for the slug."""
        (self.queue / f"{self.SLUG}.json").write_text(self.queue_json(), encoding="utf-8")
        code, fired = self.run_main("--slugs", self.SLUG)
        self.assertEqual(code, 0)
        self.assertTrue(fired, "post_pins fired nothing")
        return fired[0][1]["description"]

    def test_the_posts_front_matter_description_wins(self):
        self.write_post("The post's own description, long enough to be matched in a feed.")
        (self.queue / f"{self.SLUG}.json").write_text(self.queue_json(), encoding="utf-8")
        got = self.describer()(self.SLUG)
        self.assertEqual(got, "The post's own description, long enough to be matched in a feed.")
        self.assertEqual(got, self.sent_by_post_pins())

    def test_with_no_post_the_queue_files_description_is_used_top_folder_then_sent(self):
        top = self.queue / f"{self.SLUG}.json"
        top.write_text(self.queue_json(), encoding="utf-8")
        self.assertEqual(self.describer()(self.SLUG), self.QUEUE_DESC)
        self.assertEqual(self.describer()(self.SLUG), self.sent_by_post_pins())
        top.rename(self.queue / "sent" / f"{self.SLUG}.json")       # where Stage 3 leaves it
        self.assertEqual(self.describer()(self.SLUG), self.QUEUE_DESC)

    def test_a_long_description_is_capped_exactly_as_post_pins_caps_it(self):
        words = " ".join(f"word{n}" for n in range(400))
        self.write_post(words)
        got = self.describer()(self.SLUG)
        self.assertLessEqual(len(got), post_pins.DESC_MAX)
        self.assertTrue(words.startswith(got))
        self.assertFalse(got.endswith(" "))
        self.assertEqual(got, self.sent_by_post_pins())

    def test_nothing_anywhere_is_none_and_an_empty_description_is_none(self):
        self.assertIsNone(self.describer()("best-no-such-slug"))
        self.write_post("")
        self.assertIsNone(self.describer()(self.SLUG))

    def test_a_broken_queue_file_is_none_not_a_crash(self):
        (self.queue / "sent" / f"{self.SLUG}.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.describer()(self.SLUG))


class TestRecoverySteps(_PostPinsRepo, _NoNetwork):
    """Each claim in the issue's repost steps, run through post_pins.main()."""

    def setUp(self):
        super().setUp()
        self.key = image_key(self.IMAGE)
        self.ledger_path = self.queue / ".fired" / ".pinned.json"
        self.ledger_path.write_text(json.dumps({self.SLUG: {self.key: {
            "happypet_pin_cats": "2026-11-02", "happypet_pin_food": "2026-11-02"}}}), encoding="utf-8")
        for name in (f"{self.SLUG}.fired", f"{self.SLUG}.happypet_pin_cats.fired",
                     f"{self.SLUG}.happypet_pin_food.fired"):
            (self.queue / ".fired" / name).write_text(FIRED.isoformat(), encoding="utf-8")
        (self.queue / "sent" / f"{self.SLUG}.json").write_text(self.queue_json(), encoding="utf-8")

    def move_queue_file_back(self):
        (self.queue / "sent" / f"{self.SLUG}.json").rename(self.queue / f"{self.SLUG}.json")

    def set_ledger(self, ledger):
        self.ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    def delete_cats_entry(self):
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        del ledger[self.SLUG][self.key]["happypet_pin_cats"]
        self.set_ledger(ledger)

    def rerun(self):
        return self.run_main("--slugs", self.SLUG, "--force")

    def test_after_stage_3_a_forced_rerun_does_nothing_and_looks_green(self):
        self.assertEqual(self.rerun(), (0, []))

    def test_editing_the_ledger_alone_still_does_nothing_and_looks_green(self):
        self.delete_cats_entry()
        self.assertEqual(self.rerun(), (0, []))

    def test_moving_the_queue_file_back_alone_is_refused_with_exit_6(self):
        self.move_queue_file_back()
        code, fired = self.rerun()
        self.assertEqual((code, fired), (6, []))
        self.assertEqual(code, post_pins.pin_ledger.REPEAT_REFUSED_EXIT_CODE)

    def test_both_steps_fire_only_the_removed_board_with_the_original_description(self):
        self.delete_cats_entry()
        self.move_queue_file_back()
        code, fired = self.rerun()
        self.assertEqual(code, 0)
        self.assertEqual([event for event, _ in fired], ["happypet_pin_cats"])
        self.assertEqual(fired[0][1]["description"], self.QUEUE_DESC)
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(ledger[self.SLUG][self.key]), ["happypet_pin_cats", "happypet_pin_food"])

    def test_the_rewritten_sentinel_holds_a_new_time_the_next_sweep_picks_up(self):
        self.delete_cats_entry()
        self.move_queue_file_back()
        self.rerun()
        stamp = (self.queue / ".fired" / f"{self.SLUG}.happypet_pin_cats.fired").read_text(encoding="utf-8")
        self.assertGreater(dt.datetime.fromisoformat(stamp), FIRED + dt.timedelta(days=1))
        recent = vp.find_fired_pins(self.queue / ".fired", FIRED + dt.timedelta(days=1))
        self.assertEqual([p["event"] for p in recent], ["happypet_pin_cats"])

    def test_leaving_an_empty_image_key_breaks_the_ledger_so_the_step_says_to_delete_it(self):
        self.set_ledger({self.SLUG: {self.key: {}}})
        self.move_queue_file_back()
        self.assertEqual(self.rerun(), (1, []))

    def test_deleting_the_empty_image_and_slug_keys_lets_every_unrecorded_board_fire(self):
        self.set_ledger({})
        self.move_queue_file_back()
        code, fired = self.rerun()
        self.assertEqual(code, 0)
        self.assertEqual(sorted(event for event, _ in fired), ["happypet_pin_cats", "happypet_pin_food"])


# ---------------------------------------------------------------------------
# The watchdog on the check, in slot-watchdog.yml
# ---------------------------------------------------------------------------
class TestSlotWatchdogPinCheck(_AlertIssueStep, unittest.TestCase):
    """slot-watchdog.yml's last step: no run of pin-verify.yml created in the last
    3 hours opens one issue. It shares GitHub's scheduler with the check, so it
    cannot catch a total scheduler outage; that limit is in the step's comment."""

    WORKFLOW = "slot-watchdog.yml"
    STEP = "Pin check not running — create GitHub issue"
    TITLE_RE = r"^⚠️ Pin check not running on \d{4}-\d{2}-\d{2}$"

    # gh stand-in: `gh api` answers $API_ANSWER (or fails), issue list answers $OPEN_ISSUES.
    GH = ('gh() {\n'
          '  printf "%s\\n" "$@" >> "$GH_ARGS"; echo "' + GH_CALL_END + '" >> "$GH_ARGS"\n'
          '  if [ "$1" = "api" ]; then [ -n "$API_FAILS" ] && return 1; printf "%s\\n" "$API_ANSWER"; return 0; fi\n'
          '  if [ "$1 $2" = "issue list" ]; then [ -n "$OPEN_ISSUES" ] && printf "%s\\n" "$OPEN_ISSUES"; fi\n'
          '  return 0\n}\n')

    def scenario_env(self):
        return {"REPO_URL": "https://github.com/DMoneyOH/HappyPet"}

    def run_api(self, answer="", api_fails=False, open_issues=""):
        with tempfile.TemporaryDirectory() as d:
            env = {"RUN_URL": self.RUN_URL, "API_ANSWER": answer, "API_FAILS": "1" if api_fails else "",
                   **self.scenario_env()}
            return _run_alert_step(self, self.GH + self.script, env, d,
                                   open_issues=[open_issues] if open_issues else ())

    # The inherited checks assume `issue list` is the first gh call; here `gh api` is.
    def test_a_failure_opens_one_issue_naming_the_run(self):
        proc, calls = self.run_api("0")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual([c[:2] for c in calls], [["api", calls[0][1]], ["issue", "list"], ["issue", "create"]])
        (create,) = self.creates(calls)
        self.assertEqual(self.arg(create, "--assignee"), "DMoneyOH")
        self.assertEqual(self.arg(create, "--repo"), "DMoneyOH/HappyPet")
        body = self.body(create)
        for needle in (self.RUN_URL, "Reported: ", "_Auto-generated by slot-watchdog.yml_",
                       "https://github.com/DMoneyOH/HappyPet/actions/workflows/pin-verify.yml",
                       "cannot catch a total scheduler outage"):
            self.assertIn(needle, body)

    def test_an_open_issue_with_the_same_title_stops_a_second(self):
        title, _ = self.opened()
        proc, calls = self.run_api("0", open_issues=title)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.creates(calls), [])
        self.assertIn("already open", proc.stdout)

    def test_the_step_asks_for_runs_created_in_the_last_three_hours(self):
        import datetime
        proc, calls = self.run_api("5")
        api = calls[0]
        self.assertEqual(api[0], "api")
        self.assertEqual(api[2:], ["--jq", ".total_count"])
        m = re.fullmatch(r"repos/DMoneyOH/HappyPet/actions/workflows/pin-verify\.yml/runs"
                         r"\?created=%3E%3D(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)&per_page=1", api[1])
        self.assertIsNotNone(m, api[1])
        since = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        age = datetime.datetime.now(UTC) - since
        self.assertLess(abs(age - datetime.timedelta(hours=3)), datetime.timedelta(minutes=5), age)

    def test_runs_in_the_window_open_nothing(self):
        for answer in ("1", "3", "24"):
            with self.subTest(count=answer):
                proc, calls = self.run_api(answer)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual([c[0] for c in calls], ["api"])
                self.assertIn(f"ran {answer} time(s)", proc.stdout)

    def test_no_runs_an_unreadable_answer_or_a_failed_call_all_open_an_issue(self):
        """Silence must not read as healthy: only a positive count is a pass."""
        for label, kw in (("zero", {"answer": "0"}), ("empty", {"answer": ""}), ("null", {"answer": "null"}),
                          ("junk", {"answer": "not a number"}), ("negative", {"answer": "-1"}),
                          ("call fails", {"api_fails": True})):
            with self.subTest(case=label):
                proc, calls = self.run_api(**kw)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(len(self.creates(calls)), 1, calls)

    def test_it_runs_even_when_the_slot_step_failed_and_reads_the_real_workflow_names(self):
        head, _ = _named_step(self.workflow, self.STEP)
        self.assertIn("        if: always()\n", head)
        pin_verify = (WORKFLOWS / "pin-verify.yml").read_text(encoding="utf-8")
        self.assertIn("pin-verify.yml/runs", self.script)
        self.assertEqual(re.findall(r"^    - cron: '([^']*)'$", pin_verify, re.M), ["23 * * * *"])
        self.assertIn("date -u -d '3 hours ago'", self.script, "three hourly runs fit the window")


if __name__ == "__main__":
    unittest.main()
