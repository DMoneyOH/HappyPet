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


class TestPostPinsExitCode(unittest.TestCase):

    def _run(self, argv, queued=(), sent=(), fired=(), fire_ok=True, live=True,
             pinned=None, image=None, ledger_out=None):
        """Drive the real main() in a scratch repo dir. Returns (exit code,
        events handed to fire_webhook). `pinned` is written as the no-repeat
        ledger first (a str is written raw); `image` overrides the queued
        image_url; the ledger left behind is appended to `ledger_out`."""
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            qdir = Path(tmp) / "_pin_queue"
            (qdir / "sent").mkdir(parents=True)
            (qdir / ".fired").mkdir()
            for stem in queued:
                (qdir / f"{stem}.json").write_text(
                    json.dumps(dict(QUEUE, slug=stem, image_url=image or QUEUE["image_url"])),
                    encoding="utf-8")
            ledger = qdir / ".fired" / ".pinned.json"
            if pinned is not None:
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
                 patch.object(pp, "fire_webhook",
                              side_effect=lambda ev, *a: calls.append(ev) or fire_ok), \
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


PINNED_A = {"a": {"x/a.jpg": "2026-05-01"}}   # what QUEUE's image normalizes to


class TestNoRepeatGuard(unittest.TestCase):
    """A (post, image) pair that has been pinned is never pinned again, with or
    without --force, and with or without its .fired sentinel. Exit 6 means a
    repeat was refused; a failed or missing pin (exit 1) outranks it."""

    _run = TestPostPinsExitCode._run

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

    # --- inverse direction ----------------------------------------------------

    def test_the_same_post_with_a_fresh_image_is_pinned_and_recorded(self):
        out = []
        code, calls = self._run(["--slugs", "a", "--force"], queued=["a"], fired=["a"],
                                pinned=PINNED_A, image="https://x/a-v2.jpg", ledger_out=out)
        self.assertEqual(calls, ["happypet_pin_dogs", "happypet_pin_food"])
        self.assertEqual(code, 0)
        self.assertEqual(set(out[0]["a"]), {"x/a.jpg", "x/a-v2.jpg"})
        self.assertRegex(out[0]["a"]["x/a-v2.jpg"], r"^\d{4}-\d{2}-\d{2}$")

    def test_a_first_pin_is_recorded_and_a_forced_rerun_of_it_is_refused(self):
        out = []
        code, _ = self._run(["--slugs", "a"], queued=["a"], ledger_out=out)
        self.assertEqual(code, 0)
        self.assertEqual({k: list(v) for k, v in out[0].items()}, {"a": ["x/a.jpg"]})
        code, calls = self._run(["--slugs", "a", "--force"], queued=["a"], pinned=out[0])
        self.assertEqual((code, calls), (6, []))

    def test_a_half_failed_publication_is_not_recorded_so_its_retry_still_runs(self):
        out = []
        code, _ = self._run(["--slugs", "a"], queued=["a"], fire_ok=False, ledger_out=out)
        self.assertEqual(code, 1)
        self.assertIsNone(out[0], "a failed fire must not mark the pair as pinned")

    def test_an_unreadable_ledger_fires_nothing(self):
        code, calls = self._run(["--slugs", "a"], queued=["a"], pinned="{ torn")
        self.assertEqual((code, calls), (1, []))

    # --- the committed ledger -------------------------------------------------

    def test_every_fired_post_in_the_repo_is_in_the_ledger(self):
        """The seed: every slug with an all-events sentinel has a ledger entry,
        so a forced re-pin of any past post is refused from day one."""
        fired_dir = REPO / "_pin_queue" / ".fired"
        ledger = pin_ledger.load(fired_dir / pp.LEDGER_NAME)
        fired = {p.name[:-len(".fired")] for p in fired_dir.glob("*.fired")
                 if p.name.count(".") == 1}
        self.assertTrue(fired, "no sentinels found -- the test proved nothing")
        self.assertEqual(sorted(fired - set(ledger)), [])

    def test_the_ledger_is_a_dotfile_inside_the_committed_sentinel_dir(self):
        """Inside _pin_queue/.fired so the sentinel commit carries it; dot-named so
        refill_cli.taken_slugs skips it; never a top-level _pin_queue/*.json,
        which main() would read as a queue file."""
        self.assertTrue(pp.LEDGER_NAME.startswith("."))
        self.assertNotIn("/", pp.LEDGER_NAME)


class TestPinLedger(unittest.TestCase):
    """pin_ledger itself. Mirrors the storage venture's tests/test_pin_ledger.py;
    expected values are literals."""

    IMG = "https://raw.githubusercontent.com/o/r/main/pins/a.png"
    KEY = "raw.githubusercontent.com/o/r/main/pins/a.png"

    def test_the_same_image_is_recognized_in_every_spelling(self):
        for v in (self.IMG, "  " + self.IMG + "\n", self.IMG + "?v=2", self.IMG + "#f",
                  self.IMG.replace("https", "http"), self.IMG.replace("raw.github", "RAW.GitHub"),
                  self.IMG.replace(".com/", ".com:443/"), self.IMG.replace("/a.png", "/%61.png")):
            with self.subTest(v=v):
                self.assertEqual(pin_ledger.image_key(v), self.KEY)
                self.assertTrue(pin_ledger.already_pinned({"p": {self.KEY: "d"}}, " p ", v))

    def test_a_different_image_or_post_is_not_a_repeat(self):
        ledger = {"p": {self.KEY: "d"}}
        for post, img in (("p", self.IMG.replace("a.png", "A.png")),
                          ("p", self.IMG.replace("a.png", "a-v2.png")), ("q", self.IMG)):
            with self.subTest(post=post, img=img):
                self.assertFalse(pin_ledger.already_pinned(ledger, post, img))

    def test_unusable_pairs_are_never_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l.json"
            for post, img in ((None, self.IMG), ("", self.IMG), ("p", ""), ("p", None)):
                with self.subTest(post=post, img=img), self.assertRaises(ValueError):
                    pin_ledger.record(path, post, img, "2026-10-17")
            self.assertFalse(path.exists())

    def test_record_round_trips_and_keeps_earlier_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l.json"
            pin_ledger.record(path, "p", self.IMG, "2026-10-01")
            pin_ledger.record(path, "p", self.IMG.replace("a.png", "b.png"), "2026-10-03")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"p": {
                self.KEY: "2026-10-01",
                "raw.githubusercontent.com/o/r/main/pins/b.png": "2026-10-03"}})

    def test_an_unusable_ledger_raises_and_a_missing_one_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l.json"
            self.assertEqual(pin_ledger.load(path), {})
            for bad in ("{ torn", "[]", '{"p": ["x"]}', '{"p": {"x": 5}}', '{" ": {}}'):
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

    def test_ci_runs_this_file(self):
        ci = (REPO / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        self.assertIn("test_pin_failure_exit.py", ci)


if __name__ == "__main__":
    unittest.main()
