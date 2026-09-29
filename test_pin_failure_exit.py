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

import post_pins as pp  # noqa: E402

QUEUE = {"title": "Best Pet Thing", "article_url": "https://x/",
         "description": "d", "image_url": "https://x/a.jpg",
         "species": "dog", "topical_sheet": "HAPPYPET_SHEET_ID_FOOD"}


class TestPostPinsExitCode(unittest.TestCase):

    def _run(self, argv, queued=(), sent=(), fired=(), fire_ok=True, live=True):
        """Drive the real main() in a scratch repo dir. Returns (exit code,
        events handed to fire_webhook)."""
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            qdir = Path(tmp) / "_pin_queue"
            (qdir / "sent").mkdir(parents=True)
            (qdir / ".fired").mkdir()
            for stem in queued:
                (qdir / f"{stem}.json").write_text(
                    json.dumps(dict(QUEUE, slug=stem)), encoding="utf-8")
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

    def test_post_step_continues_on_error_and_has_an_id(self):
        post = self._find(lambda n, b: "python3 post_pins.py" in b, "post step")
        self.assertIn("\n        id: post\n", self.steps[post])
        self.assertIn("\n        continue-on-error: true\n", self.steps[post])

    def test_assert_step_fails_on_the_post_outcome(self):
        name = self._find(lambda n, b: "steps.post.outcome" in b, "assert step")
        block = self.steps[name]
        self.assertIn("\n        if: steps.post.outcome == 'failure'\n", block)
        self.assertIn("exit 1", block)
        self.assertNotIn("continue-on-error", block)

    def test_step_order_keeps_sheets_and_consume_before_assert_before_alert(self):
        post = self._find(lambda n, b: "python3 post_pins.py" in b, "post step")
        assert_step = self._find(lambda n, b: "steps.post.outcome" in b, "assert step")
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
