#!/usr/bin/env python3
"""
test_pin_failure_exit.py -- post_pins.py must exit nonzero on a failed or
missing pin, and pin.yml must turn that into a red run without skipping the
Sheets and consume steps.

Before this, post_pins counted `failed` but always exited 0, and a requested
slug with no queue file logged "No queued pins found" and exited 0, so a pin
that never reached Pinterest finished green and pin.yml's failure email never
fired.

Kept out of test_pipeline.py on purpose: that file has several branches
editing it at once. Every driven main() stubs all network and git paths.

Run: python3 -m pytest test_pin_failure_exit.py -q
"""

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

import pin_ledger  # noqa: E402
import post_pins as pp  # noqa: E402

QUEUE = {"title": "Best Pet Thing", "article_url": "https://x/",
         "description": "d", "image_url": "https://x/a.jpg",
         "species": "dog", "topical_sheet": "HAPPYPET_SHEET_ID_FOOD"}
MISSING = object()   # _run(pinned=MISSING): no ledger file at all


class TestPostPinsExitCode(unittest.TestCase):

    def _run(self, argv, queued=(), sent=(), fired=(), fire_ok=True, live=True,
             pinned=None, image=None, ledger_out=None, save_fails_on=()):
        """Drive the real main() in a scratch repo dir. Returns (exit code,
        events handed to fire_webhook).

        fired: stems ("a" -> a.fired) or stem.event ("a.happypet_pin_dogs").
        fire_ok: True/False for every event, or the set of events that succeed.
        pinned: the no-repeat ledger to start from; {} when None, MISSING for no
          file, a str written raw. image: overrides the queued image_url.
        ledger_out: gets the ledger left on disk (None if there is none).
        save_fails_on: 1-based numbers of the pin_ledger.save calls that raise.
        """
        calls = []
        saves = []
        real_save = pin_ledger.save

        def _save(path, ledger):
            saves.append(1)
            if len(saves) in save_fails_on:
                raise OSError("disk full")
            return real_save(path, ledger)

        def _fire(ev, *a):
            calls.append(ev)
            return fire_ok if isinstance(fire_ok, bool) else ev in fire_ok

        with tempfile.TemporaryDirectory() as tmp:
            qdir = Path(tmp) / "_pin_queue"
            (qdir / "sent").mkdir(parents=True)
            (qdir / ".fired").mkdir()
            for stem in queued:
                (qdir / f"{stem}.json").write_text(
                    json.dumps(dict(QUEUE, slug=stem, image_url=image or QUEUE["image_url"])),
                    encoding="utf-8")
            ledger = qdir / ".fired" / pp.LEDGER_NAME
            if pinned is None:
                pinned = {}
            if pinned is not MISSING:
                ledger.write_text(pinned if isinstance(pinned, str) else json.dumps(pinned),
                                  encoding="utf-8")
            for stem in sent:
                (qdir / "sent" / f"{stem}.json").write_text(
                    json.dumps(dict(QUEUE, slug=stem)), encoding="utf-8")
            for stem in fired:
                (qdir / ".fired" / f"{stem}.fired").write_text("t", encoding="utf-8")
            with patch.object(pp, "REPO_DIR", Path(tmp)), \
                 patch.object(pp, "LOG_PATH", Path(tmp) / "test.log"), \
                 patch.object(pp, "brain_get_secret", return_value="k"), \
                 patch.object(pp, "check_url_live", return_value=live), \
                 patch.object(pp, "check_image_has_content", return_value=True), \
                 patch.object(pp.time, "sleep", lambda *_: None), \
                 patch("subprocess.run",
                       return_value=MagicMock(returncode=0, stdout="", stderr="")), \
                 patch.object(pp, "fire_webhook", side_effect=_fire), \
                 patch.object(pp.pin_ledger, "save", side_effect=_save), \
                 patch.object(sys, "argv", ["post_pins.py"] + list(argv)):
                try:
                    pp.main()
                    code = 0
                except SystemExit as exc:
                    code = 0 if exc.code is None else exc.code
            if ledger_out is not None:
                ledger_out.append(json.loads(ledger.read_text(encoding="utf-8"))
                                  if ledger.exists() else None)
        return code, calls

    # --- must exit 1 -------------------------------------------------------

    def test_a_failed_fire_exits_1(self):
        code, calls = self._run(["--slugs", "a"], queued=["a"], fire_ok=False)
        self.assertEqual(calls, ["happypet_pin_dogs", "happypet_pin_food"],
                         "fire_webhook was not reached -- the test proved nothing")
        self.assertEqual(code, 1)

    def test_a_failed_fire_exits_1_on_an_unfiltered_run_too(self):
        code, calls = self._run([], queued=["a"], fire_ok=False)
        self.assertTrue(calls)
        self.assertEqual(code, 1)

    def test_an_article_not_live_yet_exits_1(self):
        code, calls = self._run(["--slugs", "a"], queued=["a"], live=False)
        self.assertEqual(calls, [])
        self.assertEqual(code, 1)

    def test_a_requested_slug_with_no_queue_file_and_no_sentinel_exits_1(self):
        code, calls = self._run(["--slugs", "a"])
        self.assertEqual(calls, [])
        self.assertEqual(code, 1)

    def test_a_slug_moved_to_sent_without_a_sentinel_exits_1(self):
        """The lost-pin shape: push_pins_to_sheets moved the queue file to sent/
        although the fire failed, so no all-events sentinel exists."""
        code, _ = self._run(["--slugs", "a"], sent=["a"])
        self.assertEqual(code, 1)

    def test_one_missing_slug_does_not_stop_the_others_but_still_exits_1(self):
        code, calls = self._run(["--slugs", "a,b"], queued=["a"])
        self.assertEqual(calls, ["happypet_pin_dogs", "happypet_pin_food"])
        self.assertEqual(code, 1)

    def test_a_missing_slug_exits_1_in_dry_run(self):
        """Dry-run changes nothing about whether a queue file exists."""
        code, calls = self._run(["--slugs", "a", "--dry-run"])
        self.assertEqual(calls, [])
        self.assertEqual(code, 1)

    # --- must stay 0 (inverse direction) ------------------------------------

    def test_a_successful_fire_exits_0(self):
        code, calls = self._run(["--slugs", "a"], queued=["a"])
        self.assertEqual(calls, ["happypet_pin_dogs", "happypet_pin_food"])
        self.assertEqual(code, 0)

    def test_dry_run_over_a_queued_slug_exits_0(self):
        code, calls = self._run(["--slugs", "a", "--dry-run"], queued=["a"])
        self.assertEqual(calls, [])
        self.assertEqual(code, 0)

    def test_dry_run_unfiltered_exits_0(self):
        code, calls = self._run(["--dry-run"], queued=["a", "b"])
        self.assertEqual(calls, [])
        self.assertEqual(code, 0)

    def test_rerun_skip_of_an_already_fired_slug_exits_0(self):
        code, calls = self._run(["--slugs", "a"], queued=["a"], fired=["a"])
        self.assertEqual(calls, [])
        self.assertEqual(code, 0)

    def test_rerun_after_consume_moved_the_slug_to_sent_exits_0(self):
        """Normal rerun: fired, then push_pins moved the file to sent/."""
        code, calls = self._run(["--slugs", "a"], sent=["a"], fired=["a"])
        self.assertEqual(calls, [])
        self.assertEqual(code, 0)

    def test_an_unfiltered_run_on_an_empty_queue_exits_0(self):
        code, calls = self._run([])
        self.assertEqual(calls, [])
        self.assertEqual(code, 0)


PINNED_A = {"a": {"x/a.jpg": {"*": "2026-05-01"}}}   # QUEUE's image, seeded as out everywhere
DOGS, FOOD = "happypet_pin_dogs", "happypet_pin_food"     # QUEUE's two boards


class TestNoRepeatGuard(unittest.TestCase):
    """A board a (post, image) pair has gone out on is never fired again with that
    image, with or without --force or the .fired sentinels. A run whose every
    board was refused exits 6; a failed or missing pin (exit 1) outranks it."""

    _run = TestPostPinsExitCode._run

    def _run_logged(self, *a, **k):
        out = io.StringIO()
        with patch("sys.stdout", out):
            code, calls = self._run(*a, **k)
        return code, calls, out.getvalue()

    def test_force_cannot_re_fire_a_pinned_image(self):
        code, calls = self._run(["--slugs", "a", "--force"], queued=["a"], fired=["a"],
                                pinned=PINNED_A)
        self.assertEqual(calls, [])
        self.assertEqual(code, pin_ledger.REPEAT_REFUSED_EXIT_CODE)
        self.assertEqual(code, 6)

    def test_clearing_the_sentinel_does_not_reopen_a_pinned_image(self):
        code, calls = self._run(["--slugs", "a"], queued=["a"], pinned=PINNED_A)
        self.assertEqual((code, calls), (6, []))

    def test_every_spelling_of_the_same_image_is_refused(self):
        for image in ("https://x/a.jpg?v=3", "http://X/a.jpg", " https://x/a.jpg#top",
                      "https://x/%61.jpg"):
            with self.subTest(image=image):
                code, calls = self._run(["--slugs", "a", "--force"], queued=["a"],
                                        pinned=PINNED_A, image=image)
                self.assertEqual((code, calls), (6, []))

    def test_dry_run_reports_the_refusal_too(self):
        code, calls = self._run(["--slugs", "a", "--dry-run"], queued=["a"], pinned=PINNED_A)
        self.assertEqual((code, calls), (6, []))

    def test_a_failed_pin_outranks_a_refusal(self):
        code, _ = self._run(["--slugs", "a,b"], queued=["a"], pinned=PINNED_A)
        self.assertEqual(code, 1, "b is missing, and that must not read as only a refusal")

    # --- a publication that reached some boards (per-board records) -----------

    def test_a_partial_publish_protects_the_boards_it_reached(self):
        out = []
        code, calls = self._run(["--slugs", "a"], queued=["a"], fire_ok={DOGS}, ledger_out=out)
        self.assertEqual((code, calls), (1, [DOGS, FOOD]))
        self.assertEqual(list(out[0]["a"]["x/a.jpg"]), [DOGS],
                         "the failed board must be un-recorded so it can retry")
        for argv, fired in ((["--slugs", "a", "--force"], [f"a.{DOGS}"]),   # forced retry
                            (["--slugs", "a"], [])):                        # sentinels cleared
            with self.subTest(argv=argv, fired=fired):
                code, calls = self._run(argv, queued=["a"], fired=fired, pinned=out[0])
                self.assertEqual((code, calls), (0, [FOOD]), "only the failed board may fire")

    def test_a_pair_out_on_every_board_is_refused_even_with_its_sentinels_gone(self):
        pinned = {"a": {"x/a.jpg": {DOGS: "2026-10-01", FOOD: "2026-10-01"}}}
        code, calls = self._run(["--slugs", "a", "--force"], queued=["a"], pinned=pinned)
        self.assertEqual((code, calls), (6, []))

    # --- the ledger record cannot be saved ------------------------------------

    def test_a_board_that_cannot_be_recorded_is_not_fired(self):
        out = []
        code, calls, log = self._run_logged(["--slugs", "a"], queued=["a"],
                                            save_fails_on={1}, ledger_out=out)
        self.assertEqual((code, calls), (1, []))
        self.assertEqual(out[0], {})
        self.assertIn("DONE -- 0 pinned, 1 failed", log)

    def test_a_save_failure_mid_publish_stops_it_and_a_later_force_reposts_nothing(self):
        out = []
        code, calls, log = self._run_logged(["--slugs", "a"], queued=["a"],
                                            save_fails_on={2}, ledger_out=out)
        self.assertEqual((code, calls), (1, [DOGS]), "food could not be recorded, so it must not fire")
        self.assertIn("DONE -- 0 pinned, 1 failed", log, "one slug, counted once")
        code, calls = self._run(["--slugs", "a", "--force"], queued=["a"], pinned=out[0])
        self.assertEqual((code, calls), (0, [FOOD]), "dogs is live and recorded; it must not repeat")

    def test_a_failed_board_that_cannot_be_un_recorded_stays_refused(self):
        out = []
        code, calls = self._run(["--slugs", "a"], queued=["a"], fire_ok={FOOD},
                                save_fails_on={2}, ledger_out=out)
        self.assertEqual(code, 1)
        self.assertEqual(sorted(out[0]["a"]["x/a.jpg"]), [DOGS, FOOD],
                         "fails closed: an un-record that did not save leaves the board refused")

    # --- inverse direction ----------------------------------------------------

    def test_the_same_post_with_a_fresh_image_is_pinned_and_recorded(self):
        out = []
        code, calls = self._run(["--slugs", "a", "--force"], queued=["a"], fired=["a"],
                                pinned=PINNED_A, image="https://x/a-v2.jpg", ledger_out=out)
        self.assertEqual((code, calls), (0, [DOGS, FOOD]))
        self.assertEqual(out[0]["a"]["x/a.jpg"], {"*": "2026-05-01"})
        self.assertEqual(sorted(out[0]["a"]["x/a-v2.jpg"]), [DOGS, FOOD])
        self.assertRegex(out[0]["a"]["x/a-v2.jpg"][DOGS], r"^\d{4}-\d{2}-\d{2}$")

    def test_a_first_pin_is_recorded_and_a_forced_rerun_of_it_is_refused(self):
        out = []
        code, _ = self._run(["--slugs", "a"], queued=["a"], ledger_out=out)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(out[0]["a"]["x/a.jpg"]), [DOGS, FOOD])
        code, calls = self._run(["--slugs", "a", "--force"], queued=["a"], pinned=out[0])
        self.assertEqual((code, calls), (6, []))

    # --- a missing or unusable ledger -----------------------------------------

    def test_a_missing_ledger_with_pin_history_fires_nothing(self):
        for history in ({"fired": ["b"]}, {"sent": ["b"]}):
            with self.subTest(history=history):
                code, calls = self._run(["--slugs", "a"], queued=["a"], pinned=MISSING, **history)
                self.assertEqual((code, calls), (1, []))

    def test_a_missing_ledger_on_a_first_ever_run_is_created(self):
        out = []
        code, calls = self._run(["--slugs", "a"], queued=["a"], pinned=MISSING, ledger_out=out)
        self.assertEqual((code, calls), (0, [DOGS, FOOD]))
        self.assertEqual(sorted(out[0]["a"]["x/a.jpg"]), [DOGS, FOOD])

    def test_an_unreadable_ledger_fires_nothing(self):
        for bad in ("{ torn", json.dumps({"a": {"X/a.jpg": {DOGS: "d"}}})):
            with self.subTest(bad=bad):
                code, calls = self._run(["--slugs", "a"], queued=["a"], pinned=bad)
                self.assertEqual((code, calls), (1, []))

    # --- the committed ledger -------------------------------------------------

    def test_every_post_with_pin_history_is_in_the_committed_ledger(self):
        """The seed: every slug with an all-events sentinel or a sent/ file has a
        ledger entry, so a forced re-pin of any past post is refused from day one."""
        qdir = REPO / "_pin_queue"
        ledger = pin_ledger.load(qdir / ".fired" / pp.LEDGER_NAME)
        history = {p.name[:-len(".fired")] for p in (qdir / ".fired").glob("*.fired")
                   if p.name.count(".") == 1}
        history |= {p.stem for p in (qdir / "sent").glob("*.json")}
        self.assertTrue(history, "no pin history found -- the test proved nothing")
        self.assertEqual(sorted(history - set(ledger)), [])

    def test_the_ledger_name_is_a_bare_dotfile(self):
        """Dot-named so refill_cli.taken_slugs skips it; a bare name so it lands in
        _pin_queue/.fired with the sentinels the pin commit carries."""
        self.assertTrue(pp.LEDGER_NAME.startswith("."))
        self.assertNotIn("/", pp.LEDGER_NAME)


# pin_ledger.py is mirrored byte-for-byte in a second pin pipeline whose tests pin
# the same digest (line endings normalized). Change both copies and both digests.
PIN_LEDGER_SHA256 = "e381ef56f743862a6a26515f7dba7bbb613237b7ddd7cfd4ca42cff2b4e58f8e"


class TestPinLedger(unittest.TestCase):
    """pin_ledger itself. Expected values are literals."""

    IMG = "https://raw.githubusercontent.com/o/r/main/pins/a.png"
    KEY = "raw.githubusercontent.com/o/r/main/pins/a.png"

    def test_the_mirrored_file_is_unchanged(self):
        data = (REPO / "pin_ledger.py").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha256(data).hexdigest(), PIN_LEDGER_SHA256)

    def test_the_same_image_is_recognized_in_every_spelling(self):
        for v in (self.IMG, "  " + self.IMG + "\n", self.IMG + "?v=2", self.IMG + "#f",
                  self.IMG.replace("https", "http"), self.IMG.replace("raw.github", "RAW.GitHub"),
                  self.IMG.replace(".com/", ".com:443/"), self.IMG.replace("/a.png", "/%61.png")):
            with self.subTest(v=v):
                self.assertEqual(pin_ledger.image_key(v), self.KEY)
                self.assertTrue(pin_ledger.already_pinned({"p": {self.KEY: {"b": "d"}}}, " p ", v))

    def test_a_different_image_or_post_is_not_a_repeat(self):
        ledger = {"p": {self.KEY: {"b": "d"}}}
        for post, img in (("p", self.IMG.replace("a.png", "A.png")),
                          ("p", self.IMG.replace("a.png", "a-v2.png")), ("q", self.IMG)):
            with self.subTest(post=post, img=img):
                self.assertFalse(pin_ledger.already_pinned(ledger, post, img))

    def test_board_records_and_the_any_board_wildcard(self):
        ledger = {"p": {self.KEY: {"b1": "d"}}, "q": {self.KEY: {"*": "d"}}}
        self.assertTrue(pin_ledger.already_pinned(ledger, "p", self.IMG, "b1"))
        self.assertFalse(pin_ledger.already_pinned(ledger, "p", self.IMG, "b2"))
        self.assertTrue(pin_ledger.already_pinned(ledger, "q", self.IMG, "b2"))

    def test_percent_encoding_is_canonical_so_keys_normalize_to_themselves(self):
        for url, key in (("https://x/a%2561.png", "x/a%2561.png"),
                         ("https://x/100%.png", "x/100%25.png"),
                         ("https://x/a b.png", "x/a%20b.png")):
            with self.subTest(url=url):
                self.assertEqual(pin_ledger.image_key(url), key)
                self.assertEqual(pin_ledger.image_key("//" + key), key)

    def test_an_unparsable_url_is_never_a_match_and_never_recorded(self):
        self.assertIsNone(pin_ledger.image_key("http://[bad/a.png"))
        self.assertFalse(pin_ledger.already_pinned({"p": {}}, "p", "http://[bad/a.png"))
        with self.assertRaises(ValueError):
            pin_ledger.add({}, "p", "http://[bad/a.png", "b", "d")

    def test_unusable_records_are_never_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l.json"
            for post, img, board in ((None, self.IMG, "b"), ("", self.IMG, "b"), ("p", "", "b"),
                                     ("p", None, "b"), ("p", self.IMG, ""), ("p", self.IMG, None)):
                with self.subTest(post=post, img=img, board=board), self.assertRaises(ValueError):
                    pin_ledger.record(path, post, img, board, "2026-10-17")
            self.assertFalse(path.exists())

    def test_record_round_trips_and_discard_prunes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l.json"
            pin_ledger.record(path, "p", self.IMG, "b1", "2026-10-01")
            pin_ledger.record(path, "p", self.IMG.replace("a.png", "b.png"), "b2", "2026-10-03")
            self.assertEqual(pin_ledger.load(path), {"p": {
                self.KEY: {"b1": "2026-10-01"},
                "raw.githubusercontent.com/o/r/main/pins/b.png": {"b2": "2026-10-03"}}})
            ledger = pin_ledger.load(path)
            pin_ledger.discard(ledger, "p", self.IMG, "b1")
            pin_ledger.discard(ledger, "p", self.IMG.replace("a.png", "b.png"), "b2")
            self.assertEqual(ledger, {})

    def test_an_unusable_or_unnormalized_ledger_raises_and_a_missing_one_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l.json"
            self.assertEqual(pin_ledger.load(path), {})
            for bad in ("{ torn", "[]", '{"p": ["x"]}', '{"p": {"x/a": {"b": 5}}}',
                        '{"p": {"x/a": {}}}', '{"p ": {"x/a": {"b": "d"}}}',
                        '{"p": {"X/a": {"b": "d"}}}', '{"p": {"x/%61": {"b": "d"}}}',
                        '{"p": {"x:443/a": {"b": "d"}}}', '{"p": {"x/a": {" b": "d"}}}'):
                path.write_text(bad, encoding="utf-8")
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    pin_ledger.load(path)


class TestPinWorkflowWiring(unittest.TestCase):
    """Static check of pin.yml: the post step may fail without skipping the
    later steps, and the job still ends red so 'Alert on failure' fires.
    A failing step only sets failure() for steps after it, so the assert must
    sit before the alert step."""

    def setUp(self):
        text = (REPO / ".github" / "workflows" / "pin.yml").read_text(encoding="utf-8")
        blocks = text.split("\n      - name: ")[1:]
        self.names = [b.splitlines()[0].strip() for b in blocks]
        self.steps = dict(zip(self.names, blocks))

    def _find(self, predicate, what):
        hits = [n for n, b in self.steps.items() if predicate(n, b)]
        self.assertEqual(len(hits), 1, f"expected exactly one {what}, got {hits}")
        return hits[0]

    def _assert_step(self):
        """The step that fails the job on the post outcome: the one gated by `if:` on it."""
        # Keyed on the `if:` line, not on any mention of the expression: the alert
        # step reads steps.post.outcome too (to word its issue) and is not this step.
        return self._find(lambda n, b: "\n        if: steps.post.outcome" in b, "assert step")

    def test_post_step_continues_on_error_and_has_an_id(self):
        post = self._find(lambda n, b: "python3 post_pins.py" in b, "post step")
        self.assertIn("\n        id: post\n", self.steps[post])
        self.assertIn("\n        continue-on-error: true\n", self.steps[post])

    def test_assert_step_fails_on_the_post_outcome(self):
        name = self._assert_step()
        block = self.steps[name]
        self.assertIn("\n        if: steps.post.outcome == 'failure'\n", block)
        self.assertIn("exit 1", block)
        self.assertNotIn("continue-on-error", block)

    def test_step_order_keeps_sheets_and_consume_before_assert_before_alert(self):
        post = self._find(lambda n, b: "python3 post_pins.py" in b, "post step")
        assert_step = self._assert_step()
        order = [post, "Push pins to Google Sheets", "Consume pending-slugs",
                 assert_step, "Alert on failure"]
        idx = [self.names.index(n) for n in order]
        self.assertEqual(idx, sorted(idx), f"step order wrong: {order}")

    def test_alert_step_still_keys_on_failure(self):
        self.assertIn("if: failure() && steps.resolve.outputs.nothing_to_pin != 'true'",
                      self.steps["Alert on failure"])

    # --- exit 6 (a refused repeat) is told apart from exit 1 (a failed pin) -----

    def test_post_step_keeps_the_exit_code_as_an_output(self):
        post = self._find(lambda n, b: "python3 post_pins.py" in b, "post step")
        block = self.steps[post]
        for needle in ("set +e\n", 'rc=$?\n', 'echo "rc=${rc}" >> "$GITHUB_OUTPUT"\n',
                       'exit "$rc"\n'):
            self.assertIn(needle, block)

    def test_assert_step_tells_a_refusal_from_a_failure(self):
        block = self.steps[self._assert_step()]
        self.assertIn('if [ "${{ steps.post.outputs.rc }}" = "6" ]; then', block)
        self.assertIn("refused a repeat (exit 6)", block)
        self.assertIn("failed or missing pin", block)
        self.assertIn("new image path", block)

    def test_alert_words_a_refusal_as_nothing_to_retry(self):
        block = self.steps["Alert on failure"]
        self.assertIn("RC: ${{ steps.post.outputs.rc }}", block)
        self.assertIn('elif [ "$POSTED" = "failure" ] && [ "$RC" = "6" ]; then', block)
        self.assertIn("Pin repeat refused on", block)
        self.assertIn("nothing to retry", block)
        self.assertIn("new image path", block)
        self.assertNotIn("set force to true only to re-fire", block)

    def test_force_input_no_longer_promises_a_re_fire(self):
        text = (REPO / ".github" / "workflows" / "pin.yml").read_text(encoding="utf-8")
        force = text.split("\n      force:\n", 1)[1].split("\n        default:", 1)[0]
        self.assertNotIn("Re-fire already-sent", force)
        self.assertIn("new image path", force)

    def test_ci_runs_this_file(self):
        ci = (REPO / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        self.assertIn("test_pin_failure_exit.py", ci)


if __name__ == "__main__":
    unittest.main()
