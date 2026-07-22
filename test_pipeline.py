#!/usr/bin/env python3
"""
test_pipeline.py — HappyPet pipeline test harness
Runs locally before any GHA dispatch. If it fails locally, nothing gets dispatched.

Coverage per handover STEP 7:
  - Mocks OpenRouter and Gemini API calls
  - Generates a test article from a known products.json entry
  - Validates output contract (word count, affiliate link, no first-person, no em dashes)
  - Runs reviewer against the article, confirms valid JSON with all required keys
  - Checks fact-check runs against full article (not truncated)
  - Verifies Chewy brand gate correctly rejects a known mismatch

Run: python3 -m pytest test_pipeline.py -v
"""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PRODUCT = {
    "topic":         "best-dog-puzzle-toys",
    "title":         "Best Dog Puzzle Toys to Keep Smart Dogs Busy and Engaged",
    "keyword":       "best dog puzzle toys",
    "name":          "Outward Hound Nina Ottosson Dog Tornado",
    "affiliate_url": "https://amzn.to/3TestABC",
    "asin":          "B00AKOQYXY",
    "species":       "dog",
    "category":      "dog-toys",
    "format":        "roundup",
    "image":         "https://images-na.ssl-images-amazon.com/images/P/B00AKOQYXY.01.LZZZZZZZ.jpg",
    "price":         "12.99",
    "stars":         "4.5",
    "review_count":  "41205",
}

# A well-formed article that should pass the output contract
GOOD_ARTICLE = """
Puzzle toys have transformed how dog owners deal with boredom-driven destruction.
Instead of a chewed sofa corner, you get a focused dog working through rotating discs
and hidden compartments for twenty minutes straight.

## Quick Picks

- [Outward Hound Nina Ottosson Dog Tornado](https://amzn.to/3TestABC) -- best overall
- Trixie Activity Flip Board -- best for beginners
- KONG Classic Wobbler -- best for solo play

## Featured Pick: Outward Hound Nina Ottosson Dog Tornado

With 4.5 stars across more than 41,000 Amazon reviews, the Dog Tornado earns its place
as the top pick for interactive dog puzzle toys. The three-level disc system lets dogs
hide treats in twelve compartments, working out the rotation sequence to reach them.
The toy comes apart for easy dishwasher cleaning, which matters when using it daily.
At around 13 dollars, it sits in the sweet spot between budget toys that lose interest
fast and overengineered puzzles that confuse more than challenge.

**Pros:**
- Three difficulty levels in one toy
- Dishwasher-safe components
- Non-slip base keeps it from sliding during use

**Cons:**
- Larger dogs can flip it when frustrated
- Not suited for dogs that give up easily

## Additional Picks

The Trixie Activity Flip Board takes a different approach: dogs flip panels and slide
covers rather than rotate discs. That distinction matters for dogs who figure out the
Tornado too quickly. The board has five activity zones, each with a different mechanism,
so even persistent problem-solvers stay engaged for longer.

The KONG Classic Wobbler dispenses kibble as dogs bat it around, making it a strong
option for meal feeding rather than treat-based training. It works on hardwood floors
and tiles without excessive noise, and the wide base prevents tipping.

## Buying Guide

When evaluating dog puzzle toys, consider difficulty level, material durability,
and ease of cleaning. Toys with removable parts are easier to sanitize. Start with
a level-one puzzle and work upward. Dogs that master a toy too quickly lose interest.
A good puzzle should take five to fifteen minutes, not thirty seconds.

Durability matters more than price at the low end. Toys under eight dollars tend
to crack after two weeks of regular use. The sweet spot is twelve to twenty dollars
for a toy that holds up to daily sessions across several years.

## Why Mental Stimulation Matters

Dogs evolved to problem-solve. Without mental engagement, high-intelligence breeds
become destructive not from malice but from boredom. Fifteen minutes of focused
puzzle work is more tiring than an hour of running because it engages higher-order
thinking alongside the body.

Border collies, shepherds, and poodles benefit most, but the effect extends to mixed
breeds. Once a dog figures out the basic rotation mechanic, the shift from random
pawing to deliberate problem-solving is the sign of a toy working as intended.

## Frequently Asked Questions

**How often should dogs use puzzle toys?**
Most behaviorists suggest daily use, especially for high-energy breeds.

**Are puzzle toys safe for aggressive chewers?**
Standard puzzle toys are not designed for power chewers. Look for rubber or
reinforced toys if a dog destroys things regularly.

**At what age can puppies start using puzzle toys?**
Most puppies are ready around eight to ten weeks, starting with the simplest
food-dispensing designs before moving to rotating or sliding mechanisms.


Treats work better than kibble for initial puzzle training because dogs stay
motivated longer. Once a dog understands the mechanism, switching to kibble
for meals makes the toy sustainable as a daily feeding tool. Keep sessions
under twenty minutes to prevent frustration. If a dog walks away before
finishing, the puzzle is too hard. Move down a level and build back up.

Rotating between two or three puzzles prevents mastery from killing motivation.
Dogs that solve the same puzzle in under two minutes every session are ready
for a harder challenge, not just a different treat placement. The Tornado's
three-level system handles this internally, which is part of what makes it
worth the cost compared to single-level alternatives in the same price bracket.
## Final Thoughts

The [Outward Hound Nina Ottosson Dog Tornado](https://amzn.to/3TestABC) is the
right starting point for most dogs who need daily mental engagement without a large investment. It is affordable, durable, and the multi-level
design stays challenging longer than single-layer alternatives.
""".strip()

# An article that fails: first-person voice, em dashes, no affiliate link
BAD_ARTICLE = """
We tested all the puzzle toys and I found this one to be the best — it really stands out.
Our dogs loved it and my favorite feature is the rotating disc design.
""".strip()

REVIEWER_PASS_JSON = json.dumps({
    "pass":                True,
    "scores":              {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4},
    "affiliate_link_present": True,
    "em_dash_count":       0,
    "ai_patterns_found":   [],
    "flags":               [],
    "rewrite_instructions": "",
})

REVIEWER_FAIL_JSON = json.dumps({
    "pass":                False,
    "scores":              {"human_voice": 2, "warmth": 3, "readability": 3, "accuracy": 3},
    "affiliate_link_present": False,
    "em_dash_count":       1,
    "ai_patterns_found":   ["1: em dash found", "10: uses leverage"],
    "flags":               ["first-person voice detected", "em_dash_count=1"],
    "rewrite_instructions": "Remove first-person voice. Replace em dashes with commas.",
})


# ---------------------------------------------------------------------------
# Helper: minimal mock for http_post that returns a provider response
# ---------------------------------------------------------------------------

def _make_or_response(content: str) -> str:
    return json.dumps({
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage":   {"completion_tokens": len(content.split())},
    })


def _make_gemini_response(content: str) -> str:
    return json.dumps({
        "candidates": [{"content": {"parts": [{"text": content}]}}],
    })


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestOutputContract(unittest.TestCase):
    """validate_output() gates — P1"""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_good_article_passes_contract(self):
        # Should not raise
        self.gp.validate_output("generate", GOOD_ARTICLE, "best-dog-puzzle-toys")

    def test_empty_content_fails(self):
        with self.assertRaises(self.gp.GenerationStageError):
            self.gp.validate_output("generate", "", "test-slug")

    def test_too_short_fails(self):
        with self.assertRaises(self.gp.GenerationStageError):
            self.gp.validate_output("generate", "short text", "test-slug")

    def test_no_affiliate_link_tolerated_at_generate_gate(self):
        # Gate 1 (generate) tolerates a missing link -- the rewrite step injects it
        no_link = GOOD_ARTICLE.replace("https://amzn.to/3TestABC", "https://amazon.com/dp/B00AKOQYXY")
        self.gp.validate_output("generate", no_link, "test-slug")

    def test_amzn_to_link_passes_review_gate(self):
        # Backward compat: short amzn.to links still satisfy the publish gate
        self.gp.validate_output("review", GOOD_ARTICLE, "test-slug")

    def test_amazon_com_tagged_link_passes_review_gate(self):
        # A tag-bearing amazon.com/dp link is a valid affiliate link -- must pass
        # (regression: this is the shape refill_products.py now emits for 22/23 entries)
        art = GOOD_ARTICLE.replace(
            "https://amzn.to/3TestABC",
            "https://www.amazon.com/dp/B00AKOQYXY?tag=pawpicks04-20",
        )
        self.gp.validate_output("review", art, "test-slug")

    def test_expected_affiliate_url_present_passes_review_gate(self):
        # When the entry's exact affiliate_url is supplied, it must appear verbatim
        url = "https://www.amazon.com/dp/B00AKOQYXY?tag=pawpicks04-20"
        art = GOOD_ARTICLE.replace("https://amzn.to/3TestABC", url)
        self.gp.validate_output("review", art, "test-slug", affiliate_url=url)

    def test_missing_affiliate_link_fails_at_review_gate(self):
        # Gate 2 (post-review) is the publish gate: a truly missing link must raise
        no_link = GOOD_ARTICLE.replace("https://amzn.to/3TestABC", "")
        with self.assertRaises(self.gp.GenerationStageError):
            self.gp.validate_output("review", no_link, "test-slug")

    def test_wrong_affiliate_link_fails_when_expected_url_given(self):
        # A link different from the expected one (e.g. hallucinated) must raise
        expected = "https://www.amazon.com/dp/B00AKOQYXY?tag=pawpicks04-20"
        art = GOOD_ARTICLE.replace(
            "https://amzn.to/3TestABC",
            "https://www.amazon.com/dp/WRONG9999?tag=pawpicks04-20",
        )
        with self.assertRaises(self.gp.GenerationStageError):
            self.gp.validate_output("review", art, "test-slug", affiliate_url=expected)

    def test_word_count_below_minimum_fails(self):
        # Has a link but too short
        short_with_link = "Short article. [Product](https://amzn.to/3TestABC). " * 5
        with self.assertRaises(self.gp.GenerationStageError):
            self.gp.validate_output("generate", short_with_link, "test-slug")


class TestProductValidation(unittest.TestCase):
    """validate_product() pre-publish gate -- must reject unresolved placeholders (F2)"""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp
        self.valid = dict(SAMPLE_PRODUCT)  # all required fields, real values

    def test_valid_product_passes(self):
        self.assertEqual(self.gp.validate_product("slug", self.valid), [])

    def test_missing_required_field_fails(self):
        p = dict(self.valid); del p["name"]
        self.assertTrue(self.gp.validate_product("slug", p))

    def test_needs_image_placeholder_fails(self):
        p = dict(self.valid); p["image"] = "NEEDS_IMAGE"
        errs = self.gp.validate_product("slug", p)
        self.assertTrue(any("NEEDS_IMAGE" in e for e in errs))

    def test_needs_asin_placeholder_fails(self):
        p = dict(self.valid); p["asin"] = "NEEDS_ASIN"
        errs = self.gp.validate_product("slug", p)
        self.assertTrue(any("NEEDS_ASIN" in e for e in errs))

    def test_needs_asin_in_affiliate_url_fails_even_if_image_resolved(self):
        # image is a real URL; only the affiliate_url still carries the placeholder
        p = dict(self.valid)
        p["affiliate_url"] = "https://www.amazon.com/dp/NEEDS_ASIN?tag=pawpicks04-20"
        errs = self.gp.validate_product("slug", p)
        self.assertTrue(any("NEEDS_ASIN" in e for e in errs))


class TestProductsJsonAffiliateContract(unittest.TestCase):
    """Every real products.json entry must satisfy the publish-gate link contract (F1).

    This is the guard that was missing: prior tests validated the gate against
    synthetic fixtures but never asserted the live queue could actually pass it.
    """

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_every_entry_affiliate_url_matches_gate(self):
        products = json.loads((REPO / "products.json").read_text(encoding="utf-8"))
        offenders = []
        for e in products:
            url = e.get("affiliate_url", "")
            # Unresolved placeholders are legitimately held by validate_product,
            # not by the affiliate-link gate -- skip them here.
            if "NEEDS_ASIN" in url:
                continue
            if not self.gp.AFFILIATE_LINK_RE.search(url):
                offenders.append((e.get("topic"), url))
        self.assertEqual(offenders, [], f"affiliate_url(s) not recognized by publish gate: {offenders}")


class TestScorecardEvaluation(unittest.TestCase):
    """evaluate_scorecard() -- deterministic gating, never trust the LLM's self-report (F3)"""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp
        self.clean = {
            "pass":            True,
            "scores":          {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4},
            "em_dash_count":   0,
            "flags":           [],
            "ai_patterns_found": [],
        }

    def test_clean_scorecard_passes(self):
        passed, _ = self.gp.evaluate_scorecard(self.clean, "a clean body with no dashes at all")
        self.assertTrue(passed)

    def test_pass_false_stays_false(self):
        sc = dict(self.clean); sc["pass"] = False
        passed, _ = self.gp.evaluate_scorecard(sc, "a clean body")
        self.assertFalse(passed)

    def test_low_human_voice_forces_fail(self):
        sc = dict(self.clean); sc["scores"] = {**self.clean["scores"], "human_voice": 2}
        passed, _ = self.gp.evaluate_scorecard(sc, "a clean body")
        self.assertFalse(passed)

    def test_low_readability_forces_fail(self):
        sc = dict(self.clean); sc["scores"] = {**self.clean["scores"], "readability": 2}
        passed, _ = self.gp.evaluate_scorecard(sc, "a clean body")
        self.assertFalse(passed)

    def test_em_dash_in_content_forces_fail_even_if_reported_zero(self):
        # Model wrongly reports 0 em dashes, but the body actually contains one
        passed, _ = self.gp.evaluate_scorecard(self.clean, "this body has an em dash — right here")
        self.assertFalse(passed)

    def test_reported_em_dash_count_ignored_when_body_is_clean(self):
        # The reviewer's self-reported em_dash_count is advisory only: it mislabels
        # hyphenated compounds ("extra-large") as em dashes and would falsely hold
        # a clean article. We trust the deterministic body check instead, so a
        # clean body passes even when the model over-reports em dashes.
        sc = dict(self.clean); sc["em_dash_count"] = 3
        passed, _ = self.gp.evaluate_scorecard(sc, "a clean body with no dashes")
        self.assertTrue(passed)

    def test_fabrication_flag_forces_fail(self):
        sc = dict(self.clean); sc["flags"] = ["fabricated statistic with no source"]
        passed, _ = self.gp.evaluate_scorecard(sc, "a clean body")
        self.assertFalse(passed)

    def test_human_voice_and_warmth_of_three_now_pass(self):
        # Director lowered the bar 4->3 (Open Q1) after the confirmation run held
        # at 3/3. A competent-generic 3/3 article with no hard-fail flags passes;
        # em-dash / first-person / fabrication stay hard holds.
        sc = dict(self.clean)
        sc["scores"] = {"human_voice": 3, "warmth": 3, "readability": 3, "accuracy": 3}
        passed, _ = self.gp.evaluate_scorecard(sc, "a clean body with no dashes")
        self.assertTrue(passed)

    def test_scores_below_three_still_fail(self):
        sc = dict(self.clean)
        sc["scores"] = {"human_voice": 2, "warmth": 3, "readability": 3, "accuracy": 3}
        passed, _ = self.gp.evaluate_scorecard(sc, "a clean body")
        self.assertFalse(passed)


class TestTypographyScrub(unittest.TestCase):
    """scrub_typography() -- deterministic em/en dash conversion before the
    anti-AI review gate. No LLM emits zero em dashes and the reviewer rule is
    'any em dash = FAIL', so the dashes are converted (not deleted) to the
    equivalents a human would type: commas for parenthetical appositives, a
    spaced hyphen for a break/reveal."""

    def setUp(self):
        import generate_posts as gp
        self.scrub = gp.scrub_typography

    def test_lone_em_dash_becomes_spaced_hyphen(self):
        # A single em dash marks a break/reveal -> spaced hyphen
        self.assertEqual(
            self.scrub("The winner is clear—the Kong Classic."),
            "The winner is clear - the Kong Classic.",
        )

    def test_paired_em_dashes_become_commas(self):
        # A matched pair inside one sentence brackets an appositive -> commas
        self.assertEqual(
            self.scrub("Buster—a Labrador—loves this toy."),
            "Buster, a Labrador, loves this toy.",
        )

    def test_pair_does_not_span_sentence_boundary(self):
        # Two lone dashes in two sentences must NOT be paired into commas
        self.assertEqual(
            self.scrub("I ran—fast. She walked—slow."),
            "I ran - fast. She walked - slow.",
        )

    def test_spaced_em_dash_collapses_surrounding_whitespace(self):
        self.assertEqual(
            self.scrub("The toy lasts — mostly."),
            "The toy lasts - mostly.",
        )

    def test_odd_run_pairs_then_trailing_lone(self):
        # 3 em dashes in one sentence: first two -> commas (appositive),
        # trailing lone -> spaced hyphen
        self.assertEqual(
            self.scrub("Buster—a Lab—loves it, and wow—really."),
            "Buster, a Lab, loves it, and wow - really.",
        )

    def test_en_dash_numeric_range_becomes_plain_hyphen(self):
        self.assertEqual(
            self.scrub("Give 5–10 treats daily."),
            "Give 5-10 treats daily.",
        )

    def test_en_dash_separator_becomes_spaced_hyphen(self):
        self.assertEqual(
            self.scrub("The toy is durable – mostly."),
            "The toy is durable - mostly.",
        )

    def test_existing_hyphens_untouched(self):
        self.assertEqual(self.scrub("high-quality dog food"), "high-quality dog food")

    def test_text_without_fancy_dashes_unchanged(self):
        clean = "A clean sentence with no fancy dashes."
        self.assertEqual(self.scrub(clean), clean)

    def test_output_contains_no_em_or_en_dash(self):
        out = self.scrub("Buster—a Lab—runs 5–10 miles; honestly—wow.")
        self.assertNotIn("—", out)
        self.assertNotIn("–", out)

    def test_idempotent(self):
        once = self.scrub("Buster—a Lab—runs 5–10 miles—fast.")
        self.assertEqual(self.scrub(once), once)


class TestReviewGateStripsEmDashes(unittest.TestCase):
    """review_and_rewrite() must convert em dashes BEFORE the reviewer sees the
    body, so the reviewer's own em_dash_count (and the deterministic gate) both
    read zero. Otherwise a clean-reading body still fails on the reported count."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp
        self._orig = (gp.REVIEWER_ENABLED, gp.REVIEW_PRE_SLEEP,
                      gp._call_openrouter_reviewer, gp.make_review_prompt)
        gp.REVIEWER_ENABLED = True
        gp.REVIEW_PRE_SLEEP = 0

    def tearDown(self):
        (self.gp.REVIEWER_ENABLED, self.gp.REVIEW_PRE_SLEEP,
         self.gp._call_openrouter_reviewer, self.gp.make_review_prompt) = self._orig

    def test_reviewer_and_output_never_see_an_em_dash(self):
        # Capture the article content actually handed to the reviewer (the prompt
        # template itself contains em dashes in its rubric, so assert on content).
        seen = {}
        orig_make = self.gp.make_review_prompt
        def capture_make(title, keyword, content):
            seen["content"] = content
            return orig_make(title, keyword, content)
        self.gp.make_review_prompt = capture_make
        self.gp._call_openrouter_reviewer = lambda prompt: {
            "pass": True,
            "scores": {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4},
            "em_dash_count": 0, "flags": [], "ai_patterns_found": [],
        }
        body = "This crate is roomy—big enough for a Lab—and folds flat."
        final, passed, _ = self.gp.review_and_rewrite("T", "kw", body, api_key="")
        self.assertNotIn("—", seen["content"], "reviewer must see scrubbed content")
        self.assertNotIn("—", final, "published content must be scrubbed")
        self.assertTrue(passed)


class TestPromptRuleConsistency(unittest.TestCase):
    """Generator and reviewer must enforce ONE canonical rulebook -- a term the
    generator is told to avoid is exactly a term the reviewer flags. The lists
    are the union of both prior prompts, so nothing is dropped and the quality
    bar is unchanged; they live in shared constants so the two can't drift."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp
        self.system = gp.GENERATOR_SYSTEM_PROMPT
        self.review = gp.make_review_prompt("Best Dog Crates", "dog crate", "Body.")

    @staticmethod
    def _has(term, text):
        return term.lower() in text.lower()

    def test_banned_words_in_generator_and_reviewer(self):
        self.assertTrue(self.gp.BANNED_WORDS)
        for w in self.gp.BANNED_WORDS:
            self.assertTrue(self._has(w, self.system), f"generator missing word: {w}")
            self.assertTrue(self._has(w, self.review), f"reviewer missing word: {w}")

    def test_banned_transitions_in_generator_and_reviewer(self):
        self.assertTrue(self.gp.BANNED_TRANSITIONS)
        for t in self.gp.BANNED_TRANSITIONS:
            self.assertTrue(self._has(t, self.system), f"generator missing transition: {t}")
            self.assertTrue(self._has(t, self.review), f"reviewer missing transition: {t}")

    def test_banned_phrases_in_generator_and_reviewer(self):
        self.assertTrue(self.gp.BANNED_PHRASES)
        for p in self.gp.BANNED_PHRASES:
            self.assertTrue(self._has(p, self.system), f"generator missing phrase: {p}")
            self.assertTrue(self._has(p, self.review), f"reviewer missing phrase: {p}")

    def test_hollow_intensifiers_taught_to_generator(self):
        self.assertTrue(self.gp.BANNED_INTENSIFIERS)
        for w in self.gp.BANNED_INTENSIFIERS:
            self.assertTrue(self._has(w, self.system), f"generator missing intensifier: {w}")
            self.assertTrue(self._has(w, self.review), f"reviewer missing intensifier: {w}")

    def test_no_previously_flagged_term_was_dropped(self):
        # Regression guard for "standards stay the same": every term either prompt
        # used to flag before the reconciliation must still be canonical.
        prior = {
            # generator's original banned words
            "delve", "tapestry", "testament", "paramount", "crucial", "elevate",
            "multifaceted", "leverage", "robust", "navigate",
            # reviewer's original #10 additions
            "utilize", "facilitate", "comprehensive", "innovative", "seamless",
            "streamline", "synergy", "pivotal", "foster",
            # reviewer significance / transitions the generator hadn't been told
            "revolutionize", "groundbreaking", "on the other hand", "in light of",
            "with that in mind",
        }
        canonical = " ".join(self.gp.BANNED_WORDS + self.gp.BANNED_TRANSITIONS
                             + self.gp.BANNED_PHRASES).lower()
        for term in prior:
            self.assertIn(term, canonical, f"reconciliation dropped a flagged term: {term}")

    def test_participial_openings_taught_to_generator(self):
        # reviewer #9 flags "Standing at.../Looking at..." openings; generator
        # must now be told, or it can't comply.
        self.assertIn("standing at", self.system.lower())

    def test_reviewer_prompt_states_the_gate_minimums(self):
        # The stated PASS CRITERIA must match the enforced REVIEW_SCORE_MINIMUMS,
        # or the reviewer scores against a different bar than the gate applies.
        mins = self.gp.REVIEW_SCORE_MINIMUMS
        for key in ("human_voice", "warmth", "readability", "accuracy"):
            self.assertIn(f"{key} >= {mins[key]}", self.review,
                          f"reviewer prompt bar for {key} != gate minimum {mins[key]}")


class TestFirstPersonDetection(unittest.TestCase):
    """Reviewer prompt gates first-person voice — P2"""

    def test_bad_article_contains_first_person(self):
        first_person_indicators = ["I found", "We tested", "Our dogs", "my favorite"]
        found = any(phrase in BAD_ARTICLE for phrase in first_person_indicators)
        self.assertTrue(found, "Test fixture should contain first-person voice")

    def test_good_article_no_first_person(self):
        first_person = ["\\bI\\b", "\\bwe\\b", "\\bour\\b", "\\bmy\\b", "\\bus\\b"]
        import re
        for pattern in first_person:
            match = re.search(pattern, GOOD_ARTICLE, re.IGNORECASE)
            self.assertIsNone(match, f"Good article should not contain: {pattern}")

    def test_good_article_no_em_dashes(self):
        self.assertNotIn("—", GOOD_ARTICLE)

    def test_bad_article_has_em_dashes(self):
        self.assertIn("—", BAD_ARTICLE)


class TestReviewerResponseParsing(unittest.TestCase):
    """Reviewer JSON structure validation — P2"""

    REQUIRED_KEYS = {
        "pass", "scores", "affiliate_link_present",
        "em_dash_count", "ai_patterns_found", "flags", "rewrite_instructions",
    }
    REQUIRED_SCORE_KEYS = {"human_voice", "warmth", "readability", "accuracy"}

    def _parse(self, raw: str) -> dict:
        import re
        raw = re.sub(r"```json|```", "", raw).strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group(0) if m else raw)

    def test_pass_response_has_all_keys(self):
        data = self._parse(REVIEWER_PASS_JSON)
        missing = self.REQUIRED_KEYS - data.keys()
        self.assertEqual(missing, set(), f"Missing keys in pass response: {missing}")

    def test_fail_response_has_all_keys(self):
        data = self._parse(REVIEWER_FAIL_JSON)
        missing = self.REQUIRED_KEYS - data.keys()
        self.assertEqual(missing, set(), f"Missing keys in fail response: {missing}")

    def test_scores_have_all_keys(self):
        data = self._parse(REVIEWER_PASS_JSON)
        missing = self.REQUIRED_SCORE_KEYS - data["scores"].keys()
        self.assertEqual(missing, set(), f"Missing score keys: {missing}")

    def test_em_dash_in_body_overrides_pass_true(self):
        """A real em dash in the body forces pass=false even if the model returns
        pass=true -- via the deterministic body check in evaluate_scorecard, not
        the model's advisory em_dash_count."""
        import generate_posts as gp
        scorecard = json.loads(REVIEWER_PASS_JSON)
        scorecard["pass"] = True
        scorecard["em_dash_count"] = 0  # model claims clean...
        passed, _ = gp.evaluate_scorecard(scorecard, "body with an em dash — here")
        self.assertFalse(passed)  # ...but the real body has one

    def test_pass_requires_affiliate_link(self):
        data = self._parse(REVIEWER_PASS_JSON)
        if data["pass"]:
            self.assertTrue(data["affiliate_link_present"])


class TestFactCheckNotTruncated(unittest.TestCase):
    """Fact-check must not truncate below 60% of article — P3"""

    def test_full_article_passed_to_fact_check(self):
        import generate_posts as gp

        calls = []

        def mock_http_post(url, payload, headers, **kwargs):
            data = json.loads(payload.decode())
            prompt = data["messages"][0]["content"]
            # Extract the article content passed to the fact-checker
            if "ARTICLE:" in prompt:
                article_start = prompt.index("ARTICLE:") + len("ARTICLE:")
                article_text = prompt[article_start:].strip()
                calls.append(len(article_text))
            return _make_or_response(GOOD_ARTICLE)

        with patch.object(gp, "http_post", side_effect=mock_http_post):
            with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
                result = gp.fact_check_alternatives(GOOD_ARTICLE, "Outward Hound Nina Ottosson")

        self.assertTrue(len(calls) > 0, "http_post was not called — fact-check did not run")
        passed_length = calls[0]
        article_length = len(GOOD_ARTICLE)
        coverage = passed_length / article_length
        self.assertGreaterEqual(
            coverage, 0.60,
            f"Fact-check only received {coverage:.0%} of article ({passed_length}/{article_length} chars)"
        )


class TestPinImageURLContracts(unittest.TestCase):
    """Pin image URL named contract functions — P5"""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_queue_url_has_version_stamp(self):
        url = self.gp.build_pin_image_url_for_queue("test-slug")
        self.assertIn("?v=", url, "Queue URL must contain ?v= cache-bust stamp")
        self.assertIn("test-slug.jpg", url)

    def test_ifttt_url_has_no_query_string(self):
        url = self.gp.build_pin_image_url_for_ifttt("test-slug")
        self.assertNotIn("?", url, "IFTTT URL must not contain query string")
        self.assertIn("test-slug.jpg", url)

    def test_ifttt_strips_existing_query(self):
        import post_pins as pp
        bare = pp.build_pin_image_url_for_ifttt(
            "https://happypetproductreviews.com/assets/images/pins/test-slug.jpg?v=20260518"
        )
        self.assertNotIn("?", bare)

    def test_queue_url_and_ifttt_url_same_base(self):
        q = self.gp.build_pin_image_url_for_queue("my-slug")
        i = self.gp.build_pin_image_url_for_ifttt("my-slug")
        # Base (before ?) must be identical
        self.assertEqual(q.split("?")[0], i)


class TestChewyBrandGate(unittest.TestCase):
    """Chewy brand identity gate rejects known mismatch — P10"""

    def test_matching_brand_accepted(self):
        from validate_published_chewy_links import check_brand_match
        ok, reason = check_brand_match(
            "Outward Hound Nina Ottosson Dog Tornado",
            "Outward Hound Dog Tornado Interactive Puzzle"
        )
        self.assertTrue(ok, f"Should accept brand match. Reason: {reason}")

    def test_mismatched_brand_rejected(self):
        from validate_published_chewy_links import check_brand_match
        ok, reason = check_brand_match(
            "Kong Classic Dog Toy",
            "Nylabone Power Chew Toy"
        )
        self.assertFalse(ok, f"Should reject brand mismatch. Reason: {reason}")

    def test_empty_matched_name_rejected(self):
        from validate_published_chewy_links import check_brand_match
        ok, reason = check_brand_match("Kong Classic Dog Toy", "")
        self.assertFalse(ok)

    def test_no_brand_token_passes(self):
        """If product name yields no brand token (all stop words), gate passes."""
        from validate_published_chewy_links import check_brand_match
        ok, reason = check_brand_match("the a for", "anything at all here")
        self.assertTrue(ok, f"No brand token should pass gate. Reason: {reason}")


class TestChewyWordCoverage(unittest.TestCase):
    """Same-brand matches that clear the raw score purely on common words
    (dog, treats, beef...) but share little of the product's distinguishing
    vocabulary are usually a different pack size/variant, not the same
    product -- auto-accepting them puts a skewed Chewy price in the article."""

    def test_terse_catalog_title_covers_descriptive_search_name(self):
        from chewy_lookup import _word_coverage
        coverage = _word_coverage(
            "Fumoi Automatic Self-Cleaning Cat Litter Box, Large Capacity, App Control, Grey",
            "Fumoi Automatic Self-Cleaning Cat Litter Box",
        )
        self.assertAlmostEqual(coverage, 0.545, places=2)

    def test_same_brand_different_variant_has_low_coverage(self):
        from chewy_lookup import _word_coverage
        coverage = _word_coverage(
            "Blue Buffalo Bits Beef Soft & Chewy Dog Treats, Bite-Sized for "
            "Training, Made with Real Beef & Enhanced with DHA, Heart-Shaped",
            "Blue Buffalo Blue Bits Tender Beef Dog Treats",
        )
        self.assertAlmostEqual(coverage, 0.375, places=2)

    def test_punctuation_does_not_split_tokens(self):
        # The raw score's `.split()` leaves "Box," and "Box" as different
        # tokens; _word_coverage strips punctuation so this doesn't
        # artificially deflate coverage for comma-heavy retail titles.
        from chewy_lookup import _word_coverage
        self.assertGreater(_word_coverage("Litter Box, Grey", "Litter Box"), 0.5)

    def test_lookup_downgrades_high_score_low_coverage_match_to_review(self):
        # Regression case: this exact pair scores 6 under the raw word-overlap
        # formula (well past SCORE_AUTO_ACCEPT=4) purely on shared common
        # words, with no pack-size/variant signal either way -- it must not
        # auto-accept and surface a possibly-wrong Chewy price.
        import chewy_lookup as cl
        search_name = (
            "Blue Buffalo Bits Beef Soft & Chewy Dog Treats, Bite-Sized for "
            "Training, Made with Real Beef & Enhanced with DHA, Heart-Shaped"
        )
        item = {
            "Name": "Blue Buffalo Blue Bits Tender Beef Dog Treats",
            "Manufacturer": "Blue Buffalo",
            "StockAvailability": "InStock",
            "Url": "https://chewy.example/blue-bits-tender-beef",
            "CurrentPrice": "41.99",
        }
        with patch.object(cl, "ACCOUNT_SID", "x"), \
             patch.object(cl, "AUTH_TOKEN", "y"), \
             patch.object(cl, "search_catalog", return_value=[item]):
            result = cl.lookup(search_name)
        self.assertTrue(str(result["chewy_url"]).startswith("REVIEW"))

    def test_lookup_still_auto_accepts_genuine_high_coverage_match(self):
        import chewy_lookup as cl
        search_name = "Fumoi Automatic Self-Cleaning Cat Litter Box, Large Capacity, App Control, Grey"
        item = {
            "Name": "Fumoi Automatic Self-Cleaning Cat Litter Box",
            "Manufacturer": "Fumoi",
            "StockAvailability": "InStock",
            "Url": "https://chewy.example/fumoi-litter-box",
            "CurrentPrice": "199.95",
        }
        with patch.object(cl, "ACCOUNT_SID", "x"), \
             patch.object(cl, "AUTH_TOKEN", "y"), \
             patch.object(cl, "search_catalog", return_value=[item]), \
             patch.object(cl, "scrape_chewy_rating", return_value=None):
            result = cl.lookup(search_name)
        self.assertEqual(result["chewy_url"], "https://chewy.example/fumoi-litter-box")


class TestChewyGtinMatch(unittest.TestCase):
    """An exact UPC match against Chewy's catalog Gtin field is definitive --
    it should bypass the brand-conflict and word-coverage heuristics entirely,
    since those exist only to guess whether two *names* describe the same
    product. A verified UPC makes that guess unnecessary."""

    def test_normalize_gtin_strips_punctuation_and_leading_zeros(self):
        from chewy_lookup import _normalize_gtin
        self.assertEqual(_normalize_gtin("700603718714"), "700603718714")
        self.assertEqual(_normalize_gtin("00700603718714"), "700603718714")
        self.assertEqual(_normalize_gtin("0070-0603-718714"), "700603718714")

    def test_normalize_gtin_handles_gtin14_vs_upc12_padding(self):
        # GTIN-14 is UPC-A left-padded with zeros to 14 digits -- the two
        # must normalize to the same value or a real match gets missed.
        from chewy_lookup import _normalize_gtin
        self.assertEqual(_normalize_gtin("00000700603718714"[-14:]),
                         _normalize_gtin("700603718714"))

    def test_normalize_gtin_empty_input_returns_empty(self):
        from chewy_lookup import _normalize_gtin
        self.assertEqual(_normalize_gtin(""), "")
        self.assertEqual(_normalize_gtin(None), "")

    def test_find_gtin_match_returns_item_with_matching_gtin(self):
        from chewy_lookup import _find_gtin_match
        items = [
            {"Name": "Wrong Item", "Gtin": "111111111111", "StockAvailability": "InStock"},
            {"Name": "Right Item", "Gtin": "700603718714", "StockAvailability": "InStock"},
        ]
        match = _find_gtin_match(items, "700603718714")
        self.assertEqual(match["Name"], "Right Item")

    def test_find_gtin_match_normalizes_before_comparing(self):
        from chewy_lookup import _find_gtin_match
        items = [{"Name": "Right Item", "Gtin": "00700603718714", "StockAvailability": "InStock"}]
        match = _find_gtin_match(items, "700603718714")
        self.assertEqual(match["Name"], "Right Item")

    def test_find_gtin_match_returns_none_when_no_upc_given(self):
        from chewy_lookup import _find_gtin_match
        items = [{"Name": "Item", "Gtin": "700603718714", "StockAvailability": "InStock"}]
        self.assertIsNone(_find_gtin_match(items, ""))
        self.assertIsNone(_find_gtin_match(items, None))

    def test_find_gtin_match_returns_none_when_nothing_matches(self):
        from chewy_lookup import _find_gtin_match
        items = [{"Name": "Item", "Gtin": "999999999999", "StockAvailability": "InStock"}]
        self.assertIsNone(_find_gtin_match(items, "700603718714"))

    def test_lookup_gtin_match_bypasses_low_coverage_downgrade(self):
        # Same regression pair as
        # test_lookup_downgrades_high_score_low_coverage_match_to_review, but
        # this time the searched product's known UPC exactly matches the
        # candidate's Gtin -- it must auto-accept despite low word coverage.
        import chewy_lookup as cl
        search_name = (
            "Blue Buffalo Bits Beef Soft & Chewy Dog Treats, Bite-Sized for "
            "Training, Made with Real Beef & Enhanced with DHA, Heart-Shaped"
        )
        item = {
            "Name": "Blue Buffalo Blue Bits Tender Beef Dog Treats",
            "Manufacturer": "Blue Buffalo",
            "StockAvailability": "InStock",
            "Url": "https://chewy.example/blue-bits-tender-beef",
            "CurrentPrice": "41.99",
            "Gtin": "840243160563",
        }
        with patch.object(cl, "ACCOUNT_SID", "x"), \
             patch.object(cl, "AUTH_TOKEN", "y"), \
             patch.object(cl, "search_catalog", return_value=[item]), \
             patch.object(cl, "scrape_chewy_rating", return_value=4.6):
            result = cl.lookup(search_name, upc="840243160563")
        self.assertEqual(result["chewy_url"], "https://chewy.example/blue-bits-tender-beef")
        self.assertEqual(result["chewy_rating"], 4.6)

    def test_lookup_gtin_match_bypasses_brand_conflict_gate(self):
        import chewy_lookup as cl
        item = {
            "Name": "Invenho Cooling Dog Crate Mat Anti-Slip",
            "Manufacturer": "Invenho",
            "StockAvailability": "InStock",
            "Url": "https://chewy.example/invenho-cooling-mat",
            "CurrentPrice": "39.99",
            "Gtin": "612345678901",
        }
        with patch.object(cl, "ACCOUNT_SID", "x"), \
             patch.object(cl, "AUTH_TOKEN", "y"), \
             patch.object(cl, "search_catalog", return_value=[item]), \
             patch.object(cl, "scrape_chewy_rating", return_value=None):
            result = cl.lookup("EHEYCIGA Cooling Mat for Dogs, 41x28 inches, Washable, Non-Slip, Blue",
                               upc="612345678901")
        self.assertEqual(result["chewy_url"], "https://chewy.example/invenho-cooling-mat")

    def test_lookup_without_upc_argument_keeps_existing_behavior(self):
        # Backward compatibility: omitting upc must reproduce the pre-GTIN
        # REVIEW outcome for the same regression pair -- the fast path must
        # never activate implicitly.
        import chewy_lookup as cl
        search_name = (
            "Blue Buffalo Bits Beef Soft & Chewy Dog Treats, Bite-Sized for "
            "Training, Made with Real Beef & Enhanced with DHA, Heart-Shaped"
        )
        item = {
            "Name": "Blue Buffalo Blue Bits Tender Beef Dog Treats",
            "Manufacturer": "Blue Buffalo",
            "StockAvailability": "InStock",
            "Url": "https://chewy.example/blue-bits-tender-beef",
            "CurrentPrice": "41.99",
            "Gtin": "840243160563",
        }
        with patch.object(cl, "ACCOUNT_SID", "x"), \
             patch.object(cl, "AUTH_TOKEN", "y"), \
             patch.object(cl, "search_catalog", return_value=[item]):
            result = cl.lookup(search_name)
        self.assertTrue(str(result["chewy_url"]).startswith("REVIEW"))

    def test_lookup_upc_given_but_no_match_falls_back_to_scoring(self):
        # upc provided but doesn't match any candidate's Gtin -- must fall
        # through to the normal score/coverage/brand path unchanged.
        import chewy_lookup as cl
        search_name = "Fumoi Automatic Self-Cleaning Cat Litter Box, Large Capacity, App Control, Grey"
        item = {
            "Name": "Fumoi Automatic Self-Cleaning Cat Litter Box",
            "Manufacturer": "Fumoi",
            "StockAvailability": "InStock",
            "Url": "https://chewy.example/fumoi-litter-box",
            "CurrentPrice": "199.95",
            "Gtin": "111111111111",
        }
        with patch.object(cl, "ACCOUNT_SID", "x"), \
             patch.object(cl, "AUTH_TOKEN", "y"), \
             patch.object(cl, "search_catalog", return_value=[item]), \
             patch.object(cl, "scrape_chewy_rating", return_value=None):
            result = cl.lookup(search_name, upc="999999999999")
        self.assertEqual(result["chewy_url"], "https://chewy.example/fumoi-litter-box")


class TestBrainSecretsVaultFallback(unittest.TestCase):
    """brain_secrets.py reads Maeve's SecretVault (in the sibling MaeveJarvis
    repo) so chewy_lookup.py can get IMPACT_* creds locally without them
    being exported by hand. Every failure mode here must degrade to None,
    never raise -- CI has no MaeveJarvis checkout next to this repo at all,
    and chewy_lookup.py's own credential fallback must not crash imports."""

    def setUp(self):
        import brain_secrets as bs
        self.bs = bs
        # Reset the lazy-vault cache so each test controls it explicitly.
        self._orig_vault, self._orig_tried = bs._vault, bs._vault_tried
        bs._vault, bs._vault_tried = None, False

    def tearDown(self):
        self.bs._vault, self.bs._vault_tried = self._orig_vault, self._orig_tried

    def test_get_secret_tries_project_scope_first(self):
        fake_vault = MagicMock()
        fake_vault.use.return_value = "sid-value"
        with patch.object(self.bs, "_get_vault", return_value=fake_vault):
            self.assertEqual(self.bs.get_secret("IMPACT_ACCOUNT_SID", "HappyPet"), "sid-value")
        fake_vault.use.assert_called_once_with("HAPPYPET__IMPACT_ACCOUNT_SID")

    def test_get_secret_falls_back_to_global_scope(self):
        fake_vault = MagicMock()

        def use(name):
            if name == "GLOBAL__SOME_KEY":
                return "global-value"
            raise KeyError(name)

        fake_vault.use.side_effect = use
        with patch.object(self.bs, "_get_vault", return_value=fake_vault):
            self.assertEqual(self.bs.get_secret("SOME_KEY", "HappyPet"), "global-value")

    def test_get_secret_returns_none_when_key_missing_in_both_scopes(self):
        fake_vault = MagicMock()
        fake_vault.use.side_effect = KeyError
        with patch.object(self.bs, "_get_vault", return_value=fake_vault):
            self.assertIsNone(self.bs.get_secret("NOPE", "HappyPet"))

    def test_get_secret_returns_none_when_vault_unavailable(self):
        with patch.object(self.bs, "_get_vault", return_value=None):
            self.assertIsNone(self.bs.get_secret("IMPACT_ACCOUNT_SID", "HappyPet"))

    def test_get_vault_returns_none_when_secrets_env_missing(self):
        # Simulates CI: only this repo is checked out, no sibling MaeveJarvis.
        with patch.object(self.bs, "_SECRETS_ENV", Path("Z:/does/not/exist/secrets.env")):
            self.assertIsNone(self.bs._get_vault())

    def test_chewy_lookup_survives_reimport_when_brain_secrets_unimportable(self):
        # Simulates a deployment missing brain_secrets.py entirely (e.g. a
        # stripped CI checkout) -- chewy_lookup.py's `except ImportError:
        # pass` guard around `from brain_secrets import get_secret` must
        # hold, leaving ACCOUNT_SID/AUTH_TOKEN as empty strings, not crash.
        import importlib
        import os
        import chewy_lookup as cl

        with patch.dict(os.environ, {"IMPACT_ACCOUNT_SID": "", "IMPACT_AUTH_TOKEN": ""}), \
             patch.dict(sys.modules, {"brain_secrets": None}):
            importlib.reload(cl)
        try:
            self.assertEqual(cl.ACCOUNT_SID, "")
            self.assertEqual(cl.AUTH_TOKEN, "")
        finally:
            importlib.reload(cl)  # restore real module state for later tests


class TestPostPinsSecretFallback(unittest.TestCase):
    """Regression: pin.yml failed with 'IFTTT_MAKER_KEY not set' though the key
    was present as a GitHub Secret (env var). brain_secrets.get_secret returns
    None on CI by design (no vault), and brain_secrets.py imports cleanly there,
    so post_pins.py's env fallback -- which was gated on ImportError -- never
    fired. The fallback must happen at call time: vault first, then env."""

    def setUp(self):
        import post_pins as pp
        import brain_secrets as bs
        self.pp = pp
        self.bs = bs
        self._orig = (bs._vault, bs._vault_tried)
        bs._vault, bs._vault_tried = None, False

    def tearDown(self):
        self.bs._vault, self.bs._vault_tried = self._orig

    def test_secret_falls_back_to_env_when_vault_unavailable(self):
        import os
        with patch.object(self.bs, "_get_vault", return_value=None), \
             patch.dict(os.environ, {"IFTTT_MAKER_KEY": "env-key"}):
            self.assertEqual(self.pp.brain_get_secret("IFTTT_MAKER_KEY", "global"), "env-key")

    def test_secret_prefers_vault_over_env(self):
        import os
        fake_vault = MagicMock()
        fake_vault.use.return_value = "vault-key"
        with patch.object(self.bs, "_get_vault", return_value=fake_vault), \
             patch.dict(os.environ, {"IFTTT_MAKER_KEY": "env-key"}):
            self.assertEqual(self.pp.brain_get_secret("IFTTT_MAKER_KEY", "global"), "vault-key")

    def test_sheet_id_secret_reads_env_on_ci(self):
        import os
        with patch.object(self.bs, "_get_vault", return_value=None), \
             patch.dict(os.environ, {"HAPPYPET_SHEET_ID_DOGS": "sheet-1"}):
            self.assertEqual(self.pp.brain_get_secret("HAPPYPET_SHEET_ID_DOGS"), "sheet-1")

    def test_sheets_creds_fall_back_to_gcp_sa_key_b64(self):
        import os, base64, json
        fake_info = {"type": "service_account", "project_id": "x"}
        b64 = base64.b64encode(json.dumps(fake_info).encode()).decode()
        with patch.object(self.bs, "_get_vault", return_value=None), \
             patch.dict(os.environ, {"GCP_SA_KEY_B64": b64}), \
             patch("google.oauth2.service_account.Credentials.from_service_account_info",
                   return_value="CREDS") as mk:
            self.assertEqual(self.pp.get_sheets_creds(), "CREDS")
            self.assertEqual(mk.call_args.args[0], fake_info)


class TestPushPinsToSheetsSecretFallback(unittest.TestCase):
    """push_pins_to_sheets.py (Sheets audit + Facebook-queue append) shares the
    same vault-or-env contract as post_pins.py; on CI it must build Sheets creds
    from GCP_SA_KEY_B64 when the vault is absent (regression: the Stage-3 Sheets
    step failed with 'HAPPYPET_SHEETS_KEY not found in the vault')."""

    def setUp(self):
        import push_pins_to_sheets as pk
        import brain_secrets as bs
        self.pk = pk
        self.bs = bs
        self._orig = (bs._vault, bs._vault_tried)
        bs._vault, bs._vault_tried = None, False

    def tearDown(self):
        self.bs._vault, self.bs._vault_tried = self._orig

    def test_secret_falls_back_to_env_when_vault_unavailable(self):
        import os
        with patch.object(self.bs, "_get_vault", return_value=None), \
             patch.dict(os.environ, {"FACEBOOK_QUEUE_SHEET_ID": "fbq-1"}):
            self.assertEqual(self.pk.brain_get_secret("FACEBOOK_QUEUE_SHEET_ID"), "fbq-1")

    def test_sheets_creds_fall_back_to_gcp_sa_key_b64(self):
        import os, base64, json
        fake_info = {"type": "service_account", "project_id": "x"}
        b64 = base64.b64encode(json.dumps(fake_info).encode()).decode()
        with patch.object(self.bs, "_get_vault", return_value=None), \
             patch.dict(os.environ, {"GCP_SA_KEY_B64": b64}), \
             patch("google.oauth2.service_account.Credentials.from_service_account_info",
                   return_value="CREDS") as mk:
            self.assertEqual(self.pk.get_sheets_creds(), "CREDS")
            self.assertEqual(mk.call_args.args[0], fake_info)


class TestGenerationResultJSON(unittest.TestCase):
    """GENERATION_RESULT.json is written on pipeline completion — P4"""

    def test_result_json_written_after_run(self):
        import generate_posts as gp
        import tempfile, os

        result_path = REPO / "GENERATION_RESULT.json"
        # Check the attribute exists in module (structural test)
        source = (REPO / "generate_posts.py").read_text(encoding="utf-8")
        self.assertIn("GENERATION_RESULT.json", source,
                      "generate_posts.py must write GENERATION_RESULT.json")
        self.assertIn("articles_generated", source)
        self.assertIn("articles_held", source)
        self.assertIn("articles_failed", source)

    def test_result_json_keys(self):
        """If a result file exists from a prior run, validate its structure."""
        result_path = REPO / "GENERATION_RESULT.json"
        if not result_path.exists():
            self.skipTest("No GENERATION_RESULT.json from a prior run")
        data = json.loads(result_path.read_text(encoding="utf-8"))
        for key in ("articles_generated", "articles_held", "articles_skipped", "articles_failed"):
            self.assertIn(key, data, f"Missing key: {key}")


class TestPendingDraftsJSON(unittest.TestCase):
    """PENDING_DRAFTS.json is written when articles are generated — P7"""

    def test_pending_drafts_written_in_source(self):
        source = (REPO / "generate_posts.py").read_text(encoding="utf-8")
        self.assertIn("PENDING_DRAFTS.json", source)
        self.assertIn('"drafts"', source)
        self.assertIn('"generated"', source)

    def test_publish_yml_reads_pending_drafts(self):
        publish = (REPO / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("PENDING_DRAFTS.json", publish)


class TestDraftSlugParsing(unittest.TestCase):
    """DRAFT-*.md filenames must dedup correctly — a garbage slug caused
    duplicate regeneration and draft clobbering"""

    def test_dated_post_stem(self):
        import generate_posts as gp
        self.assertEqual(gp.slug_from_post_stem("2026-04-04-best-cat-feeder"), "best-cat-feeder")

    def test_draft_stem(self):
        import generate_posts as gp
        self.assertEqual(gp.slug_from_post_stem("DRAFT-best-kitten-food"), "best-kitten-food")

    def test_garbage_stem(self):
        import generate_posts as gp
        self.assertIsNone(gp.slug_from_post_stem("readme"))


class TestSilentLegRegressions(unittest.TestCase):
    """The two bugs the harness missed: FB-queue marker ownership (pins fired
    but rows never appended) and the chewy validation import (weekly job
    rubber-stamped every URL as OK)"""

    def test_chewy_lookup_module_exports(self):
        # validate_published_chewy_links imports these at call time; when
        # _first_brand_token lived inside lookup(), the ImportError was
        # swallowed and validation silently no-opped for weeks
        from chewy_lookup import _first_brand_token, ChewyAPIError, lookup  # noqa: F401
        self.assertEqual(_first_brand_token("Blue Buffalo Life Protection"), "blue")
        self.assertEqual(_first_brand_token(""), "")

    def test_search_catalog_uses_per_catalog_items_endpoint(self):
        # The cross-catalog /Catalogs/ItemSearch endpoint hangs indefinitely
        # against the real Impact.com API for this account's 227K-item catalog
        # (confirmed live 2026-07-07) -- every chewy_enrich() call silently
        # never returned. /Catalogs/{CatalogId}/Items is the equivalent,
        # working per-catalog endpoint and must be used instead.
        import chewy_lookup as cl
        calls = []

        def fake_impact_get(path, params=None):
            calls.append((path, params))
            return {"Items": [{"Name": "Test Item"}]}

        with patch.object(cl, "_impact_get", side_effect=fake_impact_get):
            items = cl.search_catalog("dog water bottle", page_size=5)

        self.assertEqual(len(calls), 1)
        path, params = calls[0]
        self.assertEqual(path, f"/Catalogs/{cl.CATALOG_ID}/Items")
        self.assertEqual(params, {"Keyword": "dog water bottle", "PageSize": 5})
        self.assertEqual(items, [{"Name": "Test Item"}])

    def test_post_pins_never_moves_to_sent(self):
        # sent/ is push_pins_to_sheets.py's processed marker. When post_pins
        # moved fired files there first, push_pins skipped them all and the
        # FB Queue append became a permanent no-op.
        source = (REPO / "post_pins.py").read_text(encoding="utf-8")
        self.assertNotIn("shutil.move", source,
                         "post_pins must not move queue files -- sent/ belongs to push_pins")

    def test_push_pins_dedups_on_sheet(self):
        source = (REPO / "push_pins_to_sheets.py").read_text(encoding="utf-8")
        self.assertIn("read_fb_queue_state", source)
        self.assertNotIn("SKIP (already sent)", source,
                         "sent/-location dedup starves the FB queue; the sheet is the authority")

    def test_validator_treats_api_failure_as_error_not_mismatch(self):
        # An Impact.com outage must never look like a wrong-brand link --
        # --fix would clear every stored Chewy URL
        source = (REPO / "validate_published_chewy_links.py").read_text(encoding="utf-8")
        self.assertIn("ChewyAPIError", source)
        from validate_published_chewy_links import check_brand_match
        ok, _ = check_brand_match("Greenies Feline Dental Treats", "")
        self.assertFalse(ok)  # unchanged helper behavior


class TestReviewerSchema(unittest.TestCase):
    """Hybrid model strategy: reviewer JSON is schema-enforced server-side.
    The OpenAI-dialect json_schema (used via OpenRouter) requires
    additionalProperties:false on every object; Gemini's responseSchema
    rejects the same key -- both variants must be right or the respective
    provider 400s and the article is held unreviewed."""

    def _walk_objects(self, schema):
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                yield schema
            for v in schema.values():
                yield from self._walk_objects(v)
        elif isinstance(schema, list):
            for item in schema:
                yield from self._walk_objects(item)

    def test_openrouter_schema_objects_forbid_additional_properties(self):
        import generate_posts as gp
        objects = list(self._walk_objects(gp.REVIEW_SCHEMA))
        self.assertGreater(len(objects), 0)
        for obj in objects:
            self.assertIs(obj.get("additionalProperties"), False)

    def test_schema_covers_all_scorecard_keys(self):
        import generate_posts as gp
        required = set(gp.REVIEW_SCHEMA["required"])
        for key in ("pass", "scores", "affiliate_link_present", "em_dash_count",
                    "ai_patterns_found", "flags", "rewrite_instructions"):
            self.assertIn(key, required)

    def test_all_stages_route_through_openrouter(self):
        # Every stage routes through OpenRouter on the single OPENROUTER_API_KEY --
        # no direct provider accounts. The review stage still requests schema-
        # enforced JSON (GPT-4o-mini honors it natively; fallbacks best-effort).
        source = (REPO / "generate_posts.py").read_text(encoding="utf-8")
        self.assertNotIn("api.anthropic.com", source)
        self.assertNotIn("ANTHROPIC_API_KEY", source)
        self.assertIn('"response_format"', source)
        self.assertIn('"json_schema"', source)
        import generate_posts as gp
        self.assertEqual(gp.REVIEW_CHAIN[0], "openai/gpt-4o-mini")

    def test_generate_yml_has_no_anthropic_secret(self):
        workflow = (REPO / ".github/workflows/generate.yml").read_text(encoding="utf-8")
        self.assertNotIn("ANTHROPIC_API_KEY", workflow)




class TestRefillAgent(unittest.TestCase):
    """Stage 0 refill: scrape parsing, validation, dedup, threshold gate."""

    FIXTURE = """
    <div data-asin="B0ABCD1234" data-component-type="s-search-result">
      <img src="https://m.media-amazon.com/images/I/71abcDEF._AC_UL320_.jpg"
           alt="CoolPup Elevated Dog Bed with Breathable Mesh, 43 inch, Grey">
      <span class="a-price-whole">36</span><span class="a-price-fraction">99</span>
      <span>4.6 out of 5 stars</span>
    </div>
    <div data-asin="B0SPONSOR9" data-component-type="s-search-result">
      >Sponsored<
      <img src="https://m.media-amazon.com/images/I/00spon._AC_UL320_.jpg"
           alt="SponsoredBrand Raised Pet Cot for Large Dogs">
    </div>
    <div data-asin="B0WXYZ5678" data-component-type="s-search-result">
      <img src="https://m.media-amazon.com/images/I/81wxyZ._AC_UL320_.jpg"
           alt="BarkLoft Cooling Elevated Pet Bed for Outdoor and Indoor Use">
      <span class="a-price-whole">45</span><span class="a-price-fraction">50</span>
      <span>4.4 out of 5 stars</span>
    </div>
    """

    def test_parse_search_results_extracts_cards(self):
        import refill_products as rp
        cards = rp.parse_search_results(self.FIXTURE)
        self.assertEqual(cards[0]["asin"], "B0ABCD1234")
        self.assertTrue(cards[0]["name"].startswith("CoolPup Elevated"))
        self.assertEqual(cards[0]["price"], "36.99")
        self.assertEqual(cards[0]["stars"], 4.6)
        self.assertTrue(cards[0]["image"].startswith("https://m.media-amazon.com/images/I/"))
        # sponsored card skipped, organic second card kept
        asins = [c["asin"] for c in cards]
        self.assertNotIn("B0SPONSOR9", asins)
        self.assertIn("B0WXYZ5678", asins)

    def test_validate_candidate_rejects_bad_data(self):
        import refill_products as rp
        good = {"asin": "B0ABCD1234", "name": "Real Product",
                "image": "https://m.media-amazon.com/images/I/x.jpg"}
        self.assertTrue(rp.validate_candidate(good))
        # invented / malformed ASINs must never pass
        self.assertFalse(rp.validate_candidate({**good, "asin": "B12345678"}))
        self.assertFalse(rp.validate_candidate({**good, "asin": "B0abcd1234"}))
        # off-host images must never pass (hotlink target must be amazon CDN)
        self.assertFalse(rp.validate_candidate(
            {**good, "image": "https://example.com/evil.jpg"}))
        self.assertFalse(rp.validate_candidate({**good, "image": None}))
        self.assertFalse(rp.validate_candidate({**good, "name": None}))
        # mobile-endpoint sponsored cards bake "Sponsored Ad - " into the alt
        # text itself, outside the >Sponsored< / popover-class markup checks
        # in parse_search_results -- validate_candidate is the last gate.
        self.assertFalse(rp.validate_candidate(
            {**good, "name": "Sponsored Ad - Some Product Title"}))

    def test_affiliate_url_uses_tag(self):
        import refill_products as rp
        entry = rp.build_entry({
            "topic": "best-heated-cat-bed", "title": "T", "keyword": "k",
            "species": "cat", "category": "cat-gear",
            "topical_sheet": "HAPPYPET_SHEET_ID_HOME", "amazon_search_query": "q"})
        self.assertIn("tag=pawpicks04-20", entry["affiliate_url"])
        self.assertEqual(entry["asin"], "NEEDS_ASIN")
        self.assertEqual(entry["image"], "NEEDS_IMAGE")

    def test_unpublished_count_and_threshold_semantics(self):
        import refill_products as rp
        products = [{"topic": "best-a"}, {"topic": "best-b"}, {"topic": "best-c"}]
        pub = {"a", "best-b"}  # dated posts store bare or best- slugs
        # only exact topic matches count as published here (mirrors generator)
        self.assertEqual(rp.unpublished_count(products, {"best-a", "best-b"}), 1)

    def test_valid_categories_not_just_gear(self):
        # run #22's queue ended up 20/23 dog-gear+cat-gear because the enum only
        # offered gear/food/health -- every toy, bed, litter, tech topic had
        # nowhere else to go. Guard against regressing to that 6-bucket set.
        import refill_products as rp
        specific = {"dog-toys", "cat-toys", "cat-litter", "dog-training", "pet-tech"}
        self.assertTrue(specific.issubset(set(rp.VALID_CATEGORIES)))
        # TOPIC_SCHEMA's enum is built from VALID_CATEGORIES -- keep them in sync
        self.assertEqual(
            rp.TOPIC_SCHEMA["properties"]["topics"]["items"]["properties"]["category"]["enum"],
            list(rp.VALID_CATEGORIES))

    def test_image_validation_rejects_svg_placeholder(self):
        # run #2 shipped an .svg sprite as a "product image" -- never again
        import refill_products as rp
        good = {"asin": "B0ABCD1234", "name": "Real Product",
                "image": "https://m.media-amazon.com/images/I/71x._AC_UL320_.jpg"}
        self.assertTrue(rp.validate_candidate(good))
        self.assertFalse(rp.validate_candidate(
            {**good, "image": "https://m.media-amazon.com/images/I/01rrzVoKd5L.svg"}))

    def test_paapi_response_maps_to_cards(self):
        import refill_products as rp
        data = {"SearchResult": {"Items": [{
            "ASIN": "B0PAAPI123",
            "ItemInfo": {"Title": {"DisplayValue": "KONG Classic Dog Toy, Large"}},
            "Images": {"Primary": {"Large": {"URL":
                "https://m.media-amazon.com/images/I/61kong.jpg"}}},
            "Offers": {"Listings": [{"Price": {"Amount": 12.5}}]},
            "CustomerReviews": {"StarRating": {"Value": 4.7}},
        }]}}
        cards = rp._paapi_items_to_cards(data)
        self.assertEqual(cards[0]["asin"], "B0PAAPI123")
        self.assertEqual(cards[0]["price"], "12.50")
        self.assertEqual(cards[0]["stars"], 4.7)
        self.assertTrue(rp.validate_candidate(cards[0]))

    def test_paapi_search_requires_keys(self):
        import refill_products as rp
        old = rp.PAAPI_ACCESS_KEY, rp.PAAPI_SECRET_KEY
        rp.PAAPI_ACCESS_KEY = rp.PAAPI_SECRET_KEY = ""
        try:
            with self.assertRaises(RuntimeError):
                rp.paapi_search("dog toy")
        finally:
            rp.PAAPI_ACCESS_KEY, rp.PAAPI_SECRET_KEY = old

    def test_refill_workflow_never_pushes_main(self):
        workflow = (REPO / ".github/workflows/refill.yml").read_text(encoding="utf-8")
        self.assertNotIn("git push origin main", workflow)
        self.assertIn("gh pr create", workflow)
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("concurrency", workflow)


class TestManualResolve(unittest.TestCase):
    """manual_resolve.py -- apply a browser-found product to a products.json
    placeholder, reusing refill_products.py's validate_candidate/
    apply_resolution/chewy_enrich. No Amazon or Chewy network code lives here."""

    def test_happy_path_applies_and_writes(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr
        import chewy_lookup as cl

        products = [{
            "topic": "best-automatic-litter-box",
            "title": "Best Automatic Litter Boxes",
            "keyword": "best automatic litter box",
            "species": "cat", "category": "cat-gear", "format": "roundup",
            "topical_sheet": "HAPPYPET_SHEET_ID_CATS",
            "name": "NEEDS_ASIN placeholder for best automatic litter box",
            "asin": "NEEDS_ASIN",
            "affiliate_url": "https://www.amazon.com/dp/NEEDS_ASIN?tag=pawpicks04-20",
            "image": "NEEDS_IMAGE", "price": None, "stars": None,
            "chewy_url": None, "chewy_price": None,
            "chewy_stock": None, "chewy_rating": None,
        }]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            # Force the no-credentials path explicitly rather than relying on
            # the ambient environment/vault lacking IMPACT_* -- chewy_lookup
            # now falls back to Maeve's SecretVault when the env vars are
            # unset, so this test must not depend on that vault being absent.
            with patch.object(rp, "PRODUCTS_PATH", path), \
                 patch.object(cl, "ACCOUNT_SID", ""), \
                 patch.object(cl, "AUTH_TOKEN", ""):
                mr.main([
                    "--topic", "best-automatic-litter-box",
                    "--name", "PETLIBRO Automatic Self-Cleaning Litter Box",
                    "--asin", "B0ABCD1234",
                    "--image", "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg",
                    "--price", "249.99", "--stars", "4.5",
                    "--runners-up", "Litter-Robot 4; PetSafe ScoopFree",
                ])
            written = json.loads(path.read_text(encoding="utf-8"))

        entry = written[0]
        self.assertEqual(entry["asin"], "B0ABCD1234")
        self.assertEqual(entry["name"], "PETLIBRO Automatic Self-Cleaning Litter Box")
        self.assertEqual(entry["image"],
                          "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg")
        self.assertEqual(entry["price"], "249.99")
        self.assertEqual(entry["stars"], 4.5)
        self.assertEqual(entry["runners_up"], "Litter-Robot 4; PetSafe ScoopFree")
        self.assertEqual(entry["affiliate_url"],
                          "https://www.amazon.com/dp/B0ABCD1234?tag=pawpicks04-20")
        # chewy_enrich runs for real here with credentials forced empty,
        # which returns an all-None dict cleanly -- must not crash
        self.assertIsNone(entry["chewy_url"])

    def test_apply_resolution_passes_upc_to_chewy_enrich(self):
        import refill_products as rp

        entry = {"topic": "best-x", "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}
        resolved = {"name": "Some Product", "asin": "B0ABCD1234",
                    "image": "https://m.media-amazon.com/images/I/x.jpg",
                    "price": "9.99", "stars": 4.0, "upc": "810189030893"}
        with patch.object(rp, "chewy_enrich", return_value={
                "chewy_url": None, "chewy_price": None,
                "chewy_stock": None, "chewy_rating": None}) as fake_enrich:
            rp.apply_resolution(entry, resolved)
        fake_enrich.assert_called_once_with("Some Product", "810189030893")
        self.assertEqual(entry["upc"], "810189030893")

    def test_rejects_bad_asin_shape_and_does_not_write(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-dog-ramps", "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            original = path.read_text(encoding="utf-8")
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "best-dog-ramps", "--name", "Some Ramp",
                        "--asin", "NOTREAL123",
                        "--image", "https://m.media-amazon.com/images/I/x.jpg",
                        "--price", "39.99", "--stars", "4.2",
                    ])
            self.assertEqual(path.read_text(encoding="utf-8"), original, "rejected candidate must not write")

    def test_rejects_wrong_image_host_and_does_not_write(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-cat-carrier-backpacks",
                     "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            original = path.read_text(encoding="utf-8")
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "best-cat-carrier-backpacks", "--name", "Some Carrier",
                        "--asin", "B0ABCD1234", "--image", "https://example.com/evil.jpg",
                        "--price", "59.99", "--stars", "4.3",
                    ])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_rejects_sponsored_prefixed_name_and_does_not_write(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-catnip-toys", "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            original = path.read_text(encoding="utf-8")
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "best-catnip-toys",
                        "--name", "Sponsored Ad - Fancy Catnip Toy",
                        "--asin", "B0ABCD1234",
                        "--image", "https://m.media-amazon.com/images/I/x.jpg",
                        "--price", "12.99", "--stars", "4.1",
                    ])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_rejects_unknown_topic(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-dog-ramps", "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "does-not-exist", "--name", "X",
                        "--asin", "B0ABCD1234",
                        "--image", "https://m.media-amazon.com/images/I/x.jpg",
                        "--price", "9.99", "--stars", "4.0",
                    ])

    def test_rejects_already_resolved_topic(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-dog-cooling-mat", "asin": "B0GG8LR3RW",
                     "image": "https://m.media-amazon.com/images/I/existing.jpg"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "best-dog-cooling-mat", "--name", "X",
                        "--asin", "B0NEWNEWNE",
                        "--image", "https://m.media-amazon.com/images/I/y.jpg",
                        "--price", "9.99", "--stars", "4.0",
                    ])

    def test_upc_flag_is_optional_and_populates_entry_when_provided(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr
        import chewy_lookup as cl

        products = [{"topic": "best-automatic-litter-box",
                     "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            with patch.object(rp, "PRODUCTS_PATH", path), \
                 patch.object(cl, "ACCOUNT_SID", ""), \
                 patch.object(cl, "AUTH_TOKEN", ""):
                mr.main([
                    "--topic", "best-automatic-litter-box",
                    "--name", "PETLIBRO Automatic Self-Cleaning Litter Box",
                    "--asin", "B0ABCD1234",
                    "--image", "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg",
                    "--price", "249.99", "--stars", "4.5",
                    "--upc", "810189030893",
                ])
            written = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(written[0]["upc"], "810189030893")

    def test_upc_omitted_leaves_entry_without_upc_key(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr
        import chewy_lookup as cl

        products = [{"topic": "best-automatic-litter-box",
                     "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            with patch.object(rp, "PRODUCTS_PATH", path), \
                 patch.object(cl, "ACCOUNT_SID", ""), \
                 patch.object(cl, "AUTH_TOKEN", ""):
                mr.main([
                    "--topic", "best-automatic-litter-box",
                    "--name", "PETLIBRO Automatic Self-Cleaning Litter Box",
                    "--asin", "B0ABCD1234",
                    "--image", "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg",
                    "--price", "249.99", "--stars", "4.5",
                ])
            written = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("upc", written[0])


class TestJsonIO(unittest.TestCase):
    """json_io.atomic_write_json / read_json -- crash-safe products.json I/O (F7)"""

    def setUp(self):
        import json_io
        self.json_io = json_io
        import tempfile
        self.tmpdir = Path(tempfile.mkdtemp())
        self.path = self.tmpdir / "data.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_atomic_write_roundtrip(self):
        self.json_io.atomic_write_json(self.path, [{"topic": "x"}])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [{"topic": "x"}])

    def test_atomic_write_trailing_newline(self):
        self.json_io.atomic_write_json(self.path, {"a": 1}, trailing_newline=True)
        self.assertTrue(self.path.read_text(encoding="utf-8").endswith("}\n"))

    def test_atomic_write_no_trailing_newline_by_default(self):
        self.json_io.atomic_write_json(self.path, {"a": 1})
        self.assertFalse(self.path.read_text(encoding="utf-8").endswith("\n"))

    def test_atomic_write_leaves_no_temp_files(self):
        self.json_io.atomic_write_json(self.path, {"a": 1})
        leftovers = sorted(p.name for p in self.tmpdir.iterdir() if p.name != "data.json")
        self.assertEqual(leftovers, [])

    def test_atomic_write_overwrites_existing(self):
        self.json_io.atomic_write_json(self.path, {"v": 1})
        self.json_io.atomic_write_json(self.path, {"v": 2})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"v": 2})

    def test_read_json_missing_returns_default(self):
        self.assertEqual(self.json_io.read_json(self.path, default=[]), [])

    def test_read_json_valid(self):
        self.path.write_text('{"a": 1}', encoding="utf-8")
        self.assertEqual(self.json_io.read_json(self.path), {"a": 1})

    def test_read_json_corrupt_raises_clear_error(self):
        self.path.write_text('{"a": 1', encoding="utf-8")  # truncated / corrupt
        with self.assertRaises(self.json_io.CorruptJSONError):
            self.json_io.read_json(self.path)


class TestArticlePersistence(unittest.TestCase):
    """persist_generated_article() -- draft is written LAST so a crash never
    leaves a publishable draft with no pin queued (F5, orphan-draft prevention)."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_pin_queue_and_draft(self):
        draft = self.tmp / "DRAFT-x.md"
        pq = self.tmp / "_pin_queue" / "x.json"
        self.gp.persist_generated_article(draft, "body text", pq, {"slug": "x"})
        self.assertEqual(draft.read_text(encoding="utf-8"), "body text")
        self.assertEqual(json.loads(pq.read_text(encoding="utf-8")), {"slug": "x"})

    def test_draft_not_written_if_pin_queue_fails(self):
        # A non-serializable pin_data makes json.dumps raise BEFORE the draft is
        # written -- the draft must not exist, or Stage 2 would publish an orphan.
        draft = self.tmp / "DRAFT-x.md"
        pq = self.tmp / "_pin_queue" / "x.json"
        with self.assertRaises(TypeError):
            self.gp.persist_generated_article(draft, "body text", pq, {"bad": object()})
        self.assertFalse(draft.exists(), "orphan draft must not exist when pin staging fails")


class TestChewyApiRetry(unittest.TestCase):
    """_impact_get retries on 429/503 instead of dropping Chewy enrichment for the
    whole remaining batch on the first rate-limit (F8)."""

    def _http_error(self, code):
        import urllib.error, io
        return urllib.error.HTTPError("https://api.impact.com/x", code, "err", {}, io.BytesIO(b"body"))

    def test_retries_on_429_then_succeeds(self):
        import chewy_lookup as cl
        from unittest.mock import patch, MagicMock
        calls = {"n": 0}
        def fake_urlopen(req, timeout=15):
            calls["n"] += 1
            if calls["n"] == 1:
                raise self._http_error(429)
            resp = MagicMock()
            resp.read.return_value = json.dumps({"Items": [{"Name": "ok"}]}).encode()
            cm = MagicMock()
            cm.__enter__.return_value = resp
            cm.__exit__.return_value = False
            return cm
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("time.sleep"):
            result = cl._impact_get("/Catalogs/1/Items", {"Keyword": "x"})
        self.assertEqual(calls["n"], 2, "should have retried once after the 429")
        self.assertEqual(result["Items"][0]["Name"], "ok")

    def test_non_transient_error_raises_without_retry(self):
        import chewy_lookup as cl
        from unittest.mock import patch
        calls = {"n": 0}
        def fake_urlopen(req, timeout=15):
            calls["n"] += 1
            raise self._http_error(500)
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("time.sleep"):
            with self.assertRaises(cl.ChewyAPIError):
                cl._impact_get("/x")
        self.assertEqual(calls["n"], 1, "a 500 must not be retried")


class TestModelRoutingChains(unittest.TestCase):
    """3-stage Writer-Judge-Fixer routing: each stage is an ordered OpenRouter
    fallback chain (primary -> fallback 1 -> fallback 2), tried in order."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_generator_chain_models_and_order(self):
        self.assertEqual(self.gp.GENERATOR_CHAIN, [
            "anthropic/claude-sonnet-5",
            "google/gemini-2.5-flash",
            "meta-llama/llama-3.3-70b-instruct",
        ])

    def test_review_chain_models_and_order(self):
        self.assertEqual(self.gp.REVIEW_CHAIN, [
            "openai/gpt-4o-mini",
            "qwen/qwen-2.5-72b-instruct",
            "mistralai/mistral-small-24b-instruct-2501",
        ])

    def test_rewrite_chain_models_and_order(self):
        self.assertEqual(self.gp.REWRITE_CHAIN, [
            "deepseek/deepseek-chat",
            "qwen/qwen-2.5-coder-32b-instruct",
            "cohere/command-r-08-2024",
        ])

    def _ok(self, content="OK"):
        return json.dumps({
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 3},
        }).encode()

    def test_chain_uses_primary_first(self):
        from unittest.mock import patch
        seen = []
        def fake_http_post(url, payload, headers, **kw):
            seen.append(json.loads(payload)["model"])
            return self._ok("PRIMARY")
        with patch.object(self.gp, "http_post", side_effect=fake_http_post), \
             patch.dict(self.gp.os.environ, {"OPENROUTER_API_KEY": "k"}):
            out = self.gp.call_openrouter_chain(
                ["m1", "m2", "m3"], [{"role": "user", "content": "x"}],
                label="t", max_tokens=100, temperature=0.5)
        self.assertEqual(out, "PRIMARY")
        self.assertEqual(seen, ["m1"])  # only the primary was called

    def test_chain_falls_over_to_next_on_failure(self):
        from unittest.mock import patch
        seen = []
        def fake_http_post(url, payload, headers, **kw):
            model = json.loads(payload)["model"]
            seen.append(model)
            if model in ("m1", "m2"):
                raise RuntimeError(f"{model} rate limited")
            return self._ok("FALLBACK2")
        with patch.object(self.gp, "http_post", side_effect=fake_http_post), \
             patch.dict(self.gp.os.environ, {"OPENROUTER_API_KEY": "k"}):
            out = self.gp.call_openrouter_chain(
                ["m1", "m2", "m3"], [{"role": "user", "content": "x"}],
                label="t", max_tokens=100, temperature=0.5)
        self.assertEqual(out, "FALLBACK2")
        self.assertEqual(seen, ["m1", "m2", "m3"])  # tried in order until success

    def test_chain_raises_when_all_models_exhausted(self):
        from unittest.mock import patch
        with patch.object(self.gp, "http_post", side_effect=RuntimeError("down")), \
             patch.dict(self.gp.os.environ, {"OPENROUTER_API_KEY": "k"}):
            with self.assertRaises(RuntimeError):
                self.gp.call_openrouter_chain(
                    ["m1", "m2", "m3"], [{"role": "user", "content": "x"}],
                    label="t", max_tokens=100, temperature=0.5)

    def test_chain_parse_failure_triggers_fallover(self):
        # A model that returns unparseable output (e.g. non-JSON for the review
        # stage) must fall over to the next model, not return junk.
        from unittest.mock import patch
        def fake_http_post(url, payload, headers, **kw):
            model = json.loads(payload)["model"]
            return self._ok("not json" if model == "m1" else '{"ok": true}')
        with patch.object(self.gp, "http_post", side_effect=fake_http_post), \
             patch.dict(self.gp.os.environ, {"OPENROUTER_API_KEY": "k"}):
            out = self.gp.call_openrouter_chain(
                ["m1", "m2"], [{"role": "user", "content": "x"}],
                label="t", max_tokens=100, temperature=0.1, parse=json.loads)
        self.assertEqual(out, {"ok": True})


class TestGeneratorModel(unittest.TestCase):
    """call_generator routes primary generation through the GENERATOR_CHAIN
    (Claude Sonnet 5 primary) via OpenRouter, falling over down the chain."""

    def test_primary_uses_generator_chain_head_via_openrouter(self):
        import generate_posts as gp
        from unittest.mock import patch
        captured = {}
        def fake_http_post(url, payload, headers, **kw):
            captured["url"] = url
            captured["payload"] = json.loads(payload)
            return json.dumps({
                "choices": [{"message": {"content": "ARTICLE BODY"}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 5},
            }).encode()
        with patch.object(gp, "http_post", side_effect=fake_http_post), \
             patch.dict(gp.os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            out = gp.call_generator("write an article", "unused")
        self.assertEqual(out, "ARTICLE BODY")
        self.assertEqual(captured["url"], gp.OPENROUTER_URL)
        self.assertEqual(captured["payload"]["model"], gp.GENERATOR_CHAIN[0])
        self.assertEqual(gp.GENERATOR_CHAIN[0], "anthropic/claude-sonnet-5")

    def test_falls_over_to_second_generator_model_when_primary_fails(self):
        import generate_posts as gp
        from unittest.mock import patch
        seen = []
        def fake_http_post(url, payload, headers, **kw):
            model = json.loads(payload)["model"]
            seen.append(model)
            if model == gp.GENERATOR_CHAIN[0]:
                raise RuntimeError("primary down")
            return json.dumps({"choices": [{"message": {"content": "FALLBACK BODY"},
                               "finish_reason": "stop"}], "usage": {}}).encode()
        with patch.object(gp, "http_post", side_effect=fake_http_post), \
             patch.dict(gp.os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            out = gp.call_generator("write an article", "unused")
        self.assertEqual(out, "FALLBACK BODY")
        self.assertEqual(seen[:2], [gp.GENERATOR_CHAIN[0], gp.GENERATOR_CHAIN[1]])


class TestGenerationPromptHygiene(unittest.TestCase):
    """The generation prompt must not itself model the AI-tells it forbids
    (em dashes, first-person openings), and must ban the words that failed review."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp
        self.prod = {"name": "Test Cooling Mat", "affiliate_url": "https://amzn.to/testXYZ",
                     "stars": "4.5", "review_count": "1200", "price": "29.99",
                     "image": "img", "category": "dog-gear"}

    def _full(self, fmt):
        # The model receives the system rules AND the user task; assert on both.
        return self.gp.GENERATOR_SYSTEM_PROMPT + "\n" + self.gp.make_prompt(
            "Best Test Mats", "best test mat", "best-test-mat", fmt, self.prod, "", "")

    def test_roundup_prompt_models_no_em_dashes(self):
        # At most the single illustrative em dash in the "NEVER use em dashes (—)" rule.
        self.assertLessEqual(self._full("roundup").count("—"), 1)

    def test_buying_guide_prompt_models_no_em_dashes(self):
        self.assertLessEqual(self._full("buying_guide").count("—"), 1)

    def test_opening_examples_are_second_person(self):
        p = self._full("roundup")
        for fp in ["My dog", "Our cat", "I spent $40"]:
            self.assertNotIn(fp, p, "opening examples must not model first-person voice")
        self.assertIn("You step away", p)

    def test_bans_words_that_failed_review(self):
        p = self._full("roundup")
        for w in ["prioritize", "leverage", "the bottom line is"]:
            self.assertIn(w, p)

    def test_warns_against_rule_of_three(self):
        self.assertIn("rule-of-three", self._full("roundup").lower())

    def test_affiliate_url_injected_not_search_engine(self):
        p = self._full("roundup")
        self.assertIn("https://amzn.to/testXYZ", p)
        self.assertNotIn("google.com/search", p)


class TestGeneratorSystemPrompt(unittest.TestCase):
    """Generator uses a system prompt (rules) + user task, XML-structured, for adherence."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_system_prompt_carries_the_hard_rules(self):
        sp = self.gp.GENERATOR_SYSTEM_PROMPT
        low = sp.lower()
        self.assertIn("em dash", low)
        self.assertIn("first-person", low)
        self.assertIn("<writing_rules>", sp)
        self.assertIn("<output_format>", sp)

    def test_system_prompt_models_no_em_dashes(self):
        self.assertLessEqual(self.gp.GENERATOR_SYSTEM_PROMPT.count("—"), 1)

    def test_call_generator_sends_system_then_user_at_low_temp(self):
        from unittest.mock import patch
        captured = {}
        def fake_http_post(url, payload, headers, **kw):
            captured["payload"] = json.loads(payload)
            return json.dumps({"choices": [{"message": {"content": "X"}, "finish_reason": "stop"}],
                               "usage": {}}).encode()
        with patch.object(self.gp, "http_post", side_effect=fake_http_post), \
             patch.dict(self.gp.os.environ, {"OPENROUTER_API_KEY": "k"}):
            self.gp.call_generator("user task", "unused", system="SYSTEM RULES")
        msgs = captured["payload"]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        self.assertEqual(msgs[0]["content"], "SYSTEM RULES")
        self.assertLessEqual(captured["payload"]["temperature"], 0.5)


class TestRewritePromptGuardrails(unittest.TestCase):
    """The rewrite prompt must re-assert the hard rules so rewrites don't degrade a
    clean draft into fabrication/first-person (the observed 'My Lab Buster' failure)."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp
        self.p = gp.make_rewrite_prompt("Title", "kw", "body content here",
                                        "warmth is low", "https://amzn.to/x", "Prod")

    def test_rewrite_forbids_em_dashes(self):
        self.assertIn("em dash", self.p.lower())

    def test_rewrite_forbids_first_person(self):
        self.assertIn("first-person", self.p.lower())

    def test_rewrite_forbids_inventing_numbers_and_anecdotes(self):
        self.assertIn("invent", self.p.lower())
        self.assertIn("anecdote", self.p.lower())

    def test_rewrite_does_not_ask_for_a_human_moment(self):
        # The instruction that produced first-person anecdotes must be gone.
        self.assertNotIn("add a concrete human moment", self.p)


class TestAuthoritativeGate(unittest.TestCase):
    """authoritative_gate computes pass from scores + hard-checks and IGNORES
    the reviewer's `pass` boolean -- unlike evaluate_scorecard, a reviewer
    saying pass=false cannot hold an otherwise-clean, on-standard article."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def _card(self, hv=4, wa=4, re=4, ac=4, **extra):
        card = {"scores": {"human_voice": hv, "warmth": wa,
                           "readability": re, "accuracy": ac}}
        card.update(extra)
        return card

    def test_reviewer_pass_false_does_not_hold_a_clean_on_standard_article(self):
        # scores all clear the bar, body clean -> PASS even though reviewer said fail
        passed, flags = self.gp.authoritative_gate(
            self._card(**{"pass": False, "ai_patterns_found": ["em dash present"]}),
            "clean body without the forbidden character.")
        self.assertTrue(passed)

    def test_score_below_minimum_fails(self):
        passed, flags = self.gp.authoritative_gate(self._card(hv=2), "clean body")
        self.assertFalse(passed)
        self.assertTrue(any("human_voice=2" in str(f) for f in flags))

    def test_real_em_dash_in_body_fails(self):
        passed, flags = self.gp.authoritative_gate(self._card(), "has an — em dash")
        self.assertFalse(passed)
        self.assertIn("em_dash_in_body", flags)

    def test_fabrication_flag_fails(self):
        passed, _ = self.gp.authoritative_gate(
            self._card(flags=["fabricated statistic in section 2"]), "clean body")
        self.assertFalse(passed)

    def test_missing_score_fails(self):
        passed, _ = self.gp.authoritative_gate(self._card(hv=None), "clean body")
        self.assertFalse(passed)

    def test_cautionary_unsourced_prose_flag_does_not_fail(self):
        # A reviewer's cautionary prose about verified data ("no source", etc.)
        # must NOT be treated as fabrication when the accuracy score passes -- this
        # is the false positive that held a clean, verified-data article. Only
        # explicit fabrication verbs (fabricated/invented/made up) hard-fail.
        passed, _ = self.gp.authoritative_gate(
            self._card(flags=["4.8/5 rating stated as fact with no source or hedge"]),
            "clean body")
        self.assertTrue(passed)


class TestBuildVerifiedFacts(unittest.TestCase):
    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_builds_from_available_fields(self):
        out = self.gp.build_verified_facts(
            {"stars": "4.8", "review_count": 1200, "price": "34.99"})
        self.assertIn("Star rating: 4.8/5", out)
        self.assertIn("Review count: 1,200", out)
        self.assertIn("Price: $34.99", out)

    def test_empty_when_no_data(self):
        self.assertEqual(self.gp.build_verified_facts({"name": "X"}), "")


class TestReviewPromptVerifiedFacts(unittest.TestCase):
    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_verified_facts_block_present_and_instructs_not_to_flag(self):
        p = self.gp.make_review_prompt("T", "k", "body",
                                       verified_facts="Star rating: 4.8/5; Price: $34.99")
        self.assertIn("Star rating: 4.8/5", p)
        self.assertIn("Price: $34.99", p)
        low = p.lower()
        self.assertIn("verified", low)
        self.assertIn("do not flag", low)

    def test_no_block_when_no_verified_facts(self):
        p = self.gp.make_review_prompt("T", "k", "body")
        self.assertNotIn("VERIFIED PRODUCT DATA", p)


class TestSelectNextTopic(unittest.TestCase):
    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def _products(self):
        return {
            "a": {"topic": "a", "title": "A", "keyword": "ka", "format": "single_review"},
            "b": {"topic": "b", "title": "B", "keyword": "kb", "format": "roundup"},
            "c": {"topic": "c", "title": "C"},  # missing keyword/format -> skipped
        }

    def test_returns_first_unpublished_valid_topic(self):
        slug, product = self.gp.select_next_topic(self._products(), used_slugs=set())
        self.assertEqual(slug, "a")
        self.assertEqual(product["format"], "single_review")

    def test_skips_used_slugs(self):
        slug, product = self.gp.select_next_topic(self._products(), used_slugs={"a"})
        self.assertEqual(slug, "b")

    def test_skips_entries_missing_required_fields(self):
        # only 'c' left unused, but 'c' is invalid -> None
        slug_product = self.gp.select_next_topic(self._products(), used_slugs={"a", "b"})
        self.assertIsNone(slug_product)

    def test_returns_none_when_queue_empty(self):
        self.assertIsNone(self.gp.select_next_topic({}, used_slugs=set()))


class TestBuildWriterInputs(unittest.TestCase):
    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_single_review_inputs_have_system_and_user(self):
        product = {"topic": "best-x", "title": "Best X", "keyword": "best x",
                   "format": "single_review", "name": "The X", "category": "dogs",
                   "species": "dog", "affiliate_url": "https://amzn.to/abc"}
        out = self.gp.build_writer_inputs("best-x", product)
        self.assertEqual(out["system"], self.gp.GENERATOR_SYSTEM_PROMPT)
        self.assertIn("Best X", out["user"])
        self.assertEqual(out["fmt"], "single_review")
        self.assertEqual(out["species"], "dog")
        self.assertNotIn("{{ALTERNATIVE_PRODUCTS}}", out["user"])  # substituted

    def test_roundup_uses_products_json_runners_up_not_groq(self):
        product = {"topic": "best-mats", "title": "Best Mats", "keyword": "best mats",
                   "format": "roundup", "name": "TopMat", "category": "dogs",
                   "species": "dog", "runners_up": "MatA;MatB;MatC"}
        out = self.gp.build_writer_inputs("best-mats", product)
        self.assertIn("MatA", out["user"])
        self.assertIn("EXACTLY 3", out["user"])
        self.assertNotIn("{{ALTERNATIVE_PRODUCTS}}", out["user"])

    def test_roundup_without_runners_up_uses_static_fallback_no_groq(self):
        product = {"topic": "best-mats", "title": "Best Mats", "keyword": "best mats",
                   "format": "roundup", "name": "TopMat", "category": "dogs",
                   "species": "dog"}
        out = self.gp.build_writer_inputs("best-mats", product)
        self.assertIn("well-known brands", out["user"])
        self.assertNotIn("{{ALTERNATIVE_PRODUCTS}}", out["user"])


class TestStageArticle(unittest.TestCase):
    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_stages_draft_and_pin_queue(self):
        import tempfile, json
        from pathlib import Path
        from unittest.mock import patch
        gp = self.gp
        product = {"topic": "best-x", "title": "Best X", "keyword": "best x",
                   "format": "single_review", "name": "The X", "category": "dogs",
                   "species": "dog", "affiliate_url": "https://amzn.to/abc"}
        body = "## Heading\n\n" + ("word " * 800)  # >700 words, >2000 chars
        with tempfile.TemporaryDirectory() as td:
            posts = Path(td) / "_posts"; posts.mkdir()
            pinq  = Path(td) / "_pin_queue"; pinq.mkdir()
            with patch.object(gp, "POSTS_DIR", posts), \
                 patch.object(gp, "REPO_DIR", Path(td)), \
                 patch.object(gp, "PIN_GEN_AVAILABLE", False):
                out = gp.stage_article("best-x", product, body,
                                       pin_desc="Great mat for dogs.", index=0)
            draft = posts / "DRAFT-best-x.md"
            self.assertTrue(draft.exists())
            text = draft.read_text(encoding="utf-8")
            self.assertIn('layout: post', text)
            self.assertIn('affiliate_url: "https://amzn.to/abc"', text)
            pin_json = pinq / "best-x.json"
            self.assertTrue(pin_json.exists())
            data = json.loads(pin_json.read_text(encoding="utf-8"))
            self.assertEqual(data["slug"], "best-x")
            self.assertEqual(out["draft_path"], draft)

    def test_pin_source_prefers_curated_image_over_asin(self):
        # Regression: the legacy /images/P/{ASIN} scheme returns a 43-byte
        # placeholder for modern (B0G...) ASINs, so the pin rendered text-only.
        # stage_article must feed make_pin the curated /images/I/ image (which
        # fetch_image handles), not rebuild the source from the ASIN.
        import tempfile
        from pathlib import Path
        from unittest.mock import patch, MagicMock
        gp = self.gp
        curated = "https://m.media-amazon.com/images/I/71cMi1EJVIL._AC_SX425_.jpg"
        product = {"topic": "best-x", "title": "Best X", "keyword": "best x",
                   "format": "roundup", "name": "The X", "category": "dog-gear",
                   "species": "dog", "affiliate_url": "https://amzn.to/abc",
                   "asin": "B0GG8LR3RW", "image": curated}
        body = "## Heading\n\n" + ("word " * 800)
        fake_pin = MagicMock(return_value=curated)
        with tempfile.TemporaryDirectory() as td:
            posts = Path(td) / "_posts"; posts.mkdir()
            pinq  = Path(td) / "_pin_queue"; pinq.mkdir()
            with patch.object(gp, "POSTS_DIR", posts), \
                 patch.object(gp, "REPO_DIR", Path(td)), \
                 patch.object(gp, "PIN_GEN_AVAILABLE", True), \
                 patch.object(gp, "make_pin_for_post", fake_pin):
                gp.stage_article("best-x", product, body,
                                 pin_desc="Great mat for dogs.", index=0)
            pin_source = fake_pin.call_args.args[2]
            self.assertEqual(pin_source, curated,
                "pin must use the curated /images/I/ image, not an ASIN-derived URL")
            self.assertNotIn("/images/P/", pin_source)

    def test_pin_source_falls_back_to_asin_when_no_curated_image(self):
        # When no curated image exists, derive the pin source from the ASIN so
        # products without a hand-sourced image still attempt a product photo.
        import tempfile
        from pathlib import Path
        from unittest.mock import patch, MagicMock
        gp = self.gp
        product = {"topic": "best-x", "title": "Best X", "keyword": "best x",
                   "format": "roundup", "name": "The X", "category": "dog-gear",
                   "species": "dog", "affiliate_url": "https://amzn.to/abc",
                   "asin": "B0GG8LR3RW"}  # no curated image
        body = "## Heading\n\n" + ("word " * 800)
        fake_pin = MagicMock(return_value="x")
        with tempfile.TemporaryDirectory() as td:
            posts = Path(td) / "_posts"; posts.mkdir()
            pinq  = Path(td) / "_pin_queue"; pinq.mkdir()
            with patch.object(gp, "POSTS_DIR", posts), \
                 patch.object(gp, "REPO_DIR", Path(td)), \
                 patch.object(gp, "PIN_GEN_AVAILABLE", True), \
                 patch.object(gp, "make_pin_for_post", fake_pin):
                gp.stage_article("best-x", product, body,
                                 pin_desc="Great mat for dogs.", index=0)
            pin_source = fake_pin.call_args.args[2]
            self.assertEqual(
                pin_source,
                "https://images-na.ssl-images-amazon.com/images/P/B0GG8LR3RW.01.LZZZZZZZ.jpg")


class TestFbMessage(unittest.TestCase):
    """The Facebook-queue message must not reuse one repetitive fallback for
    every uncurated slug (Director feedback: 'Looking for the best... Here's
    the one worth buying' appeared across multiple posts). Curated per-slug
    hooks stay; the fallback now varies deterministically by slug."""

    def setUp(self):
        import push_pins_to_sheets as pk
        self.pk = pk

    def test_curated_hook_preserved(self):
        msg = self.pk._build_fb_message(
            "best-joint-supplement-dogs", "Best Joint Supplements", "https://x/y/")
        self.assertIn("Stiff joints don't have to slow your dog down", msg)

    def test_fallback_drops_old_repetitive_template(self):
        msg = self.pk._build_fb_message(
            "best-dog-cooling-mat", "Best Dog Cooling Mats to Beat the Summer Heat",
            "https://happypetproductreviews.com/dog-gear/best-dog-cooling-mat/")
        self.assertNotIn("Looking for the best", msg)
        self.assertNotIn("Here's the one worth buying", msg)

    def test_fallback_deterministic_per_slug(self):
        args = ("best-dog-cooling-mat", "Best Dog Cooling Mats", "https://x/y/")
        self.assertEqual(self.pk._build_fb_message(*args), self.pk._build_fb_message(*args))

    def test_fallback_template_varies_by_slug_not_just_topic(self):
        # Same title (same topic), different slugs -> the sentence itself must
        # vary, not only the interpolated topic.
        title = "Best Cooling Mats"
        msgs = {self.pk._build_fb_message(f"best-cooling-mat-{i}", title, "https://x/y/")
                for i in range(12)}
        self.assertGreater(len(msgs), 1, "fallback sentence must vary across slugs")

    def test_message_has_emoji_and_strips_query(self):
        msg = self.pk._build_fb_message(
            "best-widget", "Best Widgets", "https://x/dog-gear/best-widget/?utm=1")
        self.assertTrue(msg.startswith("\U0001f43e"))
        self.assertIn("https://x/dog-gear/best-widget/", msg)
        self.assertNotIn("?utm=1", msg)


# ---------------------------------------------------------------------------
# Category -> homepage topic-button mapping guard (recovery #45)
#
# The topic pills in _layouts/home.html bucket a post by matching keywords IN
# ITS CATEGORY SLUG. A category containing none of these keywords (the old
# "dog-gear"/"cat-gear" catch-all) is invisible under every topic button --
# the drift this guard exists to prevent from regressing. Keep PILL_KEYWORDS in
# sync with the `{% if post_cat contains ... %}` chain in _layouts/home.html.
# ---------------------------------------------------------------------------
PILL_KEYWORDS = (
    "toy", "scratch", "chew",                    # Toys
    "bed", "crate",                              # Beds & Crates
    "feed", "food", "water", "fountain",         # Feeding
    "carrier", "travel", "stroller",             # Travel
    "collar", "harness", "leash",                # Collars & Harnesses
    "litter", "grooming", "health", "training",  # Care & Training
    "tech", "camera", "gps",                     # Tech
)

# products.json topics deliberately left in a generic bucket because no
# existing pill fits (Director call, 2026-07-22). A post must never *ship* in
# one of these -- a real category gets assigned at publish time instead.
KNOWN_UNMAPPED_QUEUE = {"best-dog-pools"}


def _category_maps_to_pill(category: str) -> bool:
    cat = (category or "").lower()
    return any(kw in cat for kw in PILL_KEYWORDS)


def _post_first_category(md_path: Path):
    lines = md_path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        s = line.strip()
        if s.startswith("categories:"):
            inside = s.split(":", 1)[1].strip().strip("[]")
            first = inside.split(",")[0].strip().strip('"').strip("'")
            return first or None
    return None


def _norm_path(url: str) -> str:
    """Reduce a pin URL (or a bare path) to '/segment/segment/' -- host, query
    and fragment stripped, leading+trailing slash guaranteed."""
    path = url.split("://", 1)[-1]
    if "/" in path and "://" in url:
        path = "/" + path.split("/", 1)[1]
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path = path + "/"
    return path


def _post_file_for_slug(slug: str):
    for h in (REPO / "_posts").glob(f"*-{slug}.md"):
        parts = h.stem.split("-", 3)
        if len(parts) == 4 and parts[3] == slug:
            return h
    return None


def _post_redirect_paths(md_path: Path):
    lines = md_path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    out, in_block = [], False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        stripped = line.strip()
        if stripped.startswith("redirect_from:"):
            rest = stripped.split(":", 1)[1].strip()
            if rest.startswith("["):
                for item in rest.strip("[]").split(","):
                    it = item.strip().strip('"').strip("'")
                    if it:
                        out.append(_norm_path(it))
            else:
                in_block = True
            continue
        if in_block:
            if stripped.startswith("- "):
                out.append(_norm_path(stripped[2:].strip().strip('"').strip("'")))
            elif stripped:
                in_block = False
    return out


class TestCategoryPillMapping(unittest.TestCase):
    """Every published post must sit in a category the homepage topic buttons
    can surface. Guards against the dog-gear/cat-gear drift (recovery #45)."""

    def test_every_published_post_maps_to_a_pill(self):
        offenders = []
        for md in sorted((REPO / "_posts").glob("*.md")):
            cat = _post_first_category(md)
            if not _category_maps_to_pill(cat):
                offenders.append(f"{md.name} -> {cat}")
        self.assertEqual(
            offenders, [],
            "posts orphaned from every topic button: " + "; ".join(offenders))

    def test_products_json_categories_map_to_a_pill(self):
        data = json.loads((REPO / "products.json").read_text(encoding="utf-8"))
        entries = list(data.values()) if isinstance(data, dict) else data
        offenders = []
        for e in entries:
            slug = e.get("topic", "?")
            if slug in KNOWN_UNMAPPED_QUEUE:
                continue
            if not _category_maps_to_pill(e.get("category", "")):
                offenders.append(f"{slug} -> {e.get('category')}")
        self.assertEqual(
            offenders, [],
            "products.json topics with no pill: " + "; ".join(offenders))

    def test_pill_keywords_stay_in_sync_with_home_html(self):
        # If the pill chain in home.html changes, this flags PILL_KEYWORDS as
        # stale so the two never silently diverge.
        home = (REPO / "_layouts" / "home.html").read_text(encoding="utf-8")
        for kw in PILL_KEYWORDS:
            self.assertIn(
                f'contains "{kw}"', home,
                f'"{kw}" in PILL_KEYWORDS but not referenced in home.html')

    def test_every_fired_pin_still_resolves(self):
        # A pin was fired to Pinterest at /{old-category}/{slug}/. Re-categorizing
        # a post changes its permalink; unless the post carries a redirect_from
        # for the old path, that live pin 404s. Guards every already-sent pin.
        sent = REPO / "_pin_queue" / "sent"
        unresolved = []
        for jf in sorted(sent.glob("*.json")):
            url = json.loads(jf.read_text(encoding="utf-8")).get("article_url", "")
            if not url:
                continue
            pinned = _norm_path(url)
            post = _post_file_for_slug(jf.stem)
            if post is None:
                continue
            current = _norm_path(f"/{_post_first_category(post)}/{jf.stem}/")
            if current == pinned:
                continue
            if pinned not in _post_redirect_paths(post):
                unresolved.append(f"{jf.stem}: pinned {pinned}, post now {current}, no redirect")
        self.assertEqual(
            unresolved, [], "fired pins that would 404: " + "; ".join(unresolved))


if __name__ == "__main__":
    unittest.main(verbosity=2)
