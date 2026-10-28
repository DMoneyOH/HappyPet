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

import contextlib
import json
import os
import re
import sys
import tempfile
import textwrap
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
# The question block every new article carries (generate_posts.QUESTION_BLOCK_RULE):
# three question H2s, each answered in 40 to 80 words. Fixtures that pass the
# post-review gate include it.
QUESTION_BLOCK = """
## How long should a dog work on a puzzle toy each day?

A dog puzzle toy works best in short daily sessions of five to fifteen minutes.
Shorter sessions keep a dog keen, and longer ones tend to end in frustration or a
flipped board. If a dog walks away before finishing, drop to an easier level and
build back up over the following week.

## Are puzzle toys safe for dogs that chew hard?

Most plastic puzzle toys are built for supervised play, not for chewing. A dog
that destroys toys can crack the discs and swallow the pieces, so stay in the room
during each session. For a strong chewer, pick a solid rubber feeder instead and
put the plastic puzzle away between sessions.

## When can a puppy start using a puzzle toy?

Most puppies can start with a simple food puzzle at eight to ten weeks old. Begin
with the easiest setting and a strong-smelling treat so the puppy learns that
moving a piece pays off. Move to sliding or rotating designs once the puppy solves
the first level without help.
""".strip()

GOOD_ARTICLE = ("""
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

""" + QUESTION_BLOCK + """

## Final Thoughts

The [Outward Hound Nina Ottosson Dog Tornado](https://amzn.to/3TestABC) is the
right starting point for most dogs who need daily mental engagement without a large investment. It is affordable, durable, and the multi-level
design stays challenging longer than single-layer alternatives.
""").strip()

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
            "https://www.amazon.com/dp/B00AKOQYXY?tag=happypetdc-20",
        )
        self.gp.validate_output("review", art, "test-slug")

    def test_expected_affiliate_url_present_passes_review_gate(self):
        # When the entry's exact affiliate_url is supplied, it must appear verbatim
        url = "https://www.amazon.com/dp/B00AKOQYXY?tag=happypetdc-20"
        art = GOOD_ARTICLE.replace("https://amzn.to/3TestABC", url)
        self.gp.validate_output("review", art, "test-slug", affiliate_url=url)

    def test_missing_affiliate_link_fails_at_review_gate(self):
        # Gate 2 (post-review) is the publish gate: a truly missing link must raise
        no_link = GOOD_ARTICLE.replace("https://amzn.to/3TestABC", "")
        with self.assertRaises(self.gp.GenerationStageError):
            self.gp.validate_output("review", no_link, "test-slug")

    def test_wrong_affiliate_link_fails_when_expected_url_given(self):
        # A link different from the expected one (e.g. hallucinated) must raise
        expected = "https://www.amazon.com/dp/B00AKOQYXY?tag=happypetdc-20"
        art = GOOD_ARTICLE.replace(
            "https://amzn.to/3TestABC",
            "https://www.amazon.com/dp/WRONG9999?tag=happypetdc-20",
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
        p["affiliate_url"] = "https://www.amazon.com/dp/NEEDS_ASIN?tag=happypetdc-20"
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

    def test_every_entry_carries_a_valid_pull_date(self):
        """stage_article holds an entry without a valid pulled_on, so the queue
        must not carry one (backfilled from products.json history 2026-10-13)."""
        products = json.loads((REPO / "products.json").read_text(encoding="utf-8"))
        bad = []
        for e in products:
            try:
                self.gp.assert_pull_date("products.json", e.get("pulled_on"), e.get("topic"))
            except self.gp.GenerationStageError as err:
                bad.append(str(err))
        self.assertEqual(bad, [])


class TestAmazonAssociatesTag(unittest.TestCase):
    """Every Amazon link in products.json and _posts/ is a full amazon.com link
    carrying exactly the current Associates tag: no other tag, no missing tag,
    and no amzn.to short link.

    The tag changed on 2026-10-02 after the old Associates application was
    rejected. The expected value is a literal on purpose: refill_products'
    AFFILIATE_TAG is env-derived and is the thing under test, so importing it
    would make this test agree with whatever the code says.

    amzn.to short links are banned outright: their tag lives inside Amazon's
    redirect, so nothing offline can tell which account they credit.
    """

    TAG = "happypetdc-20"
    STALE_TAGS = ("pawpicks04-20",)
    # Anchored so image hosts (m.media-amazon.com, images-na.ssl-images-amazon.com)
    # never match: the character before "amazon." may not be a word char, dot or hyphen.
    AMAZON_URL_RE = re.compile(
        r"(?<![\w.-])(?:https?://)?(?:www\.)?amazon\.[a-z]{2,3}(?:\.[a-z]{2})?/[^\s\"')\]<>]*",
        re.IGNORECASE)
    SHORT_URL_RE = re.compile(r"(?<![\w.-])(?:https?://)?amzn\.to/[^\s\"')\]<>]*", re.IGNORECASE)
    TAG_RE = re.compile(r"[?&;]tag=([^&#\s\"')\]<>]*)", re.IGNORECASE)

    def _offenders(self, text):
        bad = [m.group(0) for m in self.SHORT_URL_RE.finditer(text)]
        for m in self.AMAZON_URL_RE.finditer(text):
            if set(self.TAG_RE.findall(m.group(0))) != {self.TAG}:
                bad.append(m.group(0))
        return bad

    def _sources(self):
        yield REPO / "products.json"
        yield from sorted((REPO / "_posts").glob("*.md"))

    def test_scanner_catches_variants_and_ignores_image_hosts(self):
        for url in ("https://www.amazon.com/dp/B0FAKE0001?tag=pawpicks04-20",
                    "HTTPS://AMAZON.COM/dp/B0FAKE0001?th=1&tag=other-20",
                    "amazon.com/dp/B0FAKE0001",
                    "https://www.amazon.com/gp/product/B0FAKE0001",
                    "https://www.amazon.com/s?k=cat+tree",
                    "https://www.amazon.com/dp/B0FAKE0001?tag=happypetdc-20&tag=other-20",
                    "https://amzn.to/4sVt7G2",
                    "HTTP://AMZN.TO/4sVt7G2",
                    "amzn.to/4sVt7G2"):
            with self.subTest(url=url):
                self.assertEqual(self._offenders(f"[x]({url})"), [url])
        clean = ("[x](https://www.amazon.com/dp/B0FAKE0001?th=1&tag=happypetdc-20) "
                 "affiliate_url: \"https://www.amazon.com/dp/B0FAKE0001?tag=happypetdc-20\" "
                 "[Fancy Feast](https://www.amazon.com/s?k=Fancy+Feast+Classic+Pate&tag=happypetdc-20) "
                 "image: \"https://m.media-amazon.com/images/I/71abc._AC_SX425_.jpg\" "
                 "https://images-na.ssl-images-amazon.com/images/P/B000FLETX8.01.jpg "
                 "https://chewy.sjv.io/c/1/2/3?u=x")
        self.assertEqual(self._offenders(clean), [])

    def test_every_amazon_link_carries_current_tag(self):
        offenders = []
        for path in self._sources():
            for url in self._offenders(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.name}: {url}")
        self.assertEqual(offenders, [], f"Amazon link(s) without tag={self.TAG}: {offenders}")

    def test_no_stale_tag_anywhere_in_corpus(self):
        hits = [f"{path.name}: {stale}" for path in self._sources()
                for stale in self.STALE_TAGS if stale in path.read_text(encoding="utf-8")]
        self.assertEqual(hits, [])


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


class TestDescriptionTypographyScrub(unittest.TestCase):
    """scrub_description_typography() -- the front-matter `description` field's
    version of the same rule, differing only in what a LONE dash becomes.

    Twelve published posts shipped an em or en dash in front matter because the
    pin-description path ran scrub_banned_phrases and nothing else; only the
    body was ever scrubbed. Sending the description through the body scrubber
    would have replaced the dash with ' - ', which is invisible mid-paragraph
    and is the same machine-set punctuation in a one-line card blurb, so a lone
    dash becomes a full stop here and the next word is capitalised."""

    def setUp(self):
        import generate_posts as gp
        self.scrub = gp.scrub_description_typography

    def test_lone_em_dash_becomes_a_sentence(self):
        self.assertEqual(
            self.scrub("Big dogs need real support — this bed holds its shape."),
            "Big dogs need real support. This bed holds its shape.")

    def test_unspaced_em_dash_becomes_a_sentence(self):
        # The specific tell: no spaces around it, which no one types by hand.
        self.assertEqual(
            self.scrub("Cats love these beds—find the perfect spot."),
            "Cats love these beds. Find the perfect spot.")

    def test_lone_en_dash_becomes_a_sentence(self):
        self.assertEqual(
            self.scrub("Stop nail-trim nightmares – discover the quiet grinder."),
            "Stop nail-trim nightmares. Discover the quiet grinder.")

    def test_never_substitutes_a_spaced_hyphen(self):
        """The body scrubber's answer, ruled out for this field on purpose."""
        self.assertNotIn(" - ", self.scrub("Real support — this bed lasts."))

    def test_paired_em_dashes_become_commas(self):
        self.assertEqual(
            self.scrub("The Kong—a classic—outlasts the rest."),
            "The Kong, a classic, outlasts the rest.")

    def test_odd_run_pairs_then_trailing_lone(self):
        self.assertEqual(
            self.scrub("The Kong—a classic—outlasts the rest—by months."),
            "The Kong, a classic, outlasts the rest. By months.")

    def test_numeric_range_becomes_a_plain_hyphen(self):
        self.assertEqual(self.scrub("Sized for 5–10 lb dogs."),
                         "Sized for 5-10 lb dogs.")

    def test_ordinary_hyphenated_copy_is_untouched(self):
        """The inverse direction: a scrubber that mangles legitimate hyphens
        would quietly damage every description it touched."""
        for clean in ("A stress-free, lounge-worthy bed for 5-10 lb dogs.",
                      "High-quality odor control, no fluff.",
                      "A clean line with no fancy dashes at all."):
            self.assertEqual(self.scrub(clean), clean)

    def test_idempotent(self):
        once = self.scrub("Real support — this bed lasts 5–10 years—easily.")
        self.assertEqual(self.scrub(once), once)

    def test_output_carries_no_em_or_en_dash(self):
        out = self.scrub("A—B—C—D and 5–10 lb")
        self.assertNotIn("—", out)
        self.assertNotIn("–", out)


class TestStagedDescriptionIsScrubbed(unittest.TestCase):
    """Behavioural, not a refusal message: drive the real stage_article over a
    temp repo and assert on the three records the description actually reaches.

    stage_article is the single funnel both publish paths use (main() and
    stage1_cli), and the scrub lives there rather than in front_matter()
    because front_matter only sees the published field -- the pin-queue entry
    and the text drawn on the pin image read the same string."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def _stage(self, pin_desc, pin_gen=False):
        import tempfile, json
        from pathlib import Path
        from unittest.mock import patch, MagicMock
        gp = self.gp
        product = {"topic": "best-x", "title": "Best X", "keyword": "best x",
                   "format": "single_review", "name": "The X", "category": "dog-beds",
                   "species": "dog", "affiliate_url": "https://amzn.to/abc", "pulled_on": "2026-10-01"}
        body = "## Heading\n\n" + ("word " * 800)
        fake_pin = MagicMock(return_value="https://example.invalid/pin.jpg")
        with tempfile.TemporaryDirectory() as td:
            posts = Path(td) / "_posts"; posts.mkdir()
            pinq  = Path(td) / "_pin_queue"; pinq.mkdir()
            with patch.object(gp, "POSTS_DIR", posts), \
                 patch.object(gp, "REPO_DIR", Path(td)), \
                 patch.object(gp, "PIN_GEN_AVAILABLE", pin_gen), \
                 patch.object(gp, "make_pin_for_post", fake_pin):
                gp.stage_article("best-x", product, body, pin_desc=pin_desc, index=0)
            return {
                "front_matter": (posts / "DRAFT-best-x.md").read_text(encoding="utf-8"),
                "pin_queue": json.loads((pinq / "best-x.json").read_text(encoding="utf-8")),
                "pin_image_call": fake_pin.call_args,
            }

    def test_published_front_matter_carries_no_em_or_en_dash(self):
        out = self._stage("Big dogs need real support — this bed holds its shape.")
        line = [l for l in out["front_matter"].splitlines()
                if l.startswith("description:")][0]
        self.assertNotIn("—", line)
        self.assertNotIn("–", line)
        self.assertIn("real support. This bed", line)

    def test_pin_queue_entry_carries_no_em_or_en_dash(self):
        out = self._stage("Cats love these beds—find the perfect spot.")
        self.assertEqual(out["pin_queue"]["description"],
                         "Cats love these beds. Find the perfect spot.")

    def test_pin_image_text_carries_no_em_or_en_dash(self):
        out = self._stage("Cats love these beds—find the perfect spot.", pin_gen=True)
        drawn = out["pin_image_call"].args[1]
        self.assertNotIn("—", drawn)
        self.assertEqual(drawn, "Cats love these beds. Find the perfect spot.")

    def test_a_clean_description_is_written_through_unchanged(self):
        clean = "A stress-free bed for 5-10 lb dogs, built to last."
        out = self._stage(clean)
        self.assertEqual(out["pin_queue"]["description"], clean)
        self.assertIn(f'description: "{clean}"', out["front_matter"])


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


class TestFactCheckRejectionHolds(unittest.TestCase):
    """A fact-check response the sanitizer refuses must hold the article.

    Both rejections used to return the ORIGINAL body with a WARN in the log, so
    the one stage that strips fabricated stats out of the alternative sections
    could fail and the unfixed article would publish anyway -- silently, since
    nothing reads a WARN in a scheduled run.

    These patch BOTH providers. The pre-existing fact-check test above patches
    only http_post and relies on GEMINI_API_KEY being unset to push execution
    onto the fallback; a test whose green depends on a missing environment
    variable proves nothing on a machine that has one.
    """

    def setUp(self):
        import generate_posts as gp
        self.gp = gp
        self.or_calls = []

    def _run(self, primary, fallback=None):
        """Drive fact_check_alternatives with a scripted response from each
        provider. `primary`/`fallback` are either the text the provider
        returns or an Exception instance it raises."""
        gp = self.gp

        def gemini(*a, **kw):
            if isinstance(primary, Exception):
                raise primary
            return primary

        def or_post(*a, **kw):
            self.or_calls.append(a)
            if isinstance(fallback, Exception):
                raise fallback
            return _make_or_response(fallback if fallback is not None else "")

        with patch.object(gp, "_call_gemini", side_effect=gemini), \
             patch.object(gp, "http_post", side_effect=or_post), \
             patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            return gp.fact_check_alternatives(GOOD_ARTICLE, "Outward Hound Nina Ottosson")

    def test_truncated_response_holds_instead_of_republishing_the_original(self):
        with self.assertRaises(self.gp.GenerationStageError) as ctx:
            self._run(primary=GOOD_ARTICLE[:len(GOOD_ARTICLE) // 2])
        self.assertIn("too short", str(ctx.exception))

    def test_a_held_primary_is_not_retried_on_the_fallback(self):
        """A rejected response is a verdict on the article, not a provider
        outage. Failing over would run the second model and publish anyway."""
        with self.assertRaises(self.gp.GenerationStageError):
            self._run(primary=GOOD_ARTICLE[:len(GOOD_ARTICLE) // 2],
                      fallback=GOOD_ARTICLE)
        self.assertEqual(self.or_calls, [], "fallback provider was consulted")

    def test_altered_affiliate_links_hold(self):
        mangled = GOOD_ARTICLE.replace("amzn.to/3TestABC", "amzn.to/3Sw4pped")
        with self.assertRaises(self.gp.GenerationStageError) as ctx:
            self._run(primary=mangled)
        self.assertIn("affiliate links", str(ctx.exception))

    def test_dropped_affiliate_link_holds(self):
        """Padding keeps the response over the length floor, so the link check
        is the only thing standing between a link-stripped body and publish."""
        stripped = GOOD_ARTICLE.replace("(https://amzn.to/3TestABC)", "(#)")
        stripped += "\n" + ("Filler sentence to stay over the length floor. " * 30)
        with self.assertRaises(self.gp.GenerationStageError):
            self._run(primary=stripped)

    def test_a_clean_response_is_returned_and_replaces_the_original(self):
        """The inverse direction. A gate that holds everything is not a gate --
        an ordinary fact-check edit must still come back, unheld."""
        edited = GOOD_ARTICLE.replace("4.5 stars", "strong ratings")
        self.assertEqual(self._run(primary=edited), edited.strip())

    def test_a_fenced_response_is_still_returned(self):
        """The unwrapping the sanitizer already did must survive the change:
        a code-fenced response is cleaned, not held."""
        self.assertEqual(self._run(primary="```markdown\n" + GOOD_ARTICLE + "\n```"),
                         GOOD_ARTICLE.strip())

    def test_a_rejected_fallback_response_holds_too(self):
        """The fallback path has its own sanitize call and its own guard. A
        fix applied only to the primary would leave this one republishing."""
        with self.assertRaises(self.gp.GenerationStageError):
            self._run(primary=RuntimeError("Gemini down"),
                      fallback=GOOD_ARTICLE[:100])

    def test_both_providers_unreachable_holds_instead_of_publishing_unchecked(self):
        """This test asserted the opposite until 2026-09-23, and the asymmetry
        it was recording is now closed rather than documented.

        The old note was right that an OUTAGE is not a REJECTED response. It
        does not follow that an outage should publish. This is the one stage
        that strips fabricated statistics out of the alternative sections, so
        "both providers are down, ship the original" ships precisely the
        article the stage exists to fix -- and silently, because nothing reads
        a WARN in a scheduled run. Unverified content going live is the
        expensive direction; a held article is a topic that publishes on
        Thursday instead of Monday.

        review_and_rewrite already holds when its reviewer chain is
        unreachable. This now matches it."""
        with self.assertRaises(self.gp.GenerationStageError) as ctx:
            self._run(primary=RuntimeError("Gemini down"),
                      fallback=RuntimeError("OpenRouter down"))
        self.assertIn("unreachable", str(ctx.exception))

    def test_the_hold_names_both_provider_failures(self):
        """A held article opens a GitHub issue somebody has to act on. "Fix the
        outage" and "fix the credentials" are different actions, so the message
        carries what each provider actually said."""
        with self.assertRaises(self.gp.GenerationStageError) as ctx:
            self._run(primary=RuntimeError("Gemini down"),
                      fallback=RuntimeError("OpenRouter down"))
        self.assertIn("Gemini down", str(ctx.exception))
        self.assertIn("OpenRouter down", str(ctx.exception))

    def test_a_fallback_that_works_is_still_used_rather_than_held(self):
        """The inverse direction. Failing closed on a TOTAL outage must not
        turn a primary-only failure into a hold -- the fallback exists to keep
        articles moving when Gemini blips, and a gate that holds through a
        working provider would be switched off within a week."""
        edited = GOOD_ARTICLE.replace("4.5 stars", "strong ratings")
        self.assertEqual(self._run(primary=RuntimeError("Gemini down"),
                                   fallback=edited),
                         edited.strip())

    def test_main_converts_a_fact_check_hold_into_a_held_article(self):
        """Structural, because main() cannot be driven without live providers.
        Without the handler the raise lands in main's outer `except Exception`,
        which counts the article as FAILED rather than HELD -- a different
        number in GENERATION_RESULT.json and a different GHA exit."""
        import ast, inspect, textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(self.gp.main)))
        guarded = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            calls = {n.func.id for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            if "fact_check_alternatives" not in calls:
                continue
            for handler in node.handlers:
                if not (isinstance(handler.type, ast.Name)
                        and handler.type.id == "GenerationStageError"):
                    continue
                if any(isinstance(n, ast.AugAssign)
                       and getattr(n.target, "id", "") == "held"
                       for n in ast.walk(ast.Module(body=handler.body, type_ignores=[]))):
                    guarded = True
        self.assertTrue(
            guarded,
            "main() does not catch GenerationStageError from fact_check_alternatives "
            "and count the article as held")


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
        self.assertIs(ok, False, f"Should reject brand mismatch. Reason: {reason}")

    def test_empty_matched_name_gives_no_verdict(self):
        """Nothing to compare is not a pass. It is not a mismatch either:
        --fix acts on a mismatch."""
        from validate_published_chewy_links import check_brand_match
        ok, reason = check_brand_match("Kong Classic Dog Toy", "")
        self.assertIsNone(ok, reason)

    def test_gate_compares_brands_on_letters_and_digits(self):
        from validate_published_chewy_links import check_brand_match
        ok, reason = check_brand_match("Hill's Science Diet Adult 7+",
                                       "Hills Science Diet Adult 7 Chicken Recipe")
        self.assertIs(ok, True, reason)

    def test_the_matched_brand_found_in_the_product_name_passes(self):
        """The reverse half of the gate: the product name leads with a
        descriptor, so only the matched name's brand word ties the two."""
        from validate_published_chewy_links import check_brand_match
        ok, reason = check_brand_match("Original ChomChom Pet Hair Roller",
                                       "ChomChom Roller Pet Hair Remover")
        self.assertIs(ok, True, reason)

    def test_no_brand_token_gives_no_verdict(self):
        """A product name with no brand token (all stop words) cannot be
        compared, so the gate gives no verdict rather than a pass."""
        from validate_published_chewy_links import check_brand_match
        ok, reason = check_brand_match("the a for", "anything at all here")
        self.assertIsNone(ok, reason)


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
        # Simulates CI: only this repo is checked out, no sibling MaeveJarvis
        # and no brain/ directory either -- so _DEFAULT_DB_PATH must be pinned
        # somewhere absent too, or on a developer box the last path candidate
        # resolves to the real vault and this stops simulating CI at all.
        # The env vars are cleared for the same reason: the answer must come
        # from the checkout, not from whatever shell launched the tests.
        with patch.object(self.bs, "_SECRETS_ENV", Path("Z:/does/not/exist/secrets.env")), \
             patch.object(self.bs, "_DEFAULT_DB_PATH", "Z:/does/not/exist/maeve-brain-v2.db"), \
             patch.dict(os.environ, {"COGNITIVE_DB_KEY": "", "COGNITIVE_DB_PATH": ""}):
            del os.environ["COGNITIVE_DB_KEY"], os.environ["COGNITIVE_DB_PATH"]
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

    # test_sheet_id_secret_reads_env_on_ci and test_sheets_creds_fall_back_to_
    # gcp_sa_key_b64 lived here. Both were about post_pins reading topical-sheet
    # IDs and building Sheets credentials; post_pins no longer touches a
    # spreadsheet at all. The default-project env fallback is still covered by
    # the two tests above, and the Sheets-creds fallback by its live twin in
    # TestPushPinsToSheetsSecretFallback below.

    def test_default_project_secret_reads_env_on_ci(self):
        """The HappyPet-project (no explicit project=) branch of the same
        fallback, which the deleted sheet-ID test was incidentally the only
        cover for."""
        import os
        with patch.object(self.bs, "_get_vault", return_value=None), \
             patch.dict(os.environ, {"GCP_SA_KEY_B64": "b64-1"}):
            self.assertEqual(self.pp.brain_get_secret("GCP_SA_KEY_B64"), "b64-1")


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
        self.assertIsNot(ok, True)  # an empty name never passes the gate


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
        self.assertIn("tag=happypetdc-20", entry["affiliate_url"])
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
        specific = {"toys", "litter", "training", "tech", "beds", "grooming"}
        self.assertTrue(specific.issubset(set(rp.VALID_CATEGORIES)))

    def test_the_llm_is_offered_only_the_species_neutral_topics(self):
        # Category restructure, phase 1 (2026-10-05): the ideation enum is the
        # topic list from _data/categories.json and nothing else -- no old
        # species-prefixed name, no "-gear" catch-all.
        import refill_products as rp
        enum = rp.TOPIC_SCHEMA["properties"]["topics"]["items"]["properties"]["category"]["enum"]
        self.assertIn("flea-tick", enum)
        self.assertIn("collars", enum)
        self.assertEqual(
            [c for c in enum if c.split("-")[0] in ("dog", "cat", "pet")], [])
        self.assertEqual(len(enum), 22)

    def test_only_the_topics_validate_and_old_names_are_rejected(self):
        # The species-prefixed names (LEGACY_CATEGORIES) are gone: only the
        # topics in _data/categories.json validate.
        import categories
        import refill_products as rp
        self.assertEqual(rp.VALID_CATEGORIES, categories.TOPICS)
        self.assertFalse(hasattr(rp, "LEGACY_CATEGORIES"))
        for name in ("dog-health", "cat-beds", "pet-grooming", "dog-travel",
                     "dog-gear", "cat-litter", "pet-tech"):
            self.assertNotIn(name, rp.VALID_CATEGORIES)
        for name in ("health", "flea-tick", "cleaning", "tech"):
            self.assertIn(name, rp.VALID_CATEGORIES)
        data = json.loads((REPO / "products.json").read_text(encoding="utf-8"))
        for e in data:
            self.assertIn(e["category"], rp.VALID_CATEGORIES, e["topic"])

    def test_the_prompt_names_every_topic_and_no_gear_fallback(self):
        import refill_products as rp
        captured = {}
        def fake_call(model, prompt, **kw):
            captured["prompt"] = prompt
            return json.dumps({"topics": []})
        with patch.object(rp.gp, "_call_gemini", side_effect=fake_call):
            rp.ideate_topics(set(), set(), 3)
        import categories
        listed = re.search(r"exactly one of: ([^\n.]+)", captured["prompt"]).group(1)
        self.assertEqual([t.strip() for t in listed.split(",")], list(categories.TOPICS))
        self.assertNotIn("gear", captured["prompt"])

    def _prompt_on(self, iso_date):
        import refill_products as rp
        captured = {}
        def fake_call(model, prompt, **kw):
            captured["prompt"] = prompt
            return json.dumps({"topics": []})
        with patch.object(rp.gp, "_call_gemini", side_effect=fake_call), \
             patch.object(rp, "eastern_date", lambda: iso_date):
            rp.ideate_topics(set(), set(), 3)
        return captured["prompt"]

    def test_through_new_years_eve_self_planned_topics_target_after_the_holidays(self):
        """Seeds fill every slot to 12-31, so a topic the refill plans itself
        publishes in January or later (Maeve-main, 2026-10-14)."""
        for day in ("2026-10-14", "2026-11-30", "2026-12-31"):
            with self.subTest(day=day):
                prompt = self._prompt_on(day)
                self.assertIn("- Every topic must target a long-tail keyword of 3 to 6 words", prompt)
                self.assertIn("- Every topic you plan yourself publishes after the holidays: "
                              "prefer topics people shop for in January to April:", prompt)
                self.assertNotIn("February to April", prompt)
                self.assertNotIn("Thanksgiving", prompt)

    def _listed(self, day):
        prompt = self._prompt_on(day)
        if "Propose these planned slugs first" not in prompt:
            return []
        listed = prompt.split("if they are not already covered or queued: ")[1]
        return listed.split(".\n")[0].split(", ")

    def test_the_seed_bullet_names_exactly_the_plans_live_seeds(self):
        import generate_posts as gp
        seeds = [s["topic"] for s in gp.load_publish_plan()["seeds"]]
        self.assertEqual(len(seeds), 10)
        self.assertEqual(self._listed("2026-10-25"), seeds)
        # 11-23 is the tree deterrents' last day; on 11-24 it and the three
        # earlier seeds have expired (publish_by 11-02, 11-05, 11-19, 11-23).
        self.assertEqual(self._listed("2026-11-23"), seeds[3:])
        self.assertEqual(self._listed("2026-11-24"), seeds[4:])
        self.assertEqual(self._listed("2026-12-31"), seeds[8:])

    def test_the_gear_guard_holds_on_every_dated_block(self):
        for day in ("2026-10-25", "2027-02-01"):
            with self.subTest(day=day):
                self.assertNotIn("gear", self._prompt_on(day))

    def test_from_new_years_day_the_long_tail_rule_stays_and_spring_replaces_q4(self):
        for day in ("2027-01-01", "2027-01-31", "2027-03-15"):
            with self.subTest(day=day):
                prompt = self._prompt_on(day)
                self.assertNotIn("Q4 mode", prompt)
                for word in ("advent", "christmas", "stocking", "gift"):
                    self.assertNotIn(word, prompt.lower())
                self.assertIn("Never use a bare \"best <product>\" head term as the keyword.", prompt)

    SEASON_LINES = ("publishes after the holidays", "February to April", "Favor products relevant in")

    def test_each_date_gets_exactly_one_season_line(self):
        """One season instruction, right for the date: after-the-holidays through
        12-31, spring through 2027-01-31, then this month and the next two."""
        for day, line in (("2026-10-25", "publishes after the holidays"),
                          ("2026-12-31", "publishes after the holidays"),
                          ("2027-01-01", "February to April"),
                          ("2027-01-31", "February to April"),
                          ("2027-02-01", "Favor products relevant in February and the coming two months."),
                          ("2027-03-15", "Favor products relevant in March and the coming two months.")):
            with self.subTest(day=day):
                prompt = self._prompt_on(day)
                self.assertIn(line, prompt)
                self.assertEqual(sum(s in prompt for s in self.SEASON_LINES), 1, prompt[-600:])

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

    def test_refill_ci_workflow_stays_retired(self):
        """refill.yml name-matched Chewy products, which the GTIN-only rule forbids;
        the local launcher superseded it. Re-adding it must be a deliberate act."""
        self.assertFalse((REPO / ".github/workflows/refill.yml").exists())


class TestRefillPlaceholdersOnlyMode(unittest.TestCase):
    """REFILL_PLACEHOLDERS_ONLY=1: the Amazon scrape is blocked (docs/refill-manual-resolve.md),
    so this mode seeds NEEDS_* placeholders for manual_resolve.py and exits 0 so the
    workflow's PR step runs. Run 36090851260 generated 4 topics, could not resolve
    them, exited 1, and the placeholders were discarded with the runner."""

    CANDIDATE = {
        "topic": "best-heated-cat-beds", "title": "Best Heated Cat Beds",
        "keyword": "heated cat bed", "species": "cat", "category": "cat-beds",
        "topical_sheet": "HAPPYPET_SHEET_ID_HOME", "amazon_search_query": "heated cat bed"}

    def _run(self, *, placeholders_only, candidates, existing=None):
        import refill_products as rp
        products = list(existing or [])
        written = {}
        resolver_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(rp, "PLACEHOLDERS_ONLY", placeholders_only), \
                 patch.object(rp, "FORCE", True), \
                 patch.object(rp, "load_products", lambda: products), \
                 patch.object(rp, "published_slugs", lambda: set()), \
                 patch.object(rp, "ideate_topics", lambda *a, **k: list(candidates)), \
                 patch.object(rp, "resolve_product",
                              lambda *a, **k: resolver_calls.append(a)), \
                 patch.object(rp, "atomic_write_json",
                              lambda path, data, **k: written.update(data=list(data))), \
                 patch.object(rp, "RESULT_PATH", Path(tmp) / "REFILL_RESULT.json"), \
                 patch.object(rp, "log", lambda *a, **k: None), \
                 patch.object(rp.time, "sleep", lambda *_: None):
                code = None
                try:
                    rp.main()
                except SystemExit as exc:
                    code = exc.code
            result = json.loads((Path(tmp) / "REFILL_RESULT.json").read_text())
        return code, written.get("data"), result, resolver_calls

    def test_seeds_placeholders_exits_zero_and_never_touches_amazon(self):
        code, data, result, calls = self._run(
            placeholders_only=True, candidates=[self.CANDIDATE])
        self.assertIsNone(code, "placeholders-only must not exit non-zero")
        self.assertEqual(calls, [], "no Amazon fetch / fit-check in this mode")
        self.assertEqual([e["topic"] for e in data], ["best-heated-cat-beds"])
        self.assertEqual(data[0]["asin"], "NEEDS_ASIN")
        self.assertEqual(data[0]["image"], "NEEDS_IMAGE")
        self.assertEqual(result["added_placeholder"], ["best-heated-cat-beds"])

    def test_existing_placeholders_are_not_backfilled_in_this_mode(self):
        import refill_products as rp
        old = rp.build_entry({**self.CANDIDATE, "topic": "best-old-topic"})
        _, _, result, calls = self._run(
            placeholders_only=True, candidates=[self.CANDIDATE], existing=[old])
        self.assertEqual(calls, [])
        self.assertEqual(result["backfilled"], [])
        self.assertEqual(result["backfill_still_held"], [])

    def test_ideation_producing_nothing_still_fails_the_run(self):
        """The mode relaxes the resolved-zero gate, not the seeded-zero gate."""
        code, data, _, _ = self._run(placeholders_only=True, candidates=[])
        self.assertEqual(code, 1)
        self.assertIsNone(data, "nothing to write when nothing was seeded")

    def test_default_mode_still_fails_when_every_resolve_fails(self):
        """Inverse direction: without the flag, run 36090851260's outcome is unchanged."""
        code, _, _, calls = self._run(placeholders_only=False, candidates=[self.CANDIDATE])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1, "default mode still attempts the resolve")


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
            "topical_sheet": "HAPPYPET_SHEET_ID_HOME",
            "name": "NEEDS_ASIN placeholder for best automatic litter box",
            "asin": "NEEDS_ASIN",
            "affiliate_url": "https://www.amazon.com/dp/NEEDS_ASIN?tag=happypetdc-20",
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
                          "https://www.amazon.com/dp/B0ABCD1234?tag=happypetdc-20")
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

    def test_apply_resolution_records_the_eastern_pull_date(self):
        # The "(as of <date>)" label on a published price comes from this field,
        # dated in America/New_York (Director, 2026-10-13), not the runner's UTC.
        import datetime
        import refill_products as rp
        entry = {"topic": "best-x", "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}
        resolved = {"name": "Some Product", "asin": "B0ABCD1234",
                    "image": "https://m.media-amazon.com/images/I/x.jpg",
                    "price": "9.99", "stars": 4.0}
        # 2025-01-02 01:30 UTC is 20:30 on Jan 1 in New York. A past date, so a
        # stamp from the machine's own clock cannot pass by coincidence.
        fixed = datetime.datetime(2025, 1, 2, 1, 30, tzinfo=datetime.timezone.utc)

        class FrozenDatetime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        with patch.object(rp.datetime, "datetime", FrozenDatetime):
            rp.apply_resolution(entry, resolved, enrich_chewy=False)
        self.assertEqual(entry["pulled_on"], "2025-01-01")

    def test_eastern_date_across_both_dst_switches(self):
        """Expected dates are worked by hand from the US rule, never by the code
        under test. The switch days (Sundays, checked below by calendar): 2026-03-08,
        2026-11-01, 2027-03-14, 2027-11-07. Only the 23:00-00:00 ET hour can tell
        EST from EDT in a date, so most cases sit in the 20:00-23:59 ET evening
        window (UTC already the next day) or just past ET midnight, on both sides
        of each switch."""
        import calendar
        import datetime
        import refill_products as rp
        for y, m, d in ((2026, 3, 8), (2026, 11, 1), (2027, 3, 14), (2027, 11, 7)):
            self.assertEqual(calendar.weekday(y, m, d), calendar.SUNDAY)
        cases = [
            # spring 2026: EST until Sun 03-08 07:00Z (02:00 EST)
            ("2026-03-08T01:00", "2026-03-07"),  # Sat 20:00 EST
            ("2026-03-08T04:59", "2026-03-07"),  # Sat 23:59 EST (EDT would be Sun 00:59)
            ("2026-03-08T06:59", "2026-03-08"),  # Sun 01:59 EST, a minute before the switch
            ("2026-03-08T07:00", "2026-03-08"),  # Sun 03:00 EDT, the switch
            ("2026-03-09T00:00", "2026-03-08"),  # Sun 20:00 EDT
            ("2026-03-09T03:59", "2026-03-08"),  # Sun 23:59 EDT
            ("2026-03-09T04:00", "2026-03-09"),  # Mon 00:00 EDT (EST would be Sun 23:00)
            # fall 2026: EDT until Sun 11-01 06:00Z (02:00 EDT)
            ("2026-11-01T00:00", "2026-10-31"),  # Sat 20:00 EDT
            ("2026-11-01T03:59", "2026-10-31"),  # Sat 23:59 EDT
            ("2026-11-01T04:30", "2026-11-01"),  # Sun 00:30 EDT (EST would be Sat 23:30)
            ("2026-11-01T05:59", "2026-11-01"),  # Sun 01:59 EDT, a minute before the switch
            ("2026-11-01T06:00", "2026-11-01"),  # Sun 01:00 EST, the switch
            ("2026-11-02T01:00", "2026-11-01"),  # Sun 20:00 EST
            ("2026-11-02T04:59", "2026-11-01"),  # Sun 23:59 EST (EDT would be Mon 00:59)
            # 2027, so the Sundays are found per year rather than fixed
            ("2027-03-14T04:30", "2027-03-13"),  # Sat 23:30 EST
            ("2027-03-15T04:30", "2027-03-15"),  # Mon 00:30 EDT
            ("2027-11-07T04:30", "2027-11-07"),  # Sun 00:30 EDT
            ("2027-11-08T04:30", "2027-11-07"),  # Sun 23:30 EST
            # mid-season and the year boundary
            ("2026-07-15T03:30", "2026-07-14"),  # 23:30 EDT
            ("2026-07-15T04:30", "2026-07-15"),  # 00:30 EDT
            ("2026-01-15T04:30", "2026-01-14"),  # 23:30 EST
            ("2026-01-15T05:30", "2026-01-15"),  # 00:30 EST
            ("2027-01-01T04:59", "2026-12-31"),  # New Year's Eve 23:59 EST
        ]
        for utc, et in cases:
            with self.subTest(utc=utc):
                now = datetime.datetime.fromisoformat(utc).replace(tzinfo=datetime.timezone.utc)
                self.assertEqual(rp.eastern_date(now), et)

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

    def test_anchor_text_is_the_short_name_not_the_listing_title(self):
        # Refill posts must not bring back 30-word listing-title anchors
        # (Scarlett's 2026-10-13 anchor rewrite; gate round 1, Spec F5).
        self.prod["name"] = "AWOOF Pet Snuffle Mat for Dogs, Interactive Feed Game, Large"
        for fmt in ("roundup", "single_review"):
            with self.subTest(fmt=fmt):
                p = self._full(fmt)
                self.assertIn("[AWOOF Pet Snuffle Mat for Dogs](https://amzn.to/testXYZ)", p)
                self.assertNotIn("[AWOOF Pet Snuffle Mat for Dogs, Interactive", p)


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


class TestFirsthandClaimGuard(unittest.TestCase):
    """Happy Pet Product Reviews does not test, try, own or handle any product --
    it works from retailer specs, star ratings and customer reviews, and about.md
    says so. An article claiming otherwise is a false advertising claim on a
    monetized affiliate page, which is why this is a hold and not a style rule.

    The generator published "We tested the top litters so you can finally say
    goodbye to that smell" as the frontmatter description of
    best-cat-litter-odor-control, plus roughly 60 body claims across 26 posts
    ("Testing found", "the test dogs (a spirited Labrador Retriever...)", "our
    testing lab", "a panel of furry taste-testers"). The prompts never forbade
    it, so nothing downstream could catch it.

    Both directions are asserted here. A guard that fires on "DNA test kit" or
    "third-party testing for purity" gets an allowlist bolted on and then gets
    deleted, so the false-positive cases are tests, not comments.
    """

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    # --- the claim shapes the generator actually produced -------------------
    def test_flags_first_person_testing_claim_in_a_pin_description(self):
        self.assertTrue(self.gp.find_firsthand_claims(
            "We tested the top litters so you can finally say goodbye to that smell."))

    def test_flags_testing_as_the_sentence_subject(self):
        for s in ("Testing found the lid stays put.",
                  "Testing put the litter through its paces in a multi-cat home.",
                  "Testing has shown how much difference the right gear makes.",
                  "That's why testing covered a range of collars.",
                  "So testing dug into the cheaper options."):
            with self.subTest(s=s):
                self.assertTrue(self.gp.find_firsthand_claims(s), s)

    def test_flags_an_apparatus_the_site_does_not_have(self):
        for s in ("The test dogs (a spirited Labrador Retriever) pulled hard.",
                  "The test cat usually turns up her nose at still water.",
                  "It became the go-to in our testing lab.",
                  "A panel of furry taste-testers weighed in.",
                  "Over the testing period the motor stayed quiet.",
                  "Testers did note the lid rattles.",
                  "A test household with two medium-sized dogs used it for days."):
            with self.subTest(s=s):
                self.assertTrue(self.gp.find_firsthand_claims(s), s)

    def test_flags_the_after_testing_opener(self):
        """The commonest shape in the published corpus by a wide margin.
        "After extensive testing and evaluating..." asserts a test that never
        happened, whatever follows it."""
        for s in ("After extensive testing and evaluating the bed, several features stood out.",
                  "After thoroughly testing the harness, the verdict is clear.",
                  "Beyond independent testing, owner sentiment is worth a look.",
                  "In testing, the difference in odor reduction was obvious.",
                  "The pros were weighed while testing each option."):
            with self.subTest(s=s):
                self.assertTrue(self.gp.find_firsthand_claims(s), s)

    def test_flags_experience_claims_that_never_say_the_word_testing(self):
        for s in ("After weeks of trials the vest held up.",
                  "These are the ones that finally survive, tested by real power chewers.",
                  "It shows firsthand how much difference the right carrier makes.",
                  "All six products were tried with a resident tabby.",
                  "Hands-on experience with the buckle says otherwise.",
                  "We put the PetSafe Easy Walk to the test on real strong pullers.",
                  "A popular choice put through its paces here."):
            with self.subTest(s=s):
                self.assertTrue(self.gp.find_firsthand_claims(s), s)

    # --- the inverse direction: legitimate prose must stay clean ------------
    def test_does_not_flag_testing_as_a_product_category_or_third_party_lab(self):
        """best-dog-dna-tests alone carries 30+ legitimate "test" hits. A guard
        that fires on any of these is the guard that gets deleted."""
        for s in ("A dog DNA test kit can reveal breed mix.",
                  "The pet DNA testing world moves fast.",
                  "Wisdom Panel is a trusted name in the pet DNA testing world.",
                  "Ask whether a brand meets the AAFCO testing requirements.",
                  "Premium brands undergo stricter testing for purity.",
                  "Look for third-party testing or a verified rating.",
                  "Test it over the kitchen sink before the first walk.",
                  "Some cats have a habit of testing fabrics with their claws.",
                  "Testing a new food should be gradual, over about a week.",
                  "Owners report the mat stays cool through an afternoon.",
                  "Amazon reviewers repeatedly mention the quiet motor.",
                  "The 5L hopper holds about a month of food for one cat."):
            with self.subTest(s=s):
                self.assertEqual(self.gp.find_firsthand_claims(s), [], s)

    # --- wiring: every path that could publish a claim is gated -------------
    def test_both_output_gates_hold_an_article_that_claims_testing(self):
        body = ("Testing found the harness sits well. " + "word " * 900 +
                "https://amzn.to/3TestABC")
        for stage in ("generate", "review"):
            with self.subTest(stage=stage):
                with self.assertRaises(self.gp.GenerationStageError) as cm:
                    self.gp.validate_output(stage, body, "slug",
                                            affiliate_url="https://amzn.to/3TestABC")
                self.assertIn("firsthand", str(cm.exception).lower())

    def test_a_clean_body_still_passes_both_gates(self):
        body = ("Owners report the harness sits well. " + "word " * 900 +
                "https://amzn.to/3TestABC\n\n" + QUESTION_BLOCK)
        for stage in ("generate", "review"):
            with self.subTest(stage=stage):
                self.gp.validate_output(stage, body, "slug",
                                        affiliate_url="https://amzn.to/3TestABC")

    def test_front_matter_refuses_to_write_a_testing_claim_as_the_description(self):
        """The published `description` IS the pin description, and it never went
        through validate_output. This is the structural backstop: every path that
        writes a post file builds its front matter here."""
        with self.assertRaises(self.gp.GenerationStageError):
            self.gp.front_matter("T", "kw", "https://amzn.to/x", "slug", "cat",
                                 "cat-litter", "We tested the top litters.")

    def test_no_post_file_is_written_when_the_pin_description_claims_testing(self):
        """Behavioural, not a refusal message: drive the real stage_article over
        a temp repo and assert on the victim's own record -- no draft file, no
        pin-queue entry. This is the path that published the litter claim."""
        product = {"topic": "best-x", "title": "Best X", "keyword": "best x",
                   "format": "single_review", "name": "The X", "category": "cat-litter",
                   "species": "cat", "affiliate_url": "https://amzn.to/abc", "pulled_on": "2026-10-01"}
        body = "## Heading\n\n" + ("word " * 800)
        with tempfile.TemporaryDirectory() as td:
            posts = Path(td) / "_posts"; posts.mkdir()
            pinq  = Path(td) / "_pin_queue"; pinq.mkdir()
            with patch.object(self.gp, "POSTS_DIR", posts), \
                 patch.object(self.gp, "REPO_DIR", Path(td)), \
                 patch.object(self.gp, "PIN_GEN_AVAILABLE", False):
                with self.assertRaises(self.gp.GenerationStageError):
                    self.gp.stage_article(
                        "best-x", product, body,
                        pin_desc="We tested the top litters so you can say goodbye to that smell.",
                        index=0)
            self.assertEqual(list(posts.glob("*.md")), [])
            self.assertEqual(list(pinq.glob("*.json")), [])

    def test_front_matter_writes_a_clean_description_normally(self):
        fm = self.gp.front_matter("T", "kw", "https://amzn.to/x", "slug", "cat",
                                  "cat-litter", "The litter that actually holds odor down.")
        self.assertIn("description:", fm)

    def test_the_rule_text_is_single_sourced_into_all_three_prompts(self):
        """Generator, reviewer and rewrite prompts must state the same rule the
        gate enforces, or an article that follows the brief gets held anyway."""
        rule = self.gp.FIRSTHAND_CLAIM_RULE
        self.assertIn(rule, self.gp.GENERATOR_SYSTEM_PROMPT)
        self.assertIn(rule, self.gp.make_review_prompt("T", "kw", "body"))
        self.assertIn(rule, self.gp.make_rewrite_prompt("T", "kw", "body", "fix it"))

    def test_the_reviewer_can_fail_an_article_on_this_alone(self):
        p = self.gp.make_review_prompt("T", "kw", "body")
        self.assertIn("22. FALSE FIRSTHAND CLAIMS", p)
        # Stated in all three places the reviewer reads: the audit category, the
        # PASS CRITERIA list, and the pass=false rule. A category the pass
        # criteria never mention is a category the model scores around.
        self.assertIn("NO claim of firsthand experience with the product", p)
        self.assertIn("firsthand-experience claim present", p)


class TestNamedTestimonialGuard(unittest.TestCase):
    """Nobody at Happy Pet Product Reviews has spoken to an owner, and no real
    customer review is reproduced verbatim, so every quoted statement attributed
    to a person in the published corpus was invented by the generator. On a
    monetized affiliate page that is a fabricated endorsement, which is why this
    holds an article instead of being a style note.

    Thirteen shipped, across four posts: five invented Amazon reviewers with
    names and review dates in best-kitten-food, four "verified buyer" quotes in
    best-cat-calming-products, three demographic person-cards in
    best-dog-life-jacket ("Samantha, 38, Portland, OR"), and one invented Reddit
    reviewer in best-cat-harness-leash.

    The prompts have said "no invented testimonials" since c1f76d7 (2026-07-20).
    Nothing enforced it in code, and authoritative_gate's fabrication check reads
    the REVIEWER'S flag text rather than the article, so catching this depended
    on an LLM spontaneously choosing the word "fabricated". These tests assert on
    the article.

    The detector keys on ATTRIBUTION, never on the name. Names are open-class and
    the next invented one is unenumerable; the ways English attaches a speaker to
    a quote are not. The strings below are the published text verbatim in both
    directions -- a guard whose inverse cases are invented rather than real gets
    an allowlist bolted on and then gets deleted.
    """

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    # --- the three attribution shapes, verbatim from the published corpus ----
    def test_flags_a_speaker_named_before_the_quote(self):
        for s in ('A 5-star review from “Emily R.” (April 2024) notes, '
                  '“a 10-week-old kitten finished the bowl in seconds and has '
                  'had no stomach upset for a month.”',
                  'Reviewer “J.T.” (Feb 2024) writes, “a rescued kitten '
                  'gained 1 lb in three weeks and his stools are firm.”',
                  '“Mia L.” (Jan 2024) says, “a 8-week-old kitten’s '
                  'energy spiked after switching, he is now chasing toys nonstop!”',
                  'A reviewer on Reddit clarified, “I ordered a size Small for my '
                  '9-lb tuxedo cat; the chart was off by an inch, so I had to size up.”'):
            with self.subTest(s=s[:48]):
                self.assertTrue(self.gp.find_named_testimonials(s), s)

    def test_flags_a_speaker_named_after_the_quote(self):
        for s in ('“Luna stopped hiding under the bed within 24 hrs; she is now '
                  'calm on car rides.” – Megan T., verified buyer.',
                  '“My cat’s grooming-induced panic stopped after three days; '
                  'she now purrs while being brushed.” – Carlos L., Amazon reviewer.',
                  '“A quick mist in the carrier kept Whiskers from hissing on the '
                  'way to the clinic.” – Jenna K., verified purchaser.',
                  '"The buoyancy held up even when the current got stronger, with no '
                  'slipping at all." -- a verified buyer'):
            with self.subTest(s=s[:48]):
                self.assertTrue(self.gp.find_named_testimonials(s), s)

    def test_flags_a_speaker_used_as_a_label(self):
        for s in ('- **Samantha, 38, Portland, OR:** "My Labrador, Bella, loves the lake '
                  'but gets nervous around other dogs."',
                  '- **Javier, 45, Austin, TX:** "We took the vest on a river rafting '
                  'trip and the buoyancy held up the whole way."',
                  '- **Leah, 27, Boston, MA:** "I was skeptical about the mesh panels '
                  'getting soggy, but they dried in minutes."'):
            with self.subTest(s=s[:48]):
                self.assertTrue(self.gp.find_named_testimonials(s), s)

    def test_flags_the_demographic_byline_even_with_its_quote_removed(self):
        """An invented person-card is the fabrication before the quote is. This
        is the one shape that survives somebody editing the quote away and
        leaving the person, which is the likeliest half-fix."""
        self.assertTrue(self.gp.find_named_testimonials(
            "- **Samantha, 38, Portland, OR:** she keeps the vest in the truck."))

    def test_flags_shapes_the_generator_has_not_produced_yet(self):
        """The published four are not the boundary of the class. A name the
        corpus has never seen, a role the corpus has never seen, a straight
        double-quote instead of a curly one, and above all a label WITHOUT the
        bold markers or the two-letter state code all have to fire. The bolded
        "Name, 38, City, ST" card is one published formatting accident, not the
        shape of the defect, and a guard that only recognizes it is a guard that
        the next run walks straight past."""
        for s in ('Reviewer "Priya N." (Aug 2025) reported, "the kibble size suits a '
                  'six-week-old and she finishes every bowl."',
                  '"The zipper gave out inside a month and support never replied." '
                  '-- Dmitri K., confirmed purchaser',
                  '- **Aroha, 52, Wellington, NZ:** "the harness survived a full winter '
                  'of coastal walks without fraying."',
                  # No bold, no state code: a label is a label.
                  'Samantha, 38, a longtime lake swimmer: "the vest gives her enough '
                  'confidence to stay in the water all afternoon."',
                  # A role instead of an age, and no bold.
                  'Megan T., verified buyer: "Luna stopped hiding under the bed within '
                  '24 hours and rides calmly now."',
                  'Carlos L., Amazon reviewer: "the grooming panic stopped after three '
                  'days and she purrs while being brushed now."'):
            with self.subTest(s=s[:48]):
                self.assertTrue(self.gp.find_named_testimonials(s), s)

    # --- the inverse direction: the corpus's real quoting must stay clean ----
    def test_does_not_flag_the_legitimate_quoting_in_the_published_corpus(self):
        """Every string here is published text. The site quotes product feature
        names, brand coinages and short idioms constantly, and it summarizes
        review sentiment on every single post. A guard that fires on any of
        these is the guard that gets weakened and then deleted."""
        for s in ('Includes “Royal Canin K-Mune” blend, highly digestible proteins.',
                  'Plus “LifeSource Bits”, a blend of antioxidants and vitamins.',
                  'A “Wild-Life” blend of antioxidants (vitamin C, E, and selenium).',
                  'It frees them from the "cat alarm clock" in the mornings.',
                  'Playtime becomes a rewarding "find-the-squirrel" hunt.',
                  'A common theme is the "worth the investment" narrative.',
                  'Many users consider it "worth every penny" for the peace of mind.',
                  'Many owners describe it as a "game-changer" for their walks.',
                  'Owners report the mat stays cool through an afternoon.',
                  'Amazon reviewers repeatedly mention the quiet motor.',
                  'Reviewers consistently highlight three themes: quietness, the LED '
                  'light, and how quickly nervous dogs settle into the routine.',
                  'Across more than 18,000 Amazon reviews, the consistent theme is that '
                  'the vest works quickly for situational anxiety.',
                  'One owner of a rescue dog with severe clipper anxiety reported that '
                  'switching to this grinder cut grooming time in half.',
                  'A peer-reviewed study of 60 cats found a reduction in stress-related '
                  'behaviors (Journal of Feline Medicine, 2021).'):
            with self.subTest(s=s[:48]):
                self.assertEqual(self.gp.find_named_testimonials(s), [], s)

    def test_does_not_flag_a_quote_attributed_to_a_class_of_owners(self):
        """Deliberately out of scope, and recorded here so a later pass does not
        quietly widen the detector to reach it. These quotes name no individual;
        catching them would also catch honest aggregate sentiment, which is what
        the Real Owner Experiences section is supposed to contain. They were left
        as a separate, smaller question for a human, not a gate.

        That question has since been answered, and it split (Director, 2026-09-22).
        The FIRST string below was a whole invented sentence in quotation marks,
        and it was DELETED BY HAND from best-cat-scratching-posts; the published
        text now reads "returning to it again and again in reviews as the
        scratcher that outlasted the ones before it", with no quotation marks.
        The short idioms -- "immediately loved it.", "worth every penny",
        "game-changer" -- were KEPT: they are honest aggregate sentiment and
        reaching them would cost the section its legitimate content.

        So the strings below are no longer all published text. They stay here
        verbatim anyway, because what this test guards is the detector's WIDTH,
        not the corpus: one hand-edit is not a reason to widen a detector, and
        the next pass reading "we deleted one of these" must not read it as
        "so catch the rest".
        """
        for s in ('Many owners rave about its longevity, with comments like, "My cats '
                  'have shredded every other scratcher, but this one is still going '
                  'strong after three years!"',
                  'Many owners echo the findings, raving about how their dogs '
                  '"immediately loved it."'):
            with self.subTest(s=s[:48]):
                self.assertEqual(self.gp.find_named_testimonials(s), [], s)

    def test_the_deleted_group_quote_is_gone_from_the_published_corpus(self):
        """The hand-deletion above, asserted on the corpus rather than trusted.
        The detector does not and will not fire on this shape, so nothing else
        in the suite would notice it coming back."""
        text = (REPO / "_posts" / "2026-04-04-best-cat-scratching-posts.md").read_text(
            encoding="utf-8")
        self.assertNotIn("shredded every other scratcher", text)
        self.assertIn("Many owners rave about its longevity", text)  # the sentence stayed

    def test_does_not_read_the_front_matter_terminator_as_an_attribution(self):
        """`image: "...jpg"` followed by the YAML `---` on the next line is a
        quoted span, a dash and a capitalized word. It is not an attribution, and
        the first draft of this guard flagged best-cat-beds because of it."""
        self.assertEqual(self.gp.find_named_testimonials(
            'image: "https://m.media-amazon.com/images/I/81-U9aAoCDL._AC_SX425_.jpg"\n'
            '---\n\nWhen Whiskers claims the laundry pile again, a proper bed helps.'), [])

    # --- wiring: every path that could publish a testimonial is gated -------
    def test_both_output_gates_hold_an_article_with_a_named_testimonial(self):
        body = ('"Luna stopped hiding under the bed within 24 hrs and rides calmly now." '
                '- Megan T., verified buyer. ' + "word " * 900 + "https://amzn.to/3TestABC")
        for stage in ("generate", "review"):
            with self.subTest(stage=stage):
                with self.assertRaises(self.gp.GenerationStageError) as cm:
                    self.gp.validate_output(stage, body, "slug",
                                            affiliate_url="https://amzn.to/3TestABC")
                self.assertIn("named individual", str(cm.exception).lower())

    def test_a_clean_body_still_passes_both_gates(self):
        body = ("Owners repeatedly mention that the vest calms car rides. " +
                "word " * 900 + "https://amzn.to/3TestABC\n\n" + QUESTION_BLOCK)
        for stage in ("generate", "review"):
            with self.subTest(stage=stage):
                self.gp.validate_output(stage, body, "slug",
                                        affiliate_url="https://amzn.to/3TestABC")

    def test_authoritative_gate_fails_on_the_body_not_on_the_reviewers_opinion(self):
        """ee2fe31 narrowed this gate's fabrication scan to the explicit verbs
        fabricated/invented/made-up, so a reviewer who describes a fake
        testimonial in the natural words ("the quote is unverified", "no source
        for the reviewer") no longer hard-fails it. Narrowing was right -- the
        broad list false-failed VERIFIED figures -- but it left this class
        resting on an LLM's word choice. The check reads the body instead."""
        clean = {"pass": True, "scores": {"human_voice": 4, "warmth": 4,
                                          "readability": 4, "accuracy": 4}, "flags": []}
        body = ('- **Samantha, 38, Portland, OR:** "My Labrador loves the lake but '
                'gets nervous around other dogs."')
        passed, flags = self.gp.authoritative_gate(clean, body)
        self.assertFalse(passed)
        self.assertTrue(any("named_testimonial_in_body" in str(f) for f in flags), flags)

    def test_authoritative_gate_still_passes_a_clean_body(self):
        clean = {"pass": True, "scores": {"human_voice": 4, "warmth": 4,
                                          "readability": 4, "accuracy": 4}, "flags": []}
        passed, flags = self.gp.authoritative_gate(
            clean, "Owners repeatedly mention the handle. Reviewers rate it 4.6/5.")
        self.assertTrue(passed, flags)

    def test_no_post_file_is_written_when_the_body_carries_a_testimonial(self):
        """Behavioural, not a refusal message: drive the real stage_article over
        a temp repo and assert on the victim's own record -- no draft file, no
        pin-queue entry. stage_article is the single funnel both publish paths
        push a body through, which is why the backstop lives there."""
        product = {"topic": "best-x", "title": "Best X", "keyword": "best x",
                   "format": "single_review", "name": "The X", "category": "dog-vests",
                   "species": "dog", "affiliate_url": "https://amzn.to/abc", "pulled_on": "2026-10-01"}
        body = ('## Real Owner Experiences\n\n- **Samantha, 38, Portland, OR:** "My '
                'Labrador loves the lake but gets nervous around other dogs."\n\n'
                + ("word " * 800))
        with tempfile.TemporaryDirectory() as td:
            posts = Path(td) / "_posts"; posts.mkdir()
            pinq  = Path(td) / "_pin_queue"; pinq.mkdir()
            with patch.object(self.gp, "POSTS_DIR", posts), \
                 patch.object(self.gp, "REPO_DIR", Path(td)), \
                 patch.object(self.gp, "PIN_GEN_AVAILABLE", False):
                with self.assertRaises(self.gp.GenerationStageError):
                    self.gp.stage_article("best-x", product, body,
                                          pin_desc="A vest that calms car rides.",
                                          index=0)
            self.assertEqual(list(posts.glob("*.md")), [])
            self.assertEqual(list(pinq.glob("*.json")), [])

    # --- the stated rule and the enforced gate must not drift ---------------
    def test_the_rule_text_is_single_sourced_into_all_three_prompts(self):
        rule = self.gp.NAMED_TESTIMONIAL_RULE
        self.assertIn(rule, self.gp.GENERATOR_SYSTEM_PROMPT)
        self.assertIn(rule, self.gp.make_review_prompt("T", "kw", "body"))
        self.assertIn(rule, self.gp.make_rewrite_prompt("T", "kw", "body", "fix it"))

    def test_the_structure_brief_defines_real_owner_experiences_as_aggregate(self):
        """The heading stays -- seven posts use it with clean aggregate prose, so
        renaming it would churn them for nothing. What changes is that the brief
        now says what the section is: sentiment, not a list of people. The
        section name invited exactly the thing the rules forbade."""
        prompt = self.gp.make_prompt(
            "T", "kw", "slug", "single_review",
            {"title": "X", "keyword": "kw", "species": "dog",
             "affiliate_url": "https://amzn.to/x"}, "", "")
        self.assertIn("Real Owner Experiences (H2)", prompt)
        self.assertIn("AGGREGATE sentiment", prompt)
        self.assertIn(self.gp.NAMED_TESTIMONIAL_RULE, prompt)

    def test_the_reviewer_can_fail_an_article_on_this_alone(self):
        p = self.gp.make_review_prompt("T", "kw", "body")
        self.assertIn("23. INVENTED NAMED TESTIMONIALS", p)
        # All three places the reviewer reads: the audit category, the PASS
        # CRITERIA list and the pass=false rule. A category the pass criteria
        # never mention is a category the model scores around.
        self.assertIn("NO quote attributed to a named individual", p)
        self.assertIn("a quote attributed to a named individual present", p)

    def test_the_audit_header_counts_the_categories_it_actually_lists(self):
        p = self.gp.make_review_prompt("T", "kw", "body")
        listed = max(int(n) for n in re.findall(r"^(\d+)\. [A-Z]", p, re.MULTILINE))
        self.assertIn(f"=== {listed}-CATEGORY AI PATTERN AUDIT ===", p)


class TestUnbackedAffiliateLinkGuard(unittest.TestCase):
    """The pipeline has verified data for exactly ONE product per article: the
    products.json entry, whose name, ASIN, link, rating and price came off the
    real listing. Any other affiliate link in the body was composed by a model
    out of nothing.

    Nine shipped, across two posts. best-dog-backpack-carrier linked three
    invented carriers at amzn.to/4Xy9ZkL, amzn.to/5Zc3LmN and amzn.to/2Jg7QfR --
    shortcodes this account does not own, pointing wherever Amazon happens to
    resolve them. best-cat-calming-products published a "Buy now:" list of six
    literal placeholders, amzn.to/xyz1 through amzn.to/xyz6.

    The brief has said "NO links for additional picks" since the roundup format
    existed, and an LLM fact-check stage runs over the alternative sections on
    every roundup. Neither is a gate, so neither stopped this.

    The detector keys on the LINK, never on the surrounding prose, because the
    link is the one part of an invented pick that is falsifiable without leaving
    the machine: the entry supplies exactly one, so any other is unbacked
    whatever it points at. The specs are handled upstream instead -- the brief
    no longer asks for alternatives that were not supplied.
    """

    def setUp(self):
        import generate_posts as gp
        self.gp = gp
        self.own = "https://amzn.to/3Q8m7Hr"   # best-dog-backpack-carrier's real entry

    # --- the published instances, verbatim -----------------------------------
    def test_flags_the_three_invented_shortlinks_it_published(self):
        body = ("[Outward Hound Trail-Pup Dog Backpack](https://amzn.to/4Xy9ZkL)\n"
                "[K&H Pet Products Cool-Flow Backpack with Cooling Pad](https://amzn.to/5Zc3LmN)\n"
                "[Armarkat Adventure Dog Backpack](https://amzn.to/2Jg7QfR)\n"
                "[PetAmi Dog Backpack Carrier](https://amzn.to/3Q8m7Hr)")
        self.assertEqual(
            self.gp.find_unbacked_affiliate_links(body, self.own),
            ["amzn.to/4Xy9ZkL", "amzn.to/5Zc3LmN", "amzn.to/2Jg7QfR"])

    def test_flags_the_six_placeholder_buy_links_it_published(self):
        """Published with no scheme and no markdown target: "- Purina Calming
        Care - [amzn.to/xyz1]". A detector that only reads https:// URLs walks
        straight past the most obviously fake link on the site."""
        body = ("**Buy now:**\n- Purina Calming Care – [amzn.to/xyz1]\n"
                "- Feliway Classic Diffuser – [amzn.to/xyz2]\n"
                "- VetriScience Composure – [amzn.to/xyz3]\n"
                "- ThunderEase Collar – [amzn.to/xyz4]\n"
                "- Pet Remedy Spray – [amzn.to/xyz5]\n"
                "- NaturVet Calming Aid – [amzn.to/xyz6]")
        self.assertEqual(
            self.gp.find_unbacked_affiliate_links(body, "https://amzn.to/4cq8St0"),
            [f"amzn.to/xyz{n}" for n in range(1, 7)])

    # --- shapes the generator has not produced yet ---------------------------
    def test_flags_shapes_the_corpus_has_not_seen(self):
        """The two published shapes are not the boundary of the class. A long
        amazon.com/dp link, a scheme-less one, a www-prefixed one and an
        uppercase scheme all reach the same place, and a guard that only knows
        the two shapes already in the corpus is a guard the next run walks past.
        """
        for body in ("see https://www.amazon.com/dp/B0FAKE0001?tag=happypetdc-20",
                     "see amazon.com/dp/B0FAKE0001",
                     "see www.amazon.com/dp/B0FAKE0001",
                     "see HTTPS://AMAZON.COM/dp/B0FAKE0001",
                     "buy it at amzn.to/9zZzZz9 today"):
            with self.subTest(body=body):
                self.assertTrue(
                    self.gp.find_unbacked_affiliate_links(body, self.own), body)

    def test_a_short_code_differing_only_in_case_is_a_different_link(self):
        """amzn.to short codes are case-sensitive. Lowercasing the path to
        "normalize" it would let amzn.to/3q8M7hR pass as the real
        amzn.to/3Q8m7Hr, which is a different destination entirely."""
        self.assertEqual(
            self.gp.find_unbacked_affiliate_links("go to https://amzn.to/3q8M7hR", self.own),
            ["amzn.to/3q8M7hR"])

    # --- the inverse direction: real published linking must stay clean -------
    def test_does_not_flag_the_articles_own_link_however_it_is_written(self):
        """Every string here is a form the published corpus actually uses. A
        guard that fires on any of them is a guard that gets an allowlist bolted
        on and then gets deleted."""
        cases = [
            # short link, markdown, repeated (best-cat-litter-odor-control does this 5x)
            ("https://amzn.to/48cV2sA",
             "[Fresh Step Clumping Cat Litter Multi-Cat 14lb](https://amzn.to/48cV2sA) "
             "truly shines. Grab [it](https://amzn.to/48cV2sA) today."),
            # long link with the associate tag (best-cat-dental-treats)
            ("https://www.amazon.com/dp/B0828WNJXC?tag=happypetdc-20",
             "[Greenies Cat Treats](https://www.amazon.com/dp/B0828WNJXC?tag=happypetdc-20) "
             "sits near the top."),
            # entry written long, body written short-hand: same destination
            ("https://www.amazon.com/dp/B0727Y5ZD7?tag=happypetdc-20",
             "the cover at amazon.com/dp/B0727Y5ZD7 holds up"),
            # a different tracking tag on the same ASIN is the same product
            ("https://www.amazon.com/dp/B093K2NDS2?tag=happypetdc-20",
             "[Fun Feeder](https://www.amazon.com/dp/B093K2NDS2?tag=other-20)"),
        ]
        for own, body in cases:
            with self.subTest(own=own):
                self.assertEqual(self.gp.find_unbacked_affiliate_links(body, own), [], body)

    def test_does_not_flag_amazon_image_hosts_or_internal_links(self):
        """Front matter carries m.media-amazon.com and
        images-na.ssl-images-amazon.com image URLs on every post, bodies carry
        internal happypetproductreviews.com links, and ten posts carry a Chewy
        affiliate link resolved by chewy_lookup.py rather than written by a
        model. None of those is an Amazon product link."""
        body = ('image: "https://m.media-amazon.com/images/I/81nV6St3lYL._AC_SX425_.jpg"\n'
                'image: "https://images-na.ssl-images-amazon.com/images/P/B0BGVF6PGW.01.LZZZZZZZ.jpg"\n'
                "check our [cat tunnel toys](https://happypetproductreviews.com/cat-gear/"
                "best-cat-tunnel-toys/) roundup\n"
                "https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=4094470\n"
                "https://bigbarker.com/pages/research")
        self.assertEqual(self.gp.find_unbacked_affiliate_links(body, self.own), [])

    def test_makes_no_judgment_without_a_verified_link_to_compare_against(self):
        """An empty affiliate_url means there is nothing to judge against, so
        nothing is flagged. The companion test below is what keeps that from
        being a hole: a products.json entry with no affiliate_url is already
        refused before any of this runs."""
        self.assertEqual(
            self.gp.find_unbacked_affiliate_links("buy at https://amzn.to/4Xy9ZkL", ""), [])

    def test_an_entry_without_an_affiliate_url_never_reaches_the_gate(self):
        errors = self.gp.validate_product("best-x", {
            "name": "The X", "species": "dog", "title": "Best X", "keyword": "best x",
            "category": "dogs", "format": "roundup", "image": "https://img/x.jpg"})
        self.assertTrue(any("affiliate_url" in e for e in errors), errors)

    # --- wiring: every path that could publish an invented link is gated -----
    def test_both_output_gates_hold_an_article_with_a_second_link(self):
        body = ("[Invented Carrier](https://amzn.to/4Xy9ZkL) " + "word " * 900
                + self.own)
        for stage in ("generate", "review"):
            with self.subTest(stage=stage):
                with self.assertRaises(self.gp.GenerationStageError) as cm:
                    self.gp.validate_output(stage, body, "slug", affiliate_url=self.own)
                self.assertIn("no record of", str(cm.exception))

    def test_a_clean_body_still_passes_both_gates(self):
        body = ("Owners repeatedly mention the mesh panels. " + "word " * 900 + self.own
                + "\n\n" + QUESTION_BLOCK)
        for stage in ("generate", "review"):
            with self.subTest(stage=stage):
                self.gp.validate_output(stage, body, "slug", affiliate_url=self.own)

    def test_no_post_file_is_written_when_the_body_carries_an_invented_link(self):
        """Behavioural, not a refusal message: drive the real stage_article over
        a temp repo and assert on the victim's own record -- no draft file, no
        pin-queue entry."""
        product = {"topic": "best-x", "title": "Best X", "keyword": "best x",
                   "format": "roundup", "name": "The X", "category": "dog-carriers",
                   "species": "dog", "affiliate_url": self.own, "pulled_on": "2026-10-01"}
        body = ("## Additional Picks\n\n[K&H Cool-Flow Backpack](https://amzn.to/5Zc3LmN)\n\n"
                + ("word " * 800))
        with tempfile.TemporaryDirectory() as td:
            posts = Path(td) / "_posts"; posts.mkdir()
            pinq = Path(td) / "_pin_queue"; pinq.mkdir()
            with patch.object(self.gp, "POSTS_DIR", posts), \
                 patch.object(self.gp, "REPO_DIR", Path(td)), \
                 patch.object(self.gp, "PIN_GEN_AVAILABLE", False):
                with self.assertRaises(self.gp.GenerationStageError):
                    self.gp.stage_article("best-x", product, body,
                                          pin_desc="A carrier for small dogs.", index=0)
            self.assertEqual(list(posts.glob("*.md")), [])
            self.assertEqual(list(pinq.glob("*.json")), [])

    def test_the_generate_gate_is_called_with_the_entrys_link(self):
        """The check cannot fire at Gate 1 unless main() passes affiliate_url
        there, and it used to pass nothing. Read off the source, because the
        only other way to prove it is a live generation run."""
        source = (REPO / "generate_posts.py").read_text(encoding="utf-8")
        self.assertIn('validate_output("generate", content, slug,\n'
                      '                                    affiliate_url=product.get("affiliate_url", ""))',
                      source)

    # --- the stated rule and the enforced gate must not drift ---------------
    def test_the_rule_text_is_single_sourced_into_all_three_prompts(self):
        rule = self.gp.UNBACKED_PICK_RULE
        self.assertIn(rule, self.gp.GENERATOR_SYSTEM_PROMPT)
        self.assertIn(rule, self.gp.make_review_prompt("T", "kw", "body"))
        self.assertIn(rule, self.gp.make_rewrite_prompt("T", "kw", "body", "fix it"))

    def test_the_reviewer_can_fail_an_article_on_this_alone(self):
        p = self.gp.make_review_prompt("T", "kw", "body")
        self.assertIn("24. UNBACKED PRODUCT PICKS", p)
        # All three places the reviewer reads: the audit category, the PASS
        # CRITERIA list and the pass=false rule.
        self.assertIn("NO second affiliate link and no invented figure for a "
                      "non-featured product", p)
        self.assertIn("a second affiliate link or an invented figure for a "
                      "non-featured product present", p)

    def test_the_fabrication_prompt_is_gone_from_the_module(self):
        """find_alternative_products() asked a model for alternatives with "a
        SPECIFIC differentiating feature ... Be concrete, not vague". It had no
        callers, which is exactly why deleting it beats leaving it: the next
        person wiring up alternatives would have found it and used it."""
        source = (REPO / "generate_posts.py").read_text(encoding="utf-8")
        self.assertNotIn("def find_alternative_products", source)
        self.assertFalse(hasattr(self.gp, "find_alternative_products"))


class TestBannedPhraseBoundaries(unittest.TestCase):
    """BANNED_PHRASE_MAP substitutes inside the word it matches, so an unanchored
    pattern eats the stem of a longer word: 'pet parent' fired inside 'pet
    parenthood' and published "a bittersweet reality of dog ownerhood"
    (best-senior-dog-food)."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_pet_parenthood_becomes_ownership_not_ownerhood(self):
        out = self.gp.scrub_banned_phrases(
            "It is a bittersweet reality of pet parenthood.", "dog")
        self.assertIn("dog ownership", out)
        self.assertNotIn("ownerhood", out)

    def test_the_ordinary_substitutions_still_fire(self):
        cases = [("Many pet parents agree.",        "dog owners"),
                 ("Every pet parent knows.",        "dog owner"),
                 ("Your furry friend will love it.", "dog"),
                 ("Furry friends deserve better.",  "dogs"),
                 ("Our furry family members nap.",  "dogs"),
                 ("A fur baby needs space.",        "dog")]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertIn(expected, self.gp.scrub_banned_phrases(text, "dog"))

    def test_every_pattern_is_word_anchored_on_both_ends(self):
        """Structural, not a list of known-bad words: any future entry that
        forgets a boundary can eat a longer word the same way 'pet parent' did."""
        unanchored = [p for p, _ in self.gp.BANNED_PHRASE_MAP
                      if not (p.startswith(r"\b") and p.endswith(r"\b"))]
        self.assertEqual(unanchored, [])

    def test_no_pattern_matches_inside_a_longer_word(self):
        """The defect in one assertion: run every pattern against its own phrase
        with a letter suffix, which must no longer match."""
        import re as _re
        bitten = []
        for pattern, _ in self.gp.BANNED_PHRASE_MAP:
            phrase = pattern.replace(r"\b", "")
            if _re.search(pattern, phrase + "hood", _re.IGNORECASE):
                bitten.append(pattern)
        self.assertEqual(bitten, [])


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


class TestSelectNextTopicSpeciesBalance(unittest.TestCase):
    """select_next_topic avoids running 3+ of the same species in a row."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def _products(self):
        # dict order: dog, dog, cat, both
        return {
            "d1": {"topic": "d1", "title": "D1", "keyword": "k", "format": "roundup", "species": "dog"},
            "d2": {"topic": "d2", "title": "D2", "keyword": "k", "format": "roundup", "species": "dog"},
            "c1": {"topic": "c1", "title": "C1", "keyword": "k", "format": "roundup", "species": "cat"},
            "b1": {"topic": "b1", "title": "B1", "keyword": "k", "format": "roundup", "species": "both"},
        }

    def test_two_dogs_in_a_row_forces_a_non_dog_next(self):
        slug, p = self.gp.select_next_topic(
            self._products(), used_slugs=set(), recent_species=["dog", "dog"])
        self.assertEqual(slug, "c1")  # skips d1/d2, picks first non-dog

    def test_two_cats_in_a_row_forces_a_non_cat_next(self):
        slug, p = self.gp.select_next_topic(
            self._products(), used_slugs=set(), recent_species=["cat", "cat"])
        self.assertEqual(slug, "d1")  # cat blocked -> first dict entry qualifies

    def test_no_streak_keeps_plain_order(self):
        slug, p = self.gp.select_next_topic(
            self._products(), used_slugs=set(), recent_species=["cat", "dog"])
        self.assertEqual(slug, "d1")

    def test_both_counts_as_breaking_a_streak(self):
        # newest is 'both', so no dog streak even though the older one is dog
        slug, p = self.gp.select_next_topic(
            self._products(), used_slugs=set(), recent_species=["both", "dog"])
        self.assertEqual(slug, "d1")

    def test_falls_back_when_only_blocked_species_remains(self):
        only_dogs = {
            "d1": {"topic": "d1", "title": "D1", "keyword": "k", "format": "roundup", "species": "dog"},
            "d2": {"topic": "d2", "title": "D2", "keyword": "k", "format": "roundup", "species": "dog"},
        }
        slug, p = self.gp.select_next_topic(
            only_dogs, used_slugs=set(), recent_species=["dog", "dog"])
        self.assertEqual(slug, "d1")  # nothing else available -> plain order

    def test_products_json_never_stacks_three_same_species(self):
        # Walk the real products.json queue applying the rule; assert no 3-run.
        products = self.gp.load_products()
        used = set()
        history = []  # newest first
        picked_species = []
        for _ in range(len(products)):
            res = self.gp.select_next_topic(products, used, recent_species=history)
            if res is None:
                break
            slug, p = res
            used.add(slug)
            sp = p.get("species")
            picked_species.append(sp)
            history = [sp] + history
        runs = [picked_species[i] == picked_species[i + 1] == picked_species[i + 2]
                for i in range(len(picked_species) - 2)]
        self.assertFalse(any(runs), f"3+ same species in a row: {picked_species}")


class TestQ4PublishOrder(unittest.TestCase):
    """Scarlett's Q4 order (Director-approved 2026-10-13,
    80-workspace/scarlett/2026-10-13-q4-topic-seed-list.md). products.json holds
    the queued part: the head for Thu 10-15, her 12 queued topics in slot order,
    then the 11 off-season topics in her January order. _data/publish_plan.json
    holds the whole order, including her 10 new topics, which come through the
    refill and are appended to the end of products.json."""

    # The queue as this commit leaves it: slug -> species, read off products.json.
    QUEUED = {
        "best-heated-outdoor-cat-houses": "cat",
        "best-self-warming-pet-pads": "both", "best-dog-collars-night-walks": "dog",
        "best-lickable-cat-treats": "cat", "best-dog-winter-coats": "dog",
        "best-pet-hair-vacuums": "both", "best-freeze-dried-dog-treats": "dog",
        "best-dog-paw-balms": "dog", "best-plush-dog-toys": "dog",
        "best-cardboard-cat-scratchers": "cat", "best-pet-first-aid-kits": "both",
        "best-puppy-teething-toys": "dog", "best-dog-training-clickers": "dog",
        "best-cat-hairball-remedies": "cat", "best-cat-litter-mats": "cat",
        "best-pet-dental-water-additives": "both", "best-cat-flea-treatments": "cat",
        "best-fish-oil-for-dogs": "dog", "best-elevated-cat-bowls": "cat",
        "best-dog-shampoo-sensitive-skin": "dog", "best-senior-cat-food": "cat",
        "best-dog-ear-cleaners": "dog", "best-airtight-pet-food-containers": "both",
        "best-cat-litter-scoops": "cat",
    }
    # Her 10 new topics, in slot order: slug -> species.
    NEW = {"best-cat-advent-calendars": "cat", "best-dog-advent-calendars": "dog",
           "best-automatic-ball-launchers": "dog", "best-cat-christmas-tree-deterrents": "cat",
           "best-dog-christmas-sweaters": "dog", "best-window-bird-feeders-cats": "cat",
           "best-heated-water-bowls-outdoor-cats": "cat", "best-breakaway-cat-collars": "cat",
           "best-cat-trees-small-apartments": "cat", "best-washable-crate-pads-puppies": "dog"}
    # Her table 1 (the 22 slots Mon 10-19 .. Thu 12-31), preceded by the 10-15
    # head and followed by her January list. Written out from her file, not
    # read from _data/publish_plan.json.
    SLOTS = [
        "best-heated-outdoor-cat-houses",
        "best-self-warming-pet-pads", "best-dog-collars-night-walks",
        "best-cat-advent-calendars", "best-dog-advent-calendars",
        "best-lickable-cat-treats", "best-dog-winter-coats", "best-pet-hair-vacuums",
        "best-automatic-ball-launchers", "best-cat-christmas-tree-deterrents",
        "best-dog-christmas-sweaters", "best-window-bird-feeders-cats",
        "best-freeze-dried-dog-treats", "best-heated-water-bowls-outdoor-cats",
        "best-dog-paw-balms", "best-breakaway-cat-collars", "best-plush-dog-toys",
        "best-cardboard-cat-scratchers", "best-pet-first-aid-kits",
        "best-puppy-teething-toys", "best-cat-trees-small-apartments",
        "best-washable-crate-pads-puppies", "best-dog-training-clickers",
        "best-cat-hairball-remedies", "best-cat-litter-mats", "best-pet-dental-water-additives",
        "best-cat-flea-treatments", "best-fish-oil-for-dogs", "best-elevated-cat-bowls",
        "best-dog-shampoo-sensitive-skin", "best-senior-cat-food", "best-dog-ear-cleaners",
        "best-airtight-pet-food-containers", "best-cat-litter-scoops",
    ]

    def _walk(self, queue, history=("dog", "cat"), order=None, today="2026-10-15"):
        """Every pick the real select_next_topic makes on `queue` (slug -> species,
        in products.json file order), from the 2026-10-13 feed (newest dog, then
        cat), with the real publish plan unless `order` is given, as of `today`."""
        import generate_posts as gp
        products = {t: {"topic": t, "title": t, "keyword": t, "format": "roundup",
                        "species": sp} for t, sp in queue.items()}
        history, picks = list(history), []
        while (res := gp.select_next_topic(products, set(picks), recent_species=history,
                                           order=order, today=today)):
            picks.append(res[0])
            history = [res[1]["species"]] + history
        return picks

    def test_the_live_queue_keeps_the_planned_relative_order(self):
        # Robust to publishing (entries retire) and to refills (entries append).
        import generate_posts as gp
        live = [t for t in gp.load_products() if t in self.QUEUED]
        self.assertEqual(live, [t for t in self.QUEUED if t in live])

    def test_the_queue_alone_in_file_order_follows_the_plan(self):
        """No plan (order=[]): file order through the species rule. Expected picks
        were worked by hand: after freeze-dried treats and paw balms (two dogs),
        plush dog toys waits one slot and cardboard cat scratchers goes early."""
        expected = list(self.QUEUED)
        expected[8], expected[9] = expected[9], expected[8]
        self.assertEqual(self._walk(self.QUEUED, order=[]), expected)

    def test_the_22_slots_publish_in_her_order_with_the_new_topics_appended_last(self):
        """The acceptance test: today's queue, then her 10 new topics appended at
        the END of products.json (where a refill puts them), through the real
        picker and the real plan. Publishes exactly her table, then January."""
        queue = dict(self.QUEUED, **self.NEW)
        self.assertEqual(list(queue)[-10:], list(self.NEW))   # appended last
        self.assertEqual(self._walk(queue), self.SLOTS)

    def test_a_new_topic_that_never_resolved_hands_its_slot_to_the_next_planned_one(self):
        queue = dict(self.QUEUED)
        queue.update({t: sp for t, sp in self.NEW.items() if t != "best-cat-advent-calendars"})
        self.assertEqual(self._walk(queue)[:5], [
            "best-heated-outdoor-cat-houses", "best-self-warming-pet-pads",
            "best-dog-collars-night-walks", "best-dog-advent-calendars",
            "best-lickable-cat-treats"])

    PUBLISH_BY = {"best-cat-advent-calendars": "2026-11-02", "best-dog-advent-calendars": "2026-11-05",
                  "best-automatic-ball-launchers": "2026-11-19",
                  "best-cat-christmas-tree-deterrents": "2026-11-23",
                  "best-dog-christmas-sweaters": "2026-11-26",
                  "best-window-bird-feeders-cats": "2026-11-30",
                  "best-heated-water-bowls-outdoor-cats": "2026-12-07",
                  "best-breakaway-cat-collars": "2026-12-14",
                  "best-cat-trees-small-apartments": "2026-12-31",
                  "best-washable-crate-pads-puppies": "2026-12-31"}

    def test_each_seed_expires_a_week_after_its_slot_capped_for_the_holidays(self):
        """Slot + 7 days (worked by hand from her table 1), capped at 12-20 for
        the Christmas-themed seeds and 12-31 for the rest: only the crate pads
        (slot Mon 12-28) hit a cap."""
        plan = json.loads((REPO / "_data" / "publish_plan.json").read_text(encoding="utf-8"))
        self.assertEqual({s["topic"]: s["publish_by"] for s in plan["seeds"]}, self.PUBLISH_BY)

    def test_a_seed_resolved_after_its_publish_by_falls_back_to_file_order(self):
        queue = dict(self.QUEUED, **self.NEW)
        on_time = self._walk(queue, today="2026-11-02")      # its last day
        self.assertEqual(on_time.index("best-cat-advent-calendars"), 3)
        late = self._walk(queue, today="2026-11-03")
        # Unplanned now: behind every planned entry, January included, then in
        # file order among the unplanned (it is the first NEW entry).
        self.assertEqual(late[-1], "best-cat-advent-calendars")
        self.assertEqual(late[:-1], [t for t in self.SLOTS if t != "best-cat-advent-calendars"])

    def test_an_unplanned_topic_waits_behind_every_planned_one(self):
        queue = dict({"best-unplanned-refill-topic": "both"}, **self.QUEUED)
        self.assertEqual(self._walk(queue)[-1], "best-unplanned-refill-topic")

    def test_every_seed_is_planned_and_seedable_now(self):
        """Each seed sits in `order`, and each one not yet taken passes the
        refill's own topic validation once the session adds a title."""
        import refill_cli
        plan = json.loads((REPO / "_data" / "publish_plan.json").read_text(encoding="utf-8"))
        self.assertEqual([s["topic"] for s in plan["seeds"]],
                         [t for t in self.SLOTS if t in self.NEW])
        self.assertEqual(plan["order"], self.SLOTS)
        taken = refill_cli.taken_slugs(json.loads((REPO / "products.json").read_text(encoding="utf-8")))
        for s in plan["seeds"]:
            if s["topic"] in taken:
                continue
            with self.subTest(seed=s["topic"]):
                self.assertEqual(s["species"], self.NEW[s["topic"]])
                self.assertNotIn("product", s)
                self.assertEqual(refill_cli.topic_problems(dict(s, title="A Title"), taken), [])

    def test_the_species_fixture_matches_the_live_queue(self):
        import generate_posts as gp
        live = gp.load_products()
        self.assertEqual([t for t, sp in self.QUEUED.items()
                          if t in live and live[t].get("species") != sp], [])


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

    def test_roundup_without_runners_up_asks_for_no_alternatives_at_all(self):
        """This test used to assert the opposite, and asserting it is what kept
        the defect alive: the old fallback told the writer "EXACTLY 3
        alternatives -- use well-known brands you are confident exist. Do not
        fabricate products." A model cannot be confident a product exists, so
        sentence one is an instruction to invent from memory and sentence two is
        a wish. It produced "Purrfect Escape-Free Outdoor Kit", "Adventure Cat
        Trail Harness" and "K&H Cool-Flow Backpack", none of which has a record
        anywhere in this repo, all three published on a monetized page with
        invented specs.

        With no runners_up there is no source for an alternative product, so the
        brief asks for none. A shorter roundup is the correct outcome.
        """
        product = {"topic": "best-mats", "title": "Best Mats", "keyword": "best mats",
                   "format": "roundup", "name": "TopMat", "category": "dogs",
                   "species": "dog"}
        out = self.gp.build_writer_inputs("best-mats", product)
        self.assertNotIn("well-known brands", out["user"])
        self.assertNotIn("confident", out["user"])
        self.assertIn("NO Additional Picks section", out["user"])
        self.assertIn("do not name, describe, rank or link another product",
                      out["user"])
        # No placeholder is emitted at all when there is nothing to put in it,
        # in either brace form.
        self.assertNotIn("{{ALTERNATIVE_PRODUCTS}}", out["user"])
        self.assertNotIn("{ALTERNATIVE_PRODUCTS}", out["user"])

    def test_roundup_without_runners_up_asks_for_no_comparison_table(self):
        """One product is not a comparison. Five published posts had to have a
        one-row "Comparison Table" deleted by hand once their invented picks
        came out of them; the brief should not ask for the shape again."""
        product = {"topic": "best-mats", "title": "Best Mats", "keyword": "best mats",
                   "format": "roundup", "name": "TopMat", "category": "dogs",
                   "species": "dog"}
        out = self.gp.build_writer_inputs("best-mats", product)
        self.assertIn("NO Comparison Table", out["user"])
        self.assertNotIn("Comparison Table (H2)", out["user"])

    def test_a_one_product_roundup_is_not_asked_for_a_four_product_word_count(self):
        """The two halves of the brief have to agree or the model resolves the
        contradiction by padding, and the only thing left to pad is
        featured-product detail -- which is the same fabrication moved rather
        than removed. The floor stays above validate_output's MIN_WORD_COUNT so
        the shorter shape is not then held for length.

        Reachable today, not hypothetical: refill_products.apply_resolution only
        sets runners_up when the search returned a distinct-brand candidate, so
        a topic whose results are all one brand reaches the generator with none.
        """
        import generate_posts as gp
        base = {"topic": "best-mats", "title": "Best Mats", "keyword": "best mats",
                "format": "roundup", "name": "TopMat", "category": "dogs",
                "species": "dog"}
        alone = self.gp.build_writer_inputs("best-mats", dict(base))["user"]
        self.assertIn("Length: 750-900 words", alone)
        self.assertNotIn("950-1100", alone)
        self.assertNotIn("This is firm", alone)
        floor = int(re.search(r"Length: (\d+)-", alone).group(1))
        self.assertGreater(floor, 700, "asks for fewer words than the gate accepts")
        # And the inverse: a real roundup keeps the original length.
        withalts = self.gp.build_writer_inputs(
            "best-mats", dict(base, runners_up="MatA;MatB"))["user"]
        self.assertIn("Length: 950-1100 words", withalts)

    def test_roundup_with_runners_up_still_asks_for_both_sections(self):
        """The inverse. Removing the fabrication source must not remove the
        feature: an entry that supplies alternatives still gets an Additional
        Picks section and a comparison table."""
        product = {"topic": "best-mats", "title": "Best Mats", "keyword": "best mats",
                   "format": "roundup", "name": "TopMat", "category": "dogs",
                   "species": "dog", "runners_up": "MatA;MatB"}
        out = self.gp.build_writer_inputs("best-mats", product)
        self.assertIn("Additional Picks: Use ONLY these products", out["user"])
        self.assertIn("Comparison Table (H2)", out["user"])
        self.assertIn("MatA", out["user"])

    def test_the_comparison_table_does_not_ask_for_a_price_tier(self):
        """The brief forbade inventing a dollar amount for an alternative and
        then asked for a $/$$/$$$ band in the same breath. A band IS a price
        claim: the pipeline knows nothing about an alternative but its name, so
        every tier it printed was guessed and then rendered in a table, which is
        the shape a reader reads as researched."""
        product = {"topic": "best-mats", "title": "Best Mats", "keyword": "best mats",
                   "format": "roundup", "name": "TopMat", "category": "dogs",
                   "species": "dog", "runners_up": "MatA;MatB"}
        user = self.gp.build_writer_inputs("best-mats", product)["user"]
        self.assertNotIn("Price Range", user)
        self.assertNotIn("$$", user)
        self.assertIn("no price", user.lower())

    def test_a_verified_featured_price_is_still_given_to_the_writer(self):
        """The inverse. Dropping the guessed tier must not drop the one price
        the pipeline actually verified off the listing."""
        product = {"topic": "best-mats", "title": "Best Mats", "keyword": "best mats",
                   "format": "roundup", "name": "TopMat", "category": "dogs",
                   "species": "dog", "runners_up": "MatA;MatB", "price": "34.99"}
        user = self.gp.build_writer_inputs("best-mats", product)["user"]
        self.assertIn("$34.99", user)
        self.assertIn("verified from Amazon", user)


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
                   "species": "dog", "affiliate_url": "https://amzn.to/abc", "pulled_on": "2026-10-01"}
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
                   "asin": "B0GG8LR3RW", "image": curated, "pulled_on": "2026-10-01"}
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
                   "asin": "B0GG8LR3RW", "pulled_on": "2026-10-01"}  # no curated image
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

    def _stage(self, product, body):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        gp = self.gp
        with tempfile.TemporaryDirectory() as td:
            posts = Path(td) / "_posts"; posts.mkdir()
            (Path(td) / "_pin_queue").mkdir()
            with patch.object(gp, "POSTS_DIR", posts), \
                 patch.object(gp, "REPO_DIR", Path(td)), \
                 patch.object(gp, "PIN_GEN_AVAILABLE", False):
                out = gp.stage_article(product["topic"], product, body,
                                       pin_desc="Great mat for dogs.", index=0)
            return out["draft_path"].read_text(encoding="utf-8")

    SNUFFLE = {"topic": "best-x", "title": "Best X", "keyword": "best x",
               "format": "roundup", "category": "dog-gear", "species": "dog",
               "name": "AWOOF Pet Snuffle Mat for Dogs, Interactive Feed Game",
               "affiliate_url": "https://www.amazon.com/dp/B07N1JYYCW?tag=happypetdc-20",
               "price": "11.98", "stars": 4.2, "pulled_on": "2026-09-25"}
    PRICED_BODY = ("## Heading\n\nIt carries a 4.2 out of 5 star rating on Amazon and "
                   "sells for $11.98. " + ("word " * 800))

    def test_staged_post_names_its_pick_in_front_matter(self):
        text = self._stage(dict(self.SNUFFLE), "## Heading\n\n" + ("word " * 800))
        fm = text.split("---", 2)[1]
        self.assertIn('product_short: "AWOOF Pet Snuffle Mat for Dogs"\n', fm)

    def test_staged_post_labels_price_and_rating_with_the_pull_date(self):
        text = self._stage(dict(self.SNUFFLE), self.PRICED_BODY)
        self.assertIn("4.2 out of 5 star rating on Amazon and sells for "
                      "$11.98 (as of Sep 25, 2026). ", text)

    def _assert_held(self, product, body, *needles):
        """stage_article raises and writes nothing: no draft, no pin-queue entry."""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        gp = self.gp
        with tempfile.TemporaryDirectory() as td:
            posts = Path(td) / "_posts"; posts.mkdir()
            pinq = Path(td) / "_pin_queue"; pinq.mkdir()
            with patch.object(gp, "POSTS_DIR", posts), \
                 patch.object(gp, "REPO_DIR", Path(td)), \
                 patch.object(gp, "PIN_GEN_AVAILABLE", False):
                with self.assertRaises(gp.GenerationStageError) as cm:
                    gp.stage_article(product["topic"], product, body,
                                     pin_desc="Great mat for dogs.", index=0)
            self.assertEqual(list(posts.iterdir()) + list(pinq.iterdir()), [])
        for needle in ("stage_article", product["topic"]) + needles:
            self.assertIn(needle, str(cm.exception))

    def test_a_missing_pull_date_is_held_not_stamped_with_today(self):
        # apply_resolution always writes pulled_on, so only hand-edited data
        # reaches here; labelling it with the staging date would be a guess.
        for missing in ({}, {"pulled_on": None}, {"pulled_on": ""}):
            with self.subTest(missing=missing):
                product = {k: v for k, v in self.SNUFFLE.items() if k != "pulled_on"}
                product.update(missing)
                self._assert_held(product, self.PRICED_BODY, "pulled_on")

    def test_a_malformed_pull_date_is_held_naming_the_value(self):
        for bad in ("yesterday", "2026-13-45", "Sep 25, 2026", "2026-09-25T10:00", 20260925):
            with self.subTest(bad=bad):
                self._assert_held(dict(self.SNUFFLE, pulled_on=bad), self.PRICED_BODY,
                                  "pulled_on", repr(bad))

    def test_a_pulled_figure_left_unlabelled_is_held(self):
        # "rated 4.2 by owners" is a phrasing the labeller does not know; the
        # post-label check catches it rather than publishing it undated.
        body = "## Heading\n\nOwners rated it 4.2 by and large. " + ("word " * 800)
        self._assert_held(dict(self.SNUFFLE), body, "4.2")


class TestAsOfLabels(unittest.TestCase):
    """label_amazon_figures: every Amazon price and star rating a post shows
    carries "(as of <date>)" -- Director's decision 2026-10-12, keep pull-time
    figures, label them. Only the pulled values are labelled, so a price band
    ("under $15") or a different product's figure is never stamped."""

    DATE = "2026-10-02"
    L = " (as of Oct 2, 2026)"

    def label(self, body, price="17.97", stars=4.8):
        import generate_posts as gp
        return gp.label_amazon_figures(body, price, stars, self.DATE)

    def test_date_format_has_no_zero_pad(self):
        import generate_posts as gp
        self.assertEqual(gp.as_of_text("2026-10-02"), "Oct 2, 2026")
        self.assertEqual(gp.as_of_text("2026-09-25"), "Sep 25, 2026")

    def test_real_phrasings_from_published_posts(self):
        cases = [
            ("At $17.97 for a 7 lb bag, it is cheap.",
             "At $17.97" + self.L + " for a 7 lb bag, it is cheap."),
            ("It is rated 4.8/5 on Amazon.", "It is rated 4.8/5" + self.L + " on Amazon."),
            ("It holds a 4.8 out of 5 star rating on Amazon.",
             "It holds a 4.8 out of 5 star rating" + self.L + " on Amazon."),
            ("It carries a 4.8-star rating.", "It carries a 4.8-star rating" + self.L + "."),
            ("It holds a 4.8 star rating, too.", "It holds a 4.8 star rating" + self.L + ", too."),
            ("With 4.8 stars across 853 ratings.", "With 4.8 stars" + self.L + " across 853 ratings."),
            ("a solid **4.8/5** rating and a price of **$17.97**, so",
             "a solid **4.8/5** rating and a price of **$17.97**" + self.L + ", so"),
        ]
        for before, after in cases:
            with self.subTest(before=before):
                self.assertEqual(self.label(before), after)

    def test_one_label_per_sentence_after_its_last_figure(self):
        body = ("It carries a 4.8 out of 5 star rating on Amazon and sells for $17.97. "
                "At $17.97 it is a bargain.")
        self.assertEqual(self.label(body),
                         "It carries a 4.8 out of 5 star rating on Amazon and sells for "
                         "$17.97" + self.L + ". At $17.97" + self.L + " it is a bargain.")

    def test_a_line_break_ends_the_sentence(self):
        body = "- Priced at $17.97\n- Rated 4.8/5 by owners"
        self.assertEqual(self.label(body),
                         "- Priced at $17.97" + self.L + "\n- Rated 4.8/5" + self.L + " by owners")

    def test_bands_and_other_figures_are_never_labelled(self):
        for body in ("Most mats sit under $15 or $10-$20.",
                     "*Price Range Key: \\$ = Under \\$50, \\$\\$ = \\$50-\\$100*",
                     "It costs $117.97 or $17.975 or $17.9.",
                     "Rated 4.85 stars, 14.8/5, 4.8/50 and 4.7/5.",
                     "Most owners give it 4.8 out of 10.",
                     "A $17 mat."):
            with self.subTest(body=body):
                self.assertEqual(self.label(body), body)

    def test_table_rows_rating_lines_and_link_targets_are_left_alone(self):
        body = ("| [Mat](https://www.amazon.com/dp/B0X) | $17.97 | 4.8/5 |\n"
                "**Our Rating: 4.8/5**\n"
                "Rating: 4.8/5\n"
                "[See it](https://example.com/p?$17.97=4.8/5)")
        self.assertEqual(self.label(body), body)

    def test_labelling_twice_changes_nothing(self):
        body = ("It carries a 4.8 out of 5 star rating and sells for $17.97. "
                "At **$17.97** it is a bargain.\n- 4.8/5 stars")
        once = self.label(body)
        self.assertNotEqual(once, body)
        self.assertEqual(self.label(once), once)

    def test_missing_values_label_nothing(self):
        body = "It sells for $17.97 with a 4.8/5 rating."
        self.assertEqual(self.label(body, price=None, stars=None), body)
        self.assertEqual(self.label(body, price="", stars=None), body)

    def test_a_comma_formatted_price_matches_its_plain_value(self):
        self.assertEqual(self.label("It costs $1,299.00 today.", price="1299.00"),
                         "It costs $1,299.00" + self.L + " today.")

    def test_a_whole_dollar_price_also_matches_without_cents(self):
        self.assertEqual(self.label("At around $47 it is fair. Not $47.50 or $470.", price="47.00"),
                         "At around $47" + self.L + " it is fair. Not $47.50 or $470.")

    def test_a_whole_star_value_matches_its_one_decimal_form(self):
        self.assertEqual(self.label("It holds a 4.0 star rating.", stars=4),
                         "It holds a 4.0 star rating" + self.L + ".")

    def test_rating_and_average_phrasings(self):
        cases = [
            ("It holds a 4.8 rating on Amazon.", "It holds a 4.8 rating" + self.L + " on Amazon."),
            ("A 4.8 average across 900 reviews.", "A 4.8 average" + self.L + " across 900 reviews."),
            ("Its 4.8 average rating is high.", "Its 4.8 average rating" + self.L + " is high."),
        ]
        for before, after in cases:
            with self.subTest(before=before):
                self.assertEqual(self.label(before), after)
        for body in ("A 4.7 rating.", "A 4.85 rating.", "A 14.8 average."):
            with self.subTest(body=body):
                self.assertEqual(self.label(body), body)

    def test_the_label_follows_the_noun_phrase_not_the_bare_figure(self):
        # Real published contexts (2026-10-13 gate round 1, Standards F6).
        cases = [
            ("with a low price and a 4.8 star owner rating behind it.",
             "with a low price and a 4.8 star owner rating" + self.L + " behind it."),
            ("with a 4.1 out of 5 rating and a $17.97 price that reads like cheap insurance.",
             "with a 4.1 out of 5 rating and a $17.97 price" + self.L
             + " that reads like cheap insurance."),
            ("Owners have given it a **4.8/5** rating, praising its build.",
             "Owners have given it a **4.8/5** rating" + self.L + ", praising its build."),
            ("The $17.97 price sits well above a manual brush.",
             "The $17.97 price" + self.L + " sits well above a manual brush."),
            ("so the $17.97 hardware price only starts the cost.",
             "so the $17.97 hardware price" + self.L + " only starts the cost."),
            ("It has a $17.97 price tag.", "It has a $17.97 price tag" + self.L + "."),
            ("The 4.8/5 rating suggests most cats approve.",
             "The 4.8/5 rating" + self.L + " suggests most cats approve."),
        ]
        for before, after in cases:
            with self.subTest(before=before):
                self.assertEqual(self.label(before), after)
                self.assertEqual(self.label(after), after)

    def test_a_word_outside_the_noun_phrase_stops_the_label(self):
        for before, after in (
                ("At $17.97 the tub is cheap.", "At $17.97" + self.L + " the tub is cheap."),
                ("Rated 4.8/5 from owners and a price of note.",
                 "Rated 4.8/5" + self.L + " from owners and a price of note."),
                ("At $17.97 it rates well.", "At $17.97" + self.L + " it rates well.")):
            with self.subTest(before=before):
                self.assertEqual(self.label(before), after)


class TestAmazonFiguresLabelledCheck(unittest.TestCase):
    """assert_amazon_figures_labelled: after labelling, a sentence that still
    states the exact pulled price, or the exact star value next to a rating
    word, without an "(as of" label holds the article. Independent of the
    labeller's patterns, so it catches phrasings the labeller does not know."""

    def check(self, body, price="17.97", stars=4.8):
        import generate_posts as gp
        gp.assert_amazon_figures_labelled("stage_article", body, "best-x", price, stars)

    def assertHeld(self, body, **kw):
        import generate_posts as gp
        with self.assertRaises(gp.GenerationStageError) as cm:
            self.check(body, **kw)
        self.assertIn("stage_article", str(cm.exception))
        self.assertIn("best-x", str(cm.exception))

    def test_unlabelled_figures_are_held(self):
        for body in ("Owners rated it 4.8 by and large.",
                     "It sits at 4.8 with most reviewers, a strong score.",
                     "You pay $17.97 and get two mats.",
                     "Fine.\nIt costs $17.97\nMore."):
            with self.subTest(body=body):
                self.assertHeld(body)

    def test_one_label_covers_its_sentence(self):
        self.check("It holds a 4.8 star rating and sells for $17.97 (as of Oct 2, 2026). "
                   "Next sentence.")

    def test_a_label_in_another_sentence_does_not_cover(self):
        self.assertHeld("It sells for $17.97 (as of Oct 2, 2026). Owners rated it 4.8 too.")

    def test_legitimate_text_is_not_held(self):
        for body in ("It is 4.8 inches wide.",
                     "Most mats sit under $15, or $17.975 in bulk, or $117.97.",
                     "| [Mat](https://www.amazon.com/dp/B0X) | $17.97 | 4.8/5 |",
                     "**Our Rating: 4.8/5**",
                     "[See it](https://example.com/p?$17.97=4.8-rating)",
                     "Rated 4.7 stars by owners.",
                     ""):
            with self.subTest(body=body):
                self.check(body)

    def test_nothing_pulled_checks_nothing(self):
        self.check("You pay $17.97, rated 4.8.", price=None, stars=None)


class TestShortProductName(unittest.TestCase):
    """short_product_name: the generator's only source of a product name is the
    Amazon listing title. The panel and buybar show the part before the
    listing's first separator. A copy heuristic: Scarlett may replace it."""

    def test_cuts_at_the_first_listing_separator(self):
        import generate_posts as gp
        cases = {
            "AWOOF Pet Snuffle Mat for Dogs, Interactive Feed Game": "AWOOF Pet Snuffle Mat for Dogs",
            "Cat Window Hammock - Mess-Free Waterproof Cats Window Perch": "Cat Window Hammock",
            "Tractive Smart Dog GPS Tracker – Live Pet Tracker": "Tractive Smart Dog GPS Tracker",
            "Catit Senses 2.0 Multi Feeder | Interactive": "Catit Senses 2.0 Multi Feeder",
            "Frisco Cat Tree: 52-in Multi-Level Condo": "Frisco Cat Tree",
            "Ratio 2:1 Dog Food": "Ratio 2:1 Dog Food",
            "Blue Buffalo Bits Dog Treats": "Blue Buffalo Bits Dog Treats",
            "Hide‑A‑Squirrel Dog Toy, 2‑in‑1": "Hide‑A‑Squirrel Dog Toy",
            "  K&H Heated Cat House, Outdoor  ": "K&H Heated Cat House",
        }
        for name, short in cases.items():
            with self.subTest(name=name):
                self.assertEqual(gp.short_product_name(name), short)

    def test_a_separatorless_title_is_capped_at_eight_words(self):
        # Eight, not six: the Director's ruling of 2026-10-13.
        import generate_posts as gp
        cases = {
            "Pet Grooming Glove Gentle Deshedding Brush Efficient Pet Hair Remover":
                "Pet Grooming Glove Gentle Deshedding Brush Efficient Pet",
            "Blue Buffalo Bits Beef Soft & Chewy Dog Treats": "Blue Buffalo Bits Beef Soft & Chewy Dog",
            # exactly eight words: kept whole
            "KONG Classic Dog Toy Durable Natural Rubber Chew":
                "KONG Classic Dog Toy Durable Natural Rubber Chew",
            # the cap never leaves a dangling connector, however many in a row
            "Hertzko Self Cleaning Slicker Brush for Dogs and Cats":
                "Hertzko Self Cleaning Slicker Brush for Dogs",
            "Calming Bed Donut Shape Cuddler Round for the Dogs and Cats":
                "Calming Bed Donut Shape Cuddler Round",
        }
        for name, short in cases.items():
            with self.subTest(name=name):
                self.assertEqual(gp.short_product_name(name), short)

    def test_empty_name_gives_empty(self):
        import generate_posts as gp
        self.assertEqual(gp.short_product_name(""), "")
        self.assertEqual(gp.short_product_name(None), "")


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


class TestFacebookLinkTracking(unittest.TestCase):
    """The link in the Facebook message carries a facebook UTM tag, so GA4 can
    tell Facebook visits from direct and referral ones (traffic plan move 7).
    It mirrors the Pinterest tag generate_posts.build_url puts on article_url:
    utm_source=pinterest&utm_medium=social&utm_campaign=pin.

    Col B (the link the Apps Script posts) carries the same facebook tag as the
    message (col C); before 2026-10-13 it carried the Pinterest tag, so Facebook
    clicks counted as Pinterest. Col B is also the dedup key: main() re-reads
    every sent/ file on each run, so a row must match on its normalized form
    whichever tag it was written with, or the back catalogue re-queues."""

    PIN_URL = ("https://happypetproductreviews.com/toys/best-snuffle-mats-dogs/"
               "?utm_source=pinterest&utm_medium=social&utm_campaign=pin")
    FB_URL = ("https://happypetproductreviews.com/toys/best-snuffle-mats-dogs/"
              "?utm_source=facebook&utm_medium=social&utm_campaign=post")

    def setUp(self):
        import push_pins_to_sheets as pk
        self.pk = pk

    def _url_in(self, msg):
        return msg.splitlines()[-1]

    def test_message_link_carries_exactly_the_facebook_tag(self):
        for slug in ("best-snuffle-mats-dogs", "best-dog-probiotic-supplements"):
            with self.subTest(slug=slug):   # fallback hook, then a curated one
                url = self._url_in(self.pk._build_fb_message(slug, "Best Snuffle Mats", self.PIN_URL))
                self.assertEqual(url, self.FB_URL)

    def test_the_pinterest_tag_never_reaches_facebook(self):
        msg = self.pk._build_fb_message("best-snuffle-mats-dogs", "Best Snuffle Mats", self.PIN_URL)
        self.assertNotIn("pinterest", msg)
        self.assertEqual(msg.count("?"), 1)

    def test_url_shapes_all_normalise_to_one_tagged_link(self):
        base = "https://happypetproductreviews.com/toys/best-snuffle-mats-dogs"
        for raw in (base, base + "/", base + "/?a=1&b=2", base + "?a=1", base + "/#top"):
            with self.subTest(raw=raw):
                url = self._url_in(self.pk._build_fb_message("best-snuffle-mats-dogs", "Best Mats", raw))
                self.assertEqual(url, self.FB_URL)

    # -- main(): the sheet row --------------------------------------------------
    def _run_main(self, existing_urls, posts=None, thumbs=()):
        """posts: {file name under _posts/: full text}; thumbs: slugs that have a
        self-hosted thumbnail in assets/images/thumbs/."""
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "_pin_queue").mkdir()
        for name, text in (posts or {}).items():
            (tmp / "_posts").mkdir(exist_ok=True)
            (tmp / "_posts" / name).write_bytes(text.encode("utf-8"))
        for slug in thumbs:
            (tmp / "assets" / "images" / "thumbs").mkdir(parents=True, exist_ok=True)
            (tmp / "assets" / "images" / "thumbs" / f"{slug}.jpg").write_bytes(b"jpg")
        (tmp / "_pin_queue" / "best-snuffle-mats-dogs.json").write_text(json.dumps({
            "title": "Best Snuffle Mats", "article_url": self.PIN_URL,
            "image_url": "https://happypetproductreviews.com/assets/images/pins/x.jpg?v=20261005",
            "species": "dog", "slug": "best-snuffle-mats-dogs"}), encoding="utf-8")
        ws = MagicMock()
        ws.get_all_values.return_value = [["Title", "URL"]] + [["t", u] for u in existing_urls]
        gspread = types.ModuleType("gspread")
        gspread.Client = MagicMock(return_value=MagicMock(
            open_by_key=MagicMock(return_value=MagicMock(
                get_worksheet=MagicMock(return_value=ws)))))
        with patch.dict(sys.modules, {"gspread": gspread}), \
             patch.object(self.pk, "REPO_DIR", tmp), \
             patch.object(self.pk, "log", lambda *a, **k: None), \
             patch.object(self.pk, "load_env", lambda: None), \
             patch.object(self.pk, "brain_get_secret", lambda *a, **k: "sheet-id"), \
             patch.object(self.pk, "get_sheets_creds", lambda: "creds"), \
             patch.object(self.pk, "retire_from_products", lambda slug: 9), \
             patch.object(self.pk, "count_unpublished", lambda: 9), \
             patch.object(sys, "argv", ["push_pins_to_sheets.py"]):
            self.pk.main()
        return ws

    def test_a_new_rows_url_column_carries_the_facebook_tag(self):
        ws = self._run_main(existing_urls=[])
        ws.append_row.assert_called_once()
        row = ws.append_row.call_args.args[0]
        self.assertEqual(row[1], self.FB_URL)
        self.assertNotIn("pinterest", row[1])

    def test_the_url_column_and_the_message_link_agree(self):
        row = self._run_main(existing_urls=[]).append_row.call_args.args[0]
        self.assertEqual(self._url_in(row[2]), row[1])

    CLEAN_URL = "https://happypetproductreviews.com/toys/best-snuffle-mats-dogs/"

    def test_an_already_queued_article_is_skipped_whatever_its_col_b_form(self):
        """Col B holds the clean URL on rows from Apr 4-16, the Pinterest-tagged URL
        from Apr 19, the Facebook-tagged URL from this change on. Each must still
        match, or every sent/ file posts a second time."""
        forms = {"clean (Apr 4-16)": self.CLEAN_URL,
                 "pinterest-tagged (Apr 19 on)": self.PIN_URL,
                 "facebook-tagged (new)": self.FB_URL,
                 "no trailing slash": self.CLEAN_URL.rstrip("/"),
                 "upper-case host, padded": "  https://HappyPetProductReviews.com/toys/best-snuffle-mats-dogs/  ",
                 "fragment": self.CLEAN_URL + "#top",
                 "http": self.CLEAN_URL.replace("https://", "http://"),
                 "www": self.CLEAN_URL.replace("https://", "https://www."),
                 "http + www, pinterest-tagged": self.PIN_URL.replace("https://", "http://www.")}
        for label, existing in forms.items():
            with self.subTest(label):
                self._run_main(existing_urls=[existing]).append_row.assert_not_called()

    def test_a_different_article_never_counts_as_queued(self):
        """Normalizing must not merge two articles: another slug, a slug that is a
        prefix of ours, our slug under another category, and our path on another
        host all still append."""
        for existing in ("https://example.com/toys/best-snuffle-mats-dogs/",
                         "https://wwwhappypetproductreviews.com/toys/best-snuffle-mats-dogs/",
                         "https://happypetproductreviews.com/toys/best-snuffle-mats/",
                         "https://happypetproductreviews.com/toys/best-snuffle-mats-dogs-2/",
                         "https://happypetproductreviews.com/dog-toys/best-snuffle-mats-dogs/"
                         "?utm_source=pinterest&utm_medium=social&utm_campaign=pin"):
            with self.subTest(existing=existing):
                self._run_main(existing_urls=[existing]).append_row.assert_called_once()

    # -- main(): the sheet row's Image column (col D) ----------------------------
    # The Facebook Queue row's Image column is what the external Apps Script
    # posts. It used to be the queue JSON's image_url, which is the 1000x1500
    # Pinterest pin -- Facebook crops or distorts a 2:3 image (Director,
    # 2026-10-13). Col D is now the product image: the self-hosted thumbnail when
    # the post has one (the buy panel's rule), else the post's own `image`.
    # Pinterest is untouched: the pin JSON and post_pins.py still carry the pin.

    SLUG = "best-snuffle-mats-dogs"
    PIN_IMG = "https://happypetproductreviews.com/assets/images/pins/best-snuffle-mats-dogs.jpg"
    AMAZON = "https://m.media-amazon.com/images/I/719auFEdkVL._AC_SX679_.jpg"
    THUMB = "https://happypetproductreviews.com/assets/images/thumbs/best-snuffle-mats-dogs.jpg"
    FALLBACK = "https://happypetproductreviews.com/assets/images/og-image.png"

    def _post(self, image_line='image: "%s"' % AMAZON):
        return ('---\nlayout: post\ntitle: "Best Snuffle Mats"\n' + image_line + '\n'
                'pin_image: "' + self.PIN_IMG + '?v=20261005"\n---\n\nBody text.\n')

    def _col_d(self, **kw):
        ws = self._run_main(existing_urls=[], **kw)
        ws.append_row.assert_called_once()
        return ws.append_row.call_args.args[0][3]

    def test_image_column_is_the_posts_product_image_not_the_pin(self):
        img = self._col_d(posts={"2026-10-05-" + self.SLUG + ".md": self._post()})
        self.assertEqual(img, self.AMAZON)

    def test_a_self_hosted_thumbnail_wins_over_the_amazon_url(self):
        img = self._col_d(posts={"2026-10-05-" + self.SLUG + ".md": self._post()},
                          thumbs=[self.SLUG])
        self.assertEqual(img, self.THUMB)

    def test_a_thumbnail_alone_is_enough(self):
        self.assertEqual(self._col_d(thumbs=[self.SLUG]), self.THUMB)

    def test_front_matter_spellings_of_image_all_resolve(self):
        for line in ("image: '%s'" % self.AMAZON, "image: %s" % self.AMAZON,
                     'image:   "%s"   ' % self.AMAZON, 'image:\t"%s"' % self.AMAZON):
            for eol in ("\n", "\r\n"):
                with self.subTest(line=line, eol=eol):
                    text = self._post(line).replace("\n", eol)
                    self.assertEqual(
                        self._col_d(posts={"2026-10-05-" + self.SLUG + ".md": text}), self.AMAZON)

    def test_pin_image_is_never_mistaken_for_image(self):
        """A post with pin_image but no image key must not leak the pin."""
        text = '---\nlayout: post\ntitle: "x"\npin_image: "' + self.PIN_IMG + '"\n---\n'
        img = self._col_d(posts={"2026-10-05-" + self.SLUG + ".md": text})
        self.assertEqual(img, self.FALLBACK)

    def test_a_post_whose_slug_merely_ends_with_ours_is_not_matched(self):
        # The decoy sorts BEFORE the real post, so a suffix match would pick it.
        other = self._post('image: "https://m.media-amazon.com/images/I/OTHER.jpg"')
        img = self._col_d(posts={"2026-10-05-aaa-" + self.SLUG + ".md": other,
                                 "2026-10-05-" + self.SLUG + ".md": self._post()})
        self.assertEqual(img, self.AMAZON)

    def test_nothing_resolvable_falls_back_to_the_landscape_site_image_never_the_pin(self):
        self.assertEqual(self._col_d(), self.FALLBACK)

    def test_the_pin_never_reaches_col_d_whatever_the_inputs(self):
        for posts, thumbs in (({}, ()), ({"2026-10-05-" + self.SLUG + ".md": self._post()}, ()),
                              ({}, [self.SLUG])):
            with self.subTest(posts=bool(posts), thumbs=bool(thumbs)):
                self.assertNotIn("/pins/", self._col_d(posts=posts, thumbs=thumbs))

    def test_orig_date_is_the_eastern_date(self):
        """OrigDate (col E) is stamped with eastern_date(), like pulled_on."""
        import refill_products
        with patch.object(refill_products, "eastern_date", lambda: "2025-01-01"):
            row = self._run_main(existing_urls=[]).append_row.call_args.args[0]
        self.assertEqual(row[4], "2025-01-01")

    def test_the_rest_of_the_row_is_unchanged(self):
        ws = self._run_main(existing_urls=[],
                            posts={"2026-10-05-" + self.SLUG + ".md": self._post()})
        row = ws.append_row.call_args.args[0]
        self.assertEqual(len(row), 9)
        self.assertEqual((row[0], row[1], row[6], row[7], row[8]),
                         ("Best Snuffle Mats", self.FB_URL, "dog", "FALSE", ""))

    def test_the_pinterest_side_still_carries_the_pin_image(self):
        """post_pins.py reads image_url from the queue JSON; this change must not
        have touched that path."""
        src = (REPO / "post_pins.py").read_text(encoding="utf-8")
        self.assertIn('data.get("image_url", "")', src)


class TestFacebookRepost(unittest.TestCase):
    """--repost SLUG DATE queues an already-live review for Facebook again: a
    row of the same shape a new post gets, with SchedDate set to DATE. The sheet
    is a fake; nothing here reaches Google. Expected values are literals."""

    SLUG = "best-kitten-food"
    LIVE = "https://happypetproductreviews.com/food/best-kitten-food/"
    FB_URL = LIVE + "?utm_source=facebook&utm_medium=social&utm_campaign=post"
    # The original row was written when the post lived under /cat-food/.
    OLD_PIN = ("https://happypetproductreviews.com/cat-food/best-kitten-food/"
               "?utm_source=pinterest&utm_medium=social&utm_campaign=pin")
    IMAGE = "https://images-na.ssl-images-amazon.com/images/P/B0000AH3RP.01.LZZZZZZZ.jpg"
    POST = ('---\nlayout: post\ntitle: "Top-Rated Kitten Food: The 4 Best Picks for Growth"\n'
            'date: 2026-04-27\ncategories: [food]\nredirect_from:\n  - /cat-food/best-kitten-food/\n'
            'species: cat\nimage: "' + IMAGE + '"\n---\n\nBody.\n')
    TODAY = "2026-10-20"

    @staticmethod
    def _row(url, sched, title="t"):
        return [title, url, "m", "i", "2026-04-27", sched, "cat", "TRUE", "123"]

    def _run(self, argv, rows=(), posts=None, today=TODAY):
        """Run main() against a fake sheet. Returns (ws, exit code, mocks, logs)."""
        import shutil
        import refill_products
        import push_pins_to_sheets as pk
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "_posts").mkdir()
        (tmp / "_pin_queue").mkdir()
        (tmp / "_pin_queue" / "queued-article.json").write_text("{}", encoding="utf-8")
        for name, text in ({"2026-04-27-" + self.SLUG + ".md": self.POST} if posts is None
                           else posts).items():
            (tmp / "_posts" / name).write_text(text, encoding="utf-8")
        ws = MagicMock()
        ws.get_all_values.return_value = [["Title", "URL"]] + [list(r) for r in rows]
        gspread = types.ModuleType("gspread")
        gspread.Client = MagicMock(return_value=MagicMock(
            open_by_key=MagicMock(return_value=MagicMock(
                get_worksheet=MagicMock(return_value=ws)))))
        retire, unpub, logs = MagicMock(return_value=9), MagicMock(return_value=9), []
        code = 0
        with patch.dict(sys.modules, {"gspread": gspread}), \
             patch.object(pk, "REPO_DIR", tmp), \
             patch.object(pk, "log", lambda msg, level="INFO": logs.append((level, msg))), \
             patch.object(pk, "load_env", lambda: None), \
             patch.object(pk, "brain_get_secret", lambda *a, **k: "sheet-id"), \
             patch.object(pk, "get_sheets_creds", lambda: "creds"), \
             patch.object(pk, "retire_from_products", retire), \
             patch.object(pk, "count_unpublished", unpub), \
             patch.object(refill_products, "eastern_date", lambda: today), \
             patch.object(sys, "argv", ["push_pins_to_sheets.py"] + argv):
            try:
                pk.main()
            except SystemExit as exc:
                code = exc.code
        self.assertTrue((tmp / "_pin_queue" / "queued-article.json").is_file(),
                        "repost mode must not touch the pin queue")
        retire.assert_not_called()
        unpub.assert_not_called()
        return ws, code, logs

    def _appended(self, ws):
        return [c.args[0] for c in ws.append_row.call_args_list]

    # -- the row ---------------------------------------------------------------
    def test_the_row_is_a_normal_new_post_row_with_the_given_sched_date(self):
        ws, code, _ = self._run(["--repost", self.SLUG, "2026-10-22"],
                                rows=[self._row(self.OLD_PIN, "2026-04-28")])
        self.assertEqual(code, 0)
        self.assertEqual(self._appended(ws), [[
            "Top-Rated Kitten Food: The 4 Best Picks for Growth",
            self.FB_URL,
            "\U0001f43e Give your kitten the best start -- top-rated food for growth, "
            "immunity, and a shiny coat.\n" + self.FB_URL,
            self.IMAGE, "2026-10-20", "2026-10-22", "cat", "FALSE", ""]])

    def test_the_link_is_the_live_permalink_tagged_once_for_facebook(self):
        ws, _, _ = self._run(["--repost", self.SLUG, "2026-10-22"],
                             rows=[self._row(self.OLD_PIN, "2026-04-28")])
        row = self._appended(ws)[0]
        for text in (row[1], row[2]):
            self.assertEqual(text.count("utm_source="), 1)
            self.assertEqual(text.count("?"), 1)
            self.assertNotIn("pinterest", text)
            self.assertNotIn("/cat-food/", text)

    def test_several_reposts_append_in_order(self):
        litter = ('---\ntitle: "Best Cat Litter for Odor Control: 3 Picks Compared"\n'
                  'categories: [litter]\nspecies: cat\n---\n')
        ws, code, _ = self._run(
            ["--repost", self.SLUG, "2026-10-22",
             "--repost", "best-cat-litter-odor-control", "2026-10-24"],
            posts={"2026-04-27-" + self.SLUG + ".md": self.POST,
                   "2026-04-04-best-cat-litter-odor-control.md": litter})
        self.assertEqual(code, 0)
        rows = self._appended(ws)
        self.assertEqual([(r[1], r[5]) for r in rows], [
            (self.FB_URL, "2026-10-22"),
            ("https://happypetproductreviews.com/litter/best-cat-litter-odor-control/"
             "?utm_source=facebook&utm_medium=social&utm_campaign=post", "2026-10-24")])

    def test_dry_run_reads_and_validates_but_writes_nothing(self):
        ws, code, logs = self._run(["--repost", self.SLUG, "2026-10-22", "--dry-run"])
        self.assertEqual(code, 0)
        ws.get_all_values.assert_called_once()
        ws.append_row.assert_not_called()
        self.assertTrue(any("2026-10-22" in m and "DRY RUN" in m for _, m in logs))

    def test_a_bad_request_anywhere_writes_nothing(self):
        ws, code, _ = self._run(["--repost", self.SLUG, "2026-10-22",
                                 "--repost", "no-such-post", "2026-10-29"])
        self.assertEqual(code, 1)
        ws.append_row.assert_not_called()

    # -- duplicate and gap guards ------------------------------------------------
    def test_the_same_slug_and_date_cannot_be_queued_twice(self):
        for label, existing in (("tagged", self.FB_URL), ("old path, pin tag", self.OLD_PIN),
                                ("clean", self.LIVE)):
            with self.subTest(label):
                ws, code, _ = self._run(["--repost", self.SLUG, "2026-10-22"],
                                        rows=[self._row(existing, "2026-10-22")])
                self.assertEqual(code, 1)
                ws.append_row.assert_not_called()

    def test_the_same_slug_and_date_twice_in_one_run_is_refused(self):
        ws, code, _ = self._run(["--repost", self.SLUG, "2026-10-22",
                                 "--repost", self.SLUG, "2026-10-22"])
        self.assertEqual(code, 1)
        ws.append_row.assert_not_called()

    def test_min_gap_per_slug_against_the_sheet_both_directions(self):
        # Existing row on 10-29: 7 days either side is allowed, 6 is not.
        for target, ok in (("2026-11-04", False), ("2026-11-05", True),
                           ("2026-10-22", True), ("2026-10-23", False)):
            with self.subTest(target=target):
                ws, code, _ = self._run(["--repost", self.SLUG, target],
                                        rows=[self._row(self.FB_URL, "2026-10-29")])
                self.assertEqual((code, ws.append_row.call_count), (0, 1) if ok else (1, 0))

    def test_min_gap_applies_within_one_run(self):
        for second, ok in (("2026-10-28", False), ("2026-10-29", True)):
            with self.subTest(second=second):
                ws, code, _ = self._run(["--repost", self.SLUG, "2026-10-22",
                                         "--repost", self.SLUG, second])
                self.assertEqual((code, ws.append_row.call_count), (0, 2) if ok else (1, 0))

    def test_a_recategorized_original_row_still_counts_for_the_gap(self):
        ws, code, _ = self._run(["--repost", self.SLUG, "2026-10-24"],
                                rows=[self._row(self.OLD_PIN, "2026-10-21")])
        self.assertEqual(code, 1)
        ws.append_row.assert_not_called()

    def test_other_articles_rows_never_block_the_slug(self):
        for other in ("https://happypetproductreviews.com/food/best-kitten-food-wet/",
                      "https://happypetproductreviews.com/food/x-best-kitten-food/",
                      "https://happypetproductreviews.com/food/best-kitten/"):
            with self.subTest(other=other):
                ws, code, logs = self._run(["--repost", self.SLUG, "2026-10-22"],
                                           rows=[self._row(other, "2026-10-22")])
                self.assertEqual((code, ws.append_row.call_count), (0, 1))
                # Another post already sits on that day: allowed, but said.
                self.assertTrue(any(lv == "WARN" and "2026-10-22" in m for lv, m in logs))

    def test_the_date_must_be_after_today(self):
        for target, ok in (("2026-10-19", False), ("2026-10-20", False), ("2026-10-21", True)):
            with self.subTest(target=target):
                ws, code, _ = self._run(["--repost", self.SLUG, target])
                self.assertEqual((code, ws.append_row.call_count), (0, 1) if ok else (1, 0))

    def test_a_malformed_date_is_refused(self):
        for target in ("10-22", "2026-13-01", "2026/10/22", "tomorrow", ""):
            with self.subTest(target=target):
                ws, code, _ = self._run(["--repost", self.SLUG, target])
                self.assertEqual(code, 1)
                ws.append_row.assert_not_called()

    # -- which slugs are reposted ------------------------------------------------
    def test_only_a_published_post_can_be_reposted(self):
        cases = {"no post": ("no-such-post", None),
                 "draft only": (self.SLUG, {"DRAFT-" + self.SLUG + ".md": self.POST}),
                 "suffix of another post": ("kitten-food", None),
                 "glob characters": ("best-*", None),
                 "path characters": ("../best-kitten-food", None),
                 "no category": (self.SLUG, {"2026-04-27-" + self.SLUG + ".md":
                                             self.POST.replace("categories: [food]\n", "")})}
        for label, (slug, posts) in cases.items():
            with self.subTest(label):
                ws, code, _ = self._run(["--repost", slug, "2026-10-22"], posts=posts)
                self.assertEqual(code, 1)
                ws.append_row.assert_not_called()

    def test_repost_and_slugs_are_mutually_exclusive(self):
        with open(os.devnull, "w") as dn, contextlib.redirect_stderr(dn):
            ws, code, _ = self._run(["--repost", self.SLUG, "2026-10-22", "--slugs", self.SLUG])
        self.assertEqual(code, 2)
        ws.append_row.assert_not_called()

    def test_dry_run_alone_is_refused(self):
        with open(os.devnull, "w") as dn, contextlib.redirect_stderr(dn):
            ws, code, _ = self._run(["--dry-run"])
        self.assertEqual(code, 2)
        ws.get_all_values.assert_not_called()


class TestRepostArgs(unittest.TestCase):
    """repost_args.py checks the fb-repost workflow's list before the step that
    holds the Sheet secrets runs. Nothing here reaches Google."""

    @staticmethod
    def _parse(text):
        import repost_args
        return repost_args.parse_repost_list(text)

    def test_the_directors_example_list_parses(self):
        self.assertEqual(
            self._parse("best-calming-diffusers-pets 2026-10-22, best-kitten-food 2026-10-24, "
                        "best-cat-litter-odor-control 2026-10-27"),
            [("best-calming-diffusers-pets", "2026-10-22"),
             ("best-kitten-food", "2026-10-24"),
             ("best-cat-litter-odor-control", "2026-10-27")])

    def test_spacing_around_the_pairs_does_not_matter(self):
        self.assertEqual(self._parse("  best-kitten-food\t2026-10-24 ,best-cat-bed   2026-10-27  "),
                         [("best-kitten-food", "2026-10-24"), ("best-cat-bed", "2026-10-27")])

    def test_a_bad_entry_is_refused(self):
        bad = {
            "empty": "",
            "blank": "   ",
            "trailing comma": "best-kitten-food 2026-10-24,",
            "no date": "best-kitten-food",
            "no slug": "2026-10-24",
            "third word": "best-kitten-food 2026-10-24 now",
            "newline instead of comma": "best-kitten-food 2026-10-24\nbest-cat-bed 2026-10-27",
            "upper case slug": "Best-Kitten-Food 2026-10-24",
            "underscore slug": "best_kitten_food 2026-10-24",
            "path slug": "../best-kitten-food 2026-10-24",
            "glob slug": "best-* 2026-10-24",
            "trailing hyphen": "best-kitten- 2026-10-24",
            "option as slug": "--dry-run 2026-10-24",
            "short option as slug": "-f 2026-10-24",
            "command substitution": "$(id) 2026-10-24",
            "backtick": "`id` 2026-10-24",
            "semicolon": "best-kitten-food; 2026-10-24",
            "month 13": "best-kitten-food 2026-13-01",
            "30 February": "best-kitten-food 2026-02-30",
            "single digit month": "best-kitten-food 2026-1-24",
            "compact date": "best-kitten-food 20261024",
            "ISO week date": "best-kitten-food 2026-W43-3",
            "full-width digits": "best-kitten-food ２０２６-10-24",
            "date with time": "best-kitten-food 2026-10-24T09:00",
            "one good one bad": "best-kitten-food 2026-10-24, nope",
        }
        for label, text in bad.items():
            with self.subTest(label), self.assertRaises(ValueError):
                self._parse(text)

    def test_leap_day_is_a_real_date_only_in_a_leap_year(self):
        self.assertEqual(self._parse("best-kitten-food 2028-02-29"),
                         [("best-kitten-food", "2028-02-29")])
        with self.assertRaises(ValueError):
            self._parse("best-kitten-food 2027-02-29")

    def test_the_script_run_prints_pairs_and_exits_zero(self):
        out = self._run_script("best-kitten-food 2026-10-24, best-cat-bed 2026-10-27")
        self.assertEqual((out.returncode, out.stdout),
                         (0, "best-kitten-food 2026-10-24\nbest-cat-bed 2026-10-27\n"))

    def test_a_bad_list_prints_nothing_to_stdout_and_exits_one(self):
        out = self._run_script("best-kitten-food 2026-13-01")
        self.assertEqual((out.returncode, out.stdout), (1, ""))
        self.assertEqual(out.stderr.count("::error::"), 1)

    def test_a_hostile_entry_cannot_inject_a_workflow_command(self):
        """The error line quotes the bad entry. A newline in it must not start a
        second line of its own, where the runner would read it as a command."""
        out = self._run_script("best-kitten-food 2026-10-24\n::add-mask::secret")
        self.assertEqual(out.returncode, 1)
        lines = out.stderr.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("::error::"))

    def test_an_unset_list_is_refused(self):
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "REPOST_LIST"}
        out = subprocess.run([sys.executable, str(REPO / "repost_args.py")], env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 1)

    @staticmethod
    def _run_script(text):
        import subprocess
        return subprocess.run([sys.executable, str(REPO / "repost_args.py")],
                              env={**os.environ, "REPOST_LIST": text},
                              capture_output=True, text=True, timeout=60)

    def test_the_slug_rule_is_the_repost_scripts_own(self):
        import push_pins_to_sheets as pk
        import repost_args
        self.assertIs(repost_args._SLUG_RE, pk._SLUG_RE)


class TestRepostWorkflowWiring(unittest.TestCase):
    """Static checks of fb-repost.yml, in the style of the other workflow tests.
    The facts it must share with pin.yml are read out of pin.yml, not retyped."""

    @staticmethod
    def _read(name):
        return (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")

    @classmethod
    def _steps(cls, name):
        """{step name: block text} for the 6-space-indented `- name:` steps."""
        blocks = cls._read(name).split("\n      - name: ")[1:]
        return {b.splitlines()[0].strip(): b for b in blocks}

    @classmethod
    def _run_blocks(cls, name):
        """The text of every `run:` value, one-line or `|` block."""
        lines, out, i = cls._read(name).splitlines(), [], 0
        while i < len(lines):
            m = re.match(r"^(\s*)(?:- )?run:(.*)$", lines[i])
            if not m:
                i += 1
                continue
            indent, body = len(m.group(1)), [m.group(2)]
            i += 1
            while i < len(lines) and (not lines[i].strip()
                                      or len(lines[i]) - len(lines[i].lstrip()) > indent):
                body.append(lines[i])
                i += 1
            out.append("\n".join(body))
        return out

    def test_the_only_trigger_is_a_manual_dispatch(self):
        text = self._read("fb-repost.yml")
        on_block = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("  workflow_dispatch:", on_block)
        for other in ("push:", "pull_request", "schedule:", "workflow_run:", "workflow_call:"):
            self.assertNotIn(other, on_block)

    def test_dry_run_is_a_boolean_that_defaults_to_true(self):
        text = self._read("fb-repost.yml")
        dry = text.split("\n      dry_run:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("\n        type: boolean\n", dry)
        self.assertIn("\n        default: true\n", dry)

    def test_permissions_are_contents_read_and_nothing_else(self):
        text = self._read("fb-repost.yml")
        self.assertEqual(text.count("permissions:"), 1)
        self.assertIn("\npermissions:\n  contents: read\n\n", text)

    def test_no_expression_reaches_a_run_block(self):
        """Script injection: an input expanded inside run: becomes shell text. Env
        variables carry the values instead, so no run: block may hold `${{`."""
        blocks = self._run_blocks("fb-repost.yml")
        self.assertEqual(len(blocks), 3)   # the helper itself must see every run:
        for block in blocks:
            self.assertNotIn("${{", block)

    def test_inputs_are_read_only_as_env_assignments(self):
        for line in self._read("fb-repost.yml").splitlines():
            if "inputs." in line and "${{" in line:
                self.assertRegex(line, r"^          [A-Z_]+:\s+\$\{\{ inputs\.[a-z_]+ \}\}$")

    def test_secrets_match_pin_yml_s_push_step_and_only_the_last_step_has_them(self):
        pin = self._steps("pin.yml")["Push pins to Google Sheets"]
        expected = set(re.findall(r"\$\{\{ secrets\.(\w+) \}\}", pin))
        self.assertEqual(expected, {"FACEBOOK_QUEUE_SHEET_ID", "GCP_SA_KEY_B64"})
        steps = self._steps("fb-repost.yml")
        for name, block in steps.items():
            found = set(re.findall(r"\$\{\{ secrets\.(\w+) \}\}", block))
            self.assertEqual(found, expected if name == "Queue the reposts" else set(), name)

    def test_python_setup_matches_pin_yml(self):
        pin, repost = self._steps("pin.yml"), self._steps("fb-repost.yml")
        for name in ("Set up Python", "Install Python dependencies"):
            self.assertEqual(repost[name].rstrip(), pin[name].rstrip(), name)

    def test_the_list_is_checked_before_the_step_with_the_secrets(self):
        names = list(self._steps("fb-repost.yml"))
        self.assertLess(names.index("Check the repost list"), names.index("Queue the reposts"))
        check = self._steps("fb-repost.yml")["Check the repost list"]
        self.assertIn("python3 repost_args.py", check)
        self.assertIn("REPOST_LIST: ${{ inputs.repost_list }}", check)

    def test_only_the_exact_string_false_writes_to_the_sheet(self):
        run = self._steps("fb-repost.yml")["Queue the reposts"]
        self.assertIn('if [ "$DRY_RUN" = "false" ]; then', run)
        self.assertIn("ARGS+=(--dry-run)", run.split("else", 1)[1])
        self.assertIn('python3 push_pins_to_sheets.py "${ARGS[@]}"', run)

    def test_the_workflow_cannot_force_a_repeat(self):
        self.assertNotIn("--force", self._read("fb-repost.yml"))


# ---------------------------------------------------------------------------
# Category -> homepage topic-button mapping guard (recovery #45)
#
# The card template buckets a post by matching keywords IN ITS CATEGORY SLUG.
# A category containing none of these keywords (the old "dog-gear"/"cat-gear"
# catch-all) is invisible under every topic filter -- the drift this guard
# exists to prevent from regressing. Keep PILL_KEYWORDS in sync with the
# `{% if category contains ... %}` chain in _includes/post-card.html.
#
# That chain lived in _layouts/home.html until the round 2 design rebuild moved
# it into the card include; the "what for" chooser in home.html now filters on
# the data-use attribute the include emits.
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
# best-dog-pools was assigned to the Toys pill (dog-toys) on 2026-08-17, so the
# queue is empty; keep the mechanism for any future parked topic.
KNOWN_UNMAPPED_QUEUE = set()


def _category_maps_to_pill(category: str) -> bool:
    import categories
    cat = (category or "").lower()
    if cat in categories.USES:
        return True   # a topic carries its own bucket (post-card.html looks it up)
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

    def test_pill_keywords_stay_in_sync_with_the_card_template(self):
        # If the keyword chain changes, this flags PILL_KEYWORDS as stale so
        # the two never silently diverge.
        #
        # The chain moved out of _layouts/home.html in the round 2 design
        # rebuild ("replace the skeleton, not the paint"), which extracted the
        # card into _includes/post-card.html and took the bucketing with it.
        # The keywords are unchanged; only the file holding them moved. The
        # guard is pointed at the new home rather than relaxed -- reading the
        # old path made it fail against a template that was in fact correct.
        card = (REPO / "_includes" / "post-card.html").read_text(encoding="utf-8")
        for kw in PILL_KEYWORDS:
            self.assertIn(
                f'contains "{kw}"', card,
                f'"{kw}" in PILL_KEYWORDS but not referenced in post-card.html')

    def test_every_topic_maps_to_a_real_filter_button(self):
        # The single source (_data/categories.json) gives every topic a "use"
        # bucket; post-card.html emits it as data-use. A bucket the homepage has
        # no button for is a topic no filter can reach.
        import categories
        home = (REPO / "_layouts" / "home.html").read_text(encoding="utf-8")
        buttons = set(re.findall(r'data-axis="use" data-value="([a-z]+)"', home)) - {"all"}
        self.assertEqual(len(buttons), 7, "home.html filter markup changed shape")
        bad = {t: u for t, u in categories.USES.items() if u not in buttons}
        self.assertEqual(bad, {}, "topics whose bucket has no homepage button")

    def test_templates_read_topics_from_the_single_source(self):
        # One include looks the category up in site.data.categories (falling
        # back to hyphen-stripping for old names); both templates use it and
        # neither carries its own copy of the lookup.
        inc = (REPO / "_includes" / "category-label.html").read_text(encoding="utf-8")
        self.assertRegex(
            inc, r"site\.data\.categories\.topics \| where: ['\"]slug['\"], include\.category")
        for rel in ("_includes/post-card.html", "_layouts/post.html"):
            text = (REPO / rel).read_text(encoding="utf-8")
            self.assertIn("include category-label.html category=category", text, rel)
            self.assertNotIn("site.data.categories", text, rel)
            self.assertNotIn("assign category_label", text, rel)
        self.assertIn("topic.use", (REPO / "_includes" / "post-card.html").read_text(encoding="utf-8"))

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


class TestTopicCategoriesSingleSource(unittest.TestCase):
    """Category restructure, phase 1 (2026-10-05). _data/categories.json is the
    one definition of the species-neutral topics; every consumer derives from
    it, and every old species-prefixed name keeps behaving exactly as before."""

    def test_the_json_is_the_topic_list(self):
        import categories
        data = json.loads((REPO / "_data" / "categories.json").read_text(encoding="utf-8"))
        self.assertEqual(categories.TOPICS, tuple(t["slug"] for t in data["topics"]))
        self.assertEqual(len(set(categories.TOPICS)), 22)

    def test_pin_labels_cover_every_topic_and_keep_the_old_ones(self):
        import generate_pin_images as g
        self.assertEqual(g.CAT_LABELS["flea-tick"], "Flea & tick")
        self.assertEqual(g.CTA_LABELS["toys"], "See Our Picks")
        # The ten old labels are unchanged, so existing pins re-render the same.
        self.assertEqual(g.CAT_LABELS["dog-beds"], "Dog Beds")
        self.assertEqual(g.CTA_LABELS["cat-carriers"], "See Our Pick")
        import categories
        self.assertEqual(set(categories.TOPICS) - set(g.CAT_LABELS), set())

    def test_chewy_classification_keeps_old_names(self):
        # is_consumable() has no callers; its sets stay exactly as on main.
        import chewy_lookup as cl
        for name in ("dog-food", "dog-health", "cat-health", "cat-litter"):
            self.assertTrue(cl.is_consumable(name), name)
        for name in ("pet-health", "pet-grooming", "dog-toys", "pet-tech"):
            self.assertFalse(cl.is_consumable(name), name)
        self.assertEqual(cl.CONSUMABLE_CATEGORIES & cl.HARD_GOOD_CATEGORIES, set())


class TestRelatedLinkScoringOnTopics(unittest.TestCase):
    """find_related_published_slug's prefix tier meant 'same species' for the
    old dog-/cat-/pet- names. Bare topics have no species prefix, so for them
    only an exact match may rank above 'any published post'."""

    def _pick(self, current, cats):
        # Candidates come back in dict order, first one first, so a tie at the
        # same score always resolves to the first slug listed.
        # The category comes from each post's own front matter.
        import generate_posts as gp
        posts = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for slug, cat in cats.items():
                f = Path(tmp) / f"2026-01-01-{slug}.md"
                f.write_text(f"---\ncategories: [{cat}]\n---\nbody\n", encoding="utf-8")
                files.append(f)
            posts.glob.return_value = files
            with patch.object(gp, "POSTS_DIR", posts), \
                 patch.dict(gp.SLUG_CATEGORIES, {}, clear=True), \
                 patch.object(gp, "build_url", side_effect=lambda s: s):
                return gp.find_related_published_slug("best-current", current)[0]

    def test_old_names_still_rank_same_species_above_unrelated(self):
        cats = {"best-a": "cat-toys", "best-b": "dog-health"}
        self.assertEqual(self._pick("dog-beds", cats), "best-b")

    def test_exact_topic_match_wins(self):
        cats = {"best-a": "toys", "best-b": "beds"}
        self.assertEqual(self._pick("beds", cats), "best-b")

    def test_bare_topics_sharing_a_first_word_are_not_related(self):
        # "flea-tick".split("-")[0] == "flea": the old prefix rule ranked any
        # "flea-*" name as related (score 2). Only dog-/cat-/pet- form that
        # tier, so both candidates tie at 1 and the first listed wins.
        cats = {"best-b": "toys", "best-a": "flea-collars-x"}
        self.assertEqual(self._pick("flea-tick", cats), "best-b")


class TestPostsAreOnTopics(unittest.TestCase):
    """Category restructure, phase 2 (2026-10-05): every published post moved to
    one topic, its old URL kept as a redirect_from stub."""

    def test_every_published_post_has_exactly_one_known_category(self):
        # A typo is what this catches -- it would silently fall through
        # post-card's keyword chain.
        import categories
        bad = []
        for md in sorted((REPO / "_posts").glob("*.md")):
            if md.stem.startswith("DRAFT-"):
                continue
            m = re.search(r"^categories: \[([^\]]*)\]", md.read_text(encoding="utf-8"), re.M)
            if not m or m.group(1) not in categories.TOPICS:
                bad.append(f"{md.name}: {m.group(1) if m else None}")
        self.assertEqual(bad, [])

    def test_related_link_candidates_use_their_published_category(self):
        # Candidates outside SLUG_CATEGORIES used to score as category-less,
        # so a new topic post tied with every post and took the first one --
        # a dog travel review linked the automatic cat feeder.
        import generate_posts as gp
        with patch.dict(gp.SLUG_CATEGORIES, {}, clear=True):
            url, _ = gp.find_related_published_slug("best-new-thing", "tech")
        self.assertIn(url, (f"{gp.SITE_BASE}/tech/best-pet-cameras/",
                            f"{gp.SITE_BASE}/tech/best-gps-dog-trackers/"))

    def test_no_post_body_links_a_published_post_off_its_live_url(self):
        # A body link to /old-category/slug/ still resolves through the stub,
        # but it is a redirect hop on every click and hides the next move.
        live = {}
        for md in (REPO / "_posts").glob("*.md"):
            parts = md.stem.split("-", 3)
            if not md.stem.startswith("DRAFT-") and len(parts) == 4:
                live[parts[3]] = _post_first_category(md)
        stale = []
        for md in (REPO / "_posts").glob("*.md"):
            body = md.read_text(encoding="utf-8").split("---", 2)[2]
            for seg, slug in re.findall(
                    r"(?:happypetproductreviews\.com|\]\()/([a-z0-9-]+)/(best-[a-z0-9-]+)/", body):
                if slug in live and seg != live[slug]:
                    stale.append(f"{md.name}: /{seg}/{slug}/")
        self.assertEqual(stale, [])

    def test_build_url_reads_a_published_posts_category(self):
        # SLUG_CATEGORIES only knows ten hardcoded slugs plus the queued
        # products, so a related link to any other post used to get a made-up
        # /pet-accessories/ path that 404'd.
        # Front matter wins over a stale SLUG_CATEGORIES entry: the post's own
        # category is what Jekyll builds its URL from.
        import generate_posts as gp
        with patch.dict(gp.SLUG_CATEGORIES, {"best-pet-cameras": "pet-tech"}, clear=True):
            self.assertEqual(gp.build_url("best-dog-ramps"),
                             f"{gp.SITE_BASE}/health/best-dog-ramps/")
            self.assertEqual(gp.build_url("best-pet-cameras"),
                             f"{gp.SITE_BASE}/tech/best-pet-cameras/")

    def test_redirect_stubs_carry_the_query_string(self):
        # Every sent pin's article_url carries ?utm_*; 41 of them now land on
        # a redirect_from stub. The plugin's built-in stub drops the query.
        text = (REPO / "_layouts" / "redirect.html").read_text(encoding="utf-8")
        self.assertRegex(text, r"location\.replace\(\{\{ page\.redirect\.to \| jsonify \}\}"
                               r" \+ location\.search \+ location\.hash\)")
        for kept in ('http-equiv="refresh"', 'rel="canonical"'):
            self.assertIn(kept, text)

    def test_post_category_strips_quotes(self):
        import generate_posts as gp
        with tempfile.TemporaryDirectory() as tmp:
            for raw in ('["toys"]', "['toys']", "[ toys, x ]"):
                f = Path(tmp) / "2026-01-01-best-x.md"
                f.write_text(f"---\ncategories: {raw}\n---\n", encoding="utf-8")
                self.assertEqual(gp._post_category(f), "toys", raw)

    # Species drives Pinterest board routing (post_pins.resolve_events). The
    # restructure must not move a single post between species boards. Taken
    # from main at e16aeb6; a post published later is simply not listed.
    SPECIES_ON_MAIN = {
        "both": "calming-diffusers-pets odor-eliminators-pet-stains pet-cameras "
                "pet-hair-removers-laundry-furniture pet-water-fountain portable-pet-playpens",
        "cat": "automatic-cat-feeder automatic-litter-box cat-beds cat-calming-products "
               "cat-carrier-backpacks cat-carrier-travel cat-dental-treats cat-grass-growing-kits "
               "cat-harness-leash cat-litter-boxes cat-litter-odor-control cat-puzzle-feeders "
               "cat-scratching-posts cat-tree-large cat-tunnel-toys cat-window-hammocks "
               "cat-window-perch catnip-toys grain-free-cat-food interactive-cat-toys "
               "kitten-food wet-cat-food",
        "dog": "calming-treats-dogs dog-anxiety-vest dog-backpack-carrier dog-beds-large-breeds "
               "dog-boots-hot-pavement dog-car-seat-covers dog-collars-small-breeds "
               "dog-cooling-mat dog-crates dog-dental-chews dog-dna-tests dog-grooming "
               "dog-joint-supplements dog-life-jacket dog-nail-grinder dog-pools "
               "dog-probiotic-supplements dog-puzzle-toys dog-ramps dog-sun-protection "
               "dog-toys-aggressive-chewers dog-training-treats dog-travel-water-bottles "
               "elevated-dog-beds flea-prevention-dogs gps-dog-trackers interactive-dog-toys "
               "no-pull-dog-harness outdoor-dog-tie-outs puppy-food puppy-training-pads "
               "senior-dog-food slow-feeder-dog-bowls snuffle-mats-dogs",
    }

    def test_no_post_changed_species(self):
        expected = {f"best-{n}": sp for sp, names in self.SPECIES_ON_MAIN.items()
                    for n in names.split()}
        self.assertEqual(len(expected), 62)
        changed = []
        for slug, sp in expected.items():
            md = _post_file_for_slug(slug)
            if md is None:
                continue
            m = re.search(r"^species:\s*(\S+)", md.read_text(encoding="utf-8"), re.M)
            if not m or m.group(1) != sp:
                changed.append(f"{slug}: {sp} -> {m.group(1) if m else None}")
        self.assertEqual(changed, [])


class TestAttributionSurvivesRedirectsAndNewPinUrls(unittest.TestCase):
    """Pin and Facebook clicks arrive with ?utm_* on the URL. GA4 loses the
    channel unless (1) every redirect_from stub forwards the query string and
    fragment, and (2) every URL the pipeline stages for a NEW pin or Facebook
    post is already the post's live permalink, so the click skips the stub.

    Found 2026-10-13: ~133 fired pins link to pre-restructure paths such as
    /cat-food/best-grain-free-cat-food/?utm_source=pinterest. The layout fix
    (db78b76) was already on main; these tests measure it instead of reading it.
    """

    TARGET = "https://happypetproductreviews.com/food/best-grain-free-cat-food/"
    PIN_QUERY = "?utm_source=pinterest&utm_medium=social&utm_campaign=pin"

    # slug -> (category the pin was fired with, category the post has now)
    RECATEGORIZED = {
        "best-grain-free-cat-food":     ("cat-food", "food"),
        "best-dog-training-treats":     ("dog-food", "treats"),
        "best-dog-joint-supplements":   ("dog-health", "supplements"),
        "best-cat-litter-odor-control": ("cat-litter", "litter"),
    }

    @staticmethod
    def _render_layout(target):
        """Fill _layouts/redirect.html as Jekyll would for page.redirect.to ==
        target. Only the forms the layout uses are understood; any other Liquid
        left in the output fails the caller's assertion, so a new tag cannot
        pass unrendered."""
        text = (REPO / "_layouts" / "redirect.html").read_text(encoding="utf-8")
        text = re.sub(r"\{%-?\s*comment\s*-?%\}.*?\{%-?\s*endcomment\s*-?%\}\s*", "",
                      text, flags=re.S)
        text = text.replace("{{ page.redirect.to | jsonify }}", json.dumps(target))
        return text.replace("{{ page.redirect.to }}", target)

    @staticmethod
    def _run_script(code, search, hash_):
        """Execute the stub's inline script in Node against a fake `location`
        and return the URL it passed to location.replace (None if it never did)."""
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node not on PATH")
        harness = (
            "let out = null;\n"
            f"new Function('location', {json.dumps(code)})("
            f"{{search: {json.dumps(search)}, hash: {json.dumps(hash_)}, "
            "replace(u) { out = u; }});\n"
            "process.stdout.write(JSON.stringify(out));\n")
        done = subprocess.run([node, "-"], input=harness, capture_output=True,
                              text=True, timeout=30)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    def _script_of(self, html):
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertEqual(len(scripts), 1)
        return scripts[0]

    # --- the stub ----------------------------------------------------------

    # Any <meta> that names a crawler (robots, googlebot, bingbot, ...) or
    # carries a noindex directive, in any quoting, case or attribute order.
    # Structural on purpose: dropping one spelling of the tag must not leave
    # another that does the same job.
    _CRAWLER_META = re.compile(
        r"<meta\b[^>]*?(?:\bname\s*=\s*[\"']?\s*(?:robots|[a-z]*bot)\b|\bnoindex\b)[^>]*>",
        re.I | re.S)

    def test_layout_renders_target_script_and_no_noindex(self):
        html = self._render_layout(self.TARGET)
        self.assertNotIn("{{", html)
        self.assertNotIn("{%", html)
        self.assertIn(f'<link rel="canonical" href="{self.TARGET}">', html)
        self.assertIn(f'<meta http-equiv="refresh" content="0; url={self.TARGET}">', html)
        self.assertIn(f'<a href="{self.TARGET}">', html)           # visible fallback
        # Dropped 2026-10-14: Google reads an instant meta refresh as a permanent
        # redirect and its site-move guide says to remove migration noindex rules;
        # noindex on a 200 stub contradicts the canonical it also carries.
        self.assertIsNone(self._CRAWLER_META.search(html),
                          "the redirect stub must not carry a robots/noindex meta")
        code = self._script_of(html)
        self.assertIn(json.dumps(self.TARGET), code)
        self.assertIn("location.search", code)
        self.assertIn("location.hash", code)

    def test_the_noindex_check_can_fail(self):
        # The matcher above is only evidence if it flags every spelling of the
        # tag it forbids and leaves the clean stub alone.
        for bad in (
            '<meta name="robots" content="noindex">',
            "<meta name='robots' content='noindex, nofollow'>",
            '<META NAME="ROBOTS" CONTENT="NOINDEX">',
            '<meta\tname="robots"\tcontent="noindex">',
            '<meta\nname="robots"\ncontent="noindex">',
            '<meta content="noindex" name="robots">',
            '<meta name=robots content=none>',
            '<meta name="googlebot" content="noindex">',
            '<meta name="bingbot" content="noindex">',
        ):
            with self.subTest(bad=bad):
                self.assertIsNotNone(self._CRAWLER_META.search(bad))
        # Inverse direction: a refresh meta whose target slug reads "none" is
        # ordinary, not a crawler directive.
        self.assertIsNone(self._CRAWLER_META.search(self._render_layout(
            "https://happypetproductreviews.com/health/none-of-the-above/")))

    def test_stub_script_forwards_query_and_fragment(self):
        code = self._script_of(self._render_layout(self.TARGET))
        for search, hash_ in (
            (self.PIN_QUERY, ""),
            ("?utm_source=facebook&utm_medium=social&utm_campaign=post", ""),
            ("?a=1&b=%20x", "#frag"),
            ("", "#only-a-fragment"),
            ("", ""),
        ):
            with self.subTest(search=search, hash=hash_):
                self.assertEqual(self._run_script(code, search, hash_),
                                 self.TARGET + search + hash_)

    def test_the_script_check_can_fail(self):
        # Prove the measurement above goes red on the plugin's own stub, which
        # drops the query: a harness that passed both ways would prove nothing.
        plugin_script = f"location={json.dumps(self.TARGET)}"
        self.assertNotEqual(self._run_script(plugin_script, self.PIN_QUERY, "#f"),
                            self.TARGET + self.PIN_QUERY + "#f")
        code = self._script_of(self._render_layout(self.TARGET))
        no_search = code.replace(" + location.search", "")
        self.assertNotEqual(no_search, code)
        self.assertNotEqual(self._run_script(no_search, self.PIN_QUERY, ""),
                            self.TARGET + self.PIN_QUERY)

    def test_the_plugin_and_its_layout_are_both_wired(self):
        cfg = (REPO / "_config.yml").read_text(encoding="utf-8")
        self.assertRegex(cfg, r"(?m)^\s*-\s*jekyll-redirect-from\s*$")
        self.assertRegex(cfg, r"(?m)^permalink:\s*/:categories/:title/\s*$")
        # Excluded files never reach the build; the layout must not be one.
        self.assertTrue((REPO / "_layouts" / "redirect.html").is_file())
        self.assertNotRegex(cfg, r"(?m)^\s*-\s*_?layouts?\b")

    # --- URLs staged for new pins and Facebook posts ----------------------

    def test_build_url_gives_the_live_permalink_for_recategorized_posts(self):
        import generate_posts as gp
        for slug, (fired_with, now) in self.RECATEGORIZED.items():
            post = _post_file_for_slug(slug)
            with self.subTest(slug=slug):
                self.assertIsNotNone(post)
                self.assertEqual(_post_first_category(post), now)
                self.assertIn(f"/{fired_with}/{slug}/", _post_redirect_paths(post))
                live = f"https://happypetproductreviews.com/{now}/{slug}/"
                # A stale registry entry (the category the pin used to carry)
                # must lose to the published post's own front matter.
                with patch.dict(gp.SLUG_CATEGORIES, {slug: fired_with}):
                    self.assertEqual(gp.build_url(slug), live)
                    self.assertEqual(gp.build_url(slug, utm=True), live + self.PIN_QUERY)

    def test_every_fired_pin_whose_post_moved_now_builds_the_live_url(self):
        # Same check driven by the real record of fired pins, not a hand list.
        import generate_posts as gp
        moved = 0
        for jf in sorted((REPO / "_pin_queue" / "sent").glob("*.json")):
            fired = _norm_path(json.loads(jf.read_text(encoding="utf-8"))["article_url"])
            post = _post_file_for_slug(jf.stem)
            if post is None:
                continue
            live = _norm_path(f"/{_post_first_category(post)}/{jf.stem}/")
            if fired == live:
                continue
            moved += 1
            fired_cat = fired.strip("/").split("/")[0]
            with self.subTest(slug=jf.stem), \
                 patch.dict(gp.SLUG_CATEGORIES, {jf.stem: fired_cat}):
                self.assertEqual(_norm_path(gp.build_url(jf.stem, utm=True)), live)
        self.assertGreater(moved, 0, "no moved post found: the check ran on nothing")

    def test_staged_pin_url_matches_the_drafts_front_matter_permalink(self):
        import generate_posts as gp
        slug = "best-grain-free-cat-food-staged"
        product = {"topic": slug, "title": "Best Grain Free Cat Food",
                   "keyword": "grain free cat food", "format": "single_review",
                   "name": "The Food", "category": "food", "species": "cat",
                   "affiliate_url": "https://amzn.to/abc", "pulled_on": "2026-10-01"}
        body = "## Heading\n\n" + ("word " * 800)
        # The registry holds the OLD category for this slug: the URL must come
        # from the same `category` that is written into the front matter.
        with tempfile.TemporaryDirectory() as td, \
             patch.dict(gp.SLUG_CATEGORIES, {slug: "cat-food"}):
            posts = Path(td) / "_posts"; posts.mkdir()
            (Path(td) / "_pin_queue").mkdir()
            with patch.object(gp, "POSTS_DIR", posts), \
                 patch.object(gp, "REPO_DIR", Path(td)), \
                 patch.object(gp, "PIN_GEN_AVAILABLE", False):
                out = gp.stage_article(slug, product, body, pin_desc="Great food.", index=0)
            fm_cat = re.search(r"^categories: \[([^\]]*)\]",
                               out["draft_path"].read_text(encoding="utf-8"), re.M).group(1)
            queued = json.loads(out["pin_queue_path"].read_text(encoding="utf-8"))["article_url"]
        self.assertEqual(fm_cat, "food")
        # _config.yml permalink is /:categories/:title/ (pinned by the wiring test).
        self.assertEqual(queued,
                         f"https://happypetproductreviews.com/{fm_cat}/{slug}/{self.PIN_QUERY}")

    def test_facebook_message_links_the_same_permalink_with_its_own_tag(self):
        import push_pins_to_sheets as pps
        msg = pps._build_fb_message(
            "best-grain-free-cat-food", "Best Grain Free Cat Food",
            self.TARGET + self.PIN_QUERY)
        self.assertTrue(msg.endswith(
            "\n" + self.TARGET + "?utm_source=facebook&utm_medium=social&utm_campaign=post"), msg)

    @staticmethod
    def _stale_queued_pins(queue_dir, posts_dir):
        """Staged-but-unsent pins whose article path is not their post's live
        permalink. A re-categorization PR that lands between Stage 1 and the
        send would otherwise ship the old path to Pinterest and Facebook."""
        stale = []
        for jf in sorted(Path(queue_dir).glob("*.json")):
            url = json.loads(jf.read_text(encoding="utf-8")).get("article_url", "")
            hits = [h for h in sorted(Path(posts_dir).glob(f"*-{jf.stem}.md"))
                    if h.stem == f"DRAFT-{jf.stem}"
                    or re.fullmatch(r"\d{4}-\d{2}-\d{2}-" + re.escape(jf.stem), h.stem)]
            if not hits:
                continue
            live = _norm_path(f"/{_post_first_category(hits[0])}/{jf.stem}/")
            if _norm_path(url) != live:
                stale.append(f"{jf.stem}: queued {_norm_path(url)}, post is at {live}")
        return stale

    def test_no_staged_pin_points_at_a_moved_post(self):
        self.assertEqual(self._stale_queued_pins(REPO / "_pin_queue", REPO / "_posts"), [])

    def test_the_staged_pin_check_can_fail_and_does_not_cry_wolf(self):
        with tempfile.TemporaryDirectory() as td:
            q = Path(td) / "q"; q.mkdir()
            p = Path(td) / "p"; p.mkdir()
            (p / "2026-10-01-best-a.md").write_text("---\ncategories: [food]\n---\n", encoding="utf-8")
            (p / "DRAFT-best-b.md").write_text("---\ncategories: [treats]\n---\n", encoding="utf-8")
            base = "https://happypetproductreviews.com"
            (q / "best-a.json").write_text(json.dumps(
                {"article_url": f"{base}/cat-food/best-a/{self.PIN_QUERY}"}), encoding="utf-8")
            (q / "best-b.json").write_text(json.dumps(
                {"article_url": f"{base}/treats/best-b/{self.PIN_QUERY}"}), encoding="utf-8")
            (q / "best-c.json").write_text(json.dumps(
                {"article_url": f"{base}/toys/best-c/"}), encoding="utf-8")  # no post yet
            stale = self._stale_queued_pins(q, p)
        self.assertEqual(len(stale), 1)
        self.assertIn("best-a", stale[0])


# ---------------------------------------------------------------------------
# Homepage control-wiring guard
#
# _layouts/home.html renders its controls and wires them in the same inline
# script. A control the markup renders but the script never binds is invisible
# to every other check we have: Liquid is valid, Jekyll builds, the button
# paints, and clicking it does nothing.
#
# That is what shipped. The round 2 design rebuild ("replace the skeleton, not
# the paint", e146504) rewrote the tail of that script, and the line binding
# #load-more-btn to showMore() was overwritten rather than moved -- so "Show
# more reviews" sat on the live homepage doing nothing through rounds 2-6,
# with showMore() as dead code nothing called.
#
# The guard is deliberately structural rather than a check for that one id:
# every button the layout gives an id to must be looked up by that id and bound
# to a listener. Closing the class beats closing the instance. Buttons wired by
# class (the .seg filter rail) carry no id and are out of scope here.
# ---------------------------------------------------------------------------

# Buttons deliberately handled by a delegated listener on an ancestor instead of
# bound directly. Empty today. If delegation is ever introduced, add the id here
# with a reason -- that is the intended escape hatch, not deleting this test.
DELEGATED_BUTTON_IDS = frozenset()


class TestHomepageControlsAreWired(unittest.TestCase):
    """Every id'd button in the home layout is actually bound to a handler."""

    @classmethod
    def setUpClass(cls):
        home = (REPO / "_layouts" / "home.html").read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", home, re.S)
        assert script, "_layouts/home.html no longer contains an inline <script> block"
        cls.script = script.group(1)
        cls.markup = home.replace(script.group(0), "")
        cls.button_ids = re.findall(r"<button\b[^>]*\bid=\"([^\"]+)\"", cls.markup)

    def test_the_guard_actually_sees_the_homepage_buttons(self):
        # Without this, a markup change the id regex stops matching would make
        # every assertion below pass over an empty list -- a green test proving
        # nothing. "Show more reviews" is the control this guard exists for.
        self.assertIn(
            "load-more-btn", self.button_ids,
            "home.html no longer renders #load-more-btn, or the markup shape "
            "changed and this guard is now scanning nothing")

    def test_every_id_button_is_looked_up_and_bound(self):
        unwired = []
        for button_id in self.button_ids:
            if button_id in DELEGATED_BUTTON_IDS:
                continue
            lookup = re.search(
                r"(\w+)\s*=\s*document\.getElementById\(['\"]"
                + re.escape(button_id) + r"['\"]\)", self.script)
            if not lookup:
                unwired.append(f"{button_id}: rendered, but the script never looks it up")
                continue
            var = lookup.group(1)
            if not re.search(r"\b" + re.escape(var) + r"\.addEventListener\s*\(", self.script):
                unwired.append(f"{button_id}: fetched as `{var}`, but no listener is bound to it")
        self.assertEqual(
            unwired, [],
            "homepage buttons that render but do nothing: " + "; ".join(unwired)
            + " -- bind a listener, or add the id to DELEGATED_BUTTON_IDS if an "
              "ancestor handles it")


# ---------------------------------------------------------------------------
# Manual light/dark theme toggle
#
# The dark palette is declared twice on purpose (CSS cannot share one block
# between a media-gated selector and an ungated one): once behind
# `@media (prefers-color-scheme: dark)` for "follow the system", once under
# `:root[data-theme="dark"]` for an explicit choice. The dangerous failure is
# quiet drift -- someone retunes a token in one copy and the site then looks
# different depending on HOW the visitor arrived at dark. So the tests compare
# the two token lists, not just their presence, and they run the real head
# script under node against a stub DOM instead of reading its text.
# ---------------------------------------------------------------------------


def _strip_css_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _css_block(css, header):
    """Inner text of the first `header { ... }` in comment-stripped css."""
    at = css.index(header)
    start = css.index("{", at) + 1
    depth = 1
    for i in range(start, len(css)):
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        if depth == 0:
            return css[start:i]
    raise AssertionError(f"unbalanced braces after {header!r}")


def _css_tokens(block):
    """{name: value} for every custom property, plus color-scheme."""
    return {k: " ".join(v.split())
            for k, v in re.findall(r"(--[\w-]+|color-scheme)\s*:\s*([^;]+);", block)}


def _run_node(harness, cfg, script):
    """Run `harness` under node with argv[1] = JSON cfg and argv[2] = the page
    script; return the JSON the harness prints. Skips the test without node."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    proc = subprocess.run([node, "-e", harness, json.dumps(cfg), script],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class TestThemeToggle(unittest.TestCase):
    MEDIA = '@media (prefers-color-scheme: dark)'
    EXPLICIT = ':root[data-theme="dark"]'

    @classmethod
    def setUpClass(cls):
        cls.css = _strip_css_comments(
            (REPO / "assets" / "css" / "style.css").read_text(encoding="utf-8"))
        cls.layout = (REPO / "_layouts" / "default.html").read_text(encoding="utf-8")
        head = re.search(r"<head>(.*?)</head>", cls.layout, re.S)
        assert head, "default.html has no <head>"
        cls.head = head.group(1)
        cls.media_block = _css_block(cls.css, cls.MEDIA)
        cls.system_dark = _css_block(cls.media_block, ":root:not([data-theme=\"light\"])")
        cls.explicit_dark = _css_block(cls.css, cls.EXPLICIT)
        cls.base = _css_block(cls.css, ":root {")

    # ------------------------------------------------------------------ css

    def test_system_dark_is_guarded_against_an_explicit_light_choice(self):
        # Without the :not(), a dark OS beats data-theme="light" and the
        # visitor's explicit choice silently does nothing.
        self.assertNotRegex(
            self.media_block.replace(self.system_dark, ""), r":root\s*\{",
            "an unguarded :root rule inside the dark media query")
        self.assertIn("color-scheme", self.system_dark)

    def test_explicit_dark_declares_dark_color_scheme(self):
        self.assertEqual(_css_tokens(self.explicit_dark).get("color-scheme"), "dark")
        self.assertEqual(_css_tokens(self.base).get("color-scheme"), "light")

    def test_the_two_dark_token_lists_cannot_drift(self):
        system, explicit = _css_tokens(self.system_dark), _css_tokens(self.explicit_dark)
        self.assertGreater(len(system), 20, "the guard is scanning an empty token list")
        self.assertIn("--paper", system)
        self.assertEqual(
            system, explicit,
            "dark tokens differ between the @media block and :root[data-theme=\"dark\"]")

    def test_the_dark_scheme_is_not_a_second_media_query_nobody_mirrored(self):
        # Structural catch-all: any NEW dark-only rule written as another
        # prefers-color-scheme block would ignore the manual toggle.
        self.assertEqual(
            self.css.count("prefers-color-scheme"), 1,
            "another prefers-color-scheme rule exists; give it a "
            "[data-theme] counterpart or fold it into the token block")

    def test_image_dim_is_token_driven_and_reaches_both_dark_paths(self):
        base = float(_css_tokens(self.base)["--img-dim"])
        self.assertEqual(base, 1.0, "light scheme must not dim product photos")
        for name, block in (("system", self.system_dark), ("explicit", self.explicit_dark)):
            self.assertLess(float(_css_tokens(block)["--img-dim"]), 1.0,
                            f"--img-dim not dimmed in the {name} dark block")
        self.assertIn("filter: brightness(var(--img-dim))", self.css)

    # --------------------------------------------------------------- layout

    def test_toggle_markup_is_an_accessible_button_hidden_without_js(self):
        m = re.search(r"<button\b[^>]*\bid=\"theme-toggle\"[^>]*>", self.layout)
        self.assertIsNotNone(m, "no #theme-toggle button in default.html")
        tag = m.group(0)
        self.assertIn('type="button"', tag)
        self.assertRegex(tag, r"\bhidden\b")  # inert until the script un-hides it
        self.assertRegex(tag, r'aria-label="[^"]+"')
        self.assertIn('aria-pressed="false"', tag)

    def test_toggle_has_a_visible_focus_ring_that_is_not_the_strip_colour(self):
        # --focus is the brand blue in the light scheme, and the toggle sits on
        # the brand-blue strip: a default outline there is invisible.
        rule = _css_block(self.css, ".theme-toggle:focus-visible")
        self.assertRegex(rule, r"outline:\s*2px solid var\(--strip-ink\)")

    def _head_script(self):
        scripts = re.findall(r"<script>(.*?)</script>", self.head, re.S)
        found = [s for s in scripts if "localStorage" in s]
        self.assertEqual(len(found), 1, "expected exactly one theme script in <head>")
        return found[0]

    def test_head_script_runs_before_the_stylesheet(self):
        self.assertLess(self.head.index("localStorage"),
                        self.head.index('rel="stylesheet"'),
                        "theme script must run before the stylesheet: no flash")

    def test_every_storage_access_is_in_a_try_block(self):
        script = self._head_script()
        accesses = script.count("localStorage.")
        guarded = len(re.findall(r"try\s*\{[^{}]*localStorage\.[^{}]*\}\s*catch", script))
        self.assertGreaterEqual(accesses, 2)
        self.assertEqual(accesses, guarded, "a localStorage access sits outside try/catch")

    def test_script_theme_colours_match_the_stylesheet_and_metas(self):
        script = self._head_script()
        light = _css_tokens(self.base)["--strip-bg"]     # what the strip paints
        dark = _css_tokens(self.system_dark)["--paper"]  # dark chrome = the page
        self.assertRegex(script, r"light:\s*'" + re.escape(light) + "'")
        self.assertRegex(script, r"dark:\s*'" + re.escape(dark) + "'")
        for colour, scheme in ((light, "light"), (dark, "dark")):
            self.assertRegex(
                self.head,
                rf'<meta name="theme-color" content="{colour}" media="\(prefers-color-scheme: {scheme}\)">')

    # ------------------------------------------------------ the script, run

    @staticmethod
    def _run(script, storage, throws=False, click=False, system_dark=False):
        """Run the head script in node against a stub DOM; return final state."""
        harness = r"""
const vm = require('vm');
const cfg = JSON.parse(process.argv[1]);
const attrs = {};
const metas = ['x', 'y'].map(c => { const m = { c };
  m.setAttribute = (k, v) => { if (k === 'content') m.c = v; }; return m; });
const btn = { hidden: true, pressed: null, handlers: {},
  setAttribute(k, v) { if (k === 'aria-pressed') this.pressed = String(v); },
  addEventListener(t, f) { this.handlers[t] = f; } };
let ready;
const store = Object.assign({}, cfg.storage);
const ctx = {
  document: {
    documentElement: {
      setAttribute(k, v) { attrs[k] = v; },
      getAttribute(k) { return k in attrs ? attrs[k] : null; } },
    querySelectorAll: () => metas,
    getElementById: id => id === 'theme-toggle' ? btn : null,
    addEventListener(t, f) { if (t === 'DOMContentLoaded') ready = f; } },
  window: { matchMedia: () => ({ matches: cfg.systemDark, addEventListener() {} }) },
  localStorage: cfg.throws ? { getItem() { throw new Error('denied'); },
                               setItem() { throw new Error('denied'); } }
                           : { getItem: k => (k in store ? store[k] : null),
                               setItem: (k, v) => { store[k] = String(v); } },
};
vm.createContext(ctx);
vm.runInContext(process.argv[2], ctx);
const before = { theme: attrs['data-theme'] || null, metas: metas.map(m => m.c) };
ready();
if (cfg.click) btn.handlers.click();
console.log(JSON.stringify({ before, theme: attrs['data-theme'] || null,
  metas: metas.map(m => m.c), hidden: btn.hidden, pressed: btn.pressed, store }));
"""
        return _run_node(harness, {"storage": storage, "throws": throws,
                                   "systemDark": system_dark, "click": click}, script)

    def _skip_without_node(self):
        import shutil
        if not shutil.which("node"):
            self.skipTest("node not installed")

    def test_a_saved_choice_is_applied_before_first_paint_with_matching_metas(self):
        self._skip_without_node()
        script = self._head_script()
        dark = self._run(script, {"happypet-theme": "dark"})
        self.assertEqual(dark["before"], {"theme": "dark", "metas": ["#0E1116"] * 2})
        light = self._run(script, {"happypet-theme": "light"}, system_dark=True)
        self.assertEqual(light["before"], {"theme": "light", "metas": ["#1C49C4"] * 2})

    def test_garbage_or_blocked_storage_leaves_the_page_on_the_system_theme(self):
        self._skip_without_node()
        script = self._head_script()
        for storage in ({"happypet-theme": "sepia"}, {"happypet-theme": ""}, {}):
            self.assertIsNone(self._run(script, storage)["before"]["theme"], storage)
        blocked = self._run(script, {}, throws=True, click=True)  # must not raise
        self.assertEqual(blocked["theme"], "dark")  # click still works, unsaved

    def test_click_flips_the_effective_theme_saves_it_and_reveals_the_button(self):
        self._skip_without_node()
        script = self._head_script()
        # light OS, no saved choice -> click goes dark
        r = self._run(script, {}, click=True, system_dark=False)
        self.assertEqual((r["theme"], r["pressed"], r["hidden"]), ("dark", "true", False))
        self.assertEqual(r["store"]["happypet-theme"], "dark")
        self.assertEqual(r["metas"], ["#0E1116"] * 2)
        # dark OS, no saved choice -> click goes LIGHT (flips what you see)
        r = self._run(script, {}, click=True, system_dark=True)
        self.assertEqual((r["theme"], r["pressed"]), ("light", "false"))
        self.assertEqual(r["store"]["happypet-theme"], "light")


# ---------------------------------------------------------------------------
# Affiliate click tracking (GA4 `affiliate_click`)
#
# One delegated listener at the foot of post.html reports every outbound
# Amazon/Chewy click with WHERE on the page it was (link_position), so a
# conversion change can be judged per position. The position comes from the
# link's place in the DOM, not from per-link markup -- so the tests run the real
# script under node against a stub DOM built from the same wrappers the layout
# and kramdown produce, and separately guard that the layout still wraps its
# buy buttons in the classes the script keys on.
#
# HONEST LIMIT: this is not a browser and not Jekyll. Whether gtag.js actually
# ships the event is GA4's side and is not checked here.
# ---------------------------------------------------------------------------

AMAZON_DP = "https://www.amazon.com/dp/B07N1JYYCW?tag=happypetdc-20"
CHEWY_IMPACT = ("https://chewy.sjv.io/c/7160344/3054490/32975?u=https%3A%2F%2Fwww.chewy.com"
                "%2Fkong-extreme-dog-toy%2Fdp%2F38493&intsrc=APIG_24727")


class TestAffiliateClickTracking(unittest.TestCase):

    SLUG_EXPR = "{{ page.slug | jsonify }}"

    @classmethod
    def setUpClass(cls):
        cls.layout = (REPO / "_layouts" / "post.html").read_text(encoding="utf-8")
        found = [s for s in re.findall(r"<script>(.*?)</script>", cls.layout, re.S)
                 if "affiliate_click" in s]
        assert len(found) == 1, "expected exactly one affiliate_click script in post.html"
        cls.source = found[0]
        # The one Liquid expression in the script, rendered the way Jekyll's
        # jsonify renders a slug: a JSON string literal.
        cls.script = cls.source.replace(cls.SLUG_EXPR, json.dumps("best-snuffle-mats-dogs"))

    # ------------------------------------------------------ the script, run

    HARNESS = r"""
const vm = require('vm');
const cfg = JSON.parse(process.argv[1]);
function el(spec, parent) {
  const m = spec.match(/^([a-z0-9]*)((?:\.[\w-]+)*)$/);
  const node = { tagName: (m[1] || 'div').toUpperCase(),
    cls: m[2].split('.').filter(Boolean), parentElement: parent, textContent: '' };
  node.matches = sel => sel.split(',').some(one => {
    const s = one.trim().match(/^([a-z0-9]*)((?:\.[\w-]+)*)(\[href\])?$/);
    if (!s) throw new Error('selector outside the stub: ' + one);
    return (!s[1] || node.tagName === s[1].toUpperCase())
      && s[2].split('.').filter(Boolean).every(c => node.cls.includes(c))
      && (!s[3] || 'href' in node);
  });
  node.closest = sel => { for (let n = node; n; n = n.parentElement) if (n.matches(sel)) return n; return null; };
  node.contains = other => { for (let n = other; n; n = n.parentElement) if (n === node) return true; return false; };
  return node;
}
let parent = null;
const chain = cfg.path.map(spec => (parent = el(spec, parent)));
const prose = chain.find(n => n.cls.includes('prose'));
if (prose) {
  const child = chain[chain.indexOf(prose) + 1];
  prose.lastElementChild = cfg.last && child ? child : el('p', prose);
  if (cfg.trailer !== null) {  // a block after the closing one, e.g. "Rating: 4.5/5"
    const after = el('p', prose);
    after.textContent = cfg.trailer;
    after.previousElementSibling = prose.lastElementChild;
    prose.lastElementChild = after;
  }
}
const a = el('a', parent);
if (cfg.href !== null) a.href = cfg.href;
const target = cfg.onImage ? el('img', a) : a;
const calls = [];
const handlers = {};
const ctx = {
  URL,
  location: { href: 'https://happypetproductreviews.com/toys/best-snuffle-mats-dogs/' },
  document: { addEventListener(t, f) { (handlers[t] = handlers[t] || []).push(f); } },
};
if (cfg.gtag === 'throws') ctx.gtag = () => { throw new Error('gtag broke'); };
else if (cfg.gtag) ctx.gtag = (...args) => calls.push(args);
vm.createContext(ctx);
vm.runInContext(process.argv[2], ctx);
const ev = { type: cfg.type, button: cfg.button, target, defaultPrevented: false,
  preventDefault() { this.defaultPrevented = true; } };
(handlers[cfg.type] || []).forEach(f => f(ev));
console.log(JSON.stringify({ calls, prevented: ev.defaultPrevented,
  types: Object.keys(handlers).sort() }));
"""

    def _click(self, path, href=AMAZON_DP, last=False, type="click", button=0,
               gtag=True, on_image=False, trailer=None):
        cfg = {"path": path, "href": href, "last": last, "type": type, "button": button,
               "gtag": gtag, "onImage": on_image, "trailer": trailer}
        return _run_node(self.HARNESS, cfg, self.script)

    ARTICLE = "article.piece"

    def _params(self, path, **kw):
        r = self._click(path, **kw)
        self.assertFalse(r["prevented"], "the listener must never block navigation")
        self.assertEqual(len(r["calls"]), 1, r)
        name, event, params = r["calls"][0]
        self.assertEqual((name, event), ("event", "affiliate_click"))
        return params

    def test_every_position_on_the_page_is_named(self):
        # Expected values are the position names this spec defines, written out
        # by hand -- not recomputed from the script.
        cases = {
            "panel": ([self.ARTICLE, "div.verdict", "div.verdict-body", "div.buy-row"], False),
            "buybar": ([self.ARTICLE, "div.reading", "div.buybar"], False),
            "quickpick": ([self.ARTICLE, "div.reading", "div.prose", "p.quick-pick"], False),
            "heading": ([self.ARTICLE, "div.prose", "h3"], False),
            "table": ([self.ARTICLE, "div.prose", "table", "tbody", "tr", "td"], False),
            "list": ([self.ARTICLE, "div.prose", "ul", "li"], False),
            "closing": ([self.ARTICLE, "div.reading", "div.prose", "p"], True),
            "inline": ([self.ARTICLE, "div.reading", "div.prose", "p"], False),
            "other": ([self.ARTICLE, "div.share"], False),
        }
        for want, (path, last) in cases.items():
            with self.subTest(position=want):
                self.assertEqual(self._params(path, last=last)["link_position"], want)

    def test_a_structural_position_wins_over_closing(self):
        # A table or list that happens to be the last block is still a table/list.
        for want, path in (("table", ["table", "tr", "td"]), ("list", ["ul", "li"])):
            with self.subTest(position=want):
                p = self._params([self.ARTICLE, "div.prose"] + path, last=True)
                self.assertEqual(p["link_position"], want)

    def test_closing_skips_a_trailing_rating_line(self):
        # Six single-product reviews end with "**Rating: 4.5/5**" after the
        # verdict paragraph; the verdict's link is still the closing one.
        prose_p = [self.ARTICLE, "div.prose", "p"]
        p = self._params(prose_p, last=True, trailer="Rating: 4.5/5")
        self.assertEqual(p["link_position"], "closing")
        # Not the verdict paragraph -> still inline, rating line or not.
        p = self._params(prose_p, last=False, trailer="Rating: 4.5/5")
        self.assertEqual(p["link_position"], "inline")
        # Only a rating line is skipped: any other trailing block IS the close.
        p = self._params(prose_p, last=True, trailer="Happy shopping, and see you soon.")
        self.assertEqual(p["link_position"], "inline")

    def test_amazon_product_link_reports_retailer_slug_and_asin(self):
        p = self._params([self.ARTICLE, "div.prose", "p"], href=AMAZON_DP)
        self.assertEqual(p, {"link_position": "inline", "retailer": "amazon",
                             "post_slug": "best-snuffle-mats-dogs",
                             "product_id": "B07N1JYYCW"})

    def test_amazon_search_link_has_no_product_id(self):
        p = self._params([self.ARTICLE, "div.prose", "p"],
                         href="https://www.amazon.com/s?k=wet+cat+food&tag=happypetdc-20")
        self.assertEqual(p["retailer"], "amazon")
        self.assertNotIn("product_id", p)

    def test_chewy_impact_link_reports_the_chewy_item_id(self):
        p = self._params([self.ARTICLE, "div.verdict", "div.buy-row"], href=CHEWY_IMPACT)
        self.assertEqual((p["retailer"], p["product_id"], p["link_position"]),
                         ("chewy", "38493", "panel"))
        direct = self._params([self.ARTICLE, "div.prose", "p"],
                              href="https://www.chewy.com/kong-extreme-dog-toy/dp/38493")
        self.assertEqual((direct["retailer"], direct["product_id"]), ("chewy", "38493"))

    def test_chewy_prodsku_wins_over_the_dp_id(self):
        # prodsku is the id Impact's order reports use, and it can differ from
        # the /dp/ id (best-dog-puzzle-toys: prodsku 86003, /dp/113786).
        href = ("https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=86003&u=https%3A%2F%2F"
                "www.chewy.com%2Foutward-hound-hide-squirrel-squeaky%2Fdp%2F113786")
        p = self._params([self.ARTICLE, "div.verdict"], href=href)
        self.assertEqual((p["retailer"], p["product_id"]), ("chewy", "86003"))
        empty = self._params([self.ARTICLE, "div.verdict"], href=href.replace("86003", ""))
        self.assertEqual(empty["product_id"], "113786", "an empty prodsku falls back to /dp/")

    def test_click_on_an_image_inside_the_link_still_counts(self):
        p = self._params([self.ARTICLE, "div.buybar"], on_image=True)
        self.assertEqual(p["link_position"], "buybar")

    def test_non_affiliate_links_are_ignored(self):
        # Including retailer image CDNs and look-alike hosts: the match is on the
        # whole hostname, so "evilamazon.com" or "amazon.com.evil.io" is not Amazon.
        for href in ("https://happypetproductreviews.com/training/best-puppy-training-pads/",
                     "https://pinterest.com/pin/create/button/?url=x",
                     "https://m.media-amazon.com/images/I/71Wmu0n5rtL._AC_SX679_.jpg",
                     "https://evilamazon.com/dp/B07N1JYYCW",
                     "https://www.amazon.com.evil.io/dp/B07N1JYYCW",
                     "https://chewy.sjv.io.evil.io/c/1?u=x",
                     "mailto:?subject=x"):
            with self.subTest(href=href):
                r = self._click([self.ARTICLE, "div.prose", "p"], href=href)
                self.assertEqual(r["calls"], [])
                self.assertFalse(r["prevented"])

    def test_host_match_is_case_insensitive(self):
        # URL() lowercases the host in a browser; the stub passes it through the
        # same URL(), so this pins that the script does not compare raw href text.
        p = self._params([self.ARTICLE, "div.prose", "p"],
                         href="https://WWW.Amazon.COM/dp/B07N1JYYCW?tag=happypetdc-20")
        self.assertEqual(p["retailer"], "amazon")

    def test_missing_gtag_is_a_silent_no_op(self):
        # If the inline gtag stub is absent, a click must still navigate untouched.
        r = self._click([self.ARTICLE, "div.verdict"], gtag=False)
        self.assertEqual(r["calls"], [])
        self.assertFalse(r["prevented"])

    def test_a_throwing_gtag_never_escapes_the_listener(self):
        # The harness dispatches without a guard, so an escaping error fails
        # the node run itself.
        r = self._click([self.ARTICLE, "div.verdict"], gtag="throws")
        self.assertFalse(r["prevented"])

    def test_a_malformed_impact_link_still_reports_the_click(self):
        p = self._params([self.ARTICLE, "div.verdict"],
                         href="https://chewy.sjv.io/c/7160344/3054490/32975?u=%%%")
        self.assertEqual(p["retailer"], "chewy")
        self.assertNotIn("product_id", p)

    def test_middle_click_counts_and_right_click_does_not(self):
        self.assertIn("auxclick", self._click([self.ARTICLE], type="click")["types"])
        middle = self._click([self.ARTICLE, "div.buybar"], type="auxclick", button=1)
        self.assertEqual(len(middle["calls"]), 1)
        right = self._click([self.ARTICLE, "div.buybar"], type="auxclick", button=2)
        self.assertEqual(right["calls"], [])

    def test_the_script_never_holds_navigation(self):
        # Structural: nothing that can delay or cancel the click is in the script.
        for banned in ("preventDefault", "event_callback", "setTimeout", "location.href ="):
            self.assertNotIn(banned, self.script)

    # ------------------------------------------------------- layout guards

    def test_the_layout_wraps_its_buy_links_where_the_script_looks(self):
        # Every retailer anchor post.html renders sits inside .verdict or
        # .buybar; a new template button outside both would report "other".
        anchors = re.findall(r"<a\b[^>]*\bclass=\"buy\b[^\"]*\"[^>]*>", self.layout)
        self.assertGreaterEqual(len(anchors), 3, "the guard is scanning no buy buttons")
        verdict = self.layout[self.layout.index('<div class="verdict">'):
                              self.layout.index('<div class="reading">')]
        buybar = self.layout[self.layout.index('<div class="buybar">'):]
        for tag in anchors:
            self.assertTrue(tag in verdict or tag in buybar, tag)

    def test_post_content_renders_inside_prose(self):
        self.assertRegex(self.layout, r'<div class="prose">\s*\{\{ content \}\}\s*</div>')

    def test_every_quick_pick_line_carries_the_class_the_script_keys_on(self):
        # Without `{: .quick-pick}` on the next line kramdown emits a bare <p>,
        # the click reports "inline", and the quick-pick line can't be read on its own.
        found, missing = 0, []
        for post in sorted((REPO / "_posts").glob("*.md")):
            lines = post.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if line.startswith("**Quick pick:**"):
                    found += 1
                    if i + 1 >= len(lines) or lines[i + 1].strip() != "{: .quick-pick}":
                        missing.append(post.name)
        self.assertGreater(found, 0, "no quick-pick lines found - the guard would pass vacuously")
        self.assertEqual(missing, [])

    def test_post_slug_comes_from_the_page_as_a_json_string(self):
        # jsonify, not a bare {{ page.slug }}: the value lands in JavaScript.
        self.assertEqual(self.source.count(self.SLUG_EXPR), 1)
        self.assertEqual(self.source.count("{{"), 1, "unexpected Liquid in the script")


class TestBuyPanelNamesThePick(unittest.TestCase):
    """The purchase panel and the sticky buybar name the product they link to
    (2026-10-12 conversion diagnosis, 1.2): the panel showed only "Our pick" and
    the buybar showed the article title. Text-level pins on post.html; the real
    render is build-check.yml's Jekyll build."""

    PICK = "{%- assign pick_name = page.product_short | default: page.product_name -%}"

    @classmethod
    def setUpClass(cls):
        cls.layout = (REPO / "_layouts" / "post.html").read_text(encoding="utf-8")
        cls.panel = cls.layout[cls.layout.index('<div class="verdict">'):
                               cls.layout.index('<div class="reading">')]
        start = cls.layout.index('<div class="buybar">')
        cls.buybar = cls.layout[start:cls.layout.index("</div>", start)]

    def test_pick_name_is_assigned_once_before_the_panel(self):
        self.assertEqual(self.layout.count(self.PICK), 1)
        self.assertLess(self.layout.index(self.PICK), self.layout.index('<div class="verdict">'))
        self.assertEqual(len(re.findall(r"assign pick_name\b", self.layout)), 1)

    def test_panel_names_the_pick_between_the_label_and_the_buttons(self):
        # Same fallback as the buybar: no name means the title, never a blank.
        m = re.search(r'<p class="verdict-name">'
                      r'\{\{ pick_name \| default: page\.title \| escape \}\}</p>', self.panel)
        self.assertIsNotNone(m, "panel does not render the pick name with the title fallback")
        self.assertNotRegex(self.panel, r"if pick_name")
        self.assertLess(self.panel.index('class="verdict-label"'), m.start())
        self.assertLess(m.end(), self.panel.index('class="buy-row"'))

    def test_buybar_names_the_pick_and_falls_back_to_the_title(self):
        self.assertIn("<b>{{ pick_name | default: page.title | escape }}</b>", self.buybar)
        self.assertNotIn("{{ page.title }}", self.buybar)

    def test_verdict_name_style_clamps_long_listing_titles(self):
        css = (REPO / "assets" / "css" / "style.css").read_text(encoding="utf-8")
        m = re.search(r"\.verdict-name\s*\{([^}]*)\}", css)
        self.assertIsNotNone(m, "no .verdict-name rule")
        self.assertIn("-webkit-line-clamp: 2", m.group(1))


class TestForDogsButton(unittest.TestCase):
    """Phase 4.2: a roundup whose pick is for cats can carry a second, species-
    labelled button for dogs, so a dog owner who lands on the purchase panel is not
    left with a cats-only link. A post opts in with a front-matter `dog_url`.
    Text-level pins on post.html and the front matter; the real render is
    build-check.yml's Jekyll build."""

    SLUG = "best-calming-diffusers-pets"
    THUNDEREASE = "https://www.amazon.com/dp/B072KP2TYK?tag=happypetdc-20"

    @classmethod
    def setUpClass(cls):
        cls.layout = (REPO / "_layouts" / "post.html").read_text(encoding="utf-8")
        cls.panel = cls.layout[cls.layout.index('<div class="verdict">'):
                               cls.layout.index('<div class="reading">')]
        start = cls.layout.index('<div class="buybar">')
        cls.buybar = cls.layout[start:cls.layout.index("</div>", start)]
        cls.css = (REPO / "assets" / "css" / "style.css").read_text(encoding="utf-8")
        cls.posts = {p.name: p.read_text(encoding="utf-8")
                     for p in sorted((REPO / "_posts").glob("*.md"))}
        cls.verified = json.loads(VERIFIED_PRODUCTS.read_text(encoding="utf-8"))

    def _front_matter(self, slug):
        (name,) = [n for n in self.posts if _post_slug(n) == slug]
        return _post_parts(self.posts[name])[0]

    BUTTON = (r'\{%-?\s*if page\.dog_url\s*-?%\}\s*'
              r'<a class="buy buy--dogs" href="\{\{ page\.dog_url \| escape \}\}" target="_blank" '
              r'rel="sponsored nofollow noopener">For dogs: '
              r'\{\{ page\.dog_name \| default: "check price" \| escape \}\} on Amazon</a>\s*'
              r'\{%-?\s*endif\s*-?%\}')

    def test_the_panel_renders_the_dog_button_inside_the_buy_row_when_dog_url_is_set(self):
        row = self.panel[self.panel.index('<div class="buy-row">'):]
        m = re.search(self.BUTTON, row)
        self.assertIsNotNone(m, "no dog_url-guarded 'For dogs' button in the buy row")
        self.assertLess(m.start(), row.index("</div>"), "the button is outside the buy row")

    def test_the_button_names_the_dog_product_and_falls_back_to_check_price(self):
        # Same rule as the primary pick (TestBuyPanelNamesThePick): the label names
        # the product, and a missing name degrades to "check price", never a blank.
        self.assertRegex(self.panel, r'\{\{ page\.dog_name \| default: "check price" \| escape \}\}')
        self.assertRegex(self._front_matter(self.SLUG), r'(?m)^dog_name: "ThunderEase \(ADAPTIL\)"$')

    def test_every_dog_url_post_carries_a_dog_name(self):
        found = [n for n, t in self.posts.items() if re.search(r"(?m)^dog_url:", _post_parts(t)[0])]
        self.assertTrue(found, "no dog_url posts - the guard would pass vacuously")
        for n in found:
            self.assertRegex(_post_parts(self.posts[n])[0], r'(?m)^dog_name: "[^"\s][^"]*"$', n)

    def test_the_href_and_the_name_are_html_escaped(self):
        # Template pins: the unescaped forms must not come back.
        self.assertNotIn('href="{{ page.dog_url }}"', self.layout)
        self.assertNotIn("{{ page.dog_name }}", self.layout)
        self.assertIn('href="{{ page.dog_url | escape }}"', self.layout)
        # What the pinned filter does with a breakout: Liquid's `escape` is
        # CGI.escapeHTML, so no raw quote or angle bracket survives into the attribute.
        import html
        payload = 'https://x.test/?a=1" onmouseover="alert(1)" data-x="<script>'
        rendered = html.escape(payload, quote=True)
        for raw in ('"', "<", ">"):
            self.assertNotIn(raw, rendered)

    def test_the_dog_button_is_one_guarded_block_inside_the_primary_pick_condition(self):
        self.assertEqual(self.layout.count("buy--dogs"), 1)
        outer = self.panel.index("{%- if page.affiliate_url or page.chewy_url %}")
        button = self.panel.index("buy--dogs")
        self.assertLess(outer, button)
        self.assertLess(button, self.panel.index("</div>", self.panel.index('<div class="buy-row">')))
        # The template says so, next to the guard.
        self.assertRegex(self.panel[:button], r"(?s)dog_url is a secondary button only.*endcomment")

    def test_the_sticky_bar_keeps_its_single_pick_button(self):
        self.assertNotIn("dog_url", self.buybar)
        self.assertNotIn("buy--dogs", self.buybar)

    def test_the_button_is_styled(self):
        rules = re.findall(r"([^{}]+)\{([^}]*)\}", self.css)
        resting = [body for sel, body in rules
                   if ".buy--dogs" in [s.strip() for s in sel.split(",")]]
        hover = [body for sel, body in rules
                 if ".buy--dogs:hover" in [s.strip() for s in sel.split(",")]]
        self.assertTrue(resting, "no resting-state .buy--dogs rule")
        self.assertTrue(any("background" in b and "color" in b for b in resting))
        self.assertTrue(hover, "no .buy--dogs:hover rule")

    def test_the_calming_diffusers_post_links_thunderease_for_dogs(self):
        fm = self._front_matter(self.SLUG)
        self.assertRegex(fm, rf'(?m)^dog_url: "{re.escape(self.THUNDEREASE)}"$')

    def test_every_dog_url_is_a_tagged_listed_product_on_a_post_that_covers_dogs(self):
        found = []
        for name, text in self.posts.items():
            fm = _post_parts(text)[0]
            if not re.search(r"(?m)^dog_url:", fm):
                continue
            found.append(name)
            link = _links(_AMAZON_LINK_RE, _front_matter_value(fm, "dog_url"))
            self.assertEqual(len(link), 1, f"{name}: dog_url is not one Amazon link")
            key = _amazon_link_key(link[0])
            self.assertIsNotNone(key, f"{name}: dog_url is not an amazon.com /dp/ link")
            self.assertEqual(key[0], "asin", name)
            self.assertTrue(link[0].group(0).endswith("?tag=happypetdc-20"), name)
            listed = {e.get("asin") for e in self.verified.get(_post_slug(name), [])}
            self.assertIn(key[1], listed, f"{name}: dog_url product is not on its verified list")
            self.assertRegex(fm, r"(?m)^species: both$", f"{name}: a dog button on a one-species post")
        self.assertEqual(found, ["2026-10-01-best-calming-diffusers-pets.md"],
                         "the guard saw a different set of posts than expected")

    def test_an_unlisted_dog_url_is_caught_by_the_corpus_guard(self):
        """The front-matter link must not be a way around the verified list."""
        post = ('---\nlayout: post\naffiliate_url: "https://www.amazon.com/dp/B09S8WMJY9?tag=happypetdc-20"\n'
                'dog_url: "https://www.amazon.com/dp/B0UNLISTED?tag=happypetdc-20"\n---\nBody.\n')
        found = unverified_amazon_links({"2026-04-04-best-automatic-cat-feeder.md": post},
                                        {"best-automatic-cat-feeder": [{"asin": "B09S8WMJY9", "name": "a"}]})
        self.assertEqual(len(found), 1, found)
        self.assertIn("B0UNLISTED", found[0])


class TestPublishedPostsNameTheirPick(unittest.TestCase):
    """Every published post with a buy panel carries product_short, so the panel
    and buybar never fall back to the article title on a live page -- except a
    post whose affiliate link is an Amazon search page, which must not name one
    product over a page of results (gate round 1, Spec F8)."""

    SEARCH = re.compile(r'^affiliate_url:\s*"?https?://(?:www\.)?amazon\.com/s[?/]', re.M)

    def _posts(self):
        for path in sorted((REPO / "_posts").glob("*.md")):
            yield path.name, path.read_text(encoding="utf-8").split("---", 2)[1]

    def test_every_post_with_an_affiliate_link_has_a_short_product_name(self):
        missing = [name for name, fm in self._posts()
                   if re.search(r"^affiliate_url:", fm, re.M) and not self.SEARCH.search(fm)
                   and not re.search(r'^product_short: "[^"\s][^"]*"$', fm, re.M)]
        self.assertEqual(missing, [])

    def test_a_search_link_post_names_no_product(self):
        search = [name for name, fm in self._posts() if self.SEARCH.search(fm)]
        self.assertGreater(len(search), 0, "search-link detection matched no post")
        self.assertEqual([n for n, fm in self._posts()
                          if self.SEARCH.search(fm) and "product_short:" in fm], [])


class _PathFilter:
    """A workflow's `paths:` filter, evaluated the way GitHub documents it:
    patterns in order, the last match decides, `*` stops at `/`, `**` does not,
    `[0-9]` is a character range; a run fires if any one file is in. Set WORKFLOW."""

    def _patterns(self):
        lines = (REPO / ".github" / "workflows" / self.WORKFLOW).read_text(
            encoding="utf-8").splitlines()
        start = lines.index("    paths:") + 1
        pats = []
        for line in lines[start:]:
            m = re.fullmatch(r"\s+- '([^']+)'", line)
            if not m:
                break
            pats.append(m.group(1))
        self.assertGreater(len(pats), 5, "paths block not found")
        return pats

    def _runs(self, files):
        def rx(p):
            return re.compile("".join(".*" if t == "**" else "[^/]*" if t == "*"
                                      else t if t.startswith("[") else re.escape(t)
                                      for t in re.split(r"(\*\*|\*|\[[^\]]+\])", p)))
        rules = [(p.startswith("!"), rx(p.lstrip("!"))) for p in self._patterns()]
        def included(path):
            state = False
            for negated, r in rules:
                if r.fullmatch(path):
                    state = not negated
            return state
        return any(included(f) for f in files)


class TestBuildCheckPathFilter(_PathFilter, unittest.TestCase):
    """build-check.yml must run on a published-post edit and never on a routine
    Stage-1 or refill PR: automerge fires once, when "CI — Tests" completes, and
    automerge_gate holds a PR while any other check is still pending."""

    WORKFLOW = "build-check.yml"

    def test_the_diff_and_summary_steps_cannot_fail_the_job(self):
        """A non-empty diff reports and never fails: every diff and grep in the
        "Diff the two sites" step ends in `|| true` (grep -c exits 1 on zero
        matches, diff exits 1 on any difference). Only a build error is red."""
        wf = (REPO / ".github" / "workflows" / "build-check.yml").read_text(encoding="utf-8")
        step = wf.split("- name: Diff the two sites", 1)[1].split("- name:", 1)[0]
        cmds = [l.strip() for l in step.splitlines()
                if re.search(r"(^|\$\()\s*(diff|grep)\b", l.strip())]
        self.assertEqual(len(cmds), 5, cmds)
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertRegex(cmd, r"\|\| true\)?$")

    def test_a_published_post_edit_runs_the_check(self):
        self.assertTrue(self._runs(["_posts/2026-04-16-best-cat-litter.md"]))

    def test_a_stage1_draft_pr_does_not(self):
        self.assertFalse(self._runs(["_posts/DRAFT-best-cat-litter.md",
                                     "_pin_queue/best-cat-litter.json",
                                     "assets/images/pins/best-cat-litter.jpg"]))

    def test_a_refill_pr_does_not(self):
        self.assertFalse(self._runs(["products.json"]))

    def test_a_topic_hub_edit_runs_the_check(self):
        self.assertTrue(self._runs(["hubs/toys.md"]))

    def test_a_draft_alongside_a_layout_change_still_runs(self):
        self.assertTrue(self._runs(["_posts/DRAFT-x.md", "_layouts/post.html"]))


class TestDeployPathFilter(_PathFilter, unittest.TestCase):
    """deploy.yml's push filter covers every page indexnow.py submits, so a page
    edit both deploys and reaches IndexNow, and no internal root doc deploys."""

    WORKFLOW = "deploy.yml"
    PAGES = {"index.md", "about.md", "cats.md", "dogs.md", "contact.md", "privacy-policy.md"}

    def test_the_filter_is_pinned_to_the_pages_indexnow_submits(self):
        import indexnow
        candidates = [p for p in REPO.iterdir() if p.suffix in (".md", ".html")]
        candidates += sorted((REPO / "hubs").glob("*.md"))
        submitted = [p.relative_to(REPO).as_posix() for p in candidates
                     if indexnow.url_for(p.relative_to(REPO).as_posix(), p.read_text(encoding="utf-8"))]
        self.assertEqual({s for s in submitted if "/" not in s}, self.PAGES)
        self.assertGreater(len([s for s in submitted if s.startswith("hubs/")]), 5)
        for path in submitted:
            with self.subTest(path=path):
                self.assertTrue(self._runs([path]))
        root_pages = {p for p in self._patterns() if p.endswith((".md", ".html")) and "/" not in p}
        self.assertEqual(root_pages, self.PAGES)
        self.assertIn("hubs/**", self._patterns())

    def test_internal_docs_and_drafts_do_not_deploy(self):
        for path in ("HANDOFF.md", "README.md", "CLAUDE.md", "AUDIT_FINDINGS.md",
                     "search.md", "docs/superpowers/plans/plan.md", "_posts/DRAFT-x.md",
                     "_pin_queue/x.json", "indexnow.py", "products.json"):
            with self.subTest(path=path):
                self.assertFalse(self._runs([path]))

    def test_a_dated_post_and_the_site_furniture_still_deploy(self):
        for path in ("_posts/2026-04-16-best-cat-litter.md", "assets/css/main.css",
                     "_layouts/post.html", "_includes/head.html", "_data/categories.json",
                     "_config.yml", "Gemfile"):
            with self.subTest(path=path):
                self.assertTrue(self._runs([path]))


class TestAutoMergePublishWiring(unittest.TestCase):
    """Preconditions the AUTO_MERGE=on routine path (SKILL Phase 2) bets on. If a
    workflow edit breaks one of these, the unattended publish would silently
    misfire -- ship a draft, or never publish after merge."""

    def _wf(self, name):
        return (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_publish_is_workflow_dispatchable(self):
        # The routine may dispatch Stage 2 as a fallback, so publish.yml must
        # still accept workflow_dispatch.
        self.assertIn("workflow_dispatch:", self._wf("publish.yml"))

    def test_publish_auto_fires_on_draft_merge(self):
        # Durable path: a Stage-1 PR merge lands _posts/DRAFT-*.md on main. Because
        # the routine merges via the GitHub App (not GITHUB_TOKEN), that push
        # triggers publish.yml -- so publishing needs no actions:write dispatch.
        # Guard the trigger so it can't silently regress back to dispatch-only.
        pub = self._wf("publish.yml")
        self.assertIn("push:", pub)
        self.assertIn("_posts/DRAFT-*.md", pub)

    def test_merged_draft_cannot_deploy_before_dating(self):
        # A Stage-1 PR merges _posts/DRAFT-<slug>.md. deploy.yml must publish only
        # DATED posts, so a draft is inert on main until Stage 2 dates it --
        # otherwise auto-merge could ship an un-dated draft.
        deploy = self._wf("deploy.yml")
        self.assertIn("_posts/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.md", deploy)
        self.assertNotIn("_posts/DRAFT", deploy)
        self.assertNotIn("_posts/**", deploy)

    def test_ci_backstop_runs_on_prs_to_main(self):
        # test.yml is the independent gate step 8 waits on before merging.
        ci = self._wf("test.yml")
        self.assertIn("pull_request:", ci)
        self.assertIn("test_pipeline.py", ci)

    def test_ci_runs_every_file_pytest_ini_names(self):
        """A file in pytest.ini's testpaths runs locally; CI must run it too."""
        ini = (REPO / "pytest.ini").read_text(encoding="utf-8")
        local = re.search(r"^testpaths\s*=\s*(.+)$", ini, re.M).group(1).split()
        ci = re.search(r"python3 -m pytest (.+) -q$", self._wf("test.yml"), re.M).group(1).split()
        self.assertEqual(sorted(ci), sorted(local))


class TestPinPhotoGuard(unittest.TestCase):
    """The cloud container can't reach Amazon image hosts, so a routine-generated
    pin renders photo-less (blank product area). Pins are regenerated on the GHA
    runner (where fetch works), and a still-missing photo must fail LOUDLY rather
    than ship a blank pin (recovery #48)."""

    def setUp(self):
        import generate_pin_images as g
        self.g = g

    @patch("generate_pin_images.PIL_AVAILABLE", True)
    @patch("generate_pin_images.make_pin", return_value=False)
    def test_strict_raises_when_photo_missing(self, _mk):
        with self.assertRaises(RuntimeError):
            self.g.make_pin_for_post("T", "D", "https://x/img.jpg", "cat-litter",
                                     "slug-x", 0, strict=True)

    @patch("generate_pin_images.PIL_AVAILABLE", True)
    @patch("generate_pin_images.make_pin", return_value=False)
    def test_non_strict_tolerates_missing_photo(self, _mk):
        # The cloud stage stays tolerant (blank is overwritten by the GHA regen).
        url = self.g.make_pin_for_post("T", "D", "https://x/img.jpg", "cat-litter",
                                       "slug-x", 0)
        self.assertIn("slug-x", url)

    @patch("generate_pin_images.PIL_AVAILABLE", True)
    @patch("generate_pin_images.make_pin", return_value=True)
    def test_strict_ok_when_photo_present(self, _mk):
        url = self.g.make_pin_for_post("T", "D", "https://x/img.jpg", "cat-litter",
                                       "slug-x", 0, strict=True)
        self.assertIn("slug-x", url)

    def test_make_pin_reports_photo_embedded(self):
        # make_pin must return whether the product photo was embedded, so callers
        # (and strict mode) can tell a real pin from a blank one.
        out = self.g.PINS_DIR / "guard-blank-test.jpg"
        self.addCleanup(lambda: out.unlink(missing_ok=True))
        with patch("generate_pin_images.fetch_image", return_value=None):
            self.assertIs(
                self.g.make_pin("T", "D", "https://x/i.jpg", "cat-litter",
                                "guard-blank-test", 0), False)

    def test_publish_regenerates_pins_on_runner(self):
        pub = (REPO / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
        self.assertIn("generate_pin_images.py --slug", pub)


# ---------------------------------------------------------------------------
# Credential Manager fallback for the vault unlock key
# ---------------------------------------------------------------------------


def _real_credential_manager_available():
    """True only where a genuine OS keyring backend is present -- never on the
    ubuntu CI runner (keyring is a win32-only optional dep, see requirements)."""
    try:
        import keyring
        from keyring.backends import fail
        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:
        return False


class _FakeKeyring:
    """Stands in for the `keyring` module. Records lookups so a test can prove
    Credential Manager was NOT consulted when an earlier tier answered."""

    def __init__(self, store=None, error=None):
        self.store = dict(store or {})
        self.error = error
        self.calls = []

    def get_password(self, service, name):
        self.calls.append((service, name))
        if self.error is not None:
            raise self.error
        return self.store.get((service, name))


class TestVaultPathConstants(unittest.TestCase):
    """Guards the module-level path constants themselves.

    These are the one part of brain_secrets.py that no behavioural test can
    cover, because every test that exercises _get_vault() patches them out --
    which is exactly how a wrong value reaches production behind a green suite.
    Reconciling the Credential Manager branch with the path fix produced that
    defect for real: both sides had added their own _DEFAULT_DB_PATH in
    different regions of the file, git merged both without a conflict, and the
    later (bare-filename, CWD-relative) assignment silently won at import.
    """

    def _module_source(self):
        import brain_secrets
        return Path(brain_secrets.__file__).read_text(encoding="utf-8")

    def test_no_module_level_constant_is_assigned_twice(self):
        """The structural catch-all. Checks every module-level name rather than
        the one that got hurt, because the next bad merge will land on a
        different constant and a per-name assertion would not be looking."""
        import ast
        from collections import Counter

        tree = ast.parse(self._module_source())
        assigned = Counter(
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        )
        duplicates = {name: n for name, n in assigned.items() if n > 1}
        self.assertEqual(
            duplicates, {},
            "a module-level constant is assigned more than once -- the later "
            "assignment silently wins at import; this is the fingerprint of a "
            "merge that took both sides")

    def test_the_default_db_path_is_absolute_and_canonical(self):
        """A bare filename resolves against the CWD, so the vault was found or
        not depending on where the pipeline was launched from -- and connect()
        then CREATED the missing file, turning it into a permanent 0-byte
        'database'. The default must name one fixed location."""
        import brain_secrets
        default = Path(str(brain_secrets._DEFAULT_DB_PATH))
        self.assertTrue(default.is_absolute(), f"{default} must be absolute")
        self.assertEqual(default.name, "maeve-brain-v2.db")
        self.assertEqual(default.parent.name, "brain")
        self.assertEqual(
            default, Path(brain_secrets.__file__).parent.parent / "brain" / "maeve-brain-v2.db")

    def test_the_secrets_env_path_points_inside_the_archived_checkout(self):
        """The MaeveJarvis checkout moved under _archive/ in the 2026-07-21
        reorg. The pre-reorg path silently yielded no key at all."""
        import brain_secrets
        secrets_env = Path(str(brain_secrets._SECRETS_ENV))
        self.assertTrue(secrets_env.is_absolute())
        self.assertEqual(secrets_env.name, "secrets.env")
        self.assertEqual(
            [p.name for p in list(secrets_env.parents)[:2]], ["MaeveJarvis", "_archive"])


class TestVaultKeyResolution(unittest.TestCase):
    """COGNITIVE_DB_KEY is moving out of the plaintext secrets.env into Windows
    Credential Manager. The order must stay env -> secrets.env -> Credential
    Manager so nothing that works today changes, and a dead end must raise on a
    host that HAS a vault -- the old silent None is what let a whole pin run
    publish with an empty audit trail and nothing in the log."""

    def setUp(self):
        import brain_secrets as bs
        import tempfile
        self.bs = bs
        self._orig = (bs._vault, bs._vault_tried, bs._vault_error)
        bs._vault, bs._vault_tried, bs._vault_error = None, False, None
        self.tmp = Path(tempfile.mkdtemp())
        # Every test states its own environment explicitly.
        self._env = patch.dict(os.environ, {"COGNITIVE_DB_KEY": "", "COGNITIVE_DB_PATH": ""})
        self._env.start()
        del os.environ["COGNITIVE_DB_KEY"], os.environ["COGNITIVE_DB_PATH"]
        # _DEFAULT_DB_PATH is the LAST path candidate and it is a real, absolute
        # path to the live brain DB on a developer machine. Left unpatched, any
        # test here that means "there is no vault" instead finds the production
        # vault and connects to it -- a false result on this box, a different
        # false result on CI. Pinned for the whole class rather than per test,
        # so a new test cannot reintroduce the leak by forgetting.
        self._default_db = patch.object(
            self.bs, "_DEFAULT_DB_PATH", str(self.tmp / "no-such-default-vault.db"))
        self._default_db.start()

    def tearDown(self):
        self._default_db.stop()
        self._env.stop()
        self.bs._vault, self.bs._vault_tried, self.bs._vault_error = self._orig
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _secrets_env(self, body):
        p = self.tmp / "secrets.env"
        p.write_text(body, encoding="utf-8")
        return patch.object(self.bs, "_SECRETS_ENV", p)

    # --- resolution order ---------------------------------------------------

    def test_environment_wins_over_both_lower_tiers(self):
        fake = _FakeKeyring({("maeve", "COGNITIVE_DB_KEY"): "from-credman"})
        with self._secrets_env("COGNITIVE_DB_KEY=from-file\n"), \
             patch.dict(sys.modules, {"keyring": fake}), \
             patch.dict(os.environ, {"COGNITIVE_DB_KEY": "from-env"}):
            key, source, _ = self.bs._resolve_db_key(
                self.bs._parse_env_file(self.bs._SECRETS_ENV))
        self.assertEqual(key, "from-env")
        self.assertEqual(source, "environment")
        self.assertEqual(fake.calls, [], "Credential Manager must not be consulted")

    def test_secrets_env_wins_over_credential_manager(self):
        """The 'nothing breaks today' guarantee: while the key is still in the
        file, the new tier must be unreachable."""
        fake = _FakeKeyring({("maeve", "COGNITIVE_DB_KEY"): "from-credman"})
        with self._secrets_env("COGNITIVE_DB_KEY=from-file\n"), \
             patch.dict(sys.modules, {"keyring": fake}):
            key, source, _ = self.bs._resolve_db_key(
                self.bs._parse_env_file(self.bs._SECRETS_ENV))
        self.assertEqual(key, "from-file")
        self.assertIn("secrets.env", source)
        self.assertEqual(fake.calls, [], "Credential Manager must not be consulted")

    def test_credential_manager_is_used_once_the_key_leaves_the_file(self):
        fake = _FakeKeyring({("maeve", "COGNITIVE_DB_KEY"): "from-credman"})
        with self._secrets_env("COGNITIVE_DB_PATH=x.db\n"), \
             patch.dict(sys.modules, {"keyring": fake}):
            key, source, _ = self.bs._resolve_db_key(
                self.bs._parse_env_file(self.bs._SECRETS_ENV))
        self.assertEqual(key, "from-credman")
        self.assertIn("Credential Manager", source)
        self.assertEqual(fake.calls, [("maeve", "COGNITIVE_DB_KEY")])

    # --- what comes back out of Credential Manager --------------------------

    def test_edge_whitespace_and_invisibles_are_stripped(self):
        """Same normalisation as the helper that writes these entries: a BOM
        (PowerShell 5.1 prepends one) or a stray newline makes the key wrong in
        a way that reads as 'bad credential', not 'bad paste'."""
        for wrapper in ("﻿{}", "{}\r\n", "﻿  {}  \r\n", "\xa0{}\xa0", "​{}​"):
            with self.subTest(wrapper=repr(wrapper)):
                fake = _FakeKeyring({("maeve", "COGNITIVE_DB_KEY"): wrapper.format("abc123")})
                with patch.dict(sys.modules, {"keyring": fake}):
                    self.assertEqual(self.bs._credman_get("COGNITIVE_DB_KEY")[0], "abc123")

    def test_interior_characters_are_preserved(self):
        """The inverse direction: normalisation must not rewrite a legitimate
        key. A 64-char hex key and a passphrase with spaces both stay intact."""
        for value in ("a" * 64, "pass phrase with spaces", "k-e_y+/=:.@"):
            with self.subTest(value=value):
                fake = _FakeKeyring({("maeve", "COGNITIVE_DB_KEY"): value})
                with patch.dict(sys.modules, {"keyring": fake}):
                    self.assertEqual(self.bs._credman_get("COGNITIVE_DB_KEY")[0], value)

    def test_a_missing_keyring_package_is_an_answer_not_a_crash(self):
        with patch.dict(sys.modules, {"keyring": None}):
            value, note = self.bs._credman_get("COGNITIVE_DB_KEY")
        self.assertIsNone(value)
        self.assertIn("not installed", note)

    def test_a_backend_error_is_reported_not_raised(self):
        fake = _FakeKeyring(error=OSError("credential store unavailable"))
        with patch.dict(sys.modules, {"keyring": fake}):
            value, note = self.bs._credman_get("COGNITIVE_DB_KEY")
        self.assertIsNone(value)
        self.assertIn("OSError", note)

    def test_an_empty_credential_manager_entry_counts_as_absent(self):
        fake = _FakeKeyring({("maeve", "COGNITIVE_DB_KEY"): "   \r\n"})
        with patch.dict(sys.modules, {"keyring": fake}):
            self.assertIsNone(self.bs._credman_get("COGNITIVE_DB_KEY")[0])

    # --- loud vs quiet ------------------------------------------------------

    def test_configured_host_with_no_key_anywhere_fails_loudly(self):
        """A secrets.env that exists but no longer carries the key, and nothing
        in Credential Manager: that is the migration going wrong, and it must
        not resolve to a quiet None."""
        fake = _FakeKeyring()
        with self._secrets_env("COGNITIVE_DB_PATH=x.db\nOTHER=1\n"), \
             patch.dict(sys.modules, {"keyring": fake}):
            with self.assertRaises(self.bs.BrainSecretsUnavailable) as ctx:
                self.bs._get_vault()
        self.assertIn("Credential Manager", str(ctx.exception))

    def test_the_failure_repeats_for_every_caller(self):
        """The open is cached. Without latching the reason, caller #1 gets the
        error and everyone after it gets a quiet None -- the same silent
        degradation, just slower."""
        fake = _FakeKeyring()
        with self._secrets_env("COGNITIVE_DB_PATH=x.db\n"), \
             patch.dict(sys.modules, {"keyring": fake}):
            for attempt in range(3):
                with self.subTest(attempt=attempt), \
                     self.assertRaises(self.bs.BrainSecretsUnavailable):
                    self.bs._get_vault()

    def test_no_vault_configured_here_still_returns_none(self):
        """The inverse direction, and the contract CI depends on: with no
        secrets.env, no key and no vault DB, this is a machine that simply has
        no vault -- callers env-fall-back and must not see an exception."""
        fake = _FakeKeyring()
        with patch.object(self.bs, "_SECRETS_ENV", self.tmp / "absent.env"), \
             patch.object(self.bs, "_DEFAULT_DB_PATH", str(self.tmp / "absent.db")), \
             patch.dict(sys.modules, {"keyring": fake}):
            self.assertIsNone(self.bs._get_vault())

    def test_a_key_with_no_vault_database_fails_loudly_and_creates_nothing(self):
        """sqlcipher3.connect() CREATES a missing file, so a wrong path used to
        leave a stray empty DB and then fail as if the key were bad."""
        missing = self.tmp / "nowhere" / "maeve-brain-v2.db"
        with patch.object(self.bs, "_SECRETS_ENV", self.tmp / "absent.env"), \
             patch.dict(os.environ, {"COGNITIVE_DB_KEY": "k", "COGNITIVE_DB_PATH": str(missing)}):
            with self.assertRaises(self.bs.BrainSecretsUnavailable) as ctx:
                self.bs._get_vault()
        self.assertIn("no vault database", str(ctx.exception))
        self.assertFalse(missing.exists(), "no stray database may be created")

    def test_a_resolved_key_never_appears_in_the_error(self):
        """The failure reports where the key came from, never what it is --
        these messages land in a log file and a GitHub Actions transcript."""
        secret = "S3CR3T-unlock-value-do-not-print"
        missing = self.tmp / "nowhere.db"
        with patch.object(self.bs, "_SECRETS_ENV", self.tmp / "absent.env"), \
             patch.dict(os.environ, {"COGNITIVE_DB_KEY": secret,
                                     "COGNITIVE_DB_PATH": str(missing)}):
            with self.assertRaises(self.bs.BrainSecretsUnavailable) as ctx:
                self.bs._get_vault()
        self.assertNotIn(secret, str(ctx.exception))
        self.assertIn("environment", str(ctx.exception))  # the source, not the value

    def test_a_failed_open_warns_without_printing_the_key(self):
        """A bad key still degrades to None (a locked brain DB is transient and
        must not take down a run) -- but it must no longer be silent."""
        secret = "S3CR3T-unlock-value-do-not-print"
        db = self.tmp / "brain.db"
        # Non-empty on purpose: a 0-byte file is what a connect() to a wrong
        # path leaves behind, so path selection rejects it before ever
        # connecting. The content is irrelevant -- sqlcipher3 is mocked below.
        db.write_bytes(b"not-really-sqlcipher-but-not-empty")
        boom = types.SimpleNamespace(
            connect=MagicMock(side_effect=RuntimeError("file is not a database")))
        warnings = []
        with patch.object(self.bs, "_SECRETS_ENV", self.tmp / "absent.env"), \
             patch.object(self.bs, "_warn", warnings.append), \
             patch.dict(sys.modules, {"keyring": _FakeKeyring(), "sqlcipher3": boom}), \
             patch.dict(os.environ, {"COGNITIVE_DB_KEY": secret, "COGNITIVE_DB_PATH": str(db)}):
            self.assertIsNone(self.bs._get_vault())
        self.assertTrue(warnings, "a failed open must not be silent")
        self.assertNotIn(secret, " ".join(warnings))

    def test_the_vault_opens_with_a_key_from_credential_manager(self):
        """End to end through _get_vault with the key coming only from
        Credential Manager -- otherwise the fallback could resolve a key that
        never reaches the reader."""
        db = self.tmp / "brain.db"
        db.write_bytes(b"not-really-sqlcipher-but-not-empty")  # see above
        conn = MagicMock()
        sqlcipher3 = types.SimpleNamespace(connect=MagicMock(return_value=conn))
        fake = _FakeKeyring({("maeve", "COGNITIVE_DB_KEY"): "unlock-me"})
        with self._secrets_env(f"COGNITIVE_DB_PATH={db}\n"), \
             patch.dict(sys.modules, {"keyring": fake, "sqlcipher3": sqlcipher3}):
            vault = self.bs._get_vault()
        self.assertIsInstance(vault, self.bs._VaultReader)
        sqlcipher3.connect.assert_called_once_with(str(db))
        pragma = conn.execute.call_args_list[0][0][0]
        self.assertIn("unlock-me", pragma, "the resolved key must reach PRAGMA key")

    def test_get_secret_propagates_the_configuration_error(self):
        err = self.bs.BrainSecretsUnavailable("boom")
        with patch.object(self.bs, "_get_vault", side_effect=err):
            with self.assertRaises(self.bs.BrainSecretsUnavailable):
                self.bs.get_secret("IMPACT_ACCOUNT_SID")

    @unittest.skipUnless(_real_credential_manager_available(),
                         "no real OS keyring backend on this host")
    def test_round_trip_through_the_real_credential_manager(self):
        """Proves the fallback against the actual Windows Credential Manager,
        not a stand-in -- under a throwaway service name so nothing production
        reads or writes is touched."""
        import keyring
        service, name = "maeve-happypet-selftest", "COGNITIVE_DB_KEY"
        self.addCleanup(lambda: keyring.delete_password(service, name)
                        if keyring.get_password(service, name) is not None else None)
        keyring.set_password(service, name, "round-trip-value")
        self.assertEqual(self.bs._credman_get(name, service)[0], "round-trip-value")
        keyring.delete_password(service, name)
        value, note = self.bs._credman_get(name, service)
        self.assertIsNone(value)
        self.assertIn("no Credential Manager entry", note)


class TestCallersDoNotSwallowTheConfigurationError(unittest.TestCase):
    """post_pins/push_pins catch Exception around every vault read so a CI run
    can fall back to env vars. That blanket catch must not also absorb a
    misconfigured vault -- doing so is how a run 'succeeds' with no data."""

    def setUp(self):
        import brain_secrets as bs
        import post_pins as pp
        import push_pins_to_sheets as pk
        self.bs, self.pp, self.pk = bs, pp, pk

    def test_post_pins_reraises_it(self):
        err = self.bs.BrainSecretsUnavailable("vault configured but locked")
        with patch.object(self.pp, "_vault_get_secret", side_effect=err):
            with self.assertRaises(self.bs.BrainSecretsUnavailable):
                self.pp.brain_get_secret("IFTTT_MAKER_KEY", "global")

    def test_push_pins_reraises_it(self):
        err = self.bs.BrainSecretsUnavailable("vault configured but locked")
        with patch.object(self.pk, "_vault_get_secret", side_effect=err):
            with self.assertRaises(self.bs.BrainSecretsUnavailable):
                self.pk.brain_get_secret("FACEBOOK_QUEUE_SHEET_ID")

    def test_post_pins_still_falls_back_to_env_on_an_ordinary_vault_miss(self):
        """The inverse direction: every OTHER failure must keep degrading to the
        environment, or CI breaks."""
        with patch.object(self.pp, "_vault_get_secret", side_effect=RuntimeError("no vault")), \
             patch.dict(os.environ, {"IFTTT_MAKER_KEY": "env-key"}):
            self.assertEqual(self.pp.brain_get_secret("IFTTT_MAKER_KEY", "global"), "env-key")

    def test_push_pins_sheets_creds_reraises_it(self):
        """Retargeted from post_pins (which no longer opens any sheet) to
        push_pins_to_sheets, where the identical re-raise now guards the ONE
        live sheet path -- the Facebook Queue append. A locked-but-configured
        vault must fail loudly rather than fall through to GCP_SA_KEY_B64."""
        err = self.bs.BrainSecretsUnavailable("vault configured but locked")
        with patch.object(self.pk, "_vault_sheets_creds", side_effect=err):
            with self.assertRaises(self.bs.BrainSecretsUnavailable):
                self.pk.get_sheets_creds()

    def test_push_pins_sheets_creds_still_falls_back_on_an_ordinary_vault_miss(self):
        """Inverse direction: only BrainSecretsUnavailable is fatal. Any other
        vault failure must still degrade to the CI credential, or the FB Queue
        append breaks on every GHA run."""
        import base64, json
        b64 = base64.b64encode(json.dumps({"type": "service_account"}).encode()).decode()
        with patch.object(self.pk, "_vault_sheets_creds", side_effect=RuntimeError("no vault")), \
             patch.dict(os.environ, {"GCP_SA_KEY_B64": b64}), \
             patch("google.oauth2.service_account.Credentials.from_service_account_info",
                   return_value="CREDS"):
            self.assertEqual(self.pk.get_sheets_creds(), "CREDS")


class TestRetiredTopicalSheetsAreGone(unittest.TestCase):
    """The six per-category HAPPYPET_SHEET_ID_* spreadsheets are decommissioned.
    Every open of them 404'd and the error was swallowed into a WARN, so a
    'successful' pin run logged six failures nobody acted on.

    Structural, not a needle list: assert the whole class of sheet-touching
    machinery is absent from these modules rather than naming one symbol at a
    time. Four of the six LABELS of the same name (FOOD/HEALTH/HOME/TOYS) survive
    in TOPICAL_EVENT as category-board labels and are asserted live below. The
    DOGS/CATS labels do not survive: species boards come from `species` alone."""

    def setUp(self):
        import post_pins as pp
        import generate_pin_images as g
        self.pp, self.g = pp, g

    def test_post_pins_has_no_sheet_machinery_left(self):
        for name in ("SHEET_ID_KEYS", "resolve_sheet_ids", "mark_pinned_in_sheet",
                     "get_sheets_creds", "_vault_sheets_creds"):
            self.assertFalse(hasattr(self.pp, name),
                             f"post_pins.{name} is retired topical-sheet code")

    def test_generate_pin_images_has_no_sheet_machinery_left(self):
        for name in ("update_sheets", "GSPREAD_AVAILABLE", "gspread",
                     "get_sheets_creds", "brain_get_secret"):
            self.assertFalse(hasattr(self.g, name),
                             f"generate_pin_images.{name} is retired topical-sheet code")

    def test_no_module_source_still_opens_a_topical_sheet(self):
        """Parsed, not grepped. A substring search for 'open_by_key' would also
        match a comment or a docstring that merely mentions the call -- and this
        commit's own explanatory comments do exactly that. An alarm that fires on
        legitimate prose is one people learn to disable, so match the call site
        itself via the AST."""
        import ast, inspect
        for mod in (self.pp, self.g):
            tree = ast.parse(inspect.getsource(mod))
            calls = [n for n in ast.walk(tree)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "open_by_key"]
            self.assertEqual(calls, [],
                             f"{mod.__name__} still opens a spreadsheet")

    def test_the_category_labels_survive_for_board_routing(self):
        """Inverse direction. These strings are products.json category labels,
        not spreadsheet IDs; a sweep that removed them by name would remap every
        pin's category to the happypet_pin_home default, so every post would
        lose its real category board.

        Four, not six: the DOGS/CATS labels were removed from TOPICAL_EVENT on
        purpose -- a species label there is exactly what double-fired the
        species board (see TestPinBoardRule)."""
        self.assertEqual(len(self.pp.TOPICAL_EVENT), 4)
        self.assertEqual(self.pp.resolve_events("cat", "HAPPYPET_SHEET_ID_FOOD"),
                         ["happypet_pin_cats", "happypet_pin_food"])


class TestPinBoardRule(unittest.TestCase):
    """The Director's rule (2026-09-28): every post pins to its species board(s)
    -- Dog and/or Cat, both when the post covers both -- PLUS exactly one of the
    four Pet [Category] boards. A species board is never the category board.

    The defect: TOPICAL_EVENT mapped HAPPYPET_SHEET_ID_DOGS/_CATS to the species
    events, so a dog post labelled DOGS fired happypet_pin_dogs twice and reached
    one distinct board (run 36420797336).

    Expected values below are literal tables, not recomputed from post_pins'
    own maps, so a wrong map cannot agree with itself."""

    SPECIES = {
        "dog":  ["happypet_pin_dogs"],
        "cat":  ["happypet_pin_cats"],
        "both": ["happypet_pin_dogs", "happypet_pin_cats"],
    }
    CATEGORY = {
        "HAPPYPET_SHEET_ID_HOME":   "happypet_pin_home",
        "HAPPYPET_SHEET_ID_TOYS":   "happypet_pin_toys",
        "HAPPYPET_SHEET_ID_FOOD":   "happypet_pin_food",
        "HAPPYPET_SHEET_ID_HEALTH": "happypet_pin_health",
    }
    DEFAULT_CATEGORY_EVENT = "happypet_pin_home"   # Pet Home & Lifestyle

    def setUp(self):
        import post_pins as pp
        self.pp = pp
        self.logged = []
        self._p = patch.object(pp, "log", side_effect=lambda m, *a, **k: self.logged.append(m))
        self._p.start()
        self.addCleanup(self._p.stop)

    def test_every_species_by_category_pair_resolves_to_the_rule(self):
        for species, sp_events in self.SPECIES.items():
            for label, cat_event in self.CATEGORY.items():
                with self.subTest(species=species, category=label):
                    events = self.pp.resolve_events(species, label)
                    self.assertEqual(events, sp_events + [cat_event])
                    self.assertEqual(len(set(events)), len(sp_events) + 1)
                    self.assertIn(len(set(events)), (2, 3))

    def test_a_species_label_never_produces_a_second_species_event(self):
        """Remapped, not rejected: post_pins exits 0 on a failed pin, so a raise
        here would be a green-looking no-pin. The species label carries no
        category information, so it goes to the default category board and a
        WARN names the bad value."""
        cases = [
            ("dog",  "HAPPYPET_SHEET_ID_DOGS"),
            ("cat",  "HAPPYPET_SHEET_ID_CATS"),
            ("both", "HAPPYPET_SHEET_ID_DOGS"),   # best-portable-pet-playpens shape
            ("both", "HAPPYPET_SHEET_ID_CATS"),
            ("dog",  "HAPPYPET_SHEET_ID_CATS"),
            ("cat",  "HAPPYPET_SHEET_ID_DOGS"),
        ]
        for species, label in cases:
            with self.subTest(species=species, topical=label):
                self.logged.clear()
                events = self.pp.resolve_events(species, label)
                self.assertEqual(events, self.SPECIES[species] + [self.DEFAULT_CATEGORY_EVENT])
                self.assertEqual(len(events), len(set(events)), "duplicate event fired")
                self.assertTrue(any(label in m for m in self.logged),
                                "the remap must be visible in the log")

    def test_missing_or_unknown_category_label_gets_exactly_one_category(self):
        """Includes the legacy shapes seen in _pin_queue/sent/: an empty string
        and a JSON null (best-calming-treats-dogs)."""
        for topical in ("", None, "   ", "HAPPYPET_SHEET_ID_BIRDS",
                        "HAPPYPET_SHEET_ID_ FOOD", "HAPPYPET_SHEET_ID_FOODS",
                        "HAPPYPET_SHEET_ID_​FOOD"):
            with self.subTest(topical=topical):
                events = self.pp.resolve_events("cat", topical)
                self.assertEqual(events, ["happypet_pin_cats", self.DEFAULT_CATEGORY_EVENT])

    def test_case_and_edge_whitespace_variants_of_a_category_label_still_match(self):
        """A hand-edited or pasted label that differs only in case or surrounding
        whitespace names the same category; it must not fall through to Home."""
        for topical in ("happypet_sheet_id_food", "Happypet_Sheet_Id_Food",
                        " HAPPYPET_SHEET_ID_FOOD", "HAPPYPET_SHEET_ID_FOOD\t",
                        "\nhappypet_sheet_id_food\r\n", "\xa0HAPPYPET_SHEET_ID_FOOD\xa0"):
            with self.subTest(topical=topical):
                self.assertEqual(self.pp.resolve_events("cat", topical),
                                 ["happypet_pin_cats", "happypet_pin_food"])

    def test_normalising_a_species_label_does_not_reopen_species_routing(self):
        """Inverse direction: case-folding must not turn a lower-case species
        label back into a species event. It still goes to the default category."""
        for topical in ("happypet_sheet_id_dogs", " HAPPYPET_SHEET_ID_CATS ",
                        "Happypet_Sheet_Id_Dogs"):
            with self.subTest(topical=topical):
                events = self.pp.resolve_events("both", topical)
                self.assertEqual(events, ["happypet_pin_dogs", "happypet_pin_cats",
                                          self.DEFAULT_CATEGORY_EVENT])

    def test_unknown_species_still_gets_a_species_board_and_one_category(self):
        """The pre-existing happypet_pin_dogs fallback survives, re-keyed to the
        case it can still reach: a species value that is not dog/cat/both even
        after case and whitespace are normalised."""
        for species in ("", None, "   ", "bird", "dogs", "c at", "kitten"):
            with self.subTest(species=species):
                self.logged.clear()
                events = self.pp.resolve_events(species, "HAPPYPET_SHEET_ID_TOYS")
                self.assertEqual(events, ["happypet_pin_dogs", "happypet_pin_toys"])
                self.assertTrue(self.logged, "the species fallback must WARN")

    def test_case_and_edge_whitespace_variants_of_a_species_reach_its_board(self):
        """A messy species value names the same species. "Cat" must reach the Cat
        board, not the Dog fallback -- the fallback would be the wrong board, not
        just a missing one -- and a recognised species must not WARN."""
        cases = {
            "Cat": ["happypet_pin_cats"], "CAT": ["happypet_pin_cats"],
            " cat ": ["happypet_pin_cats"], "cat\n": ["happypet_pin_cats"],
            "\xa0Cat\t": ["happypet_pin_cats"],
            "Dog": ["happypet_pin_dogs"], " DOG": ["happypet_pin_dogs"],
            "Both": ["happypet_pin_dogs", "happypet_pin_cats"],
            " BOTH\r\n": ["happypet_pin_dogs", "happypet_pin_cats"],
        }
        for species, sp_events in cases.items():
            with self.subTest(species=species):
                self.logged.clear()
                events = self.pp.resolve_events(species, "HAPPYPET_SHEET_ID_TOYS")
                self.assertEqual(events, sp_events + ["happypet_pin_toys"])
                self.assertEqual(self.logged, [], "a recognised species must not WARN")

    def test_category_events_and_species_events_are_disjoint(self):
        """Structural catch-all: if no category label can ever map to a species
        event, a duplicate is impossible by construction, whatever the input."""
        species_events = {"happypet_pin_dogs", "happypet_pin_cats"}
        self.assertEqual(set(self.pp.TOPICAL_EVENT.values()) & species_events, set())
        self.assertEqual(self.pp.TOPICAL_EVENT, self.CATEGORY)

    def test_every_queued_product_resolves_to_species_plus_one_of_the_four(self):
        """products.json is what generate_posts copies into each pin-queue file,
        so a species label here is the bug waiting to fire. Every entry must name
        a real category directly -- not merely survive via the default remap."""
        products = json.loads((REPO / "products.json").read_text(encoding="utf-8"))
        for p in products:
            with self.subTest(topic=p.get("topic")):
                self.assertIn(p.get("species"), self.SPECIES)
                self.assertIn(p.get("topical_sheet"), self.CATEGORY)
                events = self.pp.resolve_events(p["species"], p["topical_sheet"])
                self.assertEqual(events, self.SPECIES[p["species"]]
                                 + [self.CATEGORY[p["topical_sheet"]]])

    def test_the_driven_main_fires_each_board_once(self):
        """Behavioural: drive the real main() over a queue file carrying the
        exact shape that double-fired in run 36420797336, and assert on what
        fire_webhook was actually handed."""
        fired = self._drive_main("dog", "HAPPYPET_SHEET_ID_DOGS")
        self.assertEqual(fired, ["happypet_pin_dogs", self.DEFAULT_CATEGORY_EVENT])

    def test_the_driven_main_routes_a_real_category_label(self):
        """The DOGS case above lands on the default board whichever field main()
        reads, so it cannot see a wiring break. A cat post labelled FOOD can:
        reading the wrong queue field, or swapping species and label, changes
        the fired list."""
        fired = self._drive_main("cat", "HAPPYPET_SHEET_ID_FOOD")
        self.assertEqual(fired, ["happypet_pin_cats", "happypet_pin_food"])

    def _drive_main(self, species, topical_sheet):
        queue = {"title": "Best Pet Thing", "article_url": "https://x/",
                 "description": "d", "image_url": "https://x/a.jpg",
                 "species": species, "slug": "t", "topical_sheet": topical_sheet}
        fired = []
        with tempfile.TemporaryDirectory() as tmp:
            qdir = Path(tmp) / "_pin_queue"
            qdir.mkdir()
            (qdir / "t.json").write_text(json.dumps(queue), encoding="utf-8")
            with patch.object(self.pp, "REPO_DIR", Path(tmp)), \
                 patch.object(self.pp, "brain_get_secret", return_value="k"), \
                 patch.object(self.pp, "check_url_live", return_value=True), \
                 patch.object(self.pp, "check_image_has_content", return_value=True), \
                 patch.object(self.pp.time, "sleep", lambda *_: None), \
                 patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
                 patch.object(self.pp, "fire_webhook",
                              side_effect=lambda ev, *a: fired.append(ev) or True), \
                 patch.object(sys, "argv", ["post_pins.py"]):
                self.pp.main()
        return fired


class TestRefillTopicalSheetIsACategory(unittest.TestCase):
    """refill_products must never seed a species label as topical_sheet."""

    FOUR = {"HAPPYPET_SHEET_ID_HOME", "HAPPYPET_SHEET_ID_TOYS",
            "HAPPYPET_SHEET_ID_FOOD", "HAPPYPET_SHEET_ID_HEALTH"}

    def test_valid_sheets_is_exactly_the_four_categories(self):
        import refill_products as rp
        self.assertEqual(set(rp.VALID_SHEETS), self.FOUR)
        self.assertEqual(len(rp.VALID_SHEETS), 4)

    def test_the_schema_enum_follows_valid_sheets(self):
        import refill_products as rp
        enum = rp.TOPIC_SCHEMA["properties"]["topics"]["items"]["properties"]["topical_sheet"]["enum"]
        self.assertEqual(set(enum), self.FOUR)

    def _ideate(self, topics):
        import refill_products as rp
        raw = json.dumps({"topics": topics})
        prompts = []
        with patch.object(rp.gp, "_call_gemini",
                          side_effect=lambda model, prompt, **k: prompts.append(prompt) or raw), \
             patch.object(rp, "log", lambda *a, **k: None):
            out = rp.ideate_topics(set(), set(), 10)
        return out, prompts[0]

    def _topic(self, slug, sheet):
        return {"topic": slug, "title": "T", "keyword": "k", "species": "dog",
                "category": "dog-toys", "topical_sheet": sheet,
                "amazon_search_query": "q"}

    def test_ideation_drops_a_species_label_the_model_returned_anyway(self):
        """The responseSchema enum is the model's constraint, not ours: a topic
        carrying a non-category label is dropped rather than seeded."""
        out, _ = self._ideate([
            self._topic("best-a", "HAPPYPET_SHEET_ID_DOGS"),
            self._topic("best-b", "HAPPYPET_SHEET_ID_CATS"),
            self._topic("best-c", "HAPPYPET_SHEET_ID_TOYS"),
            self._topic("best-d", ""),
        ])
        self.assertEqual([t["topic"] for t in out], ["best-c"])

    def test_every_category_label_survives_ideation(self):
        """Inverse direction: the filter must not eat legitimate topics."""
        labels = sorted(self.FOUR)
        out, _ = self._ideate([self._topic(f"best-x{i}", s) for i, s in enumerate(labels)])
        self.assertEqual([t["topical_sheet"] for t in out], labels)

    def test_the_prompt_names_the_four_categories_and_no_species_label(self):
        _, prompt = self._ideate([])
        for label in self.FOUR:
            self.assertIn(label, prompt)
        self.assertNotIn("HAPPYPET_SHEET_ID_DOGS", prompt)
        self.assertNotIn("HAPPYPET_SHEET_ID_CATS", prompt)
        self.assertNotIn("must fit the species", prompt)

    def test_the_prompt_files_grooming_under_health(self):
        """Director's call, 2026-09-28: grooming is Health & Wellness, not Toys."""
        _, prompt = self._ideate([])
        lines = {l.strip().split(" ", 1)[0]: l for l in prompt.splitlines()
                 if l.strip().startswith("HAPPYPET_SHEET_ID_")}
        self.assertIn("grooming", lines["HAPPYPET_SHEET_ID_HEALTH"])
        self.assertNotIn("grooming", lines["HAPPYPET_SHEET_ID_TOYS"])


class TestVaultPathSelectionGuards(unittest.TestCase):
    """The guards that mutation testing found nothing was watching.

    Three deliberate breaks left the whole suite green: weakening the 0-byte
    filter (`> 0` -> `>= 0`), dropping the explicit `service=` from the
    _VaultReader construction, and dropping `have_db` from the "is a vault
    configured here" test. Each is invisible to a behavioural test unless the
    test reaches for the specific thing the guard protects, so each is asserted
    here against the victim's own record -- whether connect() was called, which
    service name the keyring was actually asked for -- rather than against a
    message or a return value that happens to look right for another reason.
    """

    def setUp(self):
        import brain_secrets as bs
        import tempfile
        self.bs = bs
        self._orig = (bs._vault, bs._vault_tried, bs._vault_error)
        bs._vault, bs._vault_tried, bs._vault_error = None, False, None
        self.tmp = Path(tempfile.mkdtemp())
        self._env = patch.dict(os.environ, {"COGNITIVE_DB_KEY": "", "COGNITIVE_DB_PATH": ""})
        self._env.start()
        del os.environ["COGNITIVE_DB_KEY"], os.environ["COGNITIVE_DB_PATH"]
        # Same reason as TestVaultKeyResolution: unpinned, the last candidate is
        # the real vault on a developer box and these tests stop meaning what
        # they say. Pinned at class level so a new test cannot forget.
        self._default_db = patch.object(
            self.bs, "_DEFAULT_DB_PATH", str(self.tmp / "no-such-default-vault.db"))
        self._default_db.start()
        # No sibling checkout unless a test asks for one.
        self._secrets_env = patch.object(
            self.bs, "_SECRETS_ENV", self.tmp / "no-such-secrets.env")
        self._secrets_env.start()

    def tearDown(self):
        self._secrets_env.stop()
        self._default_db.stop()
        self._env.stop()
        self.bs._vault, self.bs._vault_tried, self.bs._vault_error = self._orig
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _db(self, name, content):
        p = self.tmp / name
        p.write_bytes(content)
        return p

    # --- the 0-byte guard ---------------------------------------------------

    def test_is_nonempty_file_rejects_a_zero_byte_file(self):
        """The guard in isolation, both directions plus the absent case. A
        0-byte file EXISTS, so isfile() alone says yes -- only the size test
        separates 'a vault' from 'the litter a wrong connect() left behind'."""
        empty = self._db("empty.db", b"")
        full = self._db("full.db", b"x")
        missing = self.tmp / "nope.db"
        self.assertFalse(self.bs._is_nonempty_file(str(empty)),
                         "a 0-byte file is not a database")
        self.assertTrue(self.bs._is_nonempty_file(str(full)),
                        "a file with bytes in it must still be accepted")
        self.assertFalse(self.bs._is_nonempty_file(str(missing)))
        # The inverse-direction trap: isfile() is True for the empty one, so a
        # guard that only checked existence would pass the assertion above by
        # accident. Pin the distinction itself.
        self.assertTrue(os.path.isfile(empty))

    def test_a_zero_byte_candidate_is_never_connected_to(self):
        """The point of filtering BEFORE connect(): sqlcipher3.connect() CREATES
        a missing file, so connecting to a 0-byte path is what manufactures the
        permanent fake database in the first place. Asserted on connect itself,
        not on the exception -- weakening the guard to `>= 0` still produces an
        exception here (a different one, later), so only the call record can
        tell the two apart."""
        empty = self._db("brain.db", b"")
        sqlcipher3 = types.SimpleNamespace(connect=MagicMock())
        fake = _FakeKeyring({("maeve", "COGNITIVE_DB_KEY"): "unlock-me"})
        with patch.dict(sys.modules, {"sqlcipher3": sqlcipher3, "keyring": fake}), \
             patch.dict(os.environ, {"COGNITIVE_DB_KEY": "k", "COGNITIVE_DB_PATH": str(empty)}):
            with self.assertRaises(self.bs.BrainSecretsUnavailable):
                self.bs._get_vault()
        self.assertEqual(
            sqlcipher3.connect.call_count, 0,
            "a 0-byte candidate must be rejected BEFORE connect() -- connecting "
            "is what creates/keeps the bogus file the guard exists to reject")

    def test_a_nonempty_candidate_is_still_connected_to(self):
        """The inverse direction. A guard that rejected everything would pass
        the test above and quietly disable the vault for every caller."""
        real = self._db("brain.db", b"not-really-sqlcipher-but-not-empty")
        conn = MagicMock()
        sqlcipher3 = types.SimpleNamespace(connect=MagicMock(return_value=conn))
        fake = _FakeKeyring({("maeve", "COGNITIVE_DB_KEY"): "unlock-me"})
        with patch.dict(sys.modules, {"sqlcipher3": sqlcipher3, "keyring": fake}), \
             patch.dict(os.environ, {"COGNITIVE_DB_KEY": "k", "COGNITIVE_DB_PATH": str(real)}):
            vault = self.bs._get_vault()
        self.assertIsNotNone(vault)
        sqlcipher3.connect.assert_called_once_with(str(real))

    # --- the explicit service= on _VaultReader ------------------------------

    def test_the_reader_gets_the_service_name_resolved_at_call_time(self):
        """`service=_KEYRING_SERVICE` looks redundant against the constructor's
        own default -- and it is, at production values. The difference only
        shows when the module global is redirected after import: the explicit
        argument reads it at CALL time, the default bound it at IMPORT time. Drop
        the argument and the reader silently talks to the production credential
        store while the test believes it redirected it, which is precisely the
        failure a throwaway service name exists to prevent."""
        throwaway = "maeve-test-throwaway-service"
        real = self._db("brain.db", b"not-really-sqlcipher-but-not-empty")
        conn = MagicMock()
        sqlcipher3 = types.SimpleNamespace(connect=MagicMock(return_value=conn))
        # The master key exists ONLY under the throwaway service.
        fake = _FakeKeyring({
            (throwaway, "COGNITIVE_DB_KEY"): "unlock-me",
            (throwaway, "__vault_key__"): "00" * 32,
        })
        with patch.object(self.bs, "_KEYRING_SERVICE", throwaway), \
             patch.dict(sys.modules, {"sqlcipher3": sqlcipher3, "keyring": fake}), \
             patch.dict(os.environ, {"COGNITIVE_DB_PATH": str(real)}):
            vault = self.bs._get_vault()
            self.assertIsNotNone(vault)
            self.assertEqual(
                vault._service, throwaway,
                "the reader kept the import-time default instead of the "
                "redirected service name")
            # Measured on the keyring's own record, not on the attribute: the
            # lookup that actually happens is the thing that would have hit the
            # production store.
            key = vault._master_key()
        self.assertEqual(key, bytes.fromhex("00" * 32))
        self.assertIn((throwaway, "__vault_key__"), fake.calls)
        self.assertNotIn(
            "maeve", [service for service, _ in fake.calls],
            "the production credential store was consulted during a test")

    # --- have_db as positive evidence --------------------------------------

    def test_an_on_disk_vault_alone_arms_the_loud_failure(self):
        """A host with a vault DB but no secrets.env and no key anywhere is a
        real fault, not an absent feature: somebody put a database there. Drop
        have_db from the evidence test and this silently returns None, which is
        the exact regression -- pins published with an empty audit trail -- that
        the loud error was added to stop."""
        real = self._db("brain.db", b"not-really-sqlcipher-but-not-empty")
        fake = _FakeKeyring({})
        with patch.dict(sys.modules, {"keyring": fake}), \
             patch.dict(os.environ, {"COGNITIVE_DB_PATH": str(real)}):
            with self.assertRaises(self.bs.BrainSecretsUnavailable) as caught:
                self.bs._get_vault()
        self.assertIn("COGNITIVE_DB_KEY", str(caught.exception))

    def test_no_vault_no_key_no_secrets_env_stays_a_quiet_none(self):
        """The inverse direction and the contract CI depends on: with no
        evidence of a vault at all, get_vault must stay silent so callers fall
        back to env vars. An evidence test that returned True unconditionally
        would pass the test above and break every CI run."""
        fake = _FakeKeyring({})
        with patch.dict(sys.modules, {"keyring": fake}):
            self.assertIsNone(self.bs._get_vault())

    def test_vault_is_configured_here_counts_each_signal_on_its_own(self):
        """The evidence function directly: any ONE signal is enough, and none of
        them is enough only in combination with another."""
        f = self.bs._vault_is_configured_here
        self.assertFalse(f(have_secrets_env=False, have_db=False, have_key=False))
        for signal in ("have_secrets_env", "have_db", "have_key"):
            kwargs = {"have_secrets_env": False, "have_db": False, "have_key": False}
            kwargs[signal] = True
            with self.subTest(signal=signal):
                self.assertTrue(f(**kwargs), f"{signal} alone must arm the loud failure")


class TestRetireFromProductsDurability(unittest.TestCase):
    """retire_from_products() rewrites products.json -- the pipeline's central
    state file -- from a GHA runner that can be cancelled or OOM-killed mid-step.
    It used to do a bare write_text(), which truncates the target before writing,
    so a kill inside that window left a half-written products.json that every
    later run then failed to parse. It now goes through json_io.atomic_write_json
    (temp file + os.replace) like every other writer of this file.

    These pin both halves: the observable behaviour is unchanged, and an
    interrupted write leaves the original file intact."""

    def setUp(self):
        import shutil
        import tempfile
        import push_pins_to_sheets as pk
        self.pk = pk
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.products = [
            {"topic": "best-cat-beds", "title": "Best Cat Beds", "asin": "B001"},
            {"topic": "best-dog-crates", "title": "Best Dog Crates", "asin": "B002"},
        ]
        self.path = self.tmp / "products.json"
        self.path.write_text(json.dumps(self.products, indent=2), encoding="utf-8")
        for attr, value in (("REPO_DIR", self.tmp), ("log", lambda *a, **k: None)):
            p = patch.object(self.pk, attr, value)
            p.start()
            self.addCleanup(p.stop)
        # Keep the Brain-archive side trip out of it: it is skipped unless the
        # vault DB exists, so point HOME at an empty dir rather than the real one.
        p = patch.object(Path, "home", staticmethod(lambda: self.tmp))
        p.start()
        self.addCleanup(p.stop)

    def test_retire_removes_only_the_named_slug_and_returns_the_remainder(self):
        self.assertEqual(self.pk.retire_from_products("best-cat-beds"), 1)
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([e["topic"] for e in on_disk], ["best-dog-crates"])

    def test_retire_writes_byte_for_byte_what_the_bare_write_produced(self):
        """Behaviour-preservation guard for the switch to atomic_write_json:
        2-space indent, no trailing newline. Expected value is built from the
        fixture, not by re-running the writer."""
        self.pk.retire_from_products("best-cat-beds")
        self.assertEqual(self.path.read_text(encoding="utf-8"),
                         json.dumps([self.products[1]], indent=2))

    def test_an_unknown_slug_leaves_the_file_completely_untouched(self):
        original = self.path.read_bytes()
        self.assertEqual(self.pk.retire_from_products("no-such-slug"), 2)
        self.assertEqual(self.path.read_bytes(), original)

    def test_a_write_interrupted_at_the_commit_point_never_truncates(self):
        """The regression this fix is for. os.replace is the atomic commit; a
        kill just before it must leave the ORIGINAL file whole.

        Red against the old bare write_text(): nothing raised (write_text never
        calls os.replace) and the file had already been rewritten."""
        import json_io
        original = self.path.read_bytes()
        with patch.object(json_io.os, "replace",
                          side_effect=OSError("runner cancelled mid-write")):
            with self.assertRaises(OSError):
                self.pk.retire_from_products("best-cat-beds")
        self.assertEqual(self.path.read_bytes(), original,
                         "products.json must survive an interrupted write intact")
        json.loads(self.path.read_text(encoding="utf-8"))  # and still parse
        self.assertEqual([q.name for q in self.tmp.iterdir()], ["products.json"],
                         "no scratch file may be left behind")


class _StubSheetsResponse:
    """Stands in for the requests.Response gspread.APIError is built from.

    Deliberately builds a REAL gspread.exceptions.APIError rather than a mock of
    one, so these tests are pinned to that class's actual shape (.code parsed out
    of the JSON body, .response carrying the transport status) instead of to a
    shape this file invented.
    """

    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


def _sheets_api_error(status, *, json_body=True):
    """A gspread APIError as Google actually delivers it.

    json_body=False is the HTML case: gspread's own constructor then sets
    .code = -1, and the transport status is the only place the 503 survives.
    """
    import gspread.exceptions
    payload = None
    if json_body:
        payload = {"error": {"code": status,
                             "message": "The service is currently unavailable.",
                             "status": "UNAVAILABLE"}}
    return gspread.exceptions.APIError(
        _StubSheetsResponse(status, payload, text="<html>503</html>"))


class TestSheetsApiRetry(unittest.TestCase):
    """One transient 503 from Google took down pin run 32368452299 on
    2026-08-20: gc.open_by_key() sat outside every try block, so the article's
    Facebook-queue row was never appended and the workflow never reached the step
    that retires the slug from products.json and .pending-slugs.

    sheets_retry() covers the IDEMPOTENT reads only. The append is intentionally
    left bare -- see the structural tests at the bottom."""

    def setUp(self):
        import push_pins_to_sheets as pk
        self.pk = pk
        p = patch.object(pk, "log", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    # --- status extraction ------------------------------------------------

    def test_status_comes_from_the_json_body_when_there_is_one(self):
        self.assertEqual(self.pk._api_status(_sheets_api_error(503)), 503)

    def test_status_falls_back_to_the_transport_when_the_body_is_html(self):
        """gspread sets .code = -1 on an unparseable body. Reading only .code
        would classify a real 503 as non-transient and never retry it."""
        exc = _sheets_api_error(503, json_body=False)
        self.assertEqual(exc.code, -1, "guard: gspread's own -1 sentinel")
        self.assertEqual(self.pk._api_status(exc), 503)

    def test_an_ordinary_exception_has_no_status(self):
        self.assertIsNone(self.pk._api_status(ValueError("boom")))

    # --- retry behaviour --------------------------------------------------

    def test_a_transient_503_is_retried_and_then_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _sheets_api_error(503)
            return "worksheet"

        with patch("time.sleep"):
            self.assertEqual(self.pk.sheets_retry("open_by_key", flaky), "worksheet")
        self.assertEqual(calls["n"], 3)

    def test_an_html_503_is_retried_too(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise _sheets_api_error(503, json_body=False)
            return "ok"

        with patch("time.sleep"):
            self.assertEqual(self.pk.sheets_retry("open_by_key", flaky), "ok")
        self.assertEqual(calls["n"], 2)

    def test_every_transient_code_is_retried(self):
        for status in self.pk.SHEETS_TRANSIENT_CODES:
            with self.subTest(status=status):
                calls = {"n": 0}

                def flaky():
                    calls["n"] += 1
                    if calls["n"] == 1:
                        raise _sheets_api_error(status)
                    return "ok"

                with patch("time.sleep"):
                    self.pk.sheets_retry("get_all_values", flaky)
                self.assertEqual(calls["n"], 2)

    def test_a_403_is_raised_on_the_first_attempt(self):
        """The inverse direction. A revoked service account is not transient;
        retrying it only delays the same failure and hides the real cause."""
        import gspread.exceptions
        calls = {"n": 0}

        def denied():
            calls["n"] += 1
            raise _sheets_api_error(403)

        with patch("time.sleep") as slept:
            with self.assertRaises(gspread.exceptions.APIError):
                self.pk.sheets_retry("open_by_key", denied)
        self.assertEqual(calls["n"], 1, "a 403 must not be retried")
        slept.assert_not_called()

    def test_a_non_api_exception_is_raised_on_the_first_attempt(self):
        calls = {"n": 0}

        def broken():
            calls["n"] += 1
            raise ValueError("bad argument")

        with patch("time.sleep"):
            with self.assertRaises(ValueError):
                self.pk.sheets_retry("open_by_key", broken)
        self.assertEqual(calls["n"], 1)

    def test_it_gives_up_and_reraises_after_the_configured_attempts(self):
        """A sustained outage must still surface as a failure, not spin."""
        import gspread.exceptions
        calls = {"n": 0}

        def always_down():
            calls["n"] += 1
            raise _sheets_api_error(503)

        with patch("time.sleep"):
            with self.assertRaises(gspread.exceptions.APIError):
                self.pk.sheets_retry("open_by_key", always_down)
        self.assertEqual(calls["n"], self.pk.SHEETS_MAX_RETRY)

    def test_the_backoff_is_exponential(self):
        """Expected waits are written out literally, not recomputed from the
        module's own formula -- a test that recomputes agrees with any formula."""

        def always_down():
            raise _sheets_api_error(503)

        with patch("time.sleep") as slept:
            with self.assertRaises(Exception):
                self.pk.sheets_retry("open_by_key", always_down)
        self.assertEqual([c.args[0] for c in slept.call_args_list], [2, 4])

    def test_arguments_reach_the_wrapped_call_unchanged(self):
        seen = {}

        def target(key, *, index):
            seen["args"] = (key, index)
            return "ok"

        self.assertEqual(
            self.pk.sheets_retry("open_by_key", target, "sheet-id", index=0), "ok")
        self.assertEqual(seen["args"], ("sheet-id", 0))

    # --- structural: which call sites are (and are not) wrapped -----------

    def _source(self):
        return (REPO / "push_pins_to_sheets.py").read_text(encoding="utf-8")

    def test_the_calls_that_took_the_run_down_are_wrapped(self):
        """Unit-testing the helper proves the helper works, not that it is used
        at the site that actually failed. These pin the call sites."""
        source = self._source()
        for call in ("sheets_retry('open_by_key', gc.open_by_key",
                     "sheets_retry('get_worksheet', fb_sheet.get_worksheet",
                     "sheets_retry('get_all_values', ws.get_all_values"):
            with self.subTest(call=call):
                self.assertIn(call, source)
        self.assertNotIn("\n    fb_sheet = gc.open_by_key(", source,
                         "the bare open_by_key call must be gone")

    def test_the_append_is_never_retried(self):
        """A write that fails in transit may already have landed. Retrying it can
        put a second row in the Facebook queue, which this script's in-memory
        dedup cannot see, a later run cannot undo, and which posts twice."""
        source = self._source()
        self.assertIn("fb_ws.append_row(fb_row)", source,
                      "the append must still be called directly, unwrapped")
        for line in source.splitlines():
            if "sheets_retry(" in line:
                self.assertNotIn("append_row", line,
                                 "append_row must never be handed to sheets_retry")


class TestPinJsonPayload(unittest.TestCase):
    """post_pins.py POSTs {"image_url","title","description","source_url"} to
    IFTTT's json endpoint as happypet_pinjson_<board>, and the description is the
    caption printed on the pin image.

    History: the payload used to be value1/value2/value3 with value2 = the title
    alone (a3fab21 had removed "title | pin_desc" because it overran Pinterest's
    100-char title cap), so the pin's Pinterest description was the title. The
    description now travels in its own field, so the title stays title-only.

    The caption comes from the published post's front matter, not the queue
    file: publish.yml re-renders the final pin from front matter (regen_one),
    and content sweeps edit front matter without touching queue files. Expected
    captions below are hand-copied literals from _posts/, chosen from posts whose
    queue copy has drifted, so reading the queue field instead fails them.
    """

    # Hand-copied from _posts/ front matter (the queue copies differ for the
    # first two: an em-dash in best-cat-beds, a whole rewrite in the other).
    REAL_CAPTIONS = {
        "best-cat-beds":
            "Cozy indoor cats swear by these lounge-worthy beds. Find the "
            "perfect spot for your whiskered napper!",
        "best-dog-joint-supplements":
            "4 hip and joint supplements for dogs compared, led by an omega-3, "
            "the ingredient the research backs, plus what the studies do and "
            "don't show.",
        "best-puppy-food":
            "Discover the top puppy food that fuels growth without breaking the bank.",
    }

    JSON_EVENT = {
        "happypet_pin_dogs":   "happypet_pinjson_dogs",
        "happypet_pin_cats":   "happypet_pinjson_cats",
        "happypet_pin_food":   "happypet_pinjson_food",
        "happypet_pin_health": "happypet_pinjson_health",
        "happypet_pin_home":   "happypet_pinjson_home",
        "happypet_pin_toys":   "happypet_pinjson_toys",
    }

    def setUp(self):
        import post_pins as pp
        self.pp = pp

    def _source(self):
        return (REPO / "post_pins.py").read_text(encoding="utf-8")

    def _drive_main(self, slug, queue_overrides=None, fired=()):
        """Run the real main() over one queue file in a scratch repo dir with the
        network and git stubbed. Returns [(event, payload)] handed to fire_webhook
        and the names of the .fired files left behind."""
        queue = {"title": "Best Pet Thing", "article_url": "https://x/a/",
                 "description": "queue copy of the caption",
                 "image_url": "https://x/a.jpg", "species": "dog", "slug": slug,
                 "topical_sheet": "HAPPYPET_SHEET_ID_FOOD"}
        queue.update(queue_overrides or {})
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            qdir = Path(tmp) / "_pin_queue"
            (qdir / ".fired").mkdir(parents=True)
            (qdir / f"{slug}.json").write_text(json.dumps(queue), encoding="utf-8")
            for name in fired:
                (qdir / ".fired" / name).write_text("t", encoding="utf-8")
            # An empty no-repeat ledger: main() refuses to run without one once
            # sentinels exist.
            (qdir / ".fired" / self.pp.LEDGER_NAME).write_text("{}", encoding="utf-8")
            with patch.object(self.pp, "REPO_DIR", Path(tmp)), \
                 patch.object(self.pp, "LOG_PATH", Path(tmp) / "t.log"), \
                 patch.object(self.pp, "brain_get_secret", return_value="k"), \
                 patch.object(self.pp, "check_url_live", return_value=True), \
                 patch.object(self.pp, "check_image_has_content", return_value=True), \
                 patch.object(self.pp.time, "sleep", lambda *_: None), \
                 patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
                 patch.object(self.pp, "fire_webhook",
                              side_effect=lambda ev, payload, key: calls.append((ev, payload)) or True), \
                 patch.object(sys, "argv", ["post_pins.py"]):
                self.pp.main()
            left = sorted(p.name for p in (qdir / ".fired").iterdir())
        return calls, left

    # --- event mapping -------------------------------------------------------

    def test_json_event_table_is_exactly_the_six_boards(self):
        self.assertEqual(self.pp.JSON_EVENT, self.JSON_EVENT)

    def test_every_event_resolve_events_can_return_has_a_json_event(self):
        for species in ("dog", "cat", "both", "unknown"):
            for label in list(self.pp.TOPICAL_EVENT) + ["", "HAPPYPET_SHEET_ID_DOGS"]:
                for ev in self.pp.resolve_events(species, label):
                    self.assertIn(ev, self.JSON_EVENT)

    def test_fire_webhook_posts_json_to_the_json_endpoint(self):
        seen = {}

        def fake_post(url, body, headers, *, label):
            seen.update(url=url, body=body, headers=headers)
            return "Congratulations!"
        payload = self.pp.build_payload("https://x/a.jpg", "T", "D", "https://x/a/")
        with patch.object(self.pp, "http_post", side_effect=fake_post), \
             patch.object(self.pp, "log"):
            self.assertTrue(self.pp.fire_webhook("happypet_pin_food", payload, "KEY123"))
        self.assertEqual(seen["url"],
                         "https://maker.ifttt.com/trigger/happypet_pinjson_food/json/with/key/KEY123")
        self.assertEqual(seen["headers"], {"Content-Type": "application/json"})
        self.assertEqual(json.loads(seen["body"]),
                         {"image_url": "https://x/a.jpg", "title": "T",
                          "description": "D", "source_url": "https://x/a/"})

    def test_fire_webhook_refuses_an_unmapped_event(self):
        with patch.object(self.pp, "http_post") as hp:
            with self.assertRaises(KeyError):
                self.pp.fire_webhook("happypet_pin_birds", {}, "k")
            hp.assert_not_called()

    def test_the_key_never_reaches_the_log(self):
        lines = []
        payload = self.pp.build_payload("i", "t", "d", "s")
        for outcome in ("ok", RuntimeError("Maker HTTP 401: nope")):
            side = (lambda *a, **k: "ok") if outcome == "ok" else outcome
            with patch.object(self.pp, "http_post", side_effect=side), \
                 patch.object(self.pp, "log", side_effect=lambda m, *a: lines.append(m)):
                self.pp.fire_webhook("happypet_pin_dogs", payload, "SEKRETKEY99")
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertNotIn("SEKRETKEY99", line)

    # --- the description is the pin-image caption ----------------------------

    def test_description_is_the_front_matter_caption_for_real_posts(self):
        for slug, caption in self.REAL_CAPTIONS.items():
            with self.subTest(slug=slug):
                calls, _ = self._drive_main(slug)
                self.assertTrue(calls, "no webhook fired - the test proved nothing")
                for _, payload in calls:
                    self.assertEqual(payload["description"], caption)

    def test_drifted_queue_copy_is_not_used(self):
        """best-cat-beds' real queue file carries an em-dash the image doesn't."""
        calls, _ = self._drive_main("best-cat-beds", {
            "description": "Cozy indoor cats swear by these lounge-worthy beds"
                           "—find the perfect spot for your whiskered napper!"})
        for _, payload in calls:
            self.assertNotIn("—", payload["description"])

    def test_an_unpublished_slug_falls_back_to_the_queue_description(self):
        calls, _ = self._drive_main("no-such-post-xyz")
        self.assertTrue(calls)
        for _, payload in calls:
            self.assertEqual(payload["description"], "queue copy of the caption")

    def test_payload_matches_what_the_renderer_draws(self):
        """Independent of the front-matter parse: re-render real pins through
        regen_one (photo fetch stubbed, output to a scratch dir) and capture the
        lines make_pin wraps for the description -- the caption on the image."""
        import generate_pin_images as g
        if not g.PIL_AVAILABLE:
            self.skipTest("Pillow not installed")
        wrapped = []
        real_wrap = g.wrap_text

        def spy(draw, text, font, max_width):
            lines = real_wrap(draw, text, font, max_width)
            wrapped.append((text, lines))
            return lines
        for slug in self.REAL_CAPTIONS:
            with self.subTest(slug=slug), tempfile.TemporaryDirectory() as tmp:
                wrapped.clear()
                with patch.object(g, "wrap_text", side_effect=spy), \
                     patch.object(g, "fetch_image", return_value=None), \
                     patch.object(g, "PINS_DIR", Path(tmp)), \
                     patch.object(g, "log_pin"):
                    g.regen_one(slug, strict=False)
                # make_pin wraps the title first, then the description, and draws
                # at most three description lines.
                drawn = " ".join(wrapped[1][1][:3])
                calls, _ = self._drive_main(slug)
                self.assertEqual(calls[0][1]["description"], drawn)

    def test_sample_captions_fit_on_the_image_whole(self):
        """make_pin draws only the first three wrapped lines, so for these posts
        the full payload description is exactly the image text. Scoped to the
        literal sample, not every post: caption length is bounded only by the
        writer prompt ("max 20 words"), and one long LLM caption must not turn
        main red -- a four-line caption only clips the image."""
        import generate_pin_images as g
        if not g.PIL_AVAILABLE:
            self.skipTest("Pillow not installed")
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGB", (1000, 1500)))
        font = g.get_font("Nunito-Bold.ttf", 36)
        for slug, caption in self.REAL_CAPTIONS.items():
            with self.subTest(slug=slug):
                self.assertLessEqual(len(g.wrap_text(draw, caption, font, 900)), 3)

    # --- title / caps --------------------------------------------------------

    def test_title_field_is_the_title_only(self):
        calls, _ = self._drive_main("best-puppy-food",
                                    {"title": "Best Puppy Food for Healthy Growth"})
        for _, payload in calls:
            self.assertEqual(payload["title"], "Best Puppy Food for Healthy Growth")
            self.assertNotIn("|", payload["title"])
            self.assertEqual(set(payload), {"image_url", "title", "description", "source_url"})

    def test_a_long_title_is_capped_at_100_on_a_word_boundary(self):
        long_title = "Best " + ("Extremely Durable " * 12) + "Dog Toy"
        self.assertGreater(len(long_title), 100)
        calls, _ = self._drive_main("y", {"title": long_title})
        # 100 chars ends mid-"Extremely"; the cut backs off to the word before it.
        self.assertEqual(calls[0][1]["title"],
                         "Best Extremely Durable Extremely Durable Extremely Durable "
                         "Extremely Durable Extremely Durable")

    def test_cap_text_cases(self):
        cap = self.pp.cap_text
        self.assertEqual(cap("short", 100), "short")
        self.assertEqual(cap("abc def", 7), "abc def")          # exactly at the cap
        self.assertEqual(cap("abc defgh", 7), "abc")            # cut mid-word backs off
        self.assertEqual(cap("abc def ghi", 7), "abc def")      # cut lands on a space
        self.assertEqual(cap("abcdefghij", 4), "abcd")          # one long word: hard cut
        self.assertEqual(cap(None, 5), "")
        long_desc = ("word " * 300).strip()                     # 1499 chars
        out = cap(long_desc, 800)
        self.assertLessEqual(len(out), 800)
        self.assertTrue(long_desc.startswith(out))
        self.assertEqual(out.split(), ["word"] * len(out.split()))

    def test_long_description_is_capped_at_800(self):
        calls, _ = self._drive_main("no-such-post-xyz", {"description": "lorem " * 200})
        self.assertLessEqual(len(calls[0][1]["description"]), 800)
        self.assertTrue(calls[0][1]["description"].endswith("lorem"))

    # --- boards and dedup unchanged -------------------------------------------

    def test_boards_and_sentinels_keep_the_board_names(self):
        calls, left = self._drive_main("t", {"species": "both"})
        self.assertEqual([ev for ev, _ in calls],
                         ["happypet_pin_dogs", "happypet_pin_cats", "happypet_pin_food"])
        # LEDGER_NAME is the no-repeat ledger (pin_ledger.py), kept beside the sentinels.
        self.assertEqual(left, [self.pp.LEDGER_NAME, "t.fired", "t.happypet_pin_cats.fired",
                                "t.happypet_pin_dogs.fired", "t.happypet_pin_food.fired"])

    def test_a_half_fired_slug_does_not_re_pin_its_finished_boards(self):
        calls, _ = self._drive_main("t", {"species": "both"},
                                    fired=["t.happypet_pin_dogs.fired"])
        self.assertEqual([ev for ev, _ in calls], ["happypet_pin_cats", "happypet_pin_food"])

    def test_an_all_events_sentinel_still_skips_the_slug(self):
        calls, _ = self._drive_main("t", fired=["t.fired"])
        self.assertEqual(calls, [])

    def test_the_docstring_matches_the_code(self):
        doc = self._source().split('"""')[1]
        self.assertNotIn("value2", doc)
        self.assertIn('{"image_url", "title", "description", "source_url"}', doc)
        self.assertIn("happypet_pinjson_", doc)


class TestPinSenderKeyMasking(unittest.TestCase):
    """The IFTTT Maker key rides in the request URL, and exception text often
    repeats the URL. Whatever failed, the key must not reach stdout, stderr or the
    log file. Each case raises the way the real stack does, from the URL the code
    actually built, so the key is genuinely present in the exception to be caught."""

    KEYS = ("FAKEKEY-9f3c1d7a2b", "ab cd/ef+gh&ij")   # plain, and one needing URL-quoting

    def setUp(self):
        import post_pins as pp
        self.pp = pp
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = Path(self.tmp.name) / "t.log"
        for p in (patch.object(pp, "LOG_PATH", self.log_path),
                  patch.object(pp, "_SECRETS", set()),
                  patch.object(pp.time, "sleep", lambda *_: None)):
            p.start()
            self.addCleanup(p.stop)

    def _fire(self, key, make_exc):
        """fire_webhook with urlopen raising make_exc(req) on every call.
        Returns (ok, stdout, stderr, log file text)."""
        import io
        payload = self.pp.build_payload("https://x/a.jpg", "T", "D", "https://x/a/")
        out, err = io.StringIO(), io.StringIO()

        def boom(req, timeout=None):
            raise make_exc(req)
        self.log_path.write_text("", encoding="utf-8")
        with patch("urllib.request.urlopen", side_effect=boom), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            ok = self.pp.fire_webhook("happypet_pin_food", payload, key)
        logged = self.log_path.read_text(encoding="utf-8")
        return ok, out.getvalue(), err.getvalue(), logged

    def _assert_clean(self, key, ok, out, err, logged):
        import urllib.parse
        self.assertFalse(ok)
        self.assertIn("FAIL", logged, "no failure was logged - the test proved nothing")
        for form in (key, urllib.parse.quote(key), urllib.parse.quote(key, safe="")):
            for name, text in (("stdout", out), ("stderr", err), ("log file", logged)):
                self.assertNotIn(form, text, f"key leaked into {name}")

    def test_http_4xx_whose_body_echoes_the_url(self):
        import io, urllib.error
        for key in self.KEYS:
            with self.subTest(key=key):
                ok, out, err, logged = self._fire(key, lambda req: urllib.error.HTTPError(
                    req.full_url, 401, "Unauthorized", {},
                    io.BytesIO(f"invalid key in {req.full_url}".encode())))
                self._assert_clean(key, ok, out, err, logged)
                self.assertIn("HTTP 401", logged)

    def test_http_5xx_retried_then_exhausted(self):
        import io, urllib.error
        for key in self.KEYS:
            with self.subTest(key=key):
                ok, out, err, logged = self._fire(key, lambda req: urllib.error.HTTPError(
                    req.full_url, 503, "Unavailable", {},
                    io.BytesIO(f"upstream said {req.full_url}".encode())))
                self._assert_clean(key, ok, out, err, logged)
                self.assertIn("HTTP 503 attempt 1/", logged)
                self.assertIn("exhausted", logged)

    def test_connection_error_whose_reason_repeats_the_url(self):
        import urllib.error
        reasons = (lambda u: OSError(f"cannot reach {u}"), lambda u: f"cannot reach {u}")
        for key in self.KEYS:
            for reason in reasons:
                with self.subTest(key=key):
                    ok, out, err, logged = self._fire(
                        key, lambda req: urllib.error.URLError(reason(req.full_url)))
                    self._assert_clean(key, ok, out, err, logged)
                    self.assertIn("network error attempt 1", logged)

    def test_timeout_raised_mid_read(self):
        for key in self.KEYS:
            with self.subTest(key=key):
                ok, out, err, logged = self._fire(
                    key, lambda req: TimeoutError(f"timed out reading {req.full_url}"))
                self._assert_clean(key, ok, out, err, logged)
                self.assertIn("TimeoutError", logged)

    def test_invalid_url_error_quotes_the_whole_path(self):
        import http.client
        for key in self.KEYS:
            with self.subTest(key=key):
                ok, out, err, logged = self._fire(key, lambda req: http.client.InvalidURL(
                    f"URL can't contain control characters. {req.selector!r} (found at least ' ')"))
                self._assert_clean(key, ok, out, err, logged)
                self.assertIn("InvalidURL", logged)

    def test_any_other_exception_is_logged_by_type_only(self):
        ok, out, err, logged = self._fire(
            self.KEYS[0], lambda req: ValueError(f"unknown url type: {req.full_url}"))
        self._assert_clean(self.KEYS[0], ok, out, err, logged)
        self.assertIn("ValueError", logged)

    def test_log_redacts_the_key_url_shape_even_if_the_key_was_never_registered(self):
        self.pp.log("boom https://maker.ifttt.com/trigger/e/json/with/key/NEVERREGISTERED123 end")
        logged = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn("NEVERREGISTERED123", logged)
        self.assertIn("/key/<redacted> end", logged)

    def test_log_redacts_a_registered_key_wherever_it_appears(self):
        import urllib.parse
        key = self.KEYS[1]
        self.pp.register_secret(key)
        self.pp.log("FAIL: x.json -- " + str(ValueError("bad " + key)) + " / "
                    + urllib.parse.quote(key) + " / " + urllib.parse.quote(key, safe=""))
        logged = self.log_path.read_text(encoding="utf-8")
        for form in (key, urllib.parse.quote(key), urllib.parse.quote(key, safe="")):
            self.assertNotIn(form, logged)
        self.assertEqual(logged.count("<redacted>"), 3)

    def test_ordinary_log_lines_are_untouched(self):
        """The inverse: nothing registered, so a normal line must pass through
        byte for byte, including words that merely contain 'key'."""
        line = "PIN [best-dog-keys] -> ['happypet_pin_dogs'] url: https://x/posts/keyboard-cat/"
        self.pp.log(line)
        self.assertIn(line, self.log_path.read_text(encoding="utf-8"))

    def test_main_registers_the_key_so_an_unexpected_failure_line_is_scrubbed(self):
        """main() logs `FAIL: <file> -- <exc>` for anything unforeseen. Here a stubbed
        step raises with the real key in its message; the line must come out clean."""
        key = self.KEYS[0]
        queue = {"title": "T", "article_url": "https://x/a/", "description": "d",
                 "image_url": "https://x/a.jpg", "species": "dog", "slug": "s1",
                 "topical_sheet": "HAPPYPET_SHEET_ID_FOOD"}
        qdir = Path(self.tmp.name) / "_pin_queue"
        qdir.mkdir()
        (qdir / "s1.json").write_text(json.dumps(queue), encoding="utf-8")
        with patch.object(self.pp, "REPO_DIR", Path(self.tmp.name)),              patch.object(self.pp, "brain_get_secret", return_value=key),              patch.object(self.pp, "load_pin_captions", return_value={"s1": "cap"}),              patch.object(self.pp, "check_url_live",
                          side_effect=RuntimeError("proxy rewrote " + key)),              patch.object(sys, "argv", ["post_pins.py"]):
            with self.assertRaises(SystemExit):
                self.pp.main()
        logged = self.log_path.read_text(encoding="utf-8")
        self.assertIn("FAIL: s1.json", logged)
        self.assertNotIn(key, logged)

    def test_an_empty_key_is_not_registered(self):
        """str.replace('', ...) would insert a marker between every character."""
        self.pp.register_secret("")
        self.pp.log("hello")
        self.assertIn("  hello", self.log_path.read_text(encoding="utf-8"))
        self.assertEqual(self.pp._SECRETS, set())


class TestPinSenderLogEncoding(unittest.TestCase):
    """A title with an em dash or emoji crashed the pin sender's logging under a
    Windows cp1252 console (print() raised UnicodeEncodeError; the log file was
    opened in the locale encoding). Both senders share the shape."""

    TITLE = "Best Pet Beds — Cozy \U0001f43e Naps ☃"

    def _modules(self):
        import post_pins, push_pins_to_sheets
        return (("post_pins", post_pins), ("push_pins_to_sheets", push_pins_to_sheets))

    def _log_to(self, module, console):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.log"
            with patch.object(module, "LOG_PATH", path), patch.object(sys, "stdout", console):
                module.log(f"title: {self.TITLE}")
            return path.read_text(encoding="utf-8")

    def test_cp1252_console_does_not_crash_and_the_file_keeps_the_text(self):
        import io
        for name, module in self._modules():
            with self.subTest(module=name):
                raw = io.BytesIO()
                console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict",
                                           write_through=True)
                logged = self._log_to(module, console)       # must not raise
                self.assertIn(self.TITLE, logged)            # file is lossless utf-8
                shown = raw.getvalue().decode("cp1252")
                self.assertIn("Best Pet Beds — Cozy ? Naps ?", shown)   # replaced, not dropped

    def test_utf8_console_output_is_unchanged(self):
        """Linux CI: the line goes out exactly as before, no replacement."""
        import io
        for name, module in self._modules():
            with self.subTest(module=name):
                raw = io.BytesIO()
                console = io.TextIOWrapper(raw, encoding="utf-8", write_through=True)
                self._log_to(module, console)
                self.assertIn(self.TITLE, raw.getvalue().decode("utf-8"))


class TestProductReviewStructuredData(unittest.TestCase):
    """post.html emits Product/Review JSON-LD so review rich results (the star
    rating under a search listing) can show. These check the block against the
    real front matter of the real posts.

    HONEST LIMIT, stated so nobody reads a green run as more than it is: there
    is no Ruby or Jekyll on the build box, so this does NOT prove Jekyll renders
    the template. It renders the template TEXT LIFTED OUT OF post.html through a
    deliberately tiny Liquid subset covering only the constructs that block
    uses. A Liquid syntax error Jekyll would reject can still slip past. The
    only real build is the deploy workflow's `bundle exec jekyll build`, which
    runs on a push to main.

    What it does prove, which is most of the risk: the emitted text is valid
    JSON, the values come from the post's own front matter, the schema.org
    shape is right, and the block appears on exactly the single-product reviews.
    """

    SINGLE_PRODUCT = 8  # posts carrying both product_name and rating

    @classmethod
    def setUpClass(cls):
        cls.layout = (REPO / "_layouts" / "post.html").read_text(encoding="utf-8")
        cls.posts = {}
        for p in sorted((REPO / "_posts").glob("*.md")):
            raw = p.read_text(encoding="utf-8")
            fm = {}
            for line in raw.split("---", 2)[1].splitlines():
                m = re.match(r"^([a-z_]+):\s*(.+)$", line)
                if m:
                    fm[m.group(1)] = m.group(2).strip().strip('"')
            fm["url"] = "/x/" + p.stem + "/"
            cls.posts[p.name] = fm

    # -- a Liquid subset: only what the JSON-LD block uses ------------------
    def _render(self, template, page):
        site = {"title": "Happy Pet Product Reviews",
                "author": "Happy Pet Product Reviews Team"}
        # The layout assigns hero_image above this block: the self-hosted
        # thumbnail when one exists, else page.image. Only the else-branch is
        # modelled here -- site.static_files needs a real build to resolve.
        local = {"hero_image": page.get("image")}

        def lookup(token):
            token = token.strip()
            if token.startswith(("'", '"')):
                return token.strip("'\"")
            if token in local:
                return local[token]
            scope, _, key = token.partition(".")
            return {"page": page, "site": site}.get(scope, {}).get(key)

        def expr(body):
            parts = [p.strip() for p in body.split("|")]
            val = lookup(parts[0])
            for filt in parts[1:]:
                name, _, arg = filt.partition(":")
                name, arg = name.strip(), arg.strip()
                if name == "default":
                    val = val if val else lookup(arg)
                elif name == "absolute_url":
                    val = _jekyll_absolute_url(val)
                elif name == "date":
                    val = str(val)          # already YYYY-MM-DD in front matter
                elif name == "jsonify":
                    if isinstance(val, str) and re.fullmatch(r"-?\d+(\.\d+)?", val):
                        val = json.dumps(float(val) if "." in val else int(val))
                    else:
                        val = json.dumps(val)
                else:
                    raise AssertionError(f"unhandled Liquid filter {name!r}")
            return "" if val is None else str(val)

        def cond(m):
            test = m.group(1)
            op = " or " if " or " in test else " and "
            vals = [bool(lookup(t)) for t in test.split(op)]
            keep = any(vals) if op == " or " else all(vals)
            return m.group(2) if keep else ""

        # Resolve the INNERMOST if first (a body containing no further `if`),
        # looping outwards. A single non-greedy pass pairs the outer `if` with
        # the inner `endif` and silently truncates the block.
        innermost = re.compile(
            r"\{%-?\s*if ([^%]+?)\s*-?%\}((?:(?!\{%-?\s*if ).)*?)\{%-?\s*endif\s*-?%\}", re.S)
        out, guard = template, 0
        while innermost.search(out):
            out, guard = innermost.sub(cond, out), guard + 1
            assert guard < 20, "runaway if-resolution"
        out = re.sub(r"\{%-?\s*comment\s*-?%\}.*?\{%-?\s*endcomment\s*-?%\}", "", out, flags=re.S)

        def do_assign(m):
            local[m.group(1)] = expr(m.group(2))
            return ""
        out = re.sub(r"\{%-?\s*assign (\w+) = ([^%]+?)\s*-?%\}", do_assign, out)
        return re.sub(r"\{\{(.+?)\}\}", lambda m: expr(m.group(1)), out, flags=re.S)

    def _uncommented_layout(self):
        return re.sub(r"\{%-?\s*comment\s*-?%\}.*?\{%-?\s*endcomment\s*-?%\}",
                      "", self.layout, flags=re.S)

    def _block(self):
        start = self.layout.find("{%- if page.product_name and page.rating -%}")
        end = self.layout.find("<article class=\"piece\">")
        self.assertNotEqual(start, -1, "the Product/Review block is gone from post.html")
        self.assertGreater(end, start, "the block must sit above the article markup")
        return self.layout[start:end]

    def _qualifying(self):
        return {n: fm for n, fm in self.posts.items()
                if fm.get("product_name") and fm.get("rating")}

    def test_block_is_emitted_on_exactly_the_single_product_reviews(self):
        q = self._qualifying()
        self.assertEqual(len(q), self.SINGLE_PRODUCT,
                         f"expected {self.SINGLE_PRODUCT} single-product posts, got {sorted(q)}")
        # A roundup must never carry the fields: it reviews several products and
        # has no single Product to name.
        for name, fm in self.posts.items():
            if name in q:
                continue
            with self.subTest(post=name):
                self.assertNotIn("product_name", fm)
                self.assertNotIn("rating", fm)

    def test_every_emitted_block_is_valid_json_with_the_right_values(self):
        block = self._block()
        for name, fm in self._qualifying().items():
            with self.subTest(post=name):
                rendered = self._render(block, fm)
                payload = re.search(r"<script type=\"application/ld\+json\">(.*?)</script>",
                                    rendered, re.S)
                self.assertIsNotNone(payload, "no ld+json script rendered")
                data = json.loads(payload.group(1))   # raises on malformed JSON

                self.assertEqual(data["@context"], "https://schema.org")
                self.assertEqual(data["@type"], "Product")
                # Product name is the PRODUCT, never the article headline.
                self.assertEqual(data["name"], fm["product_name"])
                self.assertNotIn("Review", data["name"].split()[-1:] or [""])
                self.assertTrue(data["image"].startswith("http"))

                review = data["review"]
                self.assertEqual(review["@type"], "Review")
                self.assertEqual(review["name"], fm["title"])
                rating = review["reviewRating"]
                self.assertEqual(rating["@type"], "Rating")
                self.assertEqual(float(rating["ratingValue"]), float(fm["rating"]))
                self.assertEqual(rating["bestRating"], 5)
                self.assertGreaterEqual(float(rating["ratingValue"]), rating["worstRating"])
                self.assertLessEqual(float(rating["ratingValue"]), rating["bestRating"])
                for actor in ("author", "publisher"):
                    self.assertTrue(review[actor]["name"], f"{actor} name is empty")
                # Search Console flagged the old price-less Offer (2026-10-15).
                self.assertNotIn("offers", data)

    def test_no_offers_or_price_is_emitted(self):
        """We are an affiliate site, not the seller: the shopper buys at the
        retailer, so Merchant listings do not apply, and an Offer without a
        price fails Search Console validation. A review alone keeps the page
        eligible for Product snippets. Checked against the JSON-LD block with
        Liquid comments stripped, so the note explaining the decision does not
        itself trip the guard. Matched case-insensitively, so lowPrice,
        priceCurrency, OfferShippingDetails and MerchantReturnPolicy are caught
        along with brand and gtin. JSON \\u escapes are decoded first, since a
        JSON reader takes "\\u006fffers" as "offers"."""
        emitted = re.sub(r"\{%-?\s*comment\s*-?%\}.*?\{%-?\s*endcomment\s*-?%\}",
                         "", self._block(), flags=re.S)
        emitted = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)),
                         emitted).lower()
        for needle in ("offer", "price", "brand", "gtin", "shipping", "returnpolicy"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, emitted)

    def test_no_aggregate_rating_is_invented(self):
        """AggregateRating means an average of many ratings. This repo holds no
        such figure for a published post, and Google requires a rating shown in
        a rich result to be visible on the page. Emitting one would be
        fabricated review data on an affiliate site. Checked against the layout
        with Liquid comments stripped, so the note explaining the decision does
        not itself trip the guard."""
        emitted = self._uncommented_layout()
        self.assertNotIn("AggregateRating", emitted)
        self.assertNotIn("aggregateRating", emitted)

    def test_the_seo_plugin_block_is_left_alone(self):
        """The new script is additive. jekyll-seo-tag still owns its own
        BlogPosting/WebSite block, emitted from default.html, untouched."""
        default = (REPO / "_layouts" / "default.html").read_text(encoding="utf-8")
        self.assertIn("{% seo", default)
        self.assertNotIn("{% seo", self.layout)
        self.assertIn("jekyll-seo-tag", (REPO / "_config.yml").read_text(encoding="utf-8"))

    def test_front_matter_rating_matches_the_rating_printed_in_the_body(self):
        """Structured data that disagrees with the visible page is exactly what
        Google penalises, so the two can never drift apart silently."""
        for p in sorted((REPO / "_posts").glob("*.md")):
            raw = p.read_text(encoding="utf-8")
            body = raw.split("---", 2)[2]
            shown = re.search(r"\*\*(?:Our )?Rating:\s*([0-9.]+)/5\*\*", body)
            declared = self.posts[p.name].get("rating")
            with self.subTest(post=p.name):
                if shown:
                    self.assertIsNotNone(declared, "body shows a rating, front matter has none")
                    self.assertEqual(float(declared), float(shown.group(1)))
                else:
                    self.assertIsNone(declared, "front matter rating with nothing shown on the page")


class TestFaqStructuredData(unittest.TestCase):
    """post.html renders a post's `faq:` list twice: once as visible
    <details>/<summary> items under "Common questions", and once as
    FAQPage JSON-LD. The two must say the same thing word for word, because
    structured data that disagrees with the visible page is what Google's
    rules forbid. These render the layout's FAQ block for each post and
    compare the two halves.

    HONEST LIMIT, the same one TestProductReviewStructuredData states: there
    is no Ruby or Jekyll on the build box, so this does NOT prove Jekyll
    renders the template. It renders the FAQ block's text lifted out of
    post.html through a tiny Liquid subset (one for loop, escape, jsonify,
    unless forloop.last). Any other Liquid construct in the block fails the
    test rather than being skipped. The real build is the build-check and
    deploy workflows' `bundle exec jekyll build`.

    Front matter is read with a narrow parser for the fixed shape the five
    posts use (`answer:` and `faq:` with `- q:` / `a:` double-quoted strings),
    since PyYAML is not a dependency. A double-quoted YAML string with no
    backslash reads the same as a JSON string, so each value goes through
    json.loads, and a backslash fails the test instead of being guessed at.
    """

    # Section A of the 2026-10-27 FAQ change. Section B posts are a comparison
    # group and must stay without a faq: until that read is done.
    FAQ_POSTS = {
        "2026-04-23-best-dog-joint-supplements.md",
        "2026-04-04-best-cat-litter-odor-control.md",
        "2026-04-27-best-kitten-food.md",
        "2026-08-24-best-cat-carrier-backpacks.md",
        "2026-04-07-best-grain-free-cat-food.md",
    }
    GUARD = "{%- if page.faq %}"

    @staticmethod
    def _yaml_string(token):
        assert token.startswith('"') and token.endswith('"'), token
        assert "\\" not in token, f"backslash in front matter string: {token}"
        return json.loads(token)

    @classmethod
    def setUpClass(cls):
        cls.layout = (REPO / "_layouts" / "post.html").read_text(encoding="utf-8")
        cls.posts = {}
        for p in sorted((REPO / "_posts").glob("*.md")):
            raw = p.read_text(encoding="utf-8")
            fm = raw.split("---", 2)[1]
            body = raw.split("---", 2)[2]
            entry = {"body": body, "answer": None, "faq": None}
            m = re.search(r"^answer:\s*(.+)$", fm, re.M)
            if m:
                entry["answer"] = cls._yaml_string(m.group(1).strip())
            block = re.search(r"^faq:\s*\n((?:[ \t]+.*\n?)*)", fm, re.M)
            if block:
                lines = [ln for ln in block.group(1).splitlines() if ln.strip()]
                items = []
                for ln in lines:
                    q = re.fullmatch(r"\s+- q:\s*(.+)", ln)
                    a = re.fullmatch(r"\s+a:\s*(.+)", ln)
                    if q:
                        items.append({"q": cls._yaml_string(q.group(1).strip())})
                    elif a and items and "a" not in items[-1]:
                        items[-1]["a"] = cls._yaml_string(a.group(1).strip())
                    else:
                        raise AssertionError(f"{p.name}: unexpected faq line {ln!r}")
                entry["faq"] = items
            cls.posts[p.name] = entry
        assert cls.posts, "no posts found - the guard would pass vacuously"

    # -- the FAQ block and a Liquid subset for it -----------------------------
    def _faq_block(self):
        start = self.layout.find(self.GUARD)
        self.assertNotEqual(start, -1, "the FAQ block is gone from post.html")
        end = self.layout.find("{%- endif %}", start)
        self.assertGreater(end, start)
        return self.layout[start:end + len("{%- endif %}")]

    @staticmethod
    def _liquid_escape(s):
        # Liquid's `escape`: & < > " ' become entities.
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                 .replace('"', "&quot;").replace("'", "&#39;"))

    def _render(self, faq):
        block = self._faq_block()
        inner = block[len(self.GUARD):-len("{%- endif %}")]

        def loop(m):
            out = []
            for i, item in enumerate(faq):
                part = m.group(1)
                part = re.sub(r"\{%-?\s*unless forloop\.last\s*-?%\}(.*?)\{%-?\s*endunless\s*-?%\}",
                              lambda u: "" if i == len(faq) - 1 else u.group(1), part, flags=re.S)

                def var(v):
                    key, filt = v.group(1), v.group(2)
                    value = item[key]
                    if filt == "escape":
                        return self._liquid_escape(value)
                    if filt == "jsonify":
                        return json.dumps(value)
                    raise AssertionError(f"unhandled filter {filt!r}")
                part = re.sub(r"\{\{\s*item\.(q|a)\s*\|\s*(\w+)\s*\}\}", var, part)
                out.append(part)
            return "".join(out)

        out = re.sub(r"\{%-?\s*for item in page\.faq\s*-?%\}(.*?)\{%-?\s*endfor\s*-?%\}",
                     loop, inner, flags=re.S)
        self.assertNotIn("{{", out, "unrendered Liquid output tag in the FAQ block")
        self.assertNotIn("{%", out, "unrendered Liquid tag in the FAQ block")
        return out

    @staticmethod
    def _visible(rendered):
        from html.parser import HTMLParser

        class Collect(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.summaries, self.answers, self.details = [], [], 0
                self._tag, self._buf = None, []

            def handle_starttag(self, tag, attrs):
                if tag == "details":
                    self.details += 1
                if tag in ("summary", "p"):
                    self._tag, self._buf = tag, []

            def handle_endtag(self, tag):
                if tag == self._tag:
                    text = "".join(self._buf)
                    (self.summaries if tag == "summary" else self.answers).append(text)
                    self._tag = None

            def handle_data(self, data):
                if self._tag:
                    self._buf.append(data)

        c = Collect()
        c.feed(rendered)
        return c

    def _faq_posts(self):
        return {n: e for n, e in self.posts.items() if e["faq"] is not None}

    # -- tests ----------------------------------------------------------------
    def test_exactly_the_section_a_posts_carry_an_faq_and_answer(self):
        self.assertEqual(set(self._faq_posts()), self.FAQ_POSTS)
        answered = {n for n, e in self.posts.items() if e["answer"] is not None}
        self.assertEqual(answered, self.FAQ_POSTS)
        for name in self.FAQ_POSTS:
            with self.subTest(post=name):
                faq = self.posts[name]["faq"]
                self.assertTrue(3 <= len(faq) <= 5, f"{len(faq)} questions")
                for item in faq:
                    self.assertEqual(set(item), {"q", "a"}, f"incomplete item {item}")

    def test_faq_jsonld_matches_the_visible_questions_and_answers(self):
        for name, entry in self._faq_posts().items():
            with self.subTest(post=name):
                faq = entry["faq"]
                rendered = self._render(faq)
                scripts = re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                                     rendered, re.S)
                self.assertEqual(len(scripts), 1, "expected exactly one FAQPage script")
                data = json.loads(scripts[0])          # raises on malformed JSON
                self.assertEqual(data["@context"], "https://schema.org")
                self.assertEqual(data["@type"], "FAQPage")
                entities = data["mainEntity"]

                shown = self._visible(rendered)
                self.assertEqual(shown.details, len(faq))
                self.assertEqual(len(shown.summaries), len(faq))
                self.assertEqual(len(shown.answers), len(faq))
                self.assertEqual(len(entities), len(faq))
                for i, item in enumerate(faq):
                    ent = entities[i]
                    self.assertEqual(ent["@type"], "Question")
                    self.assertEqual(ent["acceptedAnswer"]["@type"], "Answer")
                    # schema == visible text == front matter, item by item
                    self.assertEqual(ent["name"], shown.summaries[i])
                    self.assertEqual(ent["acceptedAnswer"]["text"], shown.answers[i])
                    self.assertEqual(ent["name"], item["q"])
                    self.assertEqual(ent["acceptedAnswer"]["text"], item["a"])

    def test_ampersand_is_escaped_in_html_and_plain_in_json(self):
        """A known literal, so the escape half cannot drift with the parser:
        'Arm & Hammer' must reach the HTML as &amp; and the JSON as &."""
        faq = [{"q": "Q & A?", "a": "Arm & Hammer, Dr. Elsey's."}]
        rendered = self._render(faq)
        self.assertIn("<summary>Q &amp; A?</summary>", rendered)
        self.assertIn("<p>Arm &amp; Hammer, Dr. Elsey&#39;s.</p>", rendered)
        self.assertIn('"text": "Arm & Hammer, Dr. Elsey\'s."', rendered)

    def test_a_post_without_faq_renders_no_faq_markup(self):
        """Everything FAQ-shaped sits inside the one `if page.faq` guard, so a
        post without the field renders exactly as before. Liquid comments are
        stripped first, so the note describing the block does not count."""
        outside = self.layout.replace(self._faq_block(), "")
        outside = re.sub(r"\{%-?\s*comment\s*-?%\}.*?\{%-?\s*endcomment\s*-?%\}",
                         "", outside, flags=re.S)
        for needle in ("FAQPage", "faq-item", "<details", "Common questions"):
            with self.subTest(needle=needle):
                self.assertFalse(needle in outside, f"{needle!r} renders outside the faq guard")

    def test_answer_line_replaces_the_description_under_the_title(self):
        head = self.layout[self.layout.find('<header class="piece-head">'):
                           self.layout.find("</header>")]
        self.assertLess(head.find("<h1>"), head.find("{%- if page.answer %}"))
        self.assertIn("{{ page.answer | escape }}", head)
        self.assertIn("{%- elsif page.description %}", head)
        self.assertLess(head.find("{%- if page.answer %}"), head.find('class="piece-date"'))

    def test_answer_lines_and_faq_copy_follow_the_copy_rules(self):
        """40 to 60 word answer lines, plain text with no links, no em or en
        dash and no spaced hyphen."""
        for name, entry in self._faq_posts().items():
            strings = [entry["answer"]] + [s for it in entry["faq"] for s in (it["q"], it["a"])]
            with self.subTest(post=name):
                words = len(entry["answer"].split())
                self.assertTrue(40 <= words <= 60, f"answer line is {words} words")
                for s in strings:
                    self.assertNotRegex(s, r"https?://|\]\(|<a\b")
                    self.assertNotRegex(s, r"[\u2012-\u2015]|(?:^|\s)-(?:\s|$)")

    def test_no_faq_question_also_shows_as_a_body_heading(self):
        """The litter post's old markdown Q&A moved into faq:, so nothing may
        show twice on the page."""
        for name, entry in self._faq_posts().items():
            headings = {h.strip().lower() for h in
                        re.findall(r"^#{2,6}\s+(.+)$", entry["body"], re.M)}
            with self.subTest(post=name):
                for item in entry["faq"]:
                    self.assertNotIn(item["q"].lower(), headings)

    def test_faq_heading_reads_common_questions(self):
        self.assertIn('<h2 id="faq-title">Common questions</h2>', self._faq_block())

    def test_faq_copy_matches_the_approved_text(self):
        """Every answer line, question and answer equals the approved copy in
        _test_fixtures/faq_section_a.json, which was built from Scarlett's v2
        file plus the citation-check overrides, not from these posts. Any
        change to an FAQ fact fails here until the fixture is changed on
        purpose."""
        expected = json.loads((REPO / "_test_fixtures" / "faq_section_a.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(set(expected), self.FAQ_POSTS)
        for name, want in expected.items():
            with self.subTest(post=name):
                entry = self.posts[name]
                self.assertEqual(entry["answer"], want["answer"])
                self.assertEqual(entry["faq"], want["faq"])

    def test_bodies_keep_the_two_contradictions_out(self):
        """The two body claims most likely to creep back against their own
        FAQ: the kitten food FAQ prefers scheduled meals, so no grazing; the
        backpack FAQ pairs the bubble bag with a calm cat, so no nervous cat
        in a sentence about the Texsens or a bubble bag."""
        kitten = self.posts["2026-04-27-best-kitten-food.md"]["body"]
        self.assertNotRegex(kitten, r"(?i)\bgraz(e|es|ed|er|ers|ing)\b")
        backpack = self.posts["2026-08-24-best-cat-carrier-backpacks.md"]["body"]
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", backpack):
            if re.search(r"(?i)texsens|bubble", sentence):
                with self.subTest(sentence=sentence[:60]):
                    self.assertNotRegex(sentence, r"(?i)\bnervous\b")

    def test_css_never_hides_a_faq_answer(self):
        """Collapsed answers are hidden by the browser's closed state only. A
        display:none rule on the FAQ would remove them from what crawlers read
        and break the match with the FAQPage markup."""
        css = (REPO / "assets" / "css" / "style.css").read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if re.search(r"\bfaq\b|faq-|details|summary", selector):
                with self.subTest(selector=selector.strip()):
                    self.assertNotRegex(body, r"display\s*:\s*none|visibility\s*:\s*hidden")


class TestPublishedPostsVoiceIntegrity(unittest.TestCase):
    """Guards the published bodies against the substitution damage that commit
    fde8937 and its 30 siblings ("fix: third-person voice - <slug>") left behind.

    Those commits converted the author voice from first to third person with a
    blind find-and-replace: we -> reviewers, our -> the, my -> a. The intent was
    right (generate_posts.py's prompt has forbidden first person since 0ad8182,
    and the reviewer gate hard-fails an article that uses it), but the execution
    produced 229 defects across 34 of the 49 posts, including non-words
    ("reviewers've"), dropped subjects ("At Happy Pet Product Reviews,'re
    always...") and sentences starting mid-paragraph in lowercase.

    These assert the SHAPE of the damage rather than the individual sentences,
    so a repeat of the same mistake fails here whatever wording it produces.

    One of that pass's substitutions has since been reversed on purpose: it
    recast first-person sentences as "Testing found X" / "Testing showed X",
    which is third person and also a claim this site tested something it never
    touched. The recorded target for an observation is now the reviewer pool
    ("owners report X", "reviewers mention X") or a flat statement of the spec.
    test_no_published_post_claims_firsthand_experience below enforces that; the
    sentence-initial "testing" token in test_no_sentence_starting_in_lowercase
    stays, since a lowercase sentence start is a casing artifact either way.
    The word "reviewers" itself is deliberately not banned: several posts cite
    real third-party reviewers ("Amazon reviewers have given it 4.6/5"), which
    is legitimate and was in the text before those commits ran.
    """

    @classmethod
    def setUpClass(cls):
        cls.posts = {p.name: p.read_text(encoding="utf-8")
                     for p in sorted((REPO / "_posts").glob("*.md"))}
        assert cls.posts, "no posts found - the guard would pass vacuously"

    def _scan(self, pattern, flags=re.IGNORECASE):
        rx = re.compile(pattern, flags)
        return [f"{name}: {m.group(0)!r}"
                for name, text in self.posts.items() for m in rx.finditer(text)]

    def test_no_pronoun_substitution_nonwords(self):
        """'reviewers've' / 'reviewers're' - a contraction whose subject was
        swapped for a plural noun. Never valid English, in any wording."""
        self.assertEqual(self._scan(r"reviewers[’'](ve|re|ll|d|s)\b"), [])

    def test_no_dropped_subject_contractions(self):
        """"At Happy Pet Product Reviews,'re always..." - the subject was
        deleted and the contraction left stranded."""
        self.assertEqual(self._scan(r"(?<![A-Za-z])[’'](re|ve|ll|m)\b"), [])

    def test_no_sentence_starting_in_lowercase(self):
        """'Our goal is' -> 'the goal is' mid-paragraph. Catches the casing half
        of the damage, which fixing only the word would leave behind."""
        self.assertEqual(
            self._scan(r"(?<=[.!?])\s+(reviewers|the goal|the evidence|rating|testing)\b",
                       flags=0), [])

    def test_no_broken_possessive(self):
        """'our own pups' -> 'the own pups'."""
        self.assertEqual(self._scan(r"\bthe own\b"), [])

    def test_no_lowercase_headings(self):
        """'## Our Top Pick' -> '## the top pick'. A heading that starts
        lowercase is a casing artifact, not a style choice."""
        self.assertEqual(self._scan(r"^#{2,4} [a-z]", re.MULTILINE), [])

    def test_no_half_converted_author_voice(self):
        """The substitution rewrote "we"/"our"/"my" but not "us", so a converted
        post could still credit its own testing in the first person two
        paragraphs after dropping it. Checks the author-voice verbs only.

        NOT a blanket first-person ban. Six posts (best-cat-harness-leash,
        best-cat-tunnel-toys, best-dog-puzzle-toys, best-dog-life-jacket,
        best-dog-nail-grinder, best-cat-tree-large) were never converted at all
        and are still first person throughout; that is a live editorial question
        rather than substitution damage, so nothing here asserts it clean.
        """
        rx = re.compile(r"\b(impressed|impresses|reminded|gives|stood out to|"
                        r"convinced|surprised|showed)\s+us\b", re.IGNORECASE)
        self.assertEqual(
            [f"{n}: {m.group(0)!r}" for n, t in self.posts.items() for m in rx.finditer(t)],
            [])

    def test_no_published_post_claims_firsthand_experience(self):
        """The site does not test, try or handle any product, so no published
        post may say it did. Uses the same detector the generator gates on, so
        the published corpus and new output can never drift apart.

        This supersedes part of the voice conversion in b65f1e4, deliberately.
        That pass recast first-person sentences into "Testing found X" /
        "Testing showed X", which satisfies the no-first-person rule and asserts
        a test that never happened. "Owners report X" satisfies both. The
        recorded target voice for an observation is the reviewer pool -- owners,
        buyers, reviewers -- or a flat statement of the spec.
        """
        import generate_posts as gp
        offenders = []
        for name, text in self.posts.items():
            for claim in gp.find_firsthand_claims(text):
                offenders.append(f"{name}: {claim!r}")
        self.assertEqual(offenders, [])

    def test_firsthand_detector_catches_the_claims_it_shipped_past(self):
        """The test above passed on a corpus carrying eight firsthand claims.
        That is the whole problem with it standing alone: a detector with a gap
        makes a clean corpus and a clean-looking corpus indistinguishable, and
        the corpus was not clean.

        So this pins the detector against the exact sentences it let through,
        verbatim from the live posts at c4a522b. If a future edit widens a
        lookahead or narrows a window and reopens one of these, this test goes
        red instead of the corpus quietly going wrong again.

        Two causes, both real and independent, which is why the fix is two
        changes rather than one:
          - the product name overruns the {0,40} window ("the PetSafe Easy Walk
            No-Pull Dog Harness XL " is 45 characters on its own), and
          - the dot inside the markdown link's URL ended the span even earlier,
            because [^.!?\\n] treats "amzn.to" as a sentence boundary.
        """
        import generate_posts as gp
        shipped = [
            ("litter: site as the tester",
             "That's why the Happy Pet Product Reviews team have put in the "
             "work, testing various litters to bring you the top picks."),
            ("harness: long product name inside a markdown link",
             "That's why the Happy Pet Product Reviews team put the [PetSafe "
             "Easy Walk No-Pull Dog Harness XL](https://amzn.to/4coIuRk) to "
             "the test."),
            ("fountain: same shape, 'through its paces'",
             "After putting the [PetSafe Drinkwell Platinum Pet Fountain "
             "168oz](https://amzn.to/3NNVKFY) through its paces, several "
             "features stood out."),
            ("life jacket: bare participle, no subject anywhere",
             "Tested at dusk on a misty lake, the vest glows like a beacon."),
            ("cat beds: 'weeks went into', not 'weeks of'",
             "Weeks went into gathering feedback from cat owners, testing "
             "fabrics, and combing through online reviews."),
            ("dna tests: a buying guide built on testing that never happened",
             "To help you make an informed decision, here are a few key "
             "factors to consider, based on that testing and research."),
        ]
        missed = [label for label, text in shipped
                  if not gp.find_firsthand_claims(text)]
        self.assertEqual(missed, [])

    def test_firsthand_detector_leaves_legitimate_testing_language_alone(self):
        """The inverse direction, and the one that actually kills a guard. This
        gate HOLDS an article and the publish cron runs unattended twice a week,
        so a pattern that fires on honest prose costs a held article every run
        until somebody deletes the pattern to make the pipeline move again.

        Every string here is real published text or the phrasing the fixed
        posts now use. Third-party lab testing, "DNA test kit" as a product
        category, and reader instructions ("test it over the kitchen sink") all
        have to survive, and so does the site describing itself without
        claiming a test.
        """
        import generate_posts as gp
        legitimate = [
            "Testing a new food should be gradual.",
            "Wisdom Panel has long been a trusted name in the pet DNA testing "
            "world.",
            "Veterinary-grade supplements typically undergo stricter testing "
            "for purity and potency.",
            "Look for a lock button, then test it over the kitchen sink before "
            "you trust it on the road.",
            "Most dog DNA test kits involve a simple cheek swab.",
            "The daily walk can feel less like bonding time and more like a "
            "test of strength and endurance.",
            "That's why the Happy Pet Product Reviews team are here to guide "
            "you through the maze of options.",
            "That's why this site keeps hunting for products that genuinely "
            "last.",
            "That's why hours went into researching and comparing the best "
            "budget-friendly pet cameras.",
            "Tested in an independent lab for heavy-metal content, the formula "
            "came back clean.",
            "Tested by a third party for purity, the batch passed.",
            "Third-party testing or a verified rating adds a layer of "
            "confidence.",
            "It works well for small and medium dogs who do not test every "
            "seam.",
            "An easy way to test whether your cat tolerates a backpack at all.",
            "Put it on and forget about monthly applications.",
            "It requires a bit of trial and error to ensure it is snug.",
            "A 100-day money-back guarantee gives owners a risk-free trial.",
            "This guide digs into the science, the breed panels, and what "
            "owners report about the best dog DNA tests.",
            "This guide compares the leading litters on their specs and on "
            "what owners consistently report.",
            "Owners report cats launching themselves at this post without it "
            "wobbling.",
        ]
        false_positives = [f"{s!r} -> {gp.find_firsthand_claims(s)!r}"
                           for s in legitimate if gp.find_firsthand_claims(s)]
        self.assertEqual(false_positives, [])

    def test_no_published_post_quotes_a_named_individual(self):
        """Nobody here has spoken to an owner and no review is reproduced
        verbatim, so no published post may attribute a quote to a person. Uses
        the same detector the generator gates on, so the published corpus and
        new output cannot drift apart.

        Thirteen instances shipped across four posts and were deleted rather
        than re-sourced: real quotes would have to come from somewhere, and
        there is nowhere for them to come from.
        """
        import generate_posts as gp
        offenders = []
        for name, text in self.posts.items():
            for quote in gp.find_named_testimonials(text):
                offenders.append(f"{name}: {quote!r}")
        self.assertEqual(offenders, [])

    def test_no_published_post_carries_a_placeholder_affiliate_link(self):
        """The six "Buy now: [amzn.to/xyz1]" links shipped for four months. A
        placeholder is the one invented link that is provably invented from the
        text alone, so it gets its own assertion rather than relying on the
        count above catching it by accident."""
        offenders = []
        for name, text in self.posts.items():
            for m in re.finditer(r"amzn\.to/(?:xyz|abc|example|placeholder|link)\w*",
                                 text, re.IGNORECASE):
                offenders.append(f"{name}: {m.group(0)!r}")
        self.assertEqual(offenders, [])

    def test_no_published_description_uses_an_em_or_en_dash(self):
        """Twelve posts shipped one, and every one of them came out of the
        generator: the pin-description path never ran a typography scrub, only
        the body did. Asserted as a FIXED POINT of the generator's own scrubber
        rather than as "contains no dash", so the hand-repair and the guard stay
        one rule -- a description the scrubber would rewrite fails here even if
        the character it objects to is one this test never thought to name.

        Scoped to the `description:` field, deliberately. Em and en dashes are
        also present in 32 published BODIES; those predate the body scrub added
        with the review gate, they are not what R6.7 raised, and widening this
        assertion to cover them would make it red on arrival -- which is how a
        guard ends up with an allowlist and then means nothing.
        """
        import generate_posts as gp
        offenders = []
        for name, text in self.posts.items():
            for line in text.splitlines():
                if line.startswith("description:") and gp.scrub_description_typography(line) != line:
                    offenders.append(f"{name}: {line!r}")
        self.assertEqual(offenders, [])

    def test_no_species_mismatch_in_closing_call_to_action(self):
        """A cat post told the reader to "treat your dog to some exciting new
        playtime adventures" - a template artifact from a dog post. Checks the
        direct second-person address, where the species must match the post.

        DELIBERATELY NARROW - do not widen this to a general opposite-species
        vocabulary scan. That scan was run by hand over all 49 posts and
        returned 17 hits, of which only 3 were defects. The other 14 are
        correct prose that a wider rule would flag:

          - best-flea-prevention-dogs warns at length that "using a cat product
            on a dog can be deadly" (5 hits). It is a safety paragraph, and a
            guard that fires on it gets weakened with an allowlist or deleted.
          - best-cat-carrier-travel asks "Can you use a dog carrier for a cat?"
            and answers no (3 hits) - a deliberate comparison.
          - best-dog-pools and best-dog-nail-grinder quote product titles and
            multi-pet suitability ("for dogs and cats of all sizes", 5 hits).
          - "Bengal"/"Bengals" in two cat posts are a CAT breed (2 hits), which
            a naive dog-vocabulary list gets backwards.

        "your <species>" is the one shape where the post speaks directly to its
        own reader, so the species cannot legitimately be the other one.
        """
        offenders = []
        for name, text in self.posts.items():
            species = re.search(r"^species:\s*(\w+)", text, re.MULTILINE)
            if not species or species.group(1) not in ("cat", "dog"):
                continue
            wrong = "dog" if species.group(1) == "cat" else "cat"
            body = text.split("---", 2)[2] if text.count("---") >= 2 else text
            for m in re.finditer(rf"\byour {wrong}\b", body, re.IGNORECASE):
                offenders.append(f"{name} (species={species.group(1)}): {m.group(0)!r}")
        self.assertEqual(offenders, [])


# ---------------------------------------------------------------------------
# Verified products: every Amazon link in a post is on its article's list
# ---------------------------------------------------------------------------
# _data/verified_products.json maps an article slug to the products it may link:
# [{"asin": "B0...", "name": ...}, ...]. Seeded 2026-10 from what the posts
# linked at the time, so it records what was published, not a fresh check of it.
# Runner-up products are added here, after their ASINs are confirmed on the
# listing, before any post links them.
#
# An article's own front-matter affiliate_url is verified with or without an
# entry: it is the products.json product the pipeline generated the post from,
# and a Stage-1 PR may touch only DRAFT posts, pins and products.json
# (automerge_gate.ALLOWED_PATHS), so a new post can never arrive with an entry.
# An article that HAS an entry must list that link first, so the file cannot
# keep a stale primary after the front matter changes.
#
# Amazon search links (/s?k=) carry no ASIN. Nine are live on three posts and
# are recorded as {"asin": null, "search": <query>}; that set may only shrink.
VERIFIED_PRODUCTS = REPO / "_data" / "verified_products.json"

# Any Amazon host: amazon.com and its subdomains, other storefronts, and the
# short-link hosts. The label before "amazon." must start at a boundary, so the
# image hosts (m.media-amazon.com, images-na.ssl-images-amazon.com) never match.
# A match with neither a scheme nor a path is prose ("on Amazon.com."), not a link.
_AMAZON_LINK_RE = re.compile(
    r"(?<![\w.@-])(?P<scheme>https?://)?"
    r"(?P<host>(?:[a-z0-9-]+\.)*(?:amazon\.[a-z]{2,3}(?:\.[a-z]{2})?|amzn\.(?:to|com)|a\.co))"
    r"(?![\w-])(?P<path>[/?#][^\s\"'<>)\]]*)?",
    re.IGNORECASE)
_CHEWY_LINK_RE = re.compile(
    r"(?<![\w.@-])(?P<scheme>https?://)?"
    r"(?P<host>(?:[a-z0-9-]+\.)*(?:chewy\.com|chewy\.sjv\.io))"
    r"(?![\w-])(?P<path>[/?#][^\s\"'<>)\]]*)?",
    re.IGNORECASE)


def _links(rx, text):
    return [m for m in rx.finditer(text or "") if m.group("scheme") or m.group("path")]


def _amazon_link_key(m):
    """("asin", X) or ("search", query) for an amazon.com product or search link;
    None for any other shape (amzn.to, a.co, another storefront, a non-product
    page), because none of those says offline which product it reaches. The ASIN
    keeps its case and must be the whole path segment."""
    import urllib.parse
    host = m.group("host").lower()
    if host != "amazon.com" and not host.endswith(".amazon.com"):
        return None
    parts = urllib.parse.urlsplit("https://amazon.com" + (m.group("path") or ""))
    dp = re.fullmatch(r"(?:/[^/]+)?/(?i:dp|gp/product)/([A-Za-z0-9]+)(?:/.*)?", parts.path)
    if dp:
        return ("asin", dp.group(1))
    query = urllib.parse.parse_qs(parts.query).get("k", [])
    if parts.path in ("/s", "/s/") and len(query) == 1:
        return ("search", query[0])
    return None


def _entry_key(entry):
    return ("asin", entry["asin"]) if entry.get("asin") else ("search", entry.get("search"))


def _post_parts(text):
    """(front matter, body), split the way the corpus guards above split them."""
    parts = text.split("---", 2)
    return (parts[1], parts[2]) if len(parts) == 3 else ("", text)


def _front_matter_value(fm, field):
    m = re.search(rf'(?m)^{field}:\s*"?([^"\s]*)', fm)
    return m.group(1) if m else ""


def _post_slug(name):
    """The article slug of a post file: dated and DRAFT- names alike."""
    return re.sub(r"^(?:\d{4}-\d{2}-\d{2}-|DRAFT-)", "", name[:-3] if name.endswith(".md") else name)


def _own_amazon_key(fm):
    links = _links(_AMAZON_LINK_RE, _front_matter_value(fm, "affiliate_url"))
    return _amazon_link_key(links[0]) if links else None


def unverified_amazon_links(posts, verified):
    """Every Amazon link, front matter or body, that is neither the post's own
    affiliate_url nor on its article's verified list. Empty list = clean."""
    offenders = []
    for name, text in posts.items():
        fm, _ = _post_parts(text)
        allowed = {_entry_key(e) for e in verified.get(_post_slug(name), [])}
        own = _own_amazon_key(fm)
        if own:
            allowed.add(own)
        for m in _links(_AMAZON_LINK_RE, text):
            key = _amazon_link_key(m)
            if key is None or key not in allowed:
                offenders.append(f"{name}: {m.group(0)} -> {key or 'unverifiable link shape'}")
    return offenders


def unverified_chewy_links(posts):
    """Every Chewy link in a post BODY that is not the Impact-wrapped link to the
    same Chewy product as the front-matter chewy_url -- the one link
    validate_published_chewy_links checks. Empty list = clean."""
    from validate_published_chewy_links import parse_chewy_url
    offenders = []
    for name, text in posts.items():
        fm, body = _post_parts(text)
        own = parse_chewy_url(_front_matter_value(fm, "chewy_url"))
        for m in _links(_CHEWY_LINK_RE, body):
            url = m.group(0) if m.group("scheme") else "https://" + m.group(0)
            link = parse_chewy_url(url)
            if own["shape"] != "wrapped":
                why = "no wrapped front-matter chewy_url to match"
            elif link["shape"] != "wrapped":
                why = f"link is {link['shape']}, not the Impact wrapper"
            elif link["dp_id"] != own["dp_id"]:
                why = f"Chewy product {link['dp_id']}, front matter has {own['dp_id']}"
            else:
                continue
            offenders.append(f"{name}: {m.group(0)} -> {why}")
    return offenders


class TestVerifiedProducts(unittest.TestCase):
    """Every Amazon link in a published post is on that article's verified-product
    list, and every inline Chewy link is the front-matter one.

    This is the only corpus guard on Amazon destinations. It replaced a test that
    failed any post whose body linked two distinct Amazon products; that rule
    could not allow a runner-up link even once its ASIN was verified. The safety
    it gave is carried here: a runner-up with no entry on its own article's list
    fails, on every post, in either link shape, and a draft is held to the same
    rule (see the runner-up tests below). Search links are covered too, and a
    post may carry several only because they predate the list; the frozen set
    below keeps that from growing.

    The gap both guards share, named rather than hidden: a post's own
    front-matter affiliate_url is trusted, so a post whose only link is itself
    invented passes. best-pet-water-fountain was that shape (front matter
    amzn.to/3NNVKFY, body amzn.to/41dtOOM, settled only by resolving both links
    off this machine). No allowlist was added for it or any other post.
    """

    # The nine search links live when the list was seeded, decoded by hand from
    # the posts. Literal on purpose: computing it with the key function under
    # test would make this agree with whatever that function says.
    GRANDFATHERED_SEARCHES = {
        ("best-wet-cat-food", "Sheba Perfect Portions Variety Pack"),
        ("best-wet-cat-food", "Fancy Feast Classic Pate"),
        ("best-wet-cat-food", "Friskies Pate Wet Cat Food"),
        ("best-wet-cat-food", "Purina Pro Plan Wet Cat Food"),
        ("best-senior-dog-food", "Purina Pro Plan Bright Mind Adult 7+"),
        ("best-senior-dog-food", "Blue Buffalo Life Protection Formula Senior"),
        ("best-senior-dog-food", "Royal Canin Canine Health Nutrition Senior 7+"),
        ("best-dog-probiotic-supplements", "Purina Pro Plan FortiFlora"),
        ("best-dog-probiotic-supplements",
         "Purina Pro Plan Veterinary Supplements FortiFlora Daily Probiotics for Dogs, 30 ct"),
    }

    LISTED = "B09S8WMJY9"    # best-automatic-cat-feeder's product
    POST = (
        '---\nlayout: post\naffiliate_url: "https://www.amazon.com/dp/B09S8WMJY9?th=1&tag=happypetdc-20"\n'
        'image: "https://images-na.ssl-images-amazon.com/images/P/B09S8WMJY9.01.LZZZZZZZ.jpg"\n---\n'
        "The [PETLIBRO feeder](https://www.amazon.com/dp/B09S8WMJY9?th=1&tag=happypetdc-20) is the pick.\n"
    )

    @classmethod
    def setUpClass(cls):
        cls.posts = {p.name: p.read_text(encoding="utf-8")
                     for p in sorted((REPO / "_posts").glob("*.md"))}
        assert cls.posts, "no posts found - the guard would pass vacuously"
        cls.verified = json.loads(VERIFIED_PRODUCTS.read_text(encoding="utf-8"))

    def _with(self, extra, verified=None, post=None):
        posts = {"2026-04-04-best-automatic-cat-feeder.md": (post or self.POST) + extra}
        return unverified_amazon_links(posts, self.verified if verified is None else verified)

    def test_every_amazon_link_is_on_its_articles_verified_list(self):
        self.assertEqual(unverified_amazon_links(self.posts, self.verified), [])

    def test_the_scan_finds_links_at_all(self):
        """Vacuity guard: a scanner that matched nothing would pass every post."""
        found = sum(len(_links(_AMAZON_LINK_RE, t)) for t in self.posts.values())
        self.assertGreater(found, len(self.posts))

    def test_an_unlisted_product_is_reported_however_it_is_written(self):
        for url in ("https://www.amazon.com/dp/B0UNLISTED?tag=happypetdc-20",
                    "HTTPS://WWW.AMAZON.COM/dp/B0UNLISTED",
                    "amazon.com/dp/B0UNLISTED",
                    "https://amazon.com/DP/B0UNLISTED",
                    "https://smile.amazon.com/dp/B0UNLISTED",
                    "https://www.amazon.com/Some-Listing-Name/dp/B0UNLISTED/ref=sr_1_1",
                    "https://www.amazon.com/gp/product/B0UNLISTED",
                    f"https://www.amazon.com/dp/{self.LISTED}X",
                    f"https://www.amazon.com/dp/{self.LISTED.lower()}",
                    f"https://www.amazon.com/gp/aw/d/{self.LISTED}",
                    f"https://www.amazon.co.uk/dp/{self.LISTED}",
                    "https://www.amazon.com/s?k=PETLIBRO+Feeder&tag=happypetdc-20",
                    "https://www.amazon.com",
                    "https://amzn.to/3abcXYZ",
                    "https://a.co/d/abc1234"):
            for form in ("[x]({})", '<a href="{}">x</a>', "[1]: {}",
                         "[x](https://example.com/r?u={})"):
                with self.subTest(url=url, form=form):
                    found = self._with(form.format(url) + "\n")
                    self.assertEqual(len(found), 1, found)
                    self.assertIn(url, found[0])

    def test_ordinary_text_and_the_listed_product_are_not_reported(self):
        clean = ("Amazon reviewers rate it 4.6/5, and it is sold on Amazon.com. "
                 "Mail support@amazon.com. "
                 '![x](https://m.media-amazon.com/images/I/71abc._AC_SX425_.jpg) '
                 "https://images-na.ssl-images-amazon.com/images/P/B000FLETX8.01.jpg "
                 "[Chewy](https://chewy.sjv.io/c/1/2/3?u=https%3A%2F%2Fwww.chewy.com%2Fx%2Fdp%2F1) "
                 f"[a](https://www.amazon.com/dp/{self.LISTED}) "
                 f"[b](https://www.amazon.com/dp/{self.LISTED}/?tag=happypetdc-20) "
                 f"[c](https://www.amazon.com/PETLIBRO-Feeder/dp/{self.LISTED}/ref=x) "
                 f"[d](HTTPS://WWW.AMAZON.COM/dp/{self.LISTED}#reviews)\n")
        self.assertEqual(self._with(clean), [])

    def test_the_front_matter_link_needs_no_entry_and_nothing_else_rides_on_it(self):
        """A new post arrives from Stage-1 with no entry: its own link passes, a
        second product does not."""
        self.assertEqual(self._with("", verified={}), [])
        self.assertEqual(len(self._with("[y](https://www.amazon.com/dp/B0UNLISTED)\n",
                                        verified={})), 1)

    def test_a_listed_runner_up_passes(self):
        listed = {"best-automatic-cat-feeder": [{"asin": self.LISTED, "name": "a"},
                                                {"asin": "B0RUNNERUP", "name": "b"}]}
        self.assertEqual(self._with("[y](https://www.amazon.com/dp/B0RUNNERUP)\n", listed), [])
        self.assertEqual(len(self._with("[y](https://www.amazon.com/dp/B0RUNNERUP)\n",
                                        {"best-automatic-cat-feeder": listed[
                                            "best-automatic-cat-feeder"][:1]})), 1)

    def test_a_runner_up_with_no_verified_entry_is_reported_on_every_published_post(self):
        """The safety the retired one-product corpus test used to give, carried
        here: it failed a post that linked a second, invented Amazon product. This
        does the same against the REAL verified list and EVERY real post, with the
        runner-up written two ways -- a full /dp/ link and an amzn.to short link,
        the two shapes the invented links took -- so no post gains immunity from
        having an entry, a long entry, or none."""
        missed = []
        for name, text in self.posts.items():
            for url in ("https://www.amazon.com/dp/B0NOTLISTED?tag=happypetdc-20",
                        "https://amzn.to/3NotListed"):
                found = unverified_amazon_links({name: text + f"\n[Runner-up]({url})\n"},
                                                self.verified)
                if len(found) != 1 or url not in found[0]:
                    missed.append(f"{name} + {url}: {found}")
        self.assertEqual(missed, [])

    def test_every_invented_link_in_a_body_is_reported_not_just_the_first(self):
        """The shape that shipped: best-dog-backpack-carrier carried three invented
        carriers and best-cat-calming-products six amzn.to/xyzN placeholders. Each
        link is its own offender, so a fix that cuts one still leaves the rest red."""
        invented = ["https://www.amazon.com/dp/B0INVENTED1",
                    "https://www.amazon.com/dp/B0INVENTED2?tag=happypetdc-20",
                    "https://amzn.to/xyz1", "https://amzn.to/xyz2"]
        found = self._with("".join(f"[x]({u})\n" for u in invented), verified={})
        self.assertEqual(len(found), len(invented), found)
        for url in invented:
            self.assertTrue(any(url in f for f in found), (url, found))

    def test_a_verified_entry_covers_only_its_own_article_and_only_what_it_lists(self):
        """Adding runner-ups to the list must not widen it past what was added."""
        # Listed for ANOTHER article: no help here.
        elsewhere = {"best-cat-carrier-travel": [{"asin": "B000FLETX8", "name": "carrier"}]}
        found = self._with("[x](https://www.amazon.com/dp/B000FLETX8)\n", verified=elsewhere)
        self.assertEqual(len(found), 1, found)
        # One runner-up listed here does not whitelist a sibling that is not.
        mine = {"best-automatic-cat-feeder": [{"asin": self.LISTED, "name": "a"},
                                              {"asin": "B0RUNNERUP", "name": "b"}]}
        found = self._with("[x](https://www.amazon.com/dp/B0RUNNERUP)\n"
                           "[y](https://www.amazon.com/dp/B0INVENTED)\n", verified=mine)
        self.assertEqual(len(found), 1, found)
        self.assertIn("B0INVENTED", found[0])

    def test_a_draft_cannot_carry_a_runner_up_either(self):
        """A Stage-1 draft arrives with no entry, so its own link is the only one
        it may carry; the DRAFT- file name must not skip the check."""
        name = "DRAFT-best-automatic-cat-feeder.md"
        self.assertEqual(unverified_amazon_links({name: self.POST}, {}), [])
        found = unverified_amazon_links(
            {name: self.POST + "[x](https://www.amazon.com/dp/B0INVENTED)\n"}, {})
        self.assertEqual(len(found), 1, found)

    def test_a_draft_post_reads_the_same_article_entry(self):
        self.assertEqual(_post_slug("DRAFT-best-dog-pools.md"), "best-dog-pools")
        self.assertEqual(_post_slug("2026-06-01-best-dog-pools.md"), "best-dog-pools")

    def test_the_verified_list_is_well_formed(self):
        slugs = {_post_slug(n) for n in self.posts}
        problems = []
        for slug, entries in self.verified.items():
            if slug not in slugs:
                problems.append(f"{slug}: no published post has this slug")
                continue
            if not isinstance(entries, list) or not entries:
                problems.append(f"{slug}: not a non-empty list")
                continue
            for e in entries:
                if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not e["name"].strip():
                    problems.append(f"{slug}: entry without a name: {e!r}")
                elif e.get("asin") is not None:
                    if set(e) != {"asin", "name"} or not re.fullmatch(r"[A-Z0-9]{10}", e["asin"]):
                        problems.append(f"{slug}: malformed ASIN entry: {e!r}")
                elif set(e) != {"asin", "search", "name"} or not isinstance(e["search"], str):
                    problems.append(f"{slug}: malformed search entry: {e!r}")
            keys = [_entry_key(e) for e in entries if isinstance(e, dict)]
            if len(keys) != len(set(keys)):
                problems.append(f"{slug}: duplicate entries")
        for name, text in self.posts.items():
            entries = self.verified.get(_post_slug(name))
            own = _own_amazon_key(_post_parts(text)[0])
            if entries and own and _entry_key(entries[0]) != own:
                problems.append(f"{name}: first entry {_entry_key(entries[0])} is not "
                                f"the front-matter link {own}")
        self.assertEqual(problems, [])

    def test_search_entries_only_shrink(self):
        searches = {(slug, e["search"]) for slug, entries in self.verified.items()
                    for e in entries if e.get("asin") is None}
        self.assertEqual(searches - self.GRANDFATHERED_SEARCHES, set())

    def test_every_inline_chewy_link_is_the_front_matter_one(self):
        self.assertEqual(unverified_chewy_links(self.posts), [])

    def test_a_wrong_inline_chewy_link_is_reported(self):
        fm = ('---\nchewy_url: "https://chewy.sjv.io/c/7160344/3054490/32975?u='
              'https%3A%2F%2Fwww.chewy.com%2Fpurina-pro-plan-veterinary-diets%2Fdp%2F50029"\n---\n')
        same = ("[Buy on Chewy](https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=1&u="
                "https%3A%2F%2Fwww.chewy.com%2Fpurina-pro-plan-veterinary-diets%2Fdp%2F50029)\n"
                "Owners can find it at Chewy.com.\n")
        self.assertEqual(unverified_chewy_links({"a.md": fm + same}), [])
        for link in ("https://chewy.sjv.io/c/7160344/3054490/32975?u="
                     "https%3A%2F%2Fwww.chewy.com%2Fother-product%2Fdp%2F99999",
                     "https://www.chewy.com/purina-pro-plan-veterinary-diets/dp/50029",
                     "WWW.CHEWY.COM/purina-pro-plan-veterinary-diets/dp/50029",
                     "https://www.chewy.com/b/dog-food-288"):
            with self.subTest(link=link):
                self.assertEqual(len(unverified_chewy_links({"a.md": fm + f"[x]({link})\n"})), 1)
        self.assertEqual(len(unverified_chewy_links({"a.md": "---\n---\n" + same})), 1)


# ---------------------------------------------------------------------------
# Published Chewy links: the post's own front matter is the source of truth
# ---------------------------------------------------------------------------
# Fixtures are the three wrong Chewy links found BY HAND on 2026-09-22 and
# repaired in 2f16028. They are quoted from that commit's diff, not recomputed
# by the matcher under test, so a check that agrees with them agrees with
# something it did not produce.
CHEWY_WRONG_GLOBLAZER = (
    "https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=4187582&u="
    "https%3A%2F%2Fwww.chewy.com%2Fgloblazer-big-modern-tower-77-in%2Fdp%2F4187582"
    "%3Futm_source%3Dgoogle-product%26utm_medium%3Dorganic%26utm_content%3DGloblazer"
    "&intsrc=APIG_24727"
)
CHEWY_WRONG_GLOBLAZER_NAME = (
    "Globlazer Heavy Duty Cat Tree, 74in, 7 Sisal Posts, 2 Padded Condos, Dark Grey"
)
CHEWY_WRONG_CATIT = (
    "https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=151438&u="
    "https%3A%2F%2Fwww.chewy.com%2Fcatit-senses-20-food-tree-cat-feeder%2Fdp%2F178226"
    "%3Futm_source%3Dgoogle-product%26utm_medium%3Dorganic%26utm_content%3DCatit"
    "&intsrc=APIG_24727"
)
CHEWY_WRONG_CATIT_NAME = "Catit Senses 2.0 Multi Feeder, Interactive Cat Toys"
# The FortiFlora repair landed as a bare chewy.com URL because the Impact
# wrapper's prodsku is not derivable here. It is live on the site right now.
CHEWY_UNWRAPPED_FORTIFLORA = "https://www.chewy.com/purina-pro-plan-veterinary-diets/dp/50029"
CHEWY_GOOD_CHOMCHOM = (
    "https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=136285&u="
    "https%3A%2F%2Fwww.chewy.com%2Fchomchom-roller-pet-hair-remover%2Fdp%2F163270"
    "%3Futm_source%3Dgoogle-product%26utm_medium%3Dorganic%26utm_content%3DChomChom"
    "%2520Roller&intsrc=APIG_24727"
)
CHEWY_GOOD_CHOMCHOM_NAME = "ChomChom Roller Pet Hair Remover - Original Size Reusable Lint Roller"


class TestAlternativePickSectioning(unittest.TestCase):
    """Which part of an article is "an alternative pick" -- read structurally,
    because the heading wording drifts across the corpus."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    ROUNDUP = (
        "Opening prose.\n\n"
        "## Quick picks\n\n"
        "### Featured pick: [TopMat](https://amzn.to/3TestABC)\n"
        "The featured product has 4.7 stars and 12,000 reviews, all verified.\n\n"
        "### MatA Cooling Pad\n"
        "Owners tend to like it.\n\n"
        "### MatB Gel Mat\n"
        "Most owners find it holds up.\n\n"
        "## Buying guide\n"
        "### What to look for\n"
        "Look for 3 things and a 40 inch width.\n"
    )

    def test_the_alternatives_are_the_h3s_after_the_featured_pick(self):
        heads = [s["heading"] for s in self.gp.split_alternative_sections(self.ROUNDUP)]
        self.assertEqual(heads, ["MatA Cooling Pad", "MatB Gel Mat"])

    def test_the_featured_pick_is_not_one_of_them(self):
        """Its figures are verified off the listing. A gate that read them as
        unsourced would hold every roundup that uses its own product data."""
        for s in self.gp.split_alternative_sections(self.ROUNDUP):
            self.assertNotIn("Featured", s["heading"])
        self.assertEqual(
            self.gp.find_unsourced_alternative_figures(self.ROUNDUP, "MatA Cooling Pad; MatB Gel Mat"),
            [])

    def test_h3s_under_a_later_h2_are_not_picks(self):
        """"What to look for" is a buying-guide subheading. Reading it as a
        product pick would hold a clean article on its own advice section."""
        heads = [s["heading"] for s in self.gp.split_alternative_sections(self.ROUNDUP)]
        self.assertNotIn("What to look for", heads)

    def test_an_article_with_no_featured_pick_has_no_alternatives(self):
        """single_review and buying_guide put H3s under "What We Like" and
        "FAQ". Neither format has alternative picks and neither may be judged
        as though it did."""
        single = ("## What We Like\n### Sturdy build\nIt holds 80 lb.\n"
                  "## FAQ\n### Is it washable?\nYes, at 30 degrees.\n")
        self.assertEqual(self.gp.split_alternative_sections(single), [])

    def test_a_featured_pick_written_at_h2_still_anchors_the_scan(self):
        """Four published roundups put the featured pick at H2 with the
        alternatives at H3 beneath it."""
        text = ("## Quick Picks\n\n## Featured Pick - [Tapo C100](https://amzn.to/3TestABC)\n"
                "Verified 4.4 stars.\n\n### Furbo 360 Dog Camera\nOwners like it.\n\n"
                "## Comparison Table\n| a | b |\n")
        heads = [s["heading"] for s in self.gp.split_alternative_sections(text)]
        self.assertEqual(heads, ["Furbo 360 Dog Camera"])


class TestAlternativePicksAreBacked(unittest.TestCase):
    """#3 and the deterministic half of #2b.

    `runners_up` went into the writer's brief and was never read again by
    anything -- no gate, no staging check, nothing compared the names that came
    back against the names that went out. And the LLM fact-check stage that
    strips figures off alternative sections runs only on main(), so on the
    agent-driven stage1_cli path nothing looked at those sections at all.
    """

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def _article(self, *alt_blocks):
        return ("Opening prose.\n\n## Quick picks\n\n"
                "### Featured pick: [TopMat](https://amzn.to/3TestABC)\n"
                "Verified 4.7 stars and 12,000 reviews.\n\n"
                + "\n\n".join(alt_blocks)
                + "\n\n## Buying guide\nProse about choosing one.\n")

    SUPPLIED = "BLACK+DECKER Pet Hair Remover Roller; Pet Hair Removal Glove for Cats and Dogs"

    def test_an_invented_pick_is_caught(self):
        """"K&H Cool-Flow Backpack" is one of the products the generator
        actually shipped with no record behind it."""
        art = self._article("### K&H Cool-Flow Backpack\nIt tends to work well.")
        self.assertEqual(self.gp.find_unlisted_alternatives(art, self.SUPPLIED),
                         ["K&H Cool-Flow Backpack"])

    def test_the_supplied_picks_are_not_caught(self):
        art = self._article(
            "### BLACK+DECKER Pet Hair Remover Roller\nOwners tend to like the reach.",
            "### Pet Hair Removal Glove for Cats and Dogs\nMost owners find it gentle.")
        self.assertEqual(self.gp.find_unlisted_alternatives(art, self.SUPPLIED), [])

    def test_an_honestly_shortened_name_still_counts_as_the_supplied_one(self):
        """Writers shorten a 9-word retail title. A gate that reads that as a
        different product holds correct articles and gets switched off."""
        art = self._article("### Pet Hair Removal Glove\nMost owners find it gentle.")
        self.assertEqual(self.gp.find_unlisted_alternatives(art, self.SUPPLIED), [])

    def test_a_near_miss_built_from_the_same_generic_words_is_caught(self):
        """The hard case, and the reason coverage alone is not the rule. "Pet
        Hair Roller Pro" shares three of six words with the supplied
        BLACK+DECKER roller on entirely generic vocabulary, and is a different
        product -- which is exactly the shape an invented variant takes."""
        art = self._article("### Pet Hair Roller Pro\nOwners tend to like it.")
        self.assertEqual(self.gp.find_unlisted_alternatives(art, self.SUPPLIED),
                         ["Pet Hair Roller Pro"])

    def test_with_no_runners_up_supplied_any_alternative_is_unbacked(self):
        """An empty runners_up is a real answer, not a missing one: the brief
        asks for no Additional Picks section at all in that case."""
        art = self._article("### Some Other Mat\nIt tends to work.")
        self.assertEqual(self.gp.find_unlisted_alternatives(art, ""), ["Some Other Mat"])

    def test_a_figure_stated_about_an_alternative_is_caught(self):
        art = self._article(
            "### BLACK+DECKER Pet Hair Remover Roller\n"
            "It holds a 4.5-star rating across 12,000 reviews and cuts hair by 80%.")
        figures = self.gp.find_unsourced_alternative_figures(art, self.SUPPLIED)
        self.assertTrue(any("4.5-star" in f for f in figures), figures)
        self.assertTrue(any("80%" in f for f in figures), figures)

    def test_a_number_in_the_picks_own_name_is_not_a_claim_about_it(self):
        """"Trixie 5-in-1 Activity Center" is a real supplied name. Repeating
        it in prose states nothing the entry did not already supply."""
        art = self._article("### Trixie 5-in-1 Activity Center for Cats\n"
                            "The Trixie 5-in-1 suits cats that bore quickly.")
        self.assertEqual(
            self.gp.find_unsourced_alternative_figures(
                art, "Trixie 5-in-1 Activity Center for Cats"), [])

    def test_a_link_is_not_read_as_a_figure(self):
        """A URL is digits nobody claimed anything with, and an invented one is
        already a hold via find_unbacked_affiliate_links."""
        art = self._article("### BLACK+DECKER Pet Hair Remover Roller\n"
                            "See [the roller](https://amzn.to/3TestABC) for details.")
        self.assertEqual(self.gp.find_unsourced_alternative_figures(art, self.SUPPLIED), [])

    def test_hedged_prose_with_no_numbers_passes(self):
        """The inverse direction. The brief asks for exactly this shape, so it
        must not be held."""
        art = self._article("### BLACK+DECKER Pet Hair Remover Roller\n"
                            "Most owners find the wide head suits upholstery, though it "
                            "tends to need more passes on car seats.")
        self.assertEqual(self.gp.find_unsourced_alternative_figures(art, self.SUPPLIED), [])

    def test_published_roundups_written_to_their_real_runners_up_pass(self):
        """The inverse direction against real articles rather than fixtures.
        Three published roundups still have their entry's runners_up recoverable
        from products.json history; each used the supplied names. A name check
        that flags any of them is wrong about honest output."""
        real = {
            "2026-08-20-best-pet-hair-removers-laundry-furniture.md":
                "BLACK+DECKER Pet Hair Remover Roller; Pet Hair Removal Glove for Cats and Dogs",
            "2026-08-13-best-cat-puzzle-feeders.md":
                "Trixie 5-in-1 Activity Center for Cats; Doc & Phoebe's Indoor Hunting Cat Feeder",
            "2026-09-21-best-dog-boots-hot-pavement.md":
                "Breathable Dog Boots for Medium Large Dogs Non-Slip Summer Hot Pavement; "
                "SlowTon Dog Shoes for Large Small Medium Dogs Breathable Summer Boots",
        }
        flagged = {}
        for name, runners_up in real.items():
            body = (REPO / "_posts" / name).read_text(encoding="utf-8")
            unlisted = self.gp.find_unlisted_alternatives(body, runners_up)
            if unlisted:
                flagged[name] = unlisted
        self.assertEqual(flagged, {})


class TestAlternativeGateRunsOnBothPaths(unittest.TestCase):
    """#2b. The LLM fact-check stage runs only inside main(), so the
    agent-driven stage1_cli path published without one pass over its
    alternative sections. Both paths funnel through validate_output and
    stage_article, so the check lives there and reaches both from one edit."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp
        self.body = (
            "Opening prose. " + ("Filler words to clear the length floor. " * 160) +
            "\n\n## Quick picks\n\n"
            "### Featured pick: [TopMat](https://amzn.to/3TestABC)\n"
            "Verified 4.7 stars.\n\n"
            "### Invented Runner-Up Mat\nIt has a 4.5-star rating.\n\n"
            "## Buying guide\nProse.\n\n" + QUESTION_BLOCK)

    def test_the_post_review_gate_holds_it(self):
        with self.assertRaises(self.gp.GenerationStageError) as ctx:
            self.gp.validate_output("review", self.body, "best-mats",
                                    affiliate_url="https://amzn.to/3TestABC",
                                    runners_up="MatA Cooling Pad")
        self.assertIn("Invented Runner-Up Mat", str(ctx.exception))

    def test_a_figure_on_a_LISTED_pick_holds_too(self):
        """The name check and the figure check fail independently, so this uses
        an alternative that IS on the supplied list. Without it the gate tests
        would all be satisfied by the name check alone and the figure half
        would be unwired at this seam without any test noticing."""
        body = self.body.replace("Invented Runner-Up Mat", "MatA Cooling Pad")
        with self.assertRaises(self.gp.GenerationStageError) as ctx:
            self.gp.validate_output("review", body, "best-mats",
                                    affiliate_url="https://amzn.to/3TestABC",
                                    runners_up="MatA Cooling Pad")
        self.assertIn("4.5-star", str(ctx.exception))

    def test_the_generate_gate_does_not(self):
        """Deliberate, and the one ordering mistake that would break the
        scheduled path: fact_check_alternatives runs BETWEEN the two gates and
        exists to strip exactly these figures. Holding at "generate" would hold
        every roundup the fact-checker would have cleaned."""
        self.gp.validate_output("generate", self.body, "best-mats",
                                affiliate_url="https://amzn.to/3TestABC",
                                runners_up="MatA Cooling Pad")

    def test_a_caller_with_no_product_context_makes_no_judgment(self):
        """runners_up=None is "I cannot tell", not "there are none" -- the same
        shape as an empty affiliate_url in the link guard. Every pre-existing
        caller passes nothing and must keep its old behaviour."""
        self.gp.validate_output("review", self.body, "best-mats",
                                affiliate_url="https://amzn.to/3TestABC")

    def test_stage_article_is_the_backstop_for_both_paths(self):
        """The single funnel every publish path pushes a body through, so a
        future caller that skips validate_output still cannot stage this."""
        with self.assertRaises(self.gp.GenerationStageError):
            self.gp.stage_article("best-mats", {
                "title": "Best Mats", "keyword": "best mats", "format": "roundup",
                "affiliate_url": "https://amzn.to/3TestABC",
                "runners_up": "MatA Cooling Pad", "species": "dog",
                "category": "dog-beds", "pulled_on": "2026-10-01"}, self.body, "A pin description.")

    def test_stage1_cli_gate_flags_it_rather_than_only_holding_at_staging(self):
        """Structural: the agent's rewrite loop reads `flags`, so a flag here is
        what lets it FIX the article instead of hitting the staging hold cold.
        Mirrors the unbacked-link check that already works this way."""
        source = (REPO / "stage1_cli.py").read_text(encoding="utf-8")
        self.assertIn("find_unlisted_alternatives", source)
        self.assertIn("find_unsourced_alternative_figures", source)
        self.assertIn("runners_up=", source)


class TestQuestionBlock(unittest.TestCase):
    """Every new article opens with a paragraph that names the top pick, and
    carries three question H2s answered in 40 to 80 words each. The prompts ask
    for it; the post-review gate holds an article without it. Published posts
    are not checked: they predate the rule."""

    TOP = "Outward Hound Nina Ottosson Dog Tornado"
    LEAD = f"The [{TOP}](https://amzn.to/3TestABC) is the pick: three levels keep a dog busy.\n\n"

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def _qa(self, n_words, question="Is this a question?"):
        return f"## {question}\n\n" + " ".join(["word"] * n_words) + "\n\n"

    def _problems(self, body, name=TOP):
        return self.gp.find_question_block_problems(body, name)

    # --- the block ---------------------------------------------------------
    def test_the_fixture_block_is_complete(self):
        self.assertEqual(self._problems(self.LEAD + QUESTION_BLOCK), [])

    def test_no_question_block_fails(self):
        self.assertEqual(self._problems(self.LEAD + "## Buying guide\n\nProse here.\n"),
                         ["0 question-shaped H2(s), need 3"])

    def test_two_questions_fail_and_three_pass(self):
        self.assertEqual(self._problems(self.LEAD + self._qa(50) * 2),
                         ["2 question-shaped H2(s), need 3"])
        self.assertEqual(self._problems(self.LEAD + self._qa(50) * 3), [])

    def test_answer_length_is_40_to_80_words_inclusive(self):
        for n, ok in ((39, False), (40, True), (80, True), (81, False)):
            with self.subTest(words=n):
                body = self.LEAD + self._qa(50) * 2 + self._qa(n, "Last one?")
                problems = self._problems(body)
                if ok:
                    self.assertEqual(problems, [])
                else:
                    self.assertEqual(problems, [f"answer to 'Last one?' is {n} words, need 40-80"])

    def test_a_question_followed_straight_by_a_subheading_has_no_answer(self):
        body = self.LEAD + self._qa(50) * 2 + "## Last one?\n\n### Detail\n\n" + "word " * 50
        self.assertEqual(self._problems(body), ["answer to 'Last one?' is 0 words, need 40-80"])

    def test_link_anchors_count_and_urls_and_bare_markup_do_not(self):
        answer = ("[three anchor words](https://www.amazon.com/dp/B000000001?tag=x-20) "
                  "https://example.com/a/b - | ** " + "word " * 37)
        body = self.LEAD + self._qa(50) * 2 + "## Last one?\n\n" + answer
        self.assertEqual(self._problems(body), [])           # 3 + 37 = 40
        self.assertEqual(self._problems(body.replace("three anchor words", "two words")),
                         ["answer to 'Last one?' is 39 words, need 40-80"])

    def test_only_h2_headings_ending_in_a_question_mark_count(self):
        not_questions = ("### Is an H3 a question?\n\n" + "word " * 50 + "\n\n"
                         "## Why size matters\n\n" + "word " * 120 + "\n\n"
                         "#### Deep question?\n\n" + "word " * 50 + "\n\n")
        self.assertEqual(self._problems(self.LEAD + not_questions + self._qa(50) * 2),
                         ["2 question-shaped H2(s), need 3"])
        # Trailing spaces after the mark are still a question heading.
        self.assertEqual(self._problems(self.LEAD + self._qa(50, "Spaced?   ") * 3), [])

    # --- the answer-first lead ---------------------------------------------
    def test_a_body_that_opens_with_a_heading_or_a_list_has_no_lead(self):
        self.assertEqual(self._problems(QUESTION_BLOCK),
                         ["no paragraph before the first heading"])
        self.assertEqual(self._problems(f"- [{self.TOP}](https://amzn.to/3TestABC)\n\n" + QUESTION_BLOCK),
                         ["the first block is a list or table, not a paragraph"])

    def test_the_lead_must_name_the_top_pick(self):
        other = "Puzzle toys keep a bored dog busy for twenty minutes.\n\n"
        self.assertEqual(self._problems(other + QUESTION_BLOCK),
                         [f"the first paragraph does not name the top pick ({self.TOP!r})"])
        # Only the FIRST paragraph counts; naming it later is not answer-first.
        self.assertEqual(len(self._problems(other + self.LEAD + QUESTION_BLOCK)), 1)
        # A shortened name passes: brand plus one more word of the name.
        self.assertEqual(self._problems("The Outward Hound tornado wins for most dogs.\n\n"
                                        + QUESTION_BLOCK), [])
        # The brand alone, or the name's other words without the brand, do not.
        for lead in ("Outward is the brand to buy.", "A dog tornado puzzle wins."):
            with self.subTest(lead=lead):
                self.assertEqual(len(self._problems(lead + "\n\n" + QUESTION_BLOCK)), 1)

    def test_without_a_product_name_the_lead_is_not_matched(self):
        self.assertEqual(self._problems("Puzzle toys help.\n\n" + QUESTION_BLOCK, ""), [])

    # --- wiring --------------------------------------------------------------
    def test_the_post_review_gate_holds_an_article_without_it(self):
        body = GOOD_ARTICLE.replace(QUESTION_BLOCK, "")
        with self.assertRaises(self.gp.GenerationStageError) as cm:
            self.gp.validate_output("review", body, "slug",
                                    affiliate_url="https://amzn.to/3TestABC")
        self.assertIn("question block: 0 question-shaped", str(cm.exception))
        with self.assertRaises(self.gp.GenerationStageError) as cm:
            self.gp.validate_output("review", GOOD_ARTICLE, "slug",
                                    affiliate_url="https://amzn.to/3TestABC",
                                    product_name=self.TOP)
        self.assertIn("does not name the top pick", str(cm.exception))

    def test_the_generate_gate_does_not(self):
        """The rewrite pass runs between the two gates and can add the block."""
        self.gp.validate_output("generate", GOOD_ARTICLE.replace(QUESTION_BLOCK, ""), "slug",
                                affiliate_url="https://amzn.to/3TestABC")

    def test_a_question_with_pick_in_it_is_not_read_as_an_alternative(self):
        """The block sits after the buying guide. A question H2 that says
        "pick" must not reopen the alternative-pick scan."""
        body = (self.LEAD + "Filler words to clear the length floor. " * 160 +
                "\n\n## Quick picks\n\n### Featured pick: [TopMat](https://amzn.to/3TestABC)\n"
                "Verified 4.7 stars.\n\n### MatA Cooling Pad\nIt stays cool.\n\n"
                "## Buying guide\nProse.\n\n" + self._qa(50, "Which pick suits a 12 year old dog?") * 3)
        self.gp.validate_output("review", body, "best-mats",
                                affiliate_url="https://amzn.to/3TestABC",
                                runners_up="MatA Cooling Pad", product_name=self.TOP)

    def test_both_publish_paths_pass_the_product_name_to_the_review_gate(self):
        gp_src = (REPO / "generate_posts.py").read_text(encoding="utf-8")
        cli_src = (REPO / "stage1_cli.py").read_text(encoding="utf-8")
        # Each call runs from validate_output( to the except that catches it.
        main_call = re.search(r'validate_output\(\s*"review".*?except', gp_src, re.S)
        self.assertIsNotNone(main_call)
        self.assertIn('product_name=product.get("name", "")', main_call.group(0))
        stage_call = re.search(r'gp\.validate_output\(\s*"review".*?except', cli_src, re.S)
        self.assertIsNotNone(stage_call)
        self.assertIn('product_name=product.get("name", "")', stage_call.group(0))
        self.assertIn("find_question_block_problems(scrubbed, product_name)", cli_src)

    def test_every_format_and_the_rewrite_prompt_carry_the_rule(self):
        product = {"name": self.TOP, "affiliate_url": "https://amzn.to/3TestABC"}
        for fmt in ("roundup", "single_review", "buying_guide"):
            with self.subTest(fmt=fmt):
                prompt = self.gp.make_prompt("Best toys", "dog toys", "best-x", fmt,
                                             product, "", "")
                self.assertIn(self.gp.QUESTION_BLOCK_RULE, prompt)
                self.assertNotIn("FAQ (H2", prompt)
        rewrite = self.gp.make_rewrite_prompt("T", "k", "body", "fix it")
        self.assertIn(self.gp.QUESTION_BLOCK_RULE, rewrite)
        # The system prompt points its opening rule at the lead, not at a scene.
        self.assertIn("first paragraph is the answer-first lead",
                      self.gp.GENERATOR_SYSTEM_PROMPT)
        self.assertNotIn("when it fits, open with", self.gp.GENERATOR_SYSTEM_PROMPT)


class TestChewyUrlShape(unittest.TestCase):
    """Offline decomposition of a stored chewy_url.

    Nothing here touches the network. The wrapper is a URL with the real
    destination inside its own `u=` parameter, so the product slug, the listing
    id and the attribution sku are all readable from the string itself.
    """

    def setUp(self):
        import validate_published_chewy_links as v
        self.v = v

    def test_a_wrapped_link_yields_its_slug_listing_id_and_sku(self):
        p = self.v.parse_chewy_url(CHEWY_GOOD_CHOMCHOM)
        self.assertEqual(p["shape"], "wrapped")
        self.assertEqual(p["product_slug"], "chomchom-roller-pet-hair-remover")
        self.assertEqual(p["dp_id"], "163270")
        self.assertEqual(p["prodsku"], "136285")

    def test_a_bare_chewy_url_is_reported_as_unwrapped_not_as_fine(self):
        """The live FortiFlora link. It works, and it earns nothing: without the
        Impact wrapper the click carries no attribution. Reporting it as OK is
        how a revenue hole stays open indefinitely."""
        p = self.v.parse_chewy_url(CHEWY_UNWRAPPED_FORTIFLORA)
        self.assertEqual(p["shape"], "bare")
        self.assertEqual(p["product_slug"], "purina-pro-plan-veterinary-diets")
        self.assertEqual(p["dp_id"], "50029")

    def test_a_sentinel_is_not_mistaken_for_a_link(self):
        self.assertEqual(self.v.parse_chewy_url("REVIEW: no match found")["shape"], "sentinel")

    def test_an_empty_value_is_shapeless_rather_than_malformed(self):
        self.assertEqual(self.v.parse_chewy_url("")["shape"], "none")

    def test_a_non_chewy_url_is_malformed(self):
        self.assertEqual(self.v.parse_chewy_url("https://example.com/x")["shape"], "malformed")


class TestChewyVariantMismatch(unittest.TestCase):
    """The wrong-variant class: right brand, wrong product.

    All three links the Director found by hand were right-brand -- so the brand
    identity gate this file already tests (check_brand_match) would have passed
    every one of them. The one signal that survives offline is a model or size
    number in the URL's own slug contradicting every number in the product name.
    """

    def setUp(self):
        import validate_published_chewy_links as v
        self.v = v

    def _reason(self, url, name):
        return self.v.find_variant_mismatch(self.v.parse_chewy_url(url)["product_slug"], name)

    def test_the_globlazer_77in_link_on_a_74in_review_is_caught(self):
        reason = self._reason(CHEWY_WRONG_GLOBLAZER, CHEWY_WRONG_GLOBLAZER_NAME)
        self.assertTrue(reason, "the 77-in link on the 74in review was not flagged")
        self.assertIn("77", reason)

    def test_the_brand_gate_alone_would_have_passed_that_same_link(self):
        """Why the variant check had to exist: the brand gate agrees the 77-in
        listing and the 74in review are the same brand, which they are.

        Read this as a DEMONSTRATION, not a measurement. The matched name below
        is reconstructed from the URL slug because nobody recorded what Impact
        actually returned for this product on 2026-09-22 -- a real matched name
        could differ in wording. What the test does establish is the shape of
        the weakness: check_brand_match compares leading brand tokens, so any
        two listings from one brand pass it whatever variant each names.
        """
        ok, _ = self.v.check_brand_match(CHEWY_WRONG_GLOBLAZER_NAME,
                                         "Globlazer Big Modern Tower 77 in")
        self.assertTrue(ok)

    def test_a_correct_link_whose_slug_carries_no_number_is_not_flagged(self):
        self.assertEqual(self._reason(CHEWY_GOOD_CHOMCHOM, CHEWY_GOOD_CHOMCHOM_NAME), "")

    def test_a_version_number_written_two_ways_is_not_a_contradiction(self):
        """`catit-senses-20-...` against "Catit Senses 2.0" is the same version.
        A checker that reads the dot as a difference flags a correct link, and a
        check that fires on correct links is switched off within a week."""
        self.assertEqual(self._reason(CHEWY_WRONG_CATIT, CHEWY_WRONG_CATIT_NAME), "")

    def test_every_chewy_link_live_on_the_site_today_passes_the_variant_check(self):
        """The inverse direction, run against the real posts rather than
        fixtures. Nine posts carry a chewy_url; all nine are believed correct
        (the 2026-09-22 repair, plus best-catnip-toys added 2026-09-24 as a
        verified exact match), so any flag here is a false positive."""
        flagged = []
        for entry in self.v.published_chewy_links():
            status, detail = self.v.classify_offline(entry)
            if status == "VARIANT_MISMATCH":
                flagged.append(f"{entry['slug']}: {detail}")
        self.assertEqual(flagged, [])

    # The tie-out kit's slug names 16 (the stake) and the product names 30ft, so
    # the number rule flags it; a person confirmed it on the Chewy listing and
    # VERIFIED_VARIANT_LINKS clears exactly that (post, dp id) pair.
    _TIE_OUT_URL = ("https://chewy.sjv.io/c/7160344/3054490/32975?u=https%3A%2F%2F"
                    "www.chewy.com%2Fpetbobi-heavy-duty-16-in-ground-stake%2Fdp%2F4043878"
                    "&intsrc=APIG_24727")

    def _entry(self, slug, url, name="Petbobi 30ft Dog Tie-Out Cable"):
        return {"slug": slug, "chewy_url": url, "product_name": name}

    def test_the_number_rule_alone_flags_the_tie_out_kit(self):
        self.assertTrue(self._reason(self._TIE_OUT_URL, "Petbobi 30ft Dog Tie-Out Cable"))

    def test_the_confirmed_tie_out_kit_is_clean(self):
        status, _ = self.v.classify_offline(
            self._entry("best-outdoor-dog-tie-outs", self._TIE_OUT_URL))
        self.assertEqual(status, "CLEAN")

    def test_the_exemption_does_not_follow_the_listing_to_another_post(self):
        status, _ = self.v.classify_offline(
            self._entry("best-dog-crates", self._TIE_OUT_URL))
        self.assertEqual(status, "VARIANT_MISMATCH")

    def test_the_exemption_does_not_cover_another_link_on_the_same_post(self):
        other = self._TIE_OUT_URL.replace("4043878", "999")
        status, _ = self.v.classify_offline(
            self._entry("best-outdoor-dog-tie-outs", other))
        self.assertEqual(status, "VARIANT_MISMATCH")

    def test_the_exemption_does_not_hide_the_globlazer_mismatch(self):
        status, _ = self.v.classify_offline(
            self._entry("best-outdoor-dog-tie-outs", CHEWY_WRONG_GLOBLAZER,
                        CHEWY_WRONG_GLOBLAZER_NAME))
        self.assertEqual(status, "VARIANT_MISMATCH")


class TestPublishedChewyLinkCoverage(unittest.TestCase):
    """#1: the blind spot. A published post's own chewy_url was never read.

    `load_products()` keys off products.json -- a rolling queue holding FOUR
    entries -- and a post missing from it resolved to chewy_url "", which the
    validator reported as "SKIP -- no Chewy URL (Amazon-only)". Nine published
    posts carry a live Chewy link in their front matter and not one of the four
    queue entries is among them, so every live Chewy link on the site was
    invisible to the weekly job that exists to check them.
    """

    def setUp(self):
        import validate_published_chewy_links as v
        self.v = v
        self.entries = v.published_chewy_links()

    def test_front_matter_links_are_discovered_without_a_products_json_entry(self):
        slugs = {e["slug"] for e in self.entries}
        self.assertIn("best-dog-probiotic-supplements", slugs)
        self.assertIn("best-gps-dog-trackers", slugs)
        for e in self.entries:
            self.assertTrue(e["chewy_url"], e["slug"])

    def test_no_live_front_matter_link_is_covered_by_the_products_json_queue(self):
        """The measurement behind the claim above, stated over the links a
        reader can actually click. If a future queue does cover them this
        assertion flips, and the coverage argument needs rewriting rather than
        continuing to be quoted."""
        queued = set(self.v.load_products())
        live = {e["slug"] for e in self.entries if e["source"] == "front-matter"}
        # The number of live links grows with every post published with one (8 on
        # 2026-09-23, 9 on 09-24, 10 on 10-01), so it is not pinned. Pinned instead:
        # every post known to carry a link is found, and every slug found is one
        # published post whose own front matter carries an https chewy_url.
        known = {"best-automatic-litter-box", "best-calming-diffusers-pets",
                 "best-cat-dental-treats", "best-catnip-toys", "best-dog-nail-grinder",
                 "best-dog-probiotic-supplements",
                 "best-elevated-dog-beds", "best-gps-dog-trackers",
                 "best-pet-hair-removers-laundry-furniture"}
        self.assertLessEqual(known, live, sorted(known - live))
        for slug in live:
            posts = list((REPO / "_posts").glob(f"2*-{slug}.md"))
            self.assertEqual(len(posts), 1, slug)
            front = posts[0].read_text(encoding="utf-8").split("\n---", 1)[0]
            self.assertRegex(front, r'(?m)^chewy_url: *"?https://', slug)
        self.assertEqual(queued & live, set())

    def test_a_queue_sourced_link_is_labelled_as_such(self):
        """best-dog-cooling-mat is published with no Chewy link in its front
        matter. Its queue entry (retired 2026-09-24) held a REVIEW sentinel;
        reporting that as a live link would overstate what is on the site. The
        entry is supplied here rather than read from the rolling queue."""
        queued = {"best-dog-cooling-mat": {
            "topic": "best-dog-cooling-mat",
            "name": "EHEYCIGA Cooling Mat for Dogs, 41x28 inches, Washable, Non-Slip, Blue",
            "chewy_url": "REVIEW:https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=4094470"
                         "&u=https%3A%2F%2Fwww.chewy.com%2Finvenho-cooling-dog-crate-mat-anti"
                         "%2Fdp%2F4094470&intsrc=APIG_24727"}}
        by_slug = {e["slug"]: e for e in self.v.published_chewy_links(queued)}
        self.assertEqual(by_slug["best-dog-cooling-mat"]["source"], "products.json")
        self.assertEqual(self.v.classify_offline(by_slug["best-dog-cooling-mat"])[0], "REVIEW")

    def test_the_featured_product_name_is_read_off_the_affiliate_anchor(self):
        """Front matter carries no product name, so the name a link is judged
        against comes from the body: the anchor text of the post's own affiliate
        link. A generic anchor ("Buy on Amazon") must not be taken as the name."""
        by_slug = {e["slug"]: e for e in self.entries}
        self.assertEqual(by_slug["best-dog-probiotic-supplements"]["product_name"],
                         "Purina Pro Plan FortiFlora")

    def test_a_store_suffix_is_not_part_of_the_product_name(self):
        """Posts open with a quick-pick line whose anchor ends "on Amazon", and
        check-price cells read just "On Amazon"; neither is the product's name."""
        url = "https://www.amazon.com/dp/B0SUFFIX01?tag=happypetdc-20"
        cases = {
            f"[ChomChom Pet Hair Roller on Amazon]({url})": "ChomChom Pet Hair Roller",
            f"[KONG Classic ON  AMAZON]({url})": "KONG Classic",
            f"[On Amazon]({url}) then [Real Product Name]({url})": "Real Product Name",
            f"[Buy on Amazon]({url}) then [Real Product Name]({url})": "Real Product Name",
            # Only a trailing store name goes: "Amazon Basics" is a brand.
            f"[Amazon Basics Puppy Training Pads]({url})": "Amazon Basics Puppy Training Pads",
            f"[Ride On Amazon Trail Pack]({url})": "Ride On Amazon Trail Pack",
        }
        for body, want in cases.items():
            with self.subTest(body=body):
                self.assertEqual(self.v.extract_featured_product_name(body, url), want)

    def test_post_entry_reads_the_body_after_the_front_matter_bom_or_not(self):
        """D-3: Jekyll ignores a UTF-8 byte-order mark. post_entry has to as
        well, or the body is the whole file and an anchor in the front matter
        is taken for the product name."""
        url = "https://www.amazon.com/dp/B0BOMTEST1?tag=happypetdc-20"
        text = (f"---\nlayout: post\naffiliate_url: {url}\n"
                f'excerpt: "See [Front Matter Decoy]({url})"\n---\n'
                f"Our pick: [Real Product Name]({url}).\n")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "2026-10-05-best-bom-test.md"
            for bom in ("", "\ufeff"):
                with self.subTest(bom=bool(bom)):
                    path.write_text(bom + text, encoding="utf-8")
                    entry = self.v.post_entry(path, {})
                    self.assertEqual(entry["product_name"], "Real Product Name")

    def test_a_post_in_scope_is_never_reported_as_having_no_url(self):
        """The specific silent-skip shape. NO_URL must mean the post carries no
        Chewy link at all, never "the queue did not happen to list it"."""
        for e in self.entries:
            status, detail = self.v.classify_offline(e)
            self.assertNotEqual(status, "NO_URL", f"{e['slug']}: {detail}")

    def test_the_live_fortiflora_link_is_now_a_wrapped_deep_link(self):
        """Was the live UNWRAPPED example until the bare URL was re-wrapped on
        2026-09-28. The unwrapped verdict itself is still pinned below against
        the same URL, so the classifier keeps its coverage."""
        by_slug = {e["slug"]: e for e in self.entries}
        status, detail = self.v.classify_offline(by_slug["best-dog-probiotic-supplements"])
        self.assertEqual(status, "CLEAN", detail)

    def test_a_bare_chewy_url_is_still_reported_as_unwrapped(self):
        entry = {"chewy_url": CHEWY_UNWRAPPED_FORTIFLORA,
                 "product_name": "Purina Pro Plan FortiFlora"}
        status, detail = self.v.classify_offline(entry)
        self.assertEqual(status, "UNWRAPPED", detail)


class TestChewyApiThrottle(unittest.TestCase):
    """The Impact API check is capped per run and rotates, so growth in the post
    count cannot turn the weekly job into a 49-call burst -- and no post drops
    out of coverage to achieve that."""

    def setUp(self):
        import validate_published_chewy_links as v
        self.v = v

    def test_a_single_run_never_exceeds_the_cap(self):
        slugs = [f"post-{i}" for i in range(49)]
        for week in range(1, 54):
            self.assertLessEqual(len(self.v.api_slice(slugs, week)), self.v.MAX_API_LOOKUPS)

    def test_consecutive_runs_cover_every_post(self):
        """Throttling that revisits the same head of the list forever is the
        silent gap wearing a rate limit."""
        slugs = [f"post-{i}" for i in range(49)]
        runs = -(-len(slugs) // self.v.MAX_API_LOOKUPS)
        seen = set()
        for week in range(1, runs + 1):
            seen |= set(self.v.api_slice(slugs, week))
        self.assertEqual(seen, set(slugs))

    def test_a_list_under_the_cap_is_checked_whole_every_run(self):
        slugs = [f"post-{i}" for i in range(self.v.MAX_API_LOOKUPS)]
        self.assertEqual(set(self.v.api_slice(slugs, 7)), set(slugs))

    def test_a_post_deferred_by_the_throttle_says_so(self):
        """DEFERRED is the status that keeps the throttle honest: it is not OK
        and it is not a skip, it is "checked offline, API check queued"."""
        self.assertIn("DEFERRED", self.v.STATUSES)


# The top catalog hit for "ChomChom Pet Hair Roller" in issue #155, name as the
# issue quoted it. Its listing id is invented: the issue did not record one.
CHEWY_FIDO_GLOVE_NAME = "Fido Fave Reusable Washable Pet Hair Remover Glove"


def _chewy_item(name, dp, slug="some-listing"):
    """A catalog item whose Url deep-links to Chewy listing `dp`."""
    return {"Name": name,
            "Url": ("https://chewy.sjv.io/c/7160344/3054490/32975?u=https%3A%2F%2F"
                    f"www.chewy.com%2F{slug}%2Fdp%2F{dp}&intsrc=APIG_24727")}


CHEWY_FIDO_GLOVE_ITEM = _chewy_item(CHEWY_FIDO_GLOVE_NAME, "999001",
                                    "fido-fave-reusable-washable-pet-hair")


class TestChewyStoredLinkIsWhatIsJudged(unittest.TestCase):
    """#137 / #155: a correct link reported as a wrong-brand MISMATCH.

    The validator searched the catalog by the article's product name, took the
    top hit, and compared THAT hit's brand. The stored link was never looked at,
    so a correct ChomChom link was reported as wrong because the search ranked a
    Fido glove first -- and the issue text calls MISMATCH "safe to --fix".

    The real post is read here, so the #155 case is the live link and the
    product name the job actually derives for it, not a reconstruction.
    """

    SLUG = "best-pet-hair-removers-laundry-furniture"

    def setUp(self):
        import chewy_lookup as cl
        import validate_published_chewy_links as v
        self.v, self.cl = v, cl
        post = next((REPO / "_posts").glob(f"2*-{self.SLUG}.md"))
        self.entry = v.post_entry(post, {})
        self.dp = v.parse_chewy_url(self.entry["chewy_url"])["dp_id"]

    def _judge(self, items, url=None, name=None, creds=("x", "y")):
        """Run the validator against a fake catalog. Any real socket fails the test."""
        calls = []

        def fake_search(keyword, page_size=10):
            calls.append(keyword)
            if isinstance(items, Exception):
                raise items
            return list(items)

        with patch.object(self.cl, "ACCOUNT_SID", creds[0]), \
             patch.object(self.cl, "AUTH_TOKEN", creds[1]), \
             patch.object(self.cl, "search_catalog", side_effect=fake_search), \
             patch.object(self.cl, "lookup", side_effect=AssertionError("lookup() called")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            status, detail = self.v.validate_chewy_url(
                url or self.entry["chewy_url"], name or self.entry["product_name"])
        return status, detail, calls

    def test_the_post_under_test_is_the_one_in_issue_155(self):
        self.assertEqual(self.entry["product_name"], "ChomChom Pet Hair Roller")
        self.assertEqual(self.dp, "163270")

    def test_a_different_top_search_hit_is_not_a_mismatch(self):
        """The #155 repro: the catalog returns only the Fido glove. The stored
        listing is not in the results, so there is no verdict -- the slug naming
        ChomChom is noted, never taken as one."""
        status, detail, _ = self._judge([CHEWY_FIDO_GLOVE_ITEM])
        self.assertEqual(status, "UNVERIFIED", detail)
        self.assertIn("names 'chomchom'", detail)

    def test_a_slug_naming_the_brand_on_an_unfound_listing_is_not_ok(self):
        """The FortiFlora shape: right slug, wrong listing id. Chewy routes on
        the id, so a slug that names the brand proves nothing about the link."""
        url = self.entry["chewy_url"].replace("%2Fdp%2F163270", "%2Fdp%2F555555")
        status, detail, _ = self._judge([CHEWY_FIDO_GLOVE_ITEM], url=url)
        self.assertEqual(status, "UNVERIFIED", detail)

    def test_the_stored_listing_is_found_by_its_own_listing_id(self):
        """Fido ranks first; the ChomChom listing the link points to is in the
        results. The verdict comes from the listing, not from the rank."""
        status, detail, _ = self._judge(
            [CHEWY_FIDO_GLOVE_ITEM, _chewy_item(CHEWY_GOOD_CHOMCHOM_NAME, self.dp)])
        self.assertEqual(status, "OK", detail)
        self.assertIn("ChomChom", detail)

    def test_a_stored_listing_of_another_brand_is_a_mismatch(self):
        """The real failure this job exists for still fires: the listing the
        stored link deep-links to is, by the catalog's own name, another brand."""
        status, detail, _ = self._judge(
            [_chewy_item(CHEWY_FIDO_GLOVE_NAME, self.dp)])
        self.assertEqual(status, "MISMATCH", detail)
        self.assertIn("fido", detail.lower())

    def test_an_unidentifiable_target_is_unverified_never_a_mismatch(self):
        """No catalog item carries the stored listing id, and the link's own
        slug names no brand: nothing says where the link goes."""
        url = self.entry["chewy_url"].replace("chomchom-roller-pet-hair-remover",
                                              "reusable-roller-lint-remover")
        status, detail, _ = self._judge([CHEWY_FIDO_GLOVE_ITEM], url=url)
        self.assertEqual(status, "UNVERIFIED", detail)
        self.assertIn("UNVERIFIED", self.v.STATUSES)

    def test_a_slug_of_another_brand_alone_is_unverified_not_a_mismatch(self):
        """A slug is not what Chewy routes on, so it is never a verdict either
        way -- least of all MISMATCH, which --fix acts on."""
        url = self.entry["chewy_url"].replace("chomchom-roller-pet-hair-remover",
                                              "fido-fave-reusable-washable-pet-hair")
        status, detail, _ = self._judge([], url=url)
        self.assertEqual(status, "UNVERIFIED", detail)

    def test_a_listing_spelling_the_brand_without_punctuation_is_ok(self):
        """A listing titled "Hills ..." is the brand "Hill's"."""
        url = self.entry["chewy_url"].replace("%2Fdp%2F163270", "%2Fdp%2F37854")
        status, detail, _ = self._judge(
            [_chewy_item("Hills Science Diet Adult 7 Chicken Recipe Dry Dog Food", "37854")],
            url=url, name="Hill's Science Diet Adult 7+")
        self.assertEqual(status, "OK", detail)

    def test_a_found_listing_with_no_brand_to_compare_is_unverified(self):
        """check_brand_match gives no verdict on a product name with no brand
        token, and no verdict is UNVERIFIED -- never OK, never MISMATCH."""
        status, detail, _ = self._judge([_chewy_item(CHEWY_GOOD_CHOMCHOM_NAME, self.dp)],
                                        name="the a for")
        self.assertEqual(status, "UNVERIFIED", detail)

    def test_an_unfound_listing_with_no_brand_word_says_so(self):
        status, detail, _ = self._judge([], name="the a for")
        self.assertEqual(status, "UNVERIFIED", detail)
        self.assertIn("no brand word", detail)
        self.assertNotIn("''", detail)

    def test_no_credentials_means_no_search_and_no_verdict(self):
        status, detail, calls = self._judge([CHEWY_FIDO_GLOVE_ITEM], creds=("", ""))
        self.assertEqual(status, "UNVERIFIED", detail)
        self.assertEqual(calls, [])

    def test_an_api_failure_is_still_an_error(self):
        status, detail, _ = self._judge(self.cl.ChewyAPIError("HTTP 503"))
        self.assertEqual(status, "ERROR", detail)


class TestChewyFixNeverClearsAMatchingLink(unittest.TestCase):
    """--fix clears products.json entries reported MISMATCH. It must never clear
    a link whose own slug names the expected brand, whatever produced the
    verdict -- the issue text tells the reader MISMATCH is safe to --fix. The
    guard reads the slug only; a MISMATCH on a slug of another brand rests on
    the catalog verdict alone."""

    GOOD_URL = CHEWY_GOOD_CHOMCHOM
    BAD_URL = CHEWY_FIDO_GLOVE_ITEM["Url"]

    def setUp(self):
        import chewy_lookup as cl
        import validate_published_chewy_links as v
        self.v, self.cl = v, cl
        import shutil
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "_posts").mkdir()
        amazon = "https://www.amazon.com/dp/B00BAGTNAQ?tag=happypetdc-20"
        # No chewy_url in front matter, so the queue entry is the link judged --
        # the only source --fix can write to.
        (self.tmp / "_posts" / "2026-08-20-pet-hair.md").write_text(
            f"---\nlayout: post\naffiliate_url: {amazon}\n---\n"
            f"Pick: [ChomChom Pet Hair Roller]({amazon})\n", encoding="utf-8")

    def _run_fix(self, chewy_url, items, forced=None):
        products = self.tmp / "products.json"
        # Compact on purpose: a rewrite by atomic_write_json (indent=2) changes
        # the bytes even when no value changed.
        # The bystander is a queue entry with no post and no link: nothing judges
        # it, so a fix run must leave its price where it is.
        products.write_text(json.dumps([{"topic": "pet-hair", "name": "ChomChom Pet Hair Roller",
                                         "chewy_url": chewy_url, "chewy_price": "24.99"},
                                        {"topic": "bystander", "chewy_url": None,
                                         "chewy_price": "9.99"}],
                                       separators=(",", ":")), encoding="utf-8")
        before = products.read_bytes()
        extra = (patch.object(self.v, "validate_chewy_url", return_value=forced)
                 if forced else contextlib.nullcontext())
        with patch.object(self.v, "REPO_DIR", self.tmp), \
             patch.object(self.v, "POSTS_DIR", self.tmp / "_posts"), \
             patch.object(self.v, "LOG_PATH", self.tmp / "val.log"), \
             patch.object(self.cl, "ACCOUNT_SID", "x"), \
             patch.object(self.cl, "AUTH_TOKEN", "y"), \
             patch.object(self.cl, "search_catalog", return_value=list(items)), \
             patch("urllib.request.urlopen", side_effect=AssertionError("network used")), \
             patch.object(sys, "argv", ["validate", "--fix", "--report-only"]), \
             extra:
            with contextlib.suppress(SystemExit):
                self.v.main()
        log = self.tmp / "val.log"
        return before, products.read_bytes(), (log.read_text(encoding="utf-8") if log.exists() else "")

    def test_the_155_false_alarm_leaves_the_correct_link_alone(self):
        """Never reaches --fix at all: the stored listing is not in the results,
        so the verdict is UNVERIFIED."""
        before, after, log = self._run_fix(self.GOOD_URL, [CHEWY_FIDO_GLOVE_ITEM])
        self.assertEqual(after, before)
        self.assertIn("UNVERIFIED pet-hair", log)

    def test_a_forced_mismatch_still_cannot_clear_a_link_naming_the_brand(self):
        """The guard sits at the write, not only in the verdict: even a MISMATCH
        from somewhere else cannot clear a link whose slug names the brand."""
        before, after, log = self._run_fix(self.GOOD_URL, [], forced=("MISMATCH", "forced"))
        self.assertEqual(after, before)
        self.assertIn("FIX REFUSED pet-hair", log)

    def test_a_catalog_mismatch_on_a_slug_naming_the_brand_is_not_cleared(self):
        """Conflicting evidence, from the real verdict path: the catalog names the
        stored listing Fido while the link's slug names ChomChom. The validator
        reports MISMATCH; the guard keeps --fix off it, and a person decides."""
        before, after, log = self._run_fix(self.GOOD_URL,
                                           [_chewy_item(CHEWY_FIDO_GLOVE_NAME, "163270")])
        self.assertIn("MISMATCH pet-hair", log, "the real verdict path never reached MISMATCH")
        self.assertIn("FIX REFUSED pet-hair", log)
        self.assertEqual(after, before)

    def test_a_link_to_another_brand_is_still_cleared(self):
        """The inverse: --fix still does its job, so the two refusal tests above
        pass because of the guard, not because --fix never writes. (The #155
        test passes earlier still: its verdict is UNVERIFIED, not MISMATCH.)"""
        _, after, log = self._run_fix(self.BAD_URL, [CHEWY_FIDO_GLOVE_ITEM])
        self.assertIn("FIX: cleared chewy_url for pet-hair", log)
        entry, bystander = json.loads(after)
        self.assertIsNone(entry["chewy_url"])
        self.assertIsNone(entry["chewy_price"])
        self.assertEqual(bystander["chewy_price"], "9.99", "only the judged entry is cleared")


# ---------------------------------------------------------------------------
# Cross-module symbol integrity: generate_posts is a shared module, and the
# modules that import it reach through it at CALL time.
# ---------------------------------------------------------------------------

def _generate_posts_references(source: str) -> tuple[set, set]:
    """Return (attributes reached through a generate_posts alias, names imported
    directly out of generate_posts) for one module's source text.

    The alias is read off the module's own import statement rather than assumed
    to be `gp`. A checker hardcoded to one spelling covers exactly zero symbols
    in a module that aliases differently, and passes green while doing it.
    """
    import ast
    tree    = ast.parse(source)
    aliases = set()
    from_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name == "generate_posts":
                    aliases.add(name.asname or name.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "generate_posts":
            for name in node.names:
                from_names.add(name.name)
    attrs = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases):
            attrs.add(node.attr)
    return attrs, from_names


class TestGeneratePostsSymbolsReachedFromOtherModulesExist(unittest.TestCase):
    """refill_products.py was broken for two months and no test saw it.

    `ideate_topics()` calls `gp.GEMINI_GEN_MODEL`, which commit 0cf4890 (the
    OpenRouter routing refactor) deleted from generate_posts.py on 2026-07-21.
    Python resolves that attribute at call time, the only caller sits behind a
    threshold gate that rarely opens, and the handler around it caught
    (RuntimeError, JSONDecodeError, KeyError) -- not AttributeError. So the
    break surfaced as an uncaught traceback on a forced run, and nowhere else.

    This checks the shape of that bug, not the one symbol somebody already
    found: every non-test module in the repo that imports generate_posts is
    parsed and every symbol it reaches for must exist on the real module.
    Deleting any shared symbol goes red here, immediately, with no run needed.

    Test modules are excluded deliberately -- a test legitimately reaches for
    attributes it installs itself with patch/setattr, and the suite covers
    those by running them.
    """

    def _scanned_modules(self):
        import generate_posts as gp
        found = []
        for path in sorted(REPO.glob("*.py")):
            if path.name.startswith("test_") or path.name == "generate_posts.py":
                continue
            source = path.read_text(encoding="utf-8")
            if "generate_posts" not in source:
                continue
            attrs, from_names = _generate_posts_references(source)
            if attrs or from_names:
                found.append((path.name, attrs, from_names, gp))
        return found

    def test_the_scan_actually_finds_importers_and_symbols(self):
        """A vacuous scan passes every assertion below it. refill_products.py
        and stage1_cli.py both import generate_posts today; if neither is seen,
        the collector is broken, not the repo."""
        scanned = self._scanned_modules()
        names   = {name for name, _, _, _ in scanned}
        self.assertIn("refill_products.py", names)
        self.assertIn("stage1_cli.py", names)
        total = sum(len(a) + len(f) for _, a, f, _ in scanned)
        self.assertGreater(total, 3, "collector found almost nothing -- suspect the collector")

    def test_every_symbol_reached_through_generate_posts_exists(self):
        missing = []
        for name, attrs, from_names, gp in self._scanned_modules():
            for symbol in sorted(attrs | from_names):
                if not hasattr(gp, symbol):
                    missing.append(f"{name} -> generate_posts.{symbol}")
        self.assertEqual(missing, [], "symbols reached across modules but absent "
                                      "from generate_posts (call-time AttributeError)")

    def test_the_checker_reports_a_missing_symbol_when_there_is_one(self):
        """Prove it can fail. A checker that cannot go red is not evidence.

        Same shape as the real break: aliased import, attribute read inside a
        function body, alias spelled something other than `gp`.
        """
        import generate_posts as gp
        source = ("import generate_posts as writer\n"
                  "def f():\n"
                  "    return writer.THIS_SYMBOL_WAS_DELETED\n")
        attrs, from_names = _generate_posts_references(source)
        self.assertIn("THIS_SYMBOL_WAS_DELETED", attrs)
        self.assertFalse(hasattr(gp, "THIS_SYMBOL_WAS_DELETED"))

    def test_an_alias_that_is_not_gp_is_still_followed(self):
        """The inverse direction: the collector must not be keyed to one name.
        `from generate_posts import X` is picked up too."""
        attrs, from_names = _generate_posts_references(
            "import generate_posts as writer\n"
            "from generate_posts import build_url\n"
            "writer.SITE_BASE\n")
        self.assertIn("SITE_BASE", attrs)
        self.assertIn("build_url", from_names)


class TestRefillIdeationResolvesItsModelConstant(unittest.TestCase):
    """The call path itself, run with the network stubbed out.

    The symbol scan above is static; this one executes ideate_topics(), which is
    where the AttributeError actually fired. Two independent catches for one
    bug, on purpose -- the static check cannot see a symbol built at runtime,
    and this check cannot see a symbol nothing calls.
    """

    def test_ideate_topics_runs_and_returns_candidates(self):
        import refill_products as rp
        import generate_posts as gp

        captured = {}

        def fake_call_gemini(model, prompt, **kwargs):
            captured["model"]  = model
            captured["prompt"] = prompt
            return json.dumps({"topics": [{
                "topic": "heated-cat-bed", "title": "Best Heated Cat Beds",
                "keyword": "heated cat bed", "species": "cat",
                "category": "cat-beds", "topical_sheet": "HAPPYPET_SHEET_ID_HOME",
                "amazon_search_query": "heated cat bed",
            }]})

        with patch.object(gp, "_call_gemini", fake_call_gemini):
            out = rp.ideate_topics({"best-dog-ramps"}, {"best-cat-trees"}, 5)

        self.assertEqual([t["topic"] for t in out], ["best-heated-cat-bed"])
        self.assertTrue(captured["model"], "no model constant reached the Gemini call")

    def test_the_ideation_model_is_not_the_factcheck_model(self):
        """Restoring the deleted constant by pointing it at FACTCHECK_MODEL
        would make both tests above pass while silently running topic ideation
        on a model tuned for a different job (and a smaller one -- the deleted
        value was gemini-2.5-flash, the fact-checker is gemini-2.5-flash-lite).
        """
        import generate_posts as gp
        self.assertNotEqual(gp.GEMINI_GEN_MODEL, gp.FACTCHECK_MODEL)
        self.assertTrue(gp.GEMINI_GEN_MODEL.startswith("gemini-"),
                        "ideation goes direct to Gemini via _call_gemini, not through OpenRouter")


class TestRefillSurvivesAMissingSharedSymbol(unittest.TestCase):
    """The second half of the fix: a future deletion must degrade, not crash.

    Before this, a missing attribute escaped main()'s handler and the run died
    on a traceback -- which reads as a code fault rather than the "ideation
    produced nothing" condition the script already knows how to report.
    """

    def test_an_attributeerror_in_ideation_is_logged_and_the_run_fails_cleanly(self):
        import refill_products as rp

        lines = []

        def boom(*_a, **_k):
            raise AttributeError("module 'generate_posts' has no attribute 'GEMINI_GEN_MODEL'")

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(rp, "load_products", lambda: []), \
                 patch.object(rp, "published_slugs", lambda: set()), \
                 patch.object(rp, "unpublished_count", lambda *_: 0), \
                 patch.object(rp, "ideate_topics", boom), \
                 patch.object(rp, "RESULT_PATH", Path(tmp) / "REFILL_RESULT.json"), \
                 patch.object(rp, "log", lambda msg, level="INFO": lines.append(f"{level}:{msg}")):
                with self.assertRaises(SystemExit) as caught:
                    rp.main()

        self.assertEqual(caught.exception.code, 1,
                         "a refill that resolved nothing must exit non-zero")
        self.assertTrue(any("topic ideation failed" in ln for ln in lines),
                        f"the failure was not logged: {lines}")


def _load_automerge_gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "automerge_gate", REPO / "automerge_gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAutomergeGate(unittest.TestCase):
    """automerge_gate.py -- decides which PRs the automerge workflow may merge.

    The baseline below is the exact shape of routine Stage-1 PR #111 (author
    DMoneyOH, performed_via_github_app.slug 'claude', three added files, green
    pytest). Each case flips ONE thing and expects a hold, so a passing
    baseline plus failing variants means every guard is actually load-bearing.
    """

    REPO_NAME = "DMoneyOH/HappyPet"
    SHA = "a" * 40
    SLUG = "best-odor-eliminators-pet-stains"

    def setUp(self):
        self.g = _load_automerge_gate()

    def good(self):
        return dict(
            pr={"state": "open", "merged": False, "draft": False,
                "base": {"ref": "main"},
                "head": {"sha": self.SHA, "repo": {"full_name": self.REPO_NAME}},
                "labels": []},
            issue={"user": {"login": "DMoneyOH"},
                   "performed_via_github_app": {"slug": "claude"}},
            files=[{"filename": f"_posts/DRAFT-{self.SLUG}.md", "status": "added"},
                   {"filename": f"_pin_queue/{self.SLUG}.json", "status": "added"},
                   {"filename": f"assets/images/pins/{self.SLUG}.jpg", "status": "added"}],
            check_runs=[{"name": "pytest", "status": "completed",
                         "conclusion": "success", "app": {"slug": "github-actions"}}],
            run_sha=self.SHA, repo=self.REPO_NAME, products_text=None)

    def verdict(self, **over):
        kw = self.good()
        kw.update(over)
        names = [f["filename"] for f in kw["files"]
                 if isinstance(f, dict) and isinstance(f.get("filename"), str)]
        if "modes" not in over:
            kw["modes"] = {n: "100644" for n in names}
        if "pin_texts" not in over:
            kw["pin_texts"] = {n: json.dumps(self.pin_obj(slug=Path(n).stem))
                               for n in names if n.startswith("_pin_queue/")}
        return self.g.evaluate(**kw)

    def test_baseline_routine_pr_merges_and_publishes(self):
        v = self.verdict()
        self.assertTrue(v.ok, v.reasons)
        self.assertTrue(v.publish)

    def test_every_single_flip_is_a_hold(self):
        def with_pr(**kv):
            pr = self.good()["pr"]
            pr.update(kv)
            return {"pr": pr}

        def with_issue(**kv):
            i = self.good()["issue"]
            i.update(kv)
            return {"issue": i}

        ok_check = {"name": "pytest", "status": "completed", "conclusion": "success",
                    "app": {"slug": "github-actions"}}
        other = {"name": "lint", "app": {"slug": "github-actions"}}
        cases = {
            "draft PR":            with_pr(draft=True),
            "closed PR":           with_pr(state="closed"),
            "already merged":      with_pr(merged=True, state="closed"),
            "base is not main":    with_pr(base={"ref": "release"}),
            "fork head":           with_pr(head={"sha": self.SHA,
                                                 "repo": {"full_name": "evil/HappyPet"}}),
            "head moved after CI": with_pr(head={"sha": "b" * 40,
                                                 "repo": {"full_name": self.REPO_NAME}}),
            "opt-out label":       with_pr(labels=[{"name": "no-automerge"}]),
            "author is a stranger": with_issue(user={"login": "mallory"}),
            "owner PR, no app (like #116)": with_issue(performed_via_github_app=None),
            "other app":           with_issue(performed_via_github_app={"slug": "dependabot"}),
            "claude-lookalike slug": with_issue(performed_via_github_app={"slug": "claude-evil"}),
            "zero checks (vacuous green)": {"check_runs": []},
            "pytest red":          {"check_runs": [{**ok_check, "conclusion": "failure"}]},
            "pytest pending":      {"check_runs": [{**ok_check, "status": "in_progress",
                                                    "conclusion": None}]},
            "pytest cancelled":    {"check_runs": [{**ok_check, "conclusion": "cancelled"}]},
            "pytest from a spoofing app": {"check_runs": [{**ok_check, "app": {"slug": "shady"}}]},
            "other check pending": {"check_runs": [ok_check, {**other, "status": "queued",
                                                              "conclusion": None}]},
            "other check red":     {"check_runs": [ok_check, {**other, "status": "completed",
                                                              "conclusion": "failure"}]},
            "empty file list":     {"files": []},
            "too many files":      {"files": [{"filename": f"_pin_queue/s{n}.json",
                                               "status": "added"} for n in range(31)]},
            "CI ran on another sha": {"run_sha": "c" * 40},
        }
        for name, over in cases.items():
            with self.subTest(name):
                v = self.verdict(**over)
                self.assertFalse(v.ok, f"{name} must be held")
                self.assertTrue(v.reasons)

    def test_path_allowlist_case_table(self):
        allowed = [
            f"_posts/DRAFT-{self.SLUG}.md",
            f"_pin_queue/{self.SLUG}.json",
            "assets/images/pins/best-cat-trees-v2.jpg",
            "products.json",
        ]
        held = [
            "_posts/2026-09-24-best-odor-eliminators-pet-stains.md",  # live post, deploys directly
            "_posts/sub/DRAFT-x.md",
            "_posts/DRAFT-x.md.bak",
            "_posts/DRAFT-X.md",                    # upper case is not a slug
            "_posts/DRAFT-.md",
            "_posts/DRAFT-x.md\n",                  # fullmatch, not $-anchored
            "_pin_queue/sent/x.json",
            "_pin_queue/.fired/x.fired",
            "_pin_queue/.pending-slugs",
            "_pin_queue/../generate_posts.py",
            "assets/images/pins/x.png",
            "assets/images/pins/sub/x.jpg",
            "products.json.bak",
            "sub/products.json",
            ".github/workflows/publish.yml",
            ".github/workflows/automerge.yml",
            ".claude/skills/happypet-stage1/SKILL.md",
            "automerge_gate.py",
            "generate_posts.py",
            "test_pipeline.py",
            "_config.yml",
            "",
            None,
        ]
        for p in allowed:
            with self.subTest(allowed=p):
                self.assertTrue(self.g.path_allowed(p))
        for p in held:
            with self.subTest(held=p):
                self.assertFalse(self.g.path_allowed(p))

    def test_one_bad_file_among_good_ones_holds_the_whole_pr(self):
        files = self.good()["files"] + [{"filename": "test_pipeline.py", "status": "modified"}]
        v = self.verdict(files=files)
        self.assertFalse(v.ok)
        self.assertTrue(any("test_pipeline.py" in r for r in v.reasons))

    def test_a_rename_out_of_a_protected_path_is_held(self):
        """The new name looks fine; previous_filename is what exposes the source."""
        files = self.good()["files"] + [
            {"filename": "_posts/DRAFT-stolen.md", "status": "renamed",
             "previous_filename": ".github/workflows/publish.yml"}]
        self.assertFalse(self.verdict(files=files).ok)

    def test_an_off_allowlist_rename_source_is_held_even_if_status_lies(self):
        """Defence in depth under the status rule: previous_filename is checked on its own."""
        for prev in (".github/workflows/publish.yml", "generate_posts.py", 5, ""):
            with self.subTest(prev=prev):
                files = self.good()["files"] + [
                    {"filename": "products.json", "status": "modified", "previous_filename": prev}]
                self.assertFalse(self.verdict(files=files, products_text="[]",
                                              base_products_text="[]").ok)

    def test_deleting_an_off_allowlist_file_is_held(self):
        files = self.good()["files"] + [{"filename": "generate_posts.py", "status": "removed"}]
        self.assertFalse(self.verdict(files=files).ok)

    def test_products_json_only_with_no_placeholders_merges_without_publishing(self):
        v = self.verdict(files=[{"filename": "products.json", "status": "modified"}],
                         products_text='[{"topic": "best-a", "asin": "B0ABCD1234"}]',
                         base_products_text="[]")
        self.assertTrue(v.ok, v.reasons)
        self.assertFalse(v.publish, "a products.json merge must not publish a leftover draft")

    def test_products_json_with_placeholders_or_unreadable_is_held(self):
        for text in ('[{"asin": "NEEDS_ASIN"}]', '[{"image": "NEEDS_IMAGE"}]', None):
            with self.subTest(text=text):
                v = self.verdict(files=[{"filename": "products.json", "status": "modified"}],
                                 products_text=text)
                self.assertFalse(v.ok)

    def test_pin_only_pr_merges_without_publishing(self):
        v = self.verdict(files=[{"filename": f"_pin_queue/{self.SLUG}.json",
                                 "status": "added"}])
        self.assertTrue(v.ok, v.reasons)
        self.assertFalse(v.publish)

    def test_publish_only_when_a_draft_is_added(self):
        for status in ("modified", "removed"):
            with self.subTest(status=status):
                files = [{"filename": f"_posts/DRAFT-{self.SLUG}.md", "status": status}]
                self.assertFalse(self.verdict(files=files).publish)

    # ---- fix round (Verifier findings F1-F7) ---------------------------------------

    PIN_PATH = f"_pin_queue/{SLUG}.json"

    def pin_obj(self, **over):
        d = {"title": "Fresh Home, Happy Pets: Top Odor Eliminators for Pet Stains",
             "article_url": ("https://happypetproductreviews.com/pet-grooming/"
                             f"best-odor-eliminators-pet-stains/?utm_source=pinterest"
                             "&utm_medium=social&utm_campaign=pin"),
             "description": "An enzyme spray that actually removes pet urine smell.",
             "image_url": ("https://happypetproductreviews.com/assets/images/pins/"
                           f"{self.SLUG}.jpg?v=20260924"),
             "species": "both", "slug": self.SLUG,
             "topical_sheet": "HAPPYPET_SHEET_ID_HOME"}
        d.update(over)
        return d

    def pin_text(self, **over):
        return json.dumps(self.pin_obj(**over), indent=2)

    def pin_verdict(self, text):
        return self.verdict(pin_texts={self.PIN_PATH: text})

    # F1: pin JSON content ------------------------------------------------------------

    def test_a_real_looking_pin_file_passes(self):
        self.assertEqual(self.g.pin_problems(self.PIN_PATH, self.pin_text()), [])

    def test_all_38_real_historical_pin_files_pass_the_validator(self):
        """Inverse direction: an alarm that fires on ordinary traffic gets ignored.
        Every real file the pipeline has ever produced must validate."""
        files = sorted((REPO / "_pin_queue" / "sent").glob("*.json"))
        self.assertGreater(len(files), 30, "suspect the glob, not the repo")
        for f in files:
            with self.subTest(f.name):
                self.assertEqual(
                    self.g.pin_problems(f"_pin_queue/{f.stem}.json",
                                        f.read_text(encoding="utf-8")), [])

    def test_pin_injection_payload_table_is_held(self):
        url = "https://happypetproductreviews.com/pet-grooming/best-x/"
        base = "https://happypetproductreviews.com/pet-grooming/best-odor-eliminators-pet-stains/"
        payloads = {
            "newline then second output line (Tessa repro)": {"article_url": url + "\nslugs=evil"},
            "trailing newline": {"article_url": url + "\n"},
            "CRLF": {"article_url": url + "\r\nslugs=evil"},
            "quote-semicolon-echo": {"article_url": url + '"; echo pwned; echo "'},
            "command substitution": {"article_url": url + "$(id)"},
            "backtick": {"article_url": url + "`id`"},
            "semicolon in path": {"article_url": url + ";id"},
            "different query": {"article_url": base + "?utm_source=x&a=1"},
            "extra query after utm": {"article_url": base + "?utm_source=pinterest&utm_medium="
                                      "social&utm_campaign=pin&x=1"},
            "fragment": {"article_url": base + "#x"},
            "http scheme": {"article_url": base.replace("https", "http")},
            "other host": {"article_url": "https://evil.example/pet-grooming/best-x/"},
            "host suffix trick": {"article_url": "https://happypetproductreviews.com.evil.io/a/b/"},
            "userinfo trick": {"article_url": "https://happypetproductreviews.com@evil.io/a/b/"},
            "dot-dot path": {"article_url": "https://happypetproductreviews.com/a/../b/"},
            "curl option": {"article_url": "-o /tmp/x"},
            "url is a list": {"article_url": [url]},
            "url is null": {"article_url": None},
            "image newline": {"image_url": "https://happypetproductreviews.com/assets/images/"
                                           "pins/x.jpg\nslugs=evil"},
            "image other host": {"image_url": "https://evil.example/assets/images/pins/x.jpg"},
            "image wrong ext": {"image_url": "https://happypetproductreviews.com/assets/"
                                             "images/pins/x.svg"},
            "title newline": {"title": "ok\nslugs=evil"},
            "title CRLF": {"title": "ok\r\nx"},
            "title quote": {"title": 'say "hi"'},
            "title backtick": {"title": "a `b`"},
            "title backslash": {"title": "a\\nb"},
            "title angle": {"title": "<script>x"},
            "title spreadsheet formula": {"title": "=HYPERLINK(\"x\")"},
            "title plus formula": {"title": "+1+1"},
            "title line separator": {"title": "a\u2028b"},
            "title too long": {"title": "a" * 301},
            "title empty": {"title": ""},
            "description NUL": {"description": "a\x00b"},
            "description tab": {"description": "a\tb"},
            "description too long": {"description": "a" * 1001},
            "slug not the file name": {"slug": "best-other"},
            "slug traversal": {"slug": "../x"},
            "species bad": {"species": "dog\nx"},
            "sheet bad": {"topical_sheet": "HAPPYPET_SHEET_ID_HOME\nx"},
            "unknown key": {"evil": "x"},
        }
        for name, over in payloads.items():
            with self.subTest(name):
                problems = self.g.pin_problems(self.PIN_PATH, self.pin_text(**over))
                self.assertTrue(problems, f"{name} must be held")
                self.assertFalse(self.pin_verdict(self.pin_text(**over)).ok)

    def test_pin_structure_problems_are_held(self):
        good = self.pin_obj()
        cases = {
            "not JSON": "{nope",
            "empty": "",
            "list": "[]",
            "string": '"x"',
            "null": "null",
            "missing title": json.dumps({k: v for k, v in good.items() if k != "title"}),
            "missing article_url": json.dumps({k: v for k, v in good.items() if k != "article_url"}),
            "missing slug": json.dumps({k: v for k, v in good.items() if k != "slug"}),
            "too big": json.dumps({**good, "description": "a" * 900}) + " " * 4200,
            "None content": None,
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.assertTrue(self.g.pin_problems(self.PIN_PATH, text))
        self.assertFalse(self.verdict(pin_texts={}).ok, "unfetched pin content must hold")
        self.assertFalse(self.verdict(pin_texts={self.PIN_PATH: None}).ok)

    def test_legitimate_pin_variations_are_not_falsely_held(self):
        variants = [
            {"article_url": "https://happypetproductreviews.com/pet-grooming/best-x/"},
            {"image_url": "https://happypetproductreviews.com/assets/images/pins/x.jpg"},
            {"image_url": "https://happypetproductreviews.com/assets/images/pins/x-v2.jpg"
                          "?v=20260101"},
            {"image_url": "https://m.media-amazon.com/images/I/71gSvAen4NL._AC_SX425_.jpg"
                          "?v=20260419"},
            {"topical_sheet": None},
            {"description": "Costs $20 | it's great \u2011 really"},
            {"title": "Pawsitively Protected: The Best Dog Boots for Hot Pavement"},
        ]
        for over in variants:
            with self.subTest(over):
                self.assertEqual(self.g.pin_problems(self.PIN_PATH, self.pin_text(**over)), [])

    # Tessa re-check: mutants that survived because nothing pinned them ---------------

    def test_pin_host_dot_is_literal_not_a_wildcard(self):
        """`_SITE` must escape the dot: `happypetproductreviewsXcom` is another host."""
        for host in ("happypetproductreviewsXcom", "happypetproductreviews-com",
                     "happypetproductreviews_com", "happypetproductreviews com"):
            with self.subTest(host=host):
                url = f"https://{host}/pet-grooming/best-x/"
                self.assertTrue(self.g.pin_problems(self.PIN_PATH,
                                                    self.pin_text(article_url=url)))
                img = f"https://{host}/assets/images/pins/x.jpg"
                self.assertTrue(self.g.pin_problems(self.PIN_PATH,
                                                    self.pin_text(image_url=img)))

    def test_pin_text_starting_with_a_formula_leader_is_held_even_after_whitespace(self):
        for key in ("title", "description"):
            for value in ("@SUM(1)", " @SUM(1)", "-1+1", " -1+1", "=1+1", " =1+1", "+1", " +1",
                          "ok ", " ok"):
                with self.subTest(key=key, value=value):
                    self.assertTrue(self.g.pin_problems(self.PIN_PATH,
                                                        self.pin_text(**{key: value})))
        for key in ("title", "description"):     # inverse: an @ or - INSIDE the text is fine
            with self.subTest(inside=key):
                self.assertEqual(self.g.pin_problems(
                    self.PIN_PATH, self.pin_text(**{key: "Dogs - cats @ home, 2-pack"})), [])

    def test_pin_size_cap_boundary_is_exactly_4096_bytes(self):
        base = self.pin_text()
        for size, ok in ((4095, True), (4096, True), (4097, False)):
            with self.subTest(size=size):
                text = base + " " * (size - len(base.encode("utf-8")))
                self.assertEqual(len(text.encode("utf-8")), size)
                self.assertEqual(self.g.pin_problems(self.PIN_PATH, text) == [], ok)

    def test_pin_size_cap_counts_bytes_not_characters(self):
        obj = self.pin_obj(title="\u00e9" * 300, description="\u00e9" * 1000)
        text = json.dumps(obj, ensure_ascii=False)
        text += " " * (4090 - len(text))
        self.assertLess(len(text), 4096)
        self.assertGreater(len(text.encode("utf-8")), 4096)
        self.assertTrue(self.g.pin_problems(self.PIN_PATH, text))

    # F2: products.json is parsed, not substring-searched ------------------------------

    def test_json_escaped_placeholder_cannot_bypass_the_parse(self):
        escaped = '[{"topic": "best-x", "asin": "\\u004eEEDS_ASIN"}]'
        self.assertNotIn("NEEDS_ASIN", escaped, "the fixture must defeat a raw substring search")
        self.assertTrue(self.g.products_problems(escaped, escaped))
        cases = {
            "escaped image": '[{"image": "\\u004EEEDS_IMAGE"}]',
            "escaped inside url": '[{"affiliate_url": "https://a/dp/NEEDS_\\u0041SIN?tag=x"}]',
            "nested value": '[{"a": {"b": [{"c": "NEEDS_ASIN"}]}}]',
            "as a key": '[{"NEEDS_IMAGE": "x"}]',
            "escaped key": '{"\\u004eEEDS_ASIN": 1}',
            "top-level string": '"NEEDS_ASIN"',
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.assertTrue(self.g.products_problems(text, "[]"), name)

    def test_unparsable_products_json_is_held(self):
        for text in ("{nope", "", "[", "[]]", "NaN garbage", None):
            with self.subTest(text=text):
                self.assertTrue(self.g.products_problems(text, "[]"))
        deep = "[" * 200 + "]" * 200
        self.assertTrue(self.g.products_problems(deep, "[]"))

    def test_review_sentinel_only_blocks_when_newly_introduced(self):
        old = '[{"topic": "a", "chewy_url": "REVIEW:https://chewy.example/x"}]'
        self.assertEqual(self.g.products_problems(old, old), [], "pre-existing sentinel is fine")
        new = ('[{"topic": "a", "chewy_url": "REVIEW:https://chewy.example/x"},'
               ' {"topic": "b", "chewy_url": "REVIEW"}]')
        self.assertTrue(self.g.products_problems(new, old))
        self.assertTrue(self.g.products_problems(new, "[]"))
        self.assertTrue(self.g.products_problems(old, None), "unreadable base + sentinel = hold")
        self.assertTrue(self.g.products_problems(old, "{broken"))
        escaped = '[{"chewy_url": "\\u0052EVIEW:https://x"}]'
        self.assertTrue(self.g.products_problems(escaped, "[]"))
        prose = '[{"chewy_note": "UNVERIFIED: chewy_url is a REVIEW: sentinel"}]'
        self.assertEqual(self.g.products_problems(prose, "[]"), [], "prose is not a sentinel")

    def test_review_sentinel_is_anything_starting_with_review_like_the_pipeline(self):
        """generate_posts.py / validate_published_chewy_links.py use startswith("REVIEW")."""
        for value in ("REVIEW", "REVIEW:https://x", "REVIEW-x", "REVIEWED", "REVIEW https://x",
                      "REVIEW\u00a0x", "REVIEW_"):
            with self.subTest(value=value):
                text = json.dumps([{"topic": "a", "chewy_url": value}])
                self.assertTrue(self.g.products_problems(text, "[]"), value)
        for value in ("review", "Review:x", "UNREVIEW", " REVIEW", "x REVIEW", "", None, 5):
            with self.subTest(harmless=value):
                text = json.dumps([{"topic": "a", "chewy_url": value}])
                self.assertEqual(self.g.products_problems(text, "[]"), [], value)

    def test_review_sentinel_is_compared_per_topic_and_field(self):
        def prods(*entries):
            return json.dumps(list(entries))
        a_rev = {"topic": "a", "chewy_url": "REVIEW:https://chewy.example/a", "asin": "B0A"}
        b_clean = {"topic": "b", "chewy_url": None, "asin": "B0B"}
        base = prods(a_rev, b_clean)
        # untouched, other edits, re-ordered, sentinel re-pointed: all fine
        self.assertEqual(self.g.products_problems(base, base), [])
        self.assertEqual(self.g.products_problems(
            prods(b_clean, {**a_rev, "asin": "B0Z"}), base), [])
        self.assertEqual(self.g.products_problems(prods(b_clean, a_rev), base), [])
        self.assertEqual(self.g.products_problems(
            prods(a_rev, b_clean, {"topic": "c", "chewy_url": None}), base), [])
        self.assertEqual(self.g.products_problems(
            prods({**a_rev, "chewy_url": "REVIEW:https://chewy.example/other"}, b_clean),
            base), [], "same topic + field, new URL: not a new sentinel")
        # moved to another topic (old one cleared): held
        moved = prods({**a_rev, "chewy_url": None}, {**b_clean, "chewy_url": "REVIEW:https://x"})
        self.assertTrue(self.g.products_problems(moved, base), "moved to topic b")
        # same value on a new topic while the old one stays: held
        both = prods(a_rev, {**b_clean, "chewy_url": a_rev["chewy_url"]})
        self.assertTrue(self.g.products_problems(both, base), "copied to topic b")
        # a brand-new topic carrying one: held
        new_topic = prods(a_rev, b_clean, {"topic": "c", "chewy_url": "REVIEW"})
        self.assertTrue(self.g.products_problems(new_topic, base), "new topic c")
        # same topic, different field: held
        other_field = prods({**a_rev, "image": "REVIEW:x"}, b_clean)
        self.assertTrue(self.g.products_problems(other_field, base), "different field")
        swapped = prods({**a_rev, "chewy_url": None, "image": "REVIEW:x"}, b_clean)
        self.assertTrue(self.g.products_problems(swapped, base), "moved to another field")
        # two entries with one topic name: the second sentinel is one more than the base has
        twin = prods(a_rev, {**a_rev})
        self.assertTrue(self.g.products_problems(twin, base), "duplicate topic, second sentinel")
        # a sentinel in a dict KEY counts too
        keyed = prods(a_rev, {**b_clean, "REVIEW:k": 1})
        self.assertTrue(self.g.products_problems(keyed, base), "sentinel as a key")

    def test_verdict_holds_a_products_pr_that_moves_a_review_sentinel(self):
        base = '[{"topic": "a", "chewy_url": "REVIEW:x"}, {"topic": "b", "chewy_url": null}]'
        head = '[{"topic": "a", "chewy_url": null}, {"topic": "b", "chewy_url": "REVIEW:x"}]'
        files = [{"filename": "products.json", "status": "modified"}]
        self.assertTrue(self.verdict(files=files, products_text=base,
                                     base_products_text=base).ok)
        self.assertFalse(self.verdict(files=files, products_text=head,
                                      base_products_text=base).ok)

    def test_the_real_products_json_does_not_trip_the_parse_rules(self):
        text = (REPO / "products.json").read_text(encoding="utf-8")
        reasons = self.g.products_problems(text, text)
        self.assertFalse([r for r in reasons if "parseable" in r or "REVIEW" in r], reasons)

    def test_verdict_holds_a_products_pr_with_an_escaped_placeholder(self):
        v = self.verdict(files=[{"filename": "products.json", "status": "modified"}],
                         products_text='[{"asin": "\\u004eEEDS_ASIN"}]', base_products_text="[]")
        self.assertFalse(v.ok)

    # F3: the exact merge and dispatch commands ----------------------------------------

    def run_process(self, **over):
        """Run process() end to end against a fake gh; return (rc, gh calls made)."""
        kw = self.good()
        kw.update(over)
        calls = []
        pulls = [dict(kw["pr"], base={"ref": "main", "sha": "d" * 40})]

        def fake_json(*args):
            if args[1].endswith("/issues/117"):
                return kw["issue"]
            return pulls.pop(0) if pulls else {"merged": True}

        g = self.g
        with patch.object(g, "gh", lambda *a: calls.append(list(a)) or ""), \
             patch.object(g, "gh_json", fake_json), \
             patch.object(g, "fetch_files", lambda *a: kw["files"]), \
             patch.object(g, "fetch_check_runs", lambda *a: kw["check_runs"]), \
             patch.object(g, "fetch_text", lambda repo, path, ref:
                          self.pin_text() if path.startswith("_pin_queue/") else "[]"), \
             patch.object(g, "fetch_modes", lambda *a: {f["filename"]: "100644"
                                                        for f in kw["files"]}), \
             patch.object(g, "summary", lambda *_: None):
            try:
                rc = g.process(self.REPO_NAME, 117, kw["run_sha"], dry_run=False)
            except Exception as exc:    # a gate error: main() holds the PR and exits 1
                rc = exc
        return rc, calls

    def test_the_exact_merge_and_publish_commands_are_pinned(self):
        rc, calls = self.run_process()
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [
            ["pr", "merge", "117", "--repo", self.REPO_NAME, "--merge",
             "--match-head-commit", self.SHA],
            ["workflow", "run", "publish.yml", "--repo", self.REPO_NAME, "--ref", "main"],
        ])
        flat = " ".join(" ".join(c) for c in calls)
        for forbidden in ("--admin", "--auto", "--delete-branch", "--squash", "--rebase"):
            self.assertNotIn(forbidden, flat)

    def test_a_held_pr_makes_no_write_call_at_all(self):
        draft = dict(self.good()["pr"], draft=True)
        rc, calls = self.run_process(pr=draft)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])

    def test_an_unexpected_exception_in_the_gate_fails_closed(self):
        """It leaves process() before any write; main() holds the PR and exits 1."""
        def boom(*a, **k):
            raise RuntimeError("unforeseen shape")
        with patch.object(self.g, "evaluate", boom):
            rc, calls = self.run_process()
        self.assertIsInstance(rc, RuntimeError)
        self.assertEqual(calls, [], "a gate error must never reach the merge")

    def test_unreadable_check_runs_hold(self):
        v = self.verdict(check_runs=None)
        self.assertFalse(v.ok)
        self.assertTrue(any("check runs" in r for r in v.reasons), v.reasons)

    def test_a_products_only_merge_does_not_dispatch_publish(self):
        rc, calls = self.run_process(files=[{"filename": "products.json", "status": "modified"}])
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ["pr", "merge"])

    # F4: only 'added' for the draft, pin image and pin JSON ---------------------------

    def test_only_added_is_mergeable_for_draft_pin_image_and_pin_json(self):
        paths = [f"_posts/DRAFT-{self.SLUG}.md", self.PIN_PATH,
                 f"assets/images/pins/{self.SLUG}.jpg"]
        for path in paths:
            for status in ("modified", "removed", "renamed", "changed", "copied", None, "ADDED"):
                with self.subTest(path=path, status=status):
                    files = [f for f in self.good()["files"] if f["filename"] != path]
                    files.append({"filename": path, "status": status})
                    self.assertFalse(self.verdict(files=files).ok)

    def test_products_json_may_be_modified_but_not_removed_or_renamed(self):
        for status, ok in (("modified", True), ("added", True), ("removed", False),
                           ("renamed", False)):
            with self.subTest(status):
                v = self.verdict(files=[{"filename": "products.json", "status": status}],
                                 products_text="[]", base_products_text="[]")
                self.assertEqual(v.ok, ok, v.reasons)

    # F5: label normalisation --------------------------------------------------------

    def test_opt_out_label_matches_however_it_is_spelled(self):
        for name in ("no-automerge", "No-Automerge", "NO-AUTOMERGE", " no-automerge ",
                     "no_automerge", "no automerge", "No Automerge", "no--automerge",
                     "no-_automerge", "no\tautomerge"):
            with self.subTest(name):
                pr = dict(self.good()["pr"], labels=[{"name": name}])
                self.assertFalse(self.verdict(pr=pr).ok)
        for name in ("automerge", "no-automerge-please-not", "noautomerge", "keep"):
            with self.subTest(harmless=name):
                pr = dict(self.good()["pr"], labels=[{"name": name}])
                self.assertTrue(self.verdict(pr=pr).ok)

    def test_opt_out_label_survives_unicode_lookalikes_and_invisible_characters(self):
        dashes = ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212",
                  "\ufe63", "\uff0d"]
        names = [f"no{d}automerge" for d in dashes]
        names += ["no\u200b-automerge", "no-\u200bautomerge", "n\u200bo-automerge",
                  "\u200bno-automerge", "no-automerge\ufeff", "no\u200d-automerge",
                  "no\u2060-automerge", "no\u00ad-automerge", "no\u200b\u2010automerge",
                  "\uff4e\uff4f-\uff41\uff55\uff54\uff4f\uff4d\uff45\uff52\uff47\uff45",
                  "NO\u2014AUTOMERGE", "no\u00a0automerge", "no\u2003automerge"]
        for name in names:
            with self.subTest(repr(name)):
                pr = dict(self.good()["pr"], labels=[{"name": name}])
                self.assertFalse(self.verdict(pr=pr).ok, f"{name!r} must hold")
        for name in ("no\u2010automerge-please", "no\u200bautomerge", "automerge",
                     "no-automation", "\u2014automerge"):
            with self.subTest(harmless=repr(name)):
                pr = dict(self.good()["pr"], labels=[{"name": name}])
                self.assertTrue(self.verdict(pr=pr).ok)

    def test_a_malformed_label_entry_does_not_crash_or_disarm_the_opt_out(self):
        pr = dict(self.good()["pr"], labels=[None, "No-Automerge", {"name": None}])
        self.assertFalse(self.verdict(pr=pr).ok)

    # F6: malformed entries fail closed ----------------------------------------------

    def test_malformed_file_entries_are_held_not_skipped(self):
        for bad in (None, "x", 5, {}, {"status": "added"}, {"filename": None, "status": "added"},
                    {"filename": 123, "status": "added"}, {"filename": "", "status": "added"},
                    {"filename": ["a"], "status": "added"}):
            with self.subTest(bad=bad):
                v = self.verdict(files=self.good()["files"] + [bad])
                self.assertFalse(v.ok)
                self.assertTrue(any("malformed" in r for r in v.reasons), v.reasons)
        files = self.good()["files"] + [{"filename": "products.json", "status": "modified",
                                         "previous_filename": 5}]
        self.assertFalse(self.verdict(files=files, products_text="[]").ok)

    def test_malformed_check_entries_fail_closed_with_a_clear_reason(self):
        ok = self.good()["check_runs"][0]
        for runs in ([None], [ok, None], [ok, "pytest"], [ok, 5], [ok, []]):
            with self.subTest(runs=runs):
                v = self.verdict(check_runs=runs)
                self.assertFalse(v.ok)
                self.assertTrue(any("malformed check entry" in r for r in v.reasons), v.reasons)

    def test_a_check_entry_missing_its_app_is_held_not_a_crash(self):
        ok = self.good()["check_runs"][0]
        v = self.verdict(check_runs=[{k: val for k, val in ok.items() if k != "app"}])
        self.assertFalse(v.ok)

    # F7: symlink / executable / submodule ---------------------------------------------

    def test_only_regular_files_pass_the_mode_check(self):
        draft = f"_posts/DRAFT-{self.SLUG}.md"
        for mode in ("120000", "100755", "160000", "040000", None, ""):
            with self.subTest(mode=mode):
                modes = {f["filename"]: "100644" for f in self.good()["files"]}
                modes[draft] = mode
                self.assertFalse(self.verdict(modes=modes).ok)
        self.assertFalse(self.verdict(modes=None).ok, "an unreadable tree must hold")
        self.assertFalse(self.verdict(modes={}).ok, "a path missing from the tree must hold")

    def test_a_truncated_tree_read_is_a_gate_error(self):
        with patch.object(self.g, "gh_json", lambda *a: {"truncated": True, "tree": []}):
            with self.assertRaises(self.g.UnreadableRead):
                self.g.fetch_modes(self.REPO_NAME, self.SHA)
        tree = {"truncated": False, "tree": [
            {"path": "a.md", "type": "blob", "mode": "100644"},
            {"path": "link", "type": "blob", "mode": "120000"},
            {"path": "sub", "type": "commit", "mode": "160000"},
            {"path": "dir", "type": "tree", "mode": "040000"}]}
        with patch.object(self.g, "gh_json", lambda *a: tree):
            self.assertEqual(self.g.fetch_modes(self.REPO_NAME, self.SHA),
                             {"a.md": "100644", "link": "120000"})

    # log-injection guard on attacker-controlled names echoed in reasons ---------------

    def test_summary_output_is_one_sanitised_line(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.g.summary("PR #1: left for a human -- path not on allowlist: a\n::add-mask::b\r\n")
        out = buf.getvalue()
        self.assertEqual(out.count("\n"), 1)
        self.assertTrue(out.startswith("PR #1:"))

    # ---- the workflow file itself: text-level guards, same style as the refill test

    def wf(self):
        return (REPO / ".github/workflows/automerge.yml").read_text(encoding="utf-8")

    def wf_code(self):
        return "\n".join(ln for ln in self.wf().splitlines()
                         if not ln.lstrip().startswith("#"))

    def test_workflow_never_uses_pull_request_target_or_checks_out_pr_code(self):
        code = self.wf_code()
        self.assertNotIn("pull_request_target", code)
        self.assertIn("workflow_run:", code)
        for forbidden in ("head.ref", "head_branch", "github.event.pull_request.head",
                          "ref: ${{ github.event.workflow_run"):
            self.assertNotIn(forbidden, code)
        # exactly one checkout, and it is pinned to the default branch
        self.assertEqual(code.count("actions/checkout@"), 1)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", code)

    def test_workflow_permissions_are_least_privilege(self):
        code = self.wf_code()
        self.assertIn("\npermissions: {}\n", code, "workflow-level default must be empty")
        for perm in ("contents: write", "pull-requests: write", "checks: read", "actions: write"):
            self.assertIn(perm, code)
        for extra in ("issues:", "id-token", "packages:", "pages:", "write-all"):
            self.assertNotIn(extra, code)

    def test_workflow_actions_are_pinned_by_sha(self):
        uses = [ln for ln in self.wf_code().splitlines() if "uses:" in ln]
        self.assertTrue(uses, "the scan found no `uses:` line -- suspect the scan")
        for ln in uses:
            self.assertRegex(ln, r"@[0-9a-f]{40}\b", f"unpinned action: {ln.strip()}")

    def test_workflow_is_gated_by_the_kill_switch_variable_and_ci_success(self):
        code = self.wf_code()
        self.assertIn("vars.AUTOMERGE_ENABLED == 'true'", code)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", code)
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository",
                      code)
        # the trigger names the CI workflow by its display name -- keep them in sync
        self.assertIn('workflows: ["CI — Tests"]', code)
        ci = (REPO / ".github/workflows/test.yml").read_text(encoding="utf-8")
        self.assertTrue(ci.startswith("name: CI — Tests"))

    def test_workflow_passes_untrusted_values_through_env_not_shell_text(self):
        run_lines = [ln.strip() for ln in self.wf_code().splitlines()
                     if ln.strip().startswith("run:")]
        self.assertEqual(run_lines, ["run: python3 automerge_gate.py"])


class TestRefillAutomergeGate(unittest.TestCase):
    """automerge_gate.py's refill rule (build step 2 of the refill automation design spec,
    docs/superpowers/specs/2026-09-25-refill-automation-design.md). A `refill/*` PR is merged only when: the
    head branch fully matches refill/<slug>, the PR is same-repo, it changes ONLY
    products.json, CI is green on the exact head SHA, no NEEDS_ appears anywhere in the head
    file, existing entries are unchanged and in order with new entries only at the end
    (base placeholders may be filled in place), and every new or filled entry has no REVIEW
    marker, a unique ASIN, the canonical Amazon link, an m.media-amazon.com image and a null
    or chewy.sjv.io Chewy link.

    The baseline is one canonical new entry appended to a two-entry queue. Each case flips
    ONE thing and expects a hold. Expected values are literals, never recomputed by the
    gate's own helpers.
    """

    REPO_NAME = "DMoneyOH/HappyPet"
    SHA = "a" * 40
    BASE_SHA = "d" * 40
    MERGE_BASE = "e" * 40
    BRANCH = "refill/2026-10-05-1200"
    # Real values from products.json history (git log --all -- products.json, 2026-10-02).
    REAL_IMAGES = (
        "https://m.media-amazon.com/images/I/81r09hTCRNL._AC_SX679_.jpg",
        "https://m.media-amazon.com/images/I/61-U8XMaW2L._AC_SX679_.jpg",
        "https://m.media-amazon.com/images/I/611T5GXwQXL._AC_SX466_.jpg",
        "https://m.media-amazon.com/images/I/61MPOYxs87L._AC_SL1500_.jpg",
        "https://m.media-amazon.com/images/I/61yepIfxn0L._AC_SX300_SY300_QL70_FMwebp_.jpg",
        "https://m.media-amazon.com/images/I/711DeELZYIL._AC_SY450_.jpg",
    )
    REAL_CHEWY = (
        "https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=1112910&u=https%3A%2F%2Fwww."
        "chewy.com%2Fbeggin-flavor-stix-bacon-peanut%2Fdp%2F1112910%3Futm_source%3Dgoogle-"
        "product%26utm_medium%3Dorganic%26utm_content%3DBeggin%2527&intsrc=APIG_24727",
        "https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=1548534&u=https%3A%2F%2Fwww."
        "chewy.com%2Frocco-roxie-supply-co-professional%2Fdp%2F1548534%3Futm_source%3Dgoogle-"
        "product%26utm_medium%3Dorganic%26utm_content%3DRocco%2520%2526%2520Roxie%2520Supply"
        "%2520Co.&intsrc=APIG_24727",
    )

    def setUp(self):
        self.g = _load_automerge_gate()

    @staticmethod
    def entry(topic="best-snuffle-mats-dogs", asin="B0ABCD1234", **over):
        e = {"topic": topic, "title": "Sniff & Seek: The Best Snuffle Mats for Dogs",
             "keyword": "best snuffle mat for dogs", "name": "Paw5 Wooly Snuffle Mat",
             "asin": asin, "affiliate_url": f"https://www.amazon.com/dp/{asin}?tag=happypetdc-20",
             "image": "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg",
             "species": "dog", "category": "dog-toys", "format": "roundup",
             "topical_sheet": "HAPPYPET_SHEET_ID_TOYS", "stars": 4.5, "price": "29.99",
             "runners_up": "A; B", "chewy_url": None, "chewy_price": None,
             "chewy_stock": None, "chewy_rating": None,
             "amazon_search_query": "snuffle mat for dogs"}
        e.update(over)
        return e

    def base_entries(self):
        return [self.entry("best-heated-outdoor-cat-houses", "B07HMPRTXF"),
                self.entry("best-dog-booster-seats", "B0GG8LR3RW",
                           chewy_url="REVIEW:" + self.REAL_CHEWY[0])]

    def good(self):
        base = self.base_entries()
        return dict(
            pr={"state": "open", "merged": False, "draft": False,
                "base": {"ref": "main", "sha": self.BASE_SHA},
                "head": {"sha": self.SHA, "ref": self.BRANCH,
                         "repo": {"full_name": self.REPO_NAME}},
                "user": {"login": "github-actions[bot]"}, "labels": []},
            files=[{"filename": "products.json", "status": "modified"}],
            check_runs=[{"name": "pytest", "status": "completed",
                         "conclusion": "success", "app": {"slug": "github-actions"}}],
            run_sha=self.SHA, repo=self.REPO_NAME,
            products_text=json.dumps(base + [self.entry()], indent=2),
            merge_base_text=json.dumps(base, indent=2),
            modes={"products.json": "100644"})

    def verdict(self, **over):
        kw = self.good()
        kw.update(over)
        return self.g.evaluate_refill(**kw)

    def with_head(self, entries):
        return self.verdict(products_text=json.dumps(entries))

    # ---- baseline and single flips ------------------------------------------------

    def test_baseline_refill_pr_merges_and_never_publishes(self):
        v = self.verdict()
        self.assertTrue(v.ok, v.reasons)
        self.assertFalse(v.publish, "a products.json merge must not dispatch publish")

    def test_every_single_flip_is_a_hold(self):
        def with_pr(**kv):
            pr = self.good()["pr"]
            pr.update(kv)
            return {"pr": pr}

        head = self.good()["pr"]["head"]
        ok_check = self.good()["check_runs"][0]
        other = {"name": "lint", "app": {"slug": "github-actions"}}
        cases = {
            "draft PR":            with_pr(draft=True),
            "closed PR":           with_pr(state="closed"),
            "already merged":      with_pr(merged=True, state="closed"),
            "base is not main":    with_pr(base={"ref": "release", "sha": self.BASE_SHA}),
            "fork head":           with_pr(head={**head, "repo": {"full_name": "evil/HappyPet"}}),
            "fork, no repo":       with_pr(head={**head, "repo": None}),
            "head moved after CI": with_pr(head={**head, "sha": "b" * 40}),
            "wrong branch":        with_pr(head={**head, "ref": "claude/happy-x"}),
            "no branch":           with_pr(head={k: v for k, v in head.items() if k != "ref"}),
            "opt-out label":       with_pr(labels=[{"name": "No-Automerge"}]),
            "zero checks":         {"check_runs": []},
            "pytest red":          {"check_runs": [{**ok_check, "conclusion": "failure"}]},
            "pytest pending":      {"check_runs": [{**ok_check, "status": "in_progress",
                                                    "conclusion": None}]},
            "pytest from a spoofing app": {"check_runs": [{**ok_check, "app": {"slug": "x"}}]},
            "other check pending": {"check_runs": [ok_check, {**other, "status": "queued",
                                                              "conclusion": None}]},
            "other check red":     {"check_runs": [ok_check, {**other, "status": "completed",
                                                              "conclusion": "failure"}]},
            "CI ran on another sha": {"run_sha": "c" * 40},
            "head file unavailable": {"products_text": None},
            "merge-base file unavailable": {"merge_base_text": None},
            "tree unreadable":     {"modes": None},
        }
        for name, over in cases.items():
            with self.subTest(name):
                v = self.verdict(**over)
                self.assertFalse(v.ok, f"{name} must be held")
                self.assertTrue(v.reasons)

    # ---- condition 1: branch name -------------------------------------------------

    def test_branch_name_case_table(self):
        allowed = ["refill/2026-09-25-1232", "refill/2026-07-08-chewy-upc-backfill",
                   "refill/x"]
        held = ["Refill/2026-09-25-1232", "REFILL/x", "refill/", "refill", "refill-x",
                "xrefill/x", "a/refill/x", "refill/../x", "refill/a/b", "refill/x\n",
                "refill/x ", " refill/x", "refill/X", "refill/x.lock", "refill/-x",
                "refill/x-", "refill//x", "refill\\x", "refill∕x", "ｒefill/x",
                "refill/x​", "chewy/x", "claude/x", "main", "", None, 5, ["refill/x"]]
        for ref in allowed:
            with self.subTest(allowed=ref):
                self.assertTrue(self.g.is_refill_branch(ref))
        for ref in held:
            with self.subTest(held=ref):
                self.assertFalse(self.g.is_refill_branch(ref))
                pr = self.good()["pr"]
                pr["head"] = {**pr["head"], "ref": ref}
                self.assertFalse(self.verdict(pr=pr).ok)

    # ---- condition 2: only products.json, modified, regular file -----------------

    def test_only_a_modified_products_json_is_mergeable(self):
        pj = {"filename": "products.json", "status": "modified"}
        cases = {
            "extra test file":   [pj, {"filename": "test_pipeline.py", "status": "modified"}],
            "extra draft":       [pj, {"filename": "_posts/DRAFT-best-x.md", "status": "added"}],
            "extra workflow":    [pj, {"filename": ".github/workflows/deploy.yml",
                                       "status": "modified"}],
            "other file only":   [{"filename": "refill_products.py", "status": "modified"}],
            "lookalike path":    [{"filename": "sub/products.json", "status": "modified"}],
            "trailing newline":  [{"filename": "products.json\n", "status": "modified"}],
            "added":             [{"filename": "products.json", "status": "added"}],
            "removed":           [{"filename": "products.json", "status": "removed"}],
            "renamed in":        [{"filename": "products.json", "status": "renamed",
                                   "previous_filename": "generate_posts.py"}],
            "rename source, status lies": [{"filename": "products.json", "status": "modified",
                                            "previous_filename": "generate_posts.py"}],
            "copied":            [{"filename": "products.json", "status": "copied"}],
            "status missing":    [{"filename": "products.json"}],
            "duplicate entry":   [pj, dict(pj)],
            "empty list":        [],
            "malformed entry":   [pj, None],
            "not a list":        None,
        }
        for name, files in cases.items():
            with self.subTest(name):
                self.assertFalse(self.verdict(files=files).ok, f"{name} must be held")

    def test_only_a_regular_file_mode_passes(self):
        for mode in ("120000", "100755", "160000", None, ""):
            with self.subTest(mode=mode):
                self.assertFalse(self.verdict(modes={"products.json": mode}).ok)
        self.assertFalse(self.verdict(modes={}).ok)

    # ---- condition 4: NEEDS_ / REVIEW markers ------------------------------------

    def test_a_needs_marker_anywhere_in_the_head_file_holds(self):
        base = self.base_entries()
        cases = {
            "NEEDS_ASIN asin":   self.entry(asin="NEEDS_ASIN"),
            "NEEDS_IMAGE image": self.entry(image="NEEDS_IMAGE"),
            "other NEEDS_ value": self.entry(name="NEEDS_REVIEW something"),
            "NEEDS_ in a key":   {**self.entry(), "NEEDS_X": 1},
            "NEEDS_ nested":     {**self.entry(), "extra": {"a": ["NEEDS_ASIN"]}},
        }
        for name, e in cases.items():
            with self.subTest(name):
                v = self.with_head(base + [e])
                self.assertFalse(v.ok, name)
                self.assertTrue(any("NEEDS_" in r for r in v.reasons), v.reasons)
        # a placeholder that was already on the base and is still unfilled is a hold too
        stuck = self.entry("best-cat-trees", "NEEDS_ASIN", image="NEEDS_IMAGE",
                           affiliate_url="https://www.amazon.com/dp/NEEDS_ASIN?tag=happypetdc-20")
        v = self.verdict(products_text=json.dumps(base + [stuck, self.entry()]),
                         merge_base_text=json.dumps(base + [stuck]))
        self.assertFalse(v.ok)

    def test_a_json_escaped_needs_marker_cannot_bypass_the_parse(self):
        text = json.dumps(self.base_entries() + [self.entry()])
        text = text.replace('"B0ABCD1234"', '"\\u004eEEDS_ASIN"', 1)
        self.assertIn("\\u004eEEDS_ASIN", text)
        self.assertFalse(self.verdict(products_text=text).ok)

    def test_a_review_marker_in_a_new_entry_holds(self):
        base = self.base_entries()
        cases = {
            "REVIEW: chewy":   self.entry(chewy_url="REVIEW:" + self.REAL_CHEWY[0]),
            "bare REVIEW":     self.entry(chewy_url="REVIEW"),
            "REVIEW in name":  self.entry(name="REVIEW: check fit"),
            "REVIEW key":      {**self.entry(), "REVIEW:note": "x"},
            "REVIEW nested":   {**self.entry(), "extra": ["REVIEW:x"]},
            "REVIEW: mid-name": self.entry(name="Paw5 Wooly Snuffle Mat (REVIEW: size)"),
            "REVIEW: in runners_up": self.entry(runners_up="A; REVIEW: B"),
        }
        for name, e in cases.items():
            with self.subTest(name):
                v = self.with_head(base + [e])
                self.assertFalse(v.ok, name)
                self.assertTrue(any("REVIEW" in r for r in v.reasons), v.reasons)

    def test_an_unchanged_review_entry_already_on_the_base_does_not_hold(self):
        """Inverse: the base carries a legitimate REVIEW chewy_url. It is not in the diff."""
        self.assertTrue(any(str(e["chewy_url"]).startswith("REVIEW")
                            for e in self.base_entries()))
        self.assertTrue(self.verdict().ok)

    # ---- semantic diff: existing entries -----------------------------------------

    def test_changing_or_removing_an_existing_entry_holds(self):
        base = self.base_entries()
        retagged = dict(base[0], affiliate_url="https://www.amazon.com/dp/B07HMPRTXF?tag=evil-20")
        cases = {
            "retag existing":  [retagged, base[1], self.entry()],
            "new image":       [dict(base[0], image=self.REAL_IMAGES[1]), base[1], self.entry()],
            "price edit":      [dict(base[0], price="1.00"), base[1], self.entry()],
            "added field":     [dict(base[0], note="x"), base[1], self.entry()],
            "removed existing": [base[0], self.entry()],
            "review resolved": [base[0], dict(base[1], chewy_url=self.REAL_CHEWY[0]),
                                self.entry()],
        }
        for name, head in cases.items():
            with self.subTest(name):
                v = self.with_head(head)
                self.assertFalse(v.ok, name)

    def test_existing_entries_must_keep_their_order_with_new_ones_only_at_the_end(self):
        base = self.base_entries()
        cases = {
            "existing reordered":   [base[1], base[0], self.entry()],
            "new inserted at top":  [self.entry(), base[0], base[1]],
            "new inserted between": [base[0], self.entry(), base[1]],
            "existing removed":     [base[1], self.entry()],
        }
        for name, head in cases.items():
            with self.subTest(name):
                self.assertFalse(self.with_head(head).ok, name)
        two_new = base + [self.entry(), self.entry("best-cat-tunnels", "B0CJFQKNQ9")]
        self.assertTrue(self.with_head(two_new).ok, "several new entries at the end merge")

    def test_an_existing_entry_is_compared_with_its_json_types(self):
        """1, 1.0 and True are == in Python; they are three different values in the file."""
        for old, new in ((4, 4.0), (1, True), (0, False), (4.5, "4.5"), (None, "")):
            with self.subTest(old=old, new=new):
                base = [dict(self.base_entries()[0], stars=old), self.base_entries()[1]]
                head = [dict(base[0], stars=new), base[1], self.entry()]
                self.assertFalse(self.verdict(products_text=json.dumps(head),
                                              merge_base_text=json.dumps(base)).ok)
        # the order of keys inside an entry is not a change
        base = self.base_entries()
        flipped = dict(reversed(list(base[0].items())))
        self.assertTrue(self.with_head([flipped, base[1], self.entry()]).ok)

    def test_filling_a_base_placeholder_with_a_canonical_entry_merges(self):
        base = self.base_entries()
        seed = self.entry("best-cat-trees", "NEEDS_ASIN", image="NEEDS_IMAGE",
                          name="NEEDS_ASIN placeholder for best cat tree",
                          affiliate_url="https://www.amazon.com/dp/NEEDS_ASIN?tag=happypetdc-20")
        filled = self.entry("best-cat-trees", "B0BY7S5L92")
        v = self.verdict(products_text=json.dumps(base + [filled]),
                         merge_base_text=json.dumps(base + [seed]))
        self.assertTrue(v.ok, v.reasons)
        bad = dict(filled, affiliate_url="https://www.amazon.com/dp/B0BY7S5L92?tag=evil-20")
        self.assertFalse(self.verdict(products_text=json.dumps(base + [bad]),
                                      merge_base_text=json.dumps(base + [seed])).ok)
        # in place in the middle, with a new entry appended after it
        v = self.verdict(products_text=json.dumps([base[0], filled, base[1], self.entry()]),
                         merge_base_text=json.dumps([base[0], seed, base[1]]))
        self.assertTrue(v.ok, v.reasons)

    def test_a_placeholder_fill_that_moves_or_renames_holds(self):
        base = self.base_entries()
        seed = self.entry("best-cat-trees", "NEEDS_ASIN", image="NEEDS_IMAGE",
                          affiliate_url="https://www.amazon.com/dp/NEEDS_ASIN?tag=happypetdc-20")
        filled = self.entry("best-cat-trees", "B0BY7S5L92")
        mb = json.dumps([seed] + base)
        cases = {
            "moved to the end":   base + [filled],
            "moved after one":    [base[0], filled, base[1]],
            "topic changed":      [dict(filled, topic="best-cat-towers")] + base,
            "filled and dropped": base + [self.entry()],
        }
        for name, head in cases.items():
            with self.subTest(name):
                self.assertFalse(self.verdict(products_text=json.dumps(head),
                                              merge_base_text=mb).ok, name)
        self.assertTrue(self.verdict(products_text=json.dumps([filled] + base),
                                     merge_base_text=mb).ok)

    def test_a_pr_that_adds_no_entry_is_held(self):
        self.assertFalse(self.with_head(self.base_entries()).ok)

    def test_duplicate_or_malformed_topics_hold(self):
        base = self.base_entries()
        cases = {
            "duplicate new":       base + [self.entry(), self.entry()],
            "new dup of existing": base + [self.entry("best-heated-outdoor-cat-houses")],
            "topic missing":       base + [{k: v for k, v in self.entry().items()
                                            if k != "topic"}],
            "topic not a slug":    base + [self.entry("Best Snuffle Mats")],
            "topic not a string":  base + [self.entry(topic=5)],
            "entry not an object": base + ["best-x"],
        }
        for name, head in cases.items():
            with self.subTest(name):
                self.assertFalse(self.with_head(head).ok, name)
        dup_base = json.dumps(base + [base[0]])
        self.assertFalse(self.verdict(merge_base_text=dup_base).ok, "duplicate in base")

    def test_a_duplicate_asin_holds(self):
        base = self.base_entries()
        cases = {
            "new repeats an existing ASIN": base + [self.entry(asin="B07HMPRTXF")],
            "two new share an ASIN":        base + [self.entry(),
                                                    self.entry("best-cat-tunnels")],
        }
        for name, head in cases.items():
            with self.subTest(name):
                v = self.with_head(head)
                self.assertFalse(v.ok, name)
                self.assertTrue(any("ASIN" in r for r in v.reasons), v.reasons)

    def test_a_non_string_asin_is_a_hold_reason_not_a_crash(self):
        for asin in (["B0ABCD1234"], {"a": "B0ABCD1234"}, 5, None):
            with self.subTest(asin=asin):
                v = self.with_head(self.base_entries() + [self.entry(asin=asin)])
                self.assertFalse(v.ok)
                self.assertTrue(any("asin" in r for r in v.reasons), v.reasons)

    def test_an_unavailable_file_says_so(self):
        for over in ({"products_text": None}, {"merge_base_text": None}):
            with self.subTest(over):
                v = self.verdict(**over)
                self.assertFalse(v.ok)
                self.assertTrue(any("not available" in r for r in v.reasons), v.reasons)
                self.assertFalse(any("not a list" in r for r in v.reasons), v.reasons)

    def test_unparsable_or_wrong_shape_files_hold(self):
        for text in ("", "{", "null", "{}", '"x"', "[1]", "[" * 5000 + "]" * 5000):
            with self.subTest(head=text[:20]):
                self.assertFalse(self.verdict(products_text=text).ok)
            with self.subTest(base=text[:20]):
                self.assertFalse(self.verdict(merge_base_text=text).ok)

    # ---- semantic diff: link shape of new entries ---------------------------------

    def test_affiliate_url_must_be_the_canonical_link_for_the_asin(self):
        good = "https://www.amazon.com/dp/B0ABCD1234?tag=happypetdc-20"
        held = [
            "https://www.amazon.com/dp/B0ABCD1234?tag=other-20",
            "https://www.amazon.com/dp/B0ABCD1234?tag=pawpicks04-20",  # retired tag (#131)
            "https://www.amazon.com/dp/B0ABCD1234?tag=HAPPYPETDC-20",  # tags are case-sensitive
            "https://www.amazon.com/dp/B0ABCD1234?tag=Happypetdc-20",
            "https://WWW.AMAZON.COM/dp/B0ABCD1234?tag=happypetdc-20",
            "https://www.amazon.com/dp/B0ZZZZ9999?tag=happypetdc-20",
            "http://www.amazon.com/dp/B0ABCD1234?tag=happypetdc-20",
            "https://amazon.com/dp/B0ABCD1234?tag=happypetdc-20",
            "https://amzn.to/4cuvtEY",
            "https://www.amazon.com/dp/B0ABCD1234?tag=happypetdc-20&tag=evil-20",
            "https://www.amazon.com/dp/B0ABCD1234?tag=happypetdc-20\n",
            "https://www.amazon.com.evil.com/dp/B0ABCD1234?tag=happypetdc-20",
            "", None,
        ]
        self.assertTrue(self.with_head(self.base_entries() + [self.entry(affiliate_url=good)]).ok)
        for url in held:
            with self.subTest(url=url):
                e = self.entry(affiliate_url=url)
                self.assertFalse(self.with_head(self.base_entries() + [e]).ok)

    def test_the_gate_tag_matches_refill_products_default_tag(self):
        """A tag swap that updates only one of the two is a failure here, not a gate that
        silently holds every refill PR. Read from source so the env override cannot mask it."""
        src = (REPO / "refill_products.py").read_text(encoding="utf-8")
        m = re.search(r'^AFFILIATE_TAG\s*=\s*os\.environ\.get\(\s*"AMAZON_PAAPI_PARTNER_TAG"'
                      r'\s*,\s*"([^"]+)"\s*\)', src, re.MULTILINE)
        self.assertIsNotNone(m, "refill_products.AFFILIATE_TAG default not found -- suspect the scan")
        self.assertEqual(self.g.AFFILIATE_TAG, m.group(1))
        self.assertEqual(self.g.AFFILIATE_TAG, "happypetdc-20")

    def test_the_affiliate_tag_is_a_literal_not_the_env_override(self):
        with patch.dict(os.environ, {"AMAZON_PAAPI_PARTNER_TAG": "evil-20"}):
            g = _load_automerge_gate()
            e = self.entry(affiliate_url="https://www.amazon.com/dp/B0ABCD1234?tag=evil-20")
            kw = self.good()
            kw["products_text"] = json.dumps(self.base_entries() + [e])
            self.assertFalse(g.evaluate_refill(**kw).ok)

    def test_asin_shape(self):
        for asin in ("B0abcd1234", "B0ABCD123", "B0ABCD12345", "A0ABCD1234", "1234567890",
                     "B0ABCD123\n", "", None, 5):
            with self.subTest(asin=asin):
                e = self.entry(asin=asin,
                               affiliate_url=f"https://www.amazon.com/dp/{asin}?tag=happypetdc-20")
                self.assertFalse(self.with_head(self.base_entries() + [e]).ok)

    def test_image_must_be_an_m_media_amazon_product_photo(self):
        held = [
            "https://images-na.ssl-images-amazon.com/images/I/71abc.jpg",
            "https://m.media-amazon.com/images/I/01rrzVoKd5L.svg",
            "http://m.media-amazon.com/images/I/71abc.jpg",
            "https://m.media-amazon.com.evil.com/images/I/71abc.jpg",
            "https://mXmedia-amazon.com/images/I/71abc.jpg",
            "https://m.media-amazon.com/images/I/../x.jpg",
            "https://m.media-amazon.com/images/I/a/b.jpg",
            "https://m.media-amazon.com/images/I/71abc.jpg?x=1",
            "https://m.media-amazon.com/images/I/71abc.jpg\n",
            "https://evil.com/m.media-amazon.com/images/I/71abc.jpg",
            "NEEDS_IMAGE", "", None,
        ]
        for url in self.REAL_IMAGES:
            with self.subTest(real=url):
                self.assertTrue(self.with_head(self.base_entries() + [self.entry(image=url)]).ok)
        for url in held:
            with self.subTest(held=url):
                self.assertFalse(self.with_head(self.base_entries() + [self.entry(image=url)]).ok)

    def test_chewy_url_must_be_null_or_a_chewy_sjv_io_link(self):
        held = [
            "https://www.chewy.com/x/dp/1",
            "https://chewy.sjv.io.evil.com/c/7160344/3054490/32975?prodsku=1&u=x",
            "http://chewy.sjv.io/c/7160344/3054490/32975?prodsku=1&u=https%3A%2F%2Fwww.chewy.com%2Fx",
            "https://chewy.sjv.io/c/9999999/3054490/32975?prodsku=1&u=https%3A%2F%2Fwww.chewy.com%2Fx",
            "https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=1&u=https%3A%2F%2Fevil.com%2Fx",
            "https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=1&u=https%3A%2F%2Fwww.chewy.com"
            "%2Fx&u=https%3A%2F%2Fevil.com",
            self.REAL_CHEWY[0] + "\n",
            "", 5, False,
        ]
        for url in (None,) + self.REAL_CHEWY:
            with self.subTest(ok=url):
                v = self.with_head(self.base_entries() + [self.entry(chewy_url=url)])
                self.assertTrue(v.ok, v.reasons)
        for url in held:
            with self.subTest(held=url):
                v = self.with_head(self.base_entries() + [self.entry(chewy_url=url)])
                self.assertFalse(v.ok)
        e = {k: v for k, v in self.entry().items() if k != "chewy_url"}
        self.assertFalse(self.with_head(self.base_entries() + [e]).ok, "missing chewy_url")

    def test_the_real_products_json_is_a_valid_base(self):
        real = json.loads((REPO / "products.json").read_text(encoding="utf-8"))
        real = [e for e in real if "NEEDS_" not in json.dumps(e)]
        v = self.verdict(products_text=json.dumps(real + [self.entry("best-zz-new-topic")]),
                         merge_base_text=json.dumps(real))
        self.assertTrue(v.ok, v.reasons)

    # ---- process(): routing, merge base, no issues fetch -------------------------

    def run_process(self, dry_run=False, compare=None, fail=None, **over):
        """process() against a fake gh. Returns (rc, gh write calls, reads); rc is the
        exception instead when process() raises. `fail`=(function name, exception) makes
        that fetch raise."""
        kw = self.good()
        kw.update(over)
        calls, reads = [], []
        pulls = [kw["pr"]]

        def fake_json(*args):
            reads.append(args[1])
            if "/compare/" in args[1]:
                return compare if compare is not None else {
                    "merge_base_commit": {"sha": self.MERGE_BASE}}
            return pulls.pop(0) if pulls else {"merged": True}

        def fake_text(repo, path, ref):
            reads.append(f"{path}@{ref}")
            return {self.SHA: kw["products_text"],
                    self.MERGE_BASE: kw["merge_base_text"]}.get(ref)

        def raiser(exc):
            def boom(*a, **k):
                raise exc
            return boom

        g = self.g
        patches = [
            patch.object(g, "gh", lambda *a: calls.append(list(a)) or ""),
            patch.object(g, "gh_json", fake_json),
            patch.object(g, "fetch_files", lambda *a: kw["files"]),
            patch.object(g, "fetch_check_runs", lambda *a: kw["check_runs"]),
            patch.object(g, "fetch_text", fake_text),
            patch.object(g, "fetch_modes", lambda *a: kw["modes"]),
            patch.object(g, "summary", lambda *_: None),
        ]
        if fail:
            patches.append(patch.object(g, fail[0], raiser(fail[1])))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            try:
                rc = g.process(self.REPO_NAME, 130, kw["run_sha"], dry_run=dry_run)
            except Exception as exc:
                rc = exc
        return rc, calls, reads

    def test_a_refill_pr_is_merged_with_the_pinned_command_and_no_publish(self):
        rc, calls, reads = self.run_process()
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [["pr", "merge", "130", "--repo", self.REPO_NAME, "--merge",
                                  "--match-head-commit", self.SHA]])
        self.assertIn(f"repos/{self.REPO_NAME}/compare/{self.BASE_SHA}...{self.SHA}", reads)
        self.assertIn(f"products.json@{self.MERGE_BASE}", reads)
        self.assertNotIn(f"products.json@{self.BASE_SHA}", reads,
                         "the semantic diff is against the merge base, not the base tip")
        self.assertFalse(any("/issues/" in r for r in reads),
                         "the refill rule must not depend on the issues endpoint")

    def test_a_held_refill_pr_makes_no_write_call(self):
        bad = json.dumps(self.base_entries() + [self.entry(asin="NEEDS_ASIN")])
        rc, calls, _ = self.run_process(products_text=bad)
        self.assertEqual((rc, calls), (0, []))

    def test_dry_run_never_merges_a_good_refill_pr(self):
        rc, calls, _ = self.run_process(dry_run=True)
        self.assertEqual((rc, calls), (0, []))

    def test_an_unreadable_merge_base_is_a_gate_error(self):
        for compare in ({}, {"merge_base_commit": None}, {"merge_base_commit": {"sha": ""}},
                        {"merge_base_commit": {"sha": 5}}, []):
            with self.subTest(compare=compare):
                rc, calls, _ = self.run_process(compare=compare)
                self.assertIsInstance(rc, self.g.UnreadableRead)
                self.assertEqual(calls, [])

    def test_an_unexpected_exception_in_the_refill_rule_is_a_gate_error(self):
        """It propagates out of process() before any write; main() turns it into a hold
        line and a red run (test_a_gate_error_holds_that_pr_and_reddens_the_run)."""
        rc, calls, _ = self.run_process(fail=("evaluate_refill", RuntimeError("unforeseen")))
        self.assertIsInstance(rc, RuntimeError)
        self.assertEqual(calls, [])

    def test_a_fetch_failure_in_the_refill_path_is_a_gate_error(self):
        """Same policy as an evaluation error: no write call, and the exception reaches
        main(), which holds the PR and exits 1."""
        import subprocess as sp
        failures = {
            "files":   ("fetch_files", sp.CalledProcessError(1, ["gh"])),
            "checks":  ("fetch_check_runs", ValueError("bad json")),
            "text":    ("fetch_text", RuntimeError("x")),
            "modes":   ("fetch_modes", KeyError("tree")),
            "compare": ("fetch_merge_base", ValueError("bad json")),
        }
        for name, (fn, exc) in failures.items():
            with self.subTest(name):
                rc, calls, _ = self.run_process(fail=(fn, exc))
                self.assertIs(rc, exc)
                self.assertEqual(calls, [], "no write call")

    def test_a_refill_branch_never_falls_back_to_the_routine_rule(self):
        """Even shaped like a routine Stage-1 PR, a refill/* PR is judged by the refill rule."""
        files = [{"filename": "_posts/DRAFT-best-x.md", "status": "added"}]
        with patch.object(self.g, "evaluate", lambda *a, **k: self.g.Verdict(True, [], True)):
            rc, calls, _ = self.run_process(files=files)
        self.assertEqual((rc, calls), (0, []))

    def test_every_refill_prefixed_branch_routes_to_the_refill_rule(self):
        """Routing is on the `refill/` prefix; the strict refill/<slug> check is the refill
        rule's. A routine-shaped PR on a malformed refill branch must be held, not handed to
        the routine rule. The routine rule is stubbed to say yes, which stands in for a
        Claude-App-attributed PR: the attribution lives on the issue, and the refill path
        never reads the issue at all."""
        files = [{"filename": "_posts/DRAFT-best-x.md", "status": "added"}]
        yes = lambda *a, **k: self.g.Verdict(True, [], True)
        for ref in ("refill/Abc", "refill/a_b", "refill/", "refill/a/b", "refill/x.lock",
                    "refill/x\n", "refill/../x"):
            with self.subTest(ref=ref):
                pr = self.good()["pr"]
                pr["head"] = {**pr["head"], "ref": ref}
                with patch.object(self.g, "evaluate", yes):
                    rc, calls, reads = self.run_process(pr=pr, files=files)
                self.assertEqual((rc, calls), (0, []))
                self.assertFalse(any("/issues/" in r for r in reads), "fell back to routine")
        for ref in ("claude/x", "Refill/x", "xrefill/x", None):
            with self.subTest(routine=ref):
                pr = self.good()["pr"]
                pr["head"] = {**pr["head"], "ref": ref}
                _, _, reads = self.run_process(pr=pr)
                self.assertTrue(any("/issues/" in r for r in reads), "not routed to routine")

    # ---- check runs: every page is read, or the PR is held ------------------------

    def fetch_runs(self, pages, total):
        asked = []

        def fake(*args):
            asked.append(args[1])
            n = int(args[1].rsplit("page=", 1)[1])
            return {"total_count": total, "check_runs": pages[n - 1] if n <= len(pages) else []}
        with patch.object(self.g, "gh_json", fake):
            try:
                return self.g.fetch_check_runs(self.REPO_NAME, self.SHA), asked
            except self.g.UnreadableRead as exc:
                return exc, asked

    @staticmethod
    def runs(start, count):
        return [{"id": i, "name": f"c{i}", "status": "completed", "conclusion": "success"}
                for i in range(start, start + count)]

    def test_check_runs_are_read_across_every_page(self):
        runs, asked = self.fetch_runs(
            [self.runs(1, 100), self.runs(101, 100), self.runs(201, 7)], 207)
        self.assertEqual(sorted(r["id"] for r in runs), list(range(1, 208)))
        self.assertEqual(asked, [f"repos/{self.REPO_NAME}/commits/{self.SHA}/check-runs"
                                 f"?per_page=100&page={n}" for n in (1, 2, 3)])
        self.assertEqual(self.fetch_runs([[]], 0)[0], [])

    def test_check_runs_that_do_not_add_up_are_unreadable(self):
        no_id = [{"name": "lint", "status": "completed", "conclusion": "success"}]
        for name, pages, total in (
                ("a page came back short", [self.runs(1, 100), []], 150),
                ("more than reported", [self.runs(1, 3)], 2),
                ("total missing", [self.runs(1, 1)], None),
                ("total not an int", [self.runs(1, 1)], "1"),
                ("total is a bool", [self.runs(1, 1)], True),
                ("runs not a list", [{"x": 1}], 1),
                ("a run without an id", [no_id], 1),
                ("an id that is not an int", [[dict(self.runs(1, 1)[0], id="1")]], 1),
                ("over the page cap", [self.runs(i * 100, 100) for i in range(11)], 1100)):
            with self.subTest(name):
                self.assertIsInstance(self.fetch_runs(pages, total)[0], self.g.UnreadableRead)

    def test_a_run_repeated_across_pages_cannot_stand_in_for_a_missed_one(self):
        """Pages can shift while they are read. Run 100 shows up twice and run 101 never
        does; the raw count still equals total_count, the unique count does not."""
        page1 = self.runs(1, 100)
        page2 = [page1[-1]] + self.runs(102, 49)
        self.assertEqual(len(page1) + len(page2), 150)
        self.assertIsInstance(self.fetch_runs([page1, page2], 150)[0], self.g.UnreadableRead)

    def test_unreadable_check_runs_hold_the_refill_rule(self):
        v = self.verdict(check_runs=None)
        self.assertFalse(v.ok)
        self.assertTrue(any("check runs" in r for r in v.reasons), v.reasons)

    # ---- main(): one policy for gate errors ---------------------------------------

    def run_main_with(self, proc, numbers="[1, 2]"):
        lines = []
        env = {"REPO": self.REPO_NAME, "RUN_HEAD_SHA": self.SHA, "PR_NUMBERS": numbers}
        with patch.dict(os.environ, env, clear=False), \
             patch.object(self.g, "process", proc), \
             patch.object(self.g, "summary", lines.append):
            rc = self.g.main([])
        return rc, lines

    def test_a_gate_error_holds_that_pr_and_reddens_the_run(self):
        """Fetch failure, evaluation exception or malformed API JSON, on either path: the
        PR is held, a line names it, the other PRs still run, and the run exits 1."""
        import subprocess as sp
        for exc in (ValueError("malformed JSON from the API"), RuntimeError("x"),
                    sp.CalledProcessError(1, ["gh", "api", "x"], stderr="HTTP 502")):
            with self.subTest(type(exc).__name__):
                seen = []

                def proc(repo, n, sha, dry_run, _e=exc):
                    seen.append(n)
                    if n == 1:
                        raise _e
                    return 0
                rc, lines = self.run_main_with(proc)
                self.assertEqual(seen, [1, 2], "one bad PR must not stop the others")
                self.assertEqual(rc, 1, "a gate error must leave the run red")
                one = [l for l in lines if l.startswith("PR #1:")]
                self.assertEqual(len(one), 1, lines)
                self.assertIn("gate error", one[0])

    def test_a_plain_hold_exits_zero(self):
        rc, lines = self.run_main_with(lambda repo, n, sha, dry_run: 0)
        self.assertEqual(rc, 0)

    # ---- requirement 8 end to end: an unreadable read is a gate error -------------

    SITE_PIN = {"title": "Best X Toys", "slug": "best-x",
                "article_url": "https://happypetproductreviews.com/dog-toys/best-x/"}

    def run_two_prs(self, kind, broken):
        """main() over PRs 1 and 2 of one `kind` ("refill" or "routine") against a fake
        GitHub API. `broken` breaks one of PR 1's reads: ("text", path, "head"|"base"),
        ("checks",) or ("tree",); ("none",) breaks nothing. Both PRs share one head SHA (one
        CI run). AUTOMERGE_ENABLED is unset, so a good PR reports WOULD MERGE.
        Returns (rc, summary lines, write calls)."""
        import subprocess as sp
        mb = self.MERGE_BASE
        if kind == "refill":
            files = [{"filename": "products.json", "status": "modified"}]
            texts = {("products.json", self.SHA): self.good()["products_text"],
                     ("products.json", mb): self.good()["merge_base_text"]}
        else:
            files = [{"filename": "_posts/DRAFT-best-x.md", "status": "added"},
                     {"filename": "_pin_queue/best-x.json", "status": "added"}]
            texts = {("_pin_queue/best-x.json", self.SHA): json.dumps(self.SITE_PIN)}
        side = {self.SHA: "head", mb: "base", self.BASE_SHA: "base"}
        writes, current = [], {"n": None}

        def pr1_broken(what):
            return current["n"] == 1 and broken[0] == what

        def gh(*a):
            if a[0] != "api":
                writes.append(list(a))
                return ""
            url = next(x for x in a if x.startswith("repos/"))
            if "/files" in url:
                return "\n".join(json.dumps(f) for f in files)
            path, ref = url.split("/contents/")[1].split("?ref=")
            if pr1_broken("text") and broken[1:] == (path, side[ref]):
                raise sp.CalledProcessError(1, ["gh", "api", url], stderr="HTTP 502")
            return texts[(path, ref)]

        def gh_json(*a):
            url = a[1]
            if url.endswith(("/pulls/1", "/pulls/2")):
                current["n"] = int(url[-1])
                ref = "refill/2026-10-05-1200" if kind == "refill" else "claude/x"
                return {"state": "open", "merged": False, "draft": False, "labels": [],
                        "base": {"ref": "main", "sha": self.BASE_SHA},
                        "head": {"sha": self.SHA, "ref": ref,
                                 "repo": {"full_name": self.REPO_NAME}}}
            if "/issues/" in url:
                return {"user": {"login": "DMoneyOH"},
                        "performed_via_github_app": {"slug": "claude"}}
            if "/compare/" in url:
                return {"merge_base_commit": {"sha": mb}}
            if "/check-runs" in url:
                run = {"id": 7, "name": "pytest", "status": "completed",
                       "conclusion": "success", "app": {"slug": "github-actions"}}
                return {"total_count": 2 if pr1_broken("checks") else 1, "check_runs": [run]}
            if "/git/trees/" in url:
                return {"truncated": pr1_broken("tree"),
                        "tree": [{"path": f["filename"], "type": "blob", "mode": "100644"}
                                 for f in files]}
            raise AssertionError(f"unexpected read {url}")

        lines = []
        env = {"REPO": self.REPO_NAME, "RUN_HEAD_SHA": self.SHA, "PR_NUMBERS": "[1, 2]"}
        g = self.g
        with patch.dict(os.environ, env, clear=False), \
             patch.object(g, "gh", gh), patch.object(g, "gh_json", gh_json), \
             patch.object(g, "summary", lines.append):
            os.environ.pop("AUTOMERGE_ENABLED", None)
            rc = g.main([])
        return rc, lines, writes

    def assert_gate_error_on_pr1_only(self, rc, lines, writes):
        self.assertEqual(rc, 1, "an unreadable read must leave the run red")
        self.assertEqual(writes, [], "nothing may be merged or dispatched")
        one = [l for l in lines if l.startswith("PR #1:")]
        two = [l for l in lines if l.startswith("PR #2:")]
        self.assertEqual(len(one), 1, lines)
        self.assertIn("gate error", one[0])
        self.assertEqual(len(two), 1, lines)
        self.assertIn("WOULD MERGE", two[0], "the other PR must still be evaluated")

    def test_the_two_pr_harness_passes_both_prs_when_nothing_breaks(self):
        for kind in ("refill", "routine"):
            with self.subTest(kind):
                rc, lines, writes = self.run_two_prs(kind, ("none",))
                self.assertEqual((rc, writes), (0, []), lines)
                self.assertEqual(sum("WOULD MERGE" in l for l in lines), 2, lines)

    def test_a_failed_file_fetch_is_a_gate_error(self):
        for kind, broken in (("refill", ("text", "products.json", "head")),
                             ("refill", ("text", "products.json", "base")),
                             ("routine", ("text", "_pin_queue/best-x.json", "head"))):
            with self.subTest(kind=kind, broken=broken):
                self.assert_gate_error_on_pr1_only(*self.run_two_prs(kind, broken))

    def test_unreadable_check_runs_are_a_gate_error(self):
        for kind in ("refill", "routine"):
            with self.subTest(kind):
                self.assert_gate_error_on_pr1_only(*self.run_two_prs(kind, ("checks",)))

    def test_a_truncated_tree_is_a_gate_error(self):
        for kind in ("refill", "routine"):
            with self.subTest(kind):
                self.assert_gate_error_on_pr1_only(*self.run_two_prs(kind, ("tree",)))

    # ---- the AUTOMERGE_ENABLED kill switch, mirrored in Python ---------------------

    def run_main(self, env_value, argv=()):
        seen = []
        env = {"REPO": self.REPO_NAME, "RUN_HEAD_SHA": self.SHA, "PR_NUMBERS": "[130]"}
        if env_value is not None:
            env["AUTOMERGE_ENABLED"] = env_value
        with patch.dict(os.environ, env, clear=False), \
             patch.object(self.g, "process",
                          lambda repo, n, sha, dry_run: seen.append(dry_run) or 0), \
             patch.object(self.g, "summary", lambda *_: None):
            if env_value is None:
                os.environ.pop("AUTOMERGE_ENABLED", None)
            self.g.main(list(argv))
        return seen

    def test_merging_needs_automerge_enabled_to_be_exactly_true(self):
        self.assertEqual(self.run_main("true"), [False])
        for value in (None, "", "false", "TRUE", "True", " true", "true\n", "1", "yes"):
            with self.subTest(value=value):
                self.assertEqual(self.run_main(value), [True],
                                 f"AUTOMERGE_ENABLED={value!r} must evaluate only")
        self.assertEqual(self.run_main("true", ["--dry-run"]), [True])

    def test_workflow_passes_the_kill_switch_variable_to_the_gate(self):
        code = (REPO / ".github/workflows/automerge.yml").read_text(encoding="utf-8")
        self.assertIn("AUTOMERGE_ENABLED: ${{ vars.AUTOMERGE_ENABLED }}", code)


class TestCreedBandRetailerLink(unittest.TestCase):
    """The home page's "how we choose" band: order, the retailer count, and
    the link from "Retailers we link" to the About page's disclosure."""

    @classmethod
    def setUpClass(cls):
        cls.home = (REPO / "_layouts" / "home.html").read_text(encoding="utf-8")
        cls.about = (REPO / "about.md").read_text(encoding="utf-8")
        cls.css = _strip_css_comments(
            (REPO / "assets" / "css" / "style.css").read_text(encoding="utf-8"))
        band = re.search(r'<div class="creed-nums">(.*?)</div>\s*</div>', cls.home, re.S)
        assert band, "home.html has no creed-nums block"
        cls.band = band.group(1)

    def test_band_order_and_retailer_count(self):
        labels = re.findall(r"<span>([^<]+)</span>", self.band)
        self.assertEqual(
            labels, ["Reviews published", "Retailers we link", "Sponsored posts"])
        self.assertRegex(
            self.band, r"<b>2</b><span>Retailers we link</span>")

    def test_retailers_item_links_to_an_anchor_that_exists_on_about(self):
        m = re.search(
            r'<a class="creed-num[^"]*" href="\{\{ site\.baseurl \}\}/about/#([\w-]+)">'
            r'<b>2</b><span>Retailers we link</span></a>', self.band)
        self.assertIsNotNone(m, "the retailer item is not an anchor to /about/#...")
        self.assertRegex(self.about, r'<h2 id="%s">Affiliate disclosure</h2>' % m.group(1))
        self.assertEqual(self.about.count('id="%s"' % m.group(1)), 1)

    def test_link_keeps_a_visible_focus_state(self):
        # The ring comes from the global :focus-visible rule; nothing may
        # switch it off for the band's link.
        self.assertRegex(self.css, r":focus-visible\s*\{\s*outline:\s*2px solid")
        for rule in re.findall(r"[^{}]*creed-num[^{}]*\{[^}]*\}", self.css):
            self.assertNotRegex(rule, r"outline\s*:\s*(none|0)")

    def test_dropped_retailer_is_gone_from_the_visible_site(self):
        # Built from parts so a repo-wide grep for the name stays at zero.
        gone = "pet" + "co"
        for rel in ("_layouts/default.html", "_layouts/home.html",
                    "_layouts/post.html", "about.md", "contact.md",
                    "privacy-policy.md"):
            text = (REPO / rel).read_text(encoding="utf-8")
            self.assertNotIn(gone, text.lower(), rel)
        self.assertIn("Amazon and Chewy", self.about)


class TestManualResolveChewyOffByDefault(unittest.TestCase):
    """manual_resolve.py skips Chewy unless --chewy is passed (refill automation,
    2026-10-06). The name-matching lookup violates the GTIN-only rule for Chewy
    links, and importing chewy_lookup reads Impact credentials from the vault."""

    PLACEHOLDER = {
        "topic": "best-x-things", "title": "T", "keyword": "k", "species": "dog",
        "category": "toys", "format": "roundup", "topical_sheet": "HAPPYPET_SHEET_ID_TOYS",
        "name": "NEEDS_ASIN placeholder for k", "asin": "NEEDS_ASIN",
        "affiliate_url": "https://www.amazon.com/dp/NEEDS_ASIN?tag=happypetdc-20",
        "image": "NEEDS_IMAGE", "price": None, "stars": None, "chewy_url": None,
        "chewy_price": None, "chewy_stock": None, "chewy_rating": None}
    ARGS = ["--topic", "best-x-things", "--name", "Acme Dog Toy, Large",
            "--asin", "B0ABCD1234",
            "--image", "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg",
            "--price", "12.99", "--stars", "4.5", "--upc", "012345678905"]

    def _run(self, extra):
        import refill_products as rp
        import manual_resolve as mr
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps([dict(self.PLACEHOLDER)]))
            with patch.object(rp, "PRODUCTS_PATH", path):
                mr.main(self.ARGS + extra)
            return json.loads(path.read_text(encoding="utf-8"))[0]

    def test_default_never_calls_chewy_enrich(self):
        import refill_products as rp
        with patch.object(rp, "chewy_enrich", side_effect=AssertionError("chewy_enrich ran")):
            entry = self._run([])
        self.assertEqual(entry["asin"], "B0ABCD1234")
        self.assertEqual(entry["upc"], "012345678905")
        self.assertIsNone(entry["chewy_url"])

    def test_the_opt_in_flag_still_reaches_chewy_enrich(self):
        # Inverse direction: --chewy is the only way in.
        import refill_products as rp
        calls = []
        with patch.object(rp, "chewy_enrich",
                          side_effect=lambda *a: calls.append(a) or {"chewy_url": None}):
            self._run(["--chewy"])
        self.assertEqual(calls, [("Acme Dog Toy, Large", "012345678905")])

    def test_importing_the_refill_modules_never_touches_the_vault(self):
        # Fresh interpreter, IMPACT_* unset: chewy_lookup (which reads the vault at
        # import) and brain_secrets must not be loaded by any of these imports.
        import subprocess
        env = {k: v for k, v in os.environ.items() if not k.startswith("IMPACT_")}
        code = ("import sys; import refill_cli, manual_resolve, refill_watchdog, "
                "refill_products, queue_alert; "
                "print(sorted(m for m in ('chewy_lookup', 'brain_secrets') if m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "[]")

    def test_impact_placeholders_keep_chewy_lookup_out_of_the_vault(self):
        # Second layer, for the opt-in path: run-refill.ps1 sets IMPACT_* to a
        # non-secret placeholder, and chewy_lookup reads the vault only when one
        # of them is empty.
        import importlib.util
        import brain_secrets
        def load_fresh():
            spec = importlib.util.spec_from_file_location("chewy_lookup_fresh",
                                                          REPO / "chewy_lookup.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        env = {"IMPACT_ACCOUNT_SID": "disabled-local-refill",
               "IMPACT_AUTH_TOKEN": "disabled-local-refill"}
        with patch.dict(os.environ, env), patch.object(
                brain_secrets, "get_secret", side_effect=AssertionError("vault read")):
            self.assertEqual(load_fresh().ACCOUNT_SID, "disabled-local-refill")
        calls = []
        with patch.dict(os.environ, {"IMPACT_ACCOUNT_SID": "", "IMPACT_AUTH_TOKEN": ""}), \
             patch.object(brain_secrets, "get_secret", side_effect=lambda k: calls.append(k)):
            load_fresh()
        self.assertEqual(calls, ["IMPACT_ACCOUNT_SID", "IMPACT_AUTH_TOKEN"])

    def test_the_manual_path_doc_never_uses_the_flag(self):
        doc = (REPO / "docs" / "refill-manual-resolve.md").read_text(encoding="utf-8")
        self.assertNotIn("--no-chewy", doc)
        self.assertNotRegex(doc, r"manual_resolve\.py[^`]*--chewy")
        self.assertIn("Never pass `--chewy`", doc)


class TestRefillCli(unittest.TestCase):
    """refill_cli.py -- the offline half of the local refill session. Every test runs
    on a fixture repo (posts, pin files, a two-entry queue), never the live
    products.json. Expected values are literals."""

    GOOD_IMAGE = "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg"
    QUEUED = ("best-queued-one", "best-queued-two")

    def setUp(self):
        import refill_cli
        self.cli = refill_cli
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        (d / "_posts").mkdir()
        (d / "_posts" / "2026-01-01-best-published-post.md").write_text("---\n---\n")
        (d / "_posts" / "DRAFT-best-drafted-post.md").write_text("---\n---\n")
        (d / "_posts" / "2026-01-02-bare-post.md").write_text("---\n---\n")
        q = d / "_pin_queue"
        (q / "sent").mkdir(parents=True)
        (q / ".fired").mkdir()
        (q / "best-only-pending.json").write_text("{}")
        (q / "sent" / "best-only-sent.json").write_text("{}")
        (q / ".fired" / "best-only-fired.fired").write_text("")
        (q / ".fired" / ".gitkeep").write_text("")
        self.base = d / "base.json"
        self.head = d / "products.json"
        self.plan = d / "plan.json"
        queue = [self.full_entry(topic=t, asin=f"B0QUEUED0{i}") for i, t in
                 enumerate(self.QUEUED)]
        for p in (self.base, self.head):
            p.write_text(json.dumps(queue, indent=2), encoding="utf-8")
        for patcher in (patch.object(self.cli, "REPO_DIR", d),
                        patch.object(self.cli.gp, "POSTS_DIR", d / "_posts")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def topic(self, **over):
        t = {"topic": "best-widget-for-tests", "title": "Best Widgets for Test Pets",
             "keyword": "best widget for pets", "species": "dog", "category": "toys",
             "topical_sheet": "HAPPYPET_SHEET_ID_TOYS",
             "amazon_search_query": "widget for dogs", "product": None}
        t.update(over)
        return t

    def product(self, **over):
        p = {"name": "Acme Widget for Dogs, Medium", "asin": "B0ABCD1234",
             "image": self.GOOD_IMAGE, "price": "19.99", "stars": 4.6,
             "runners_up": "Brand B Widget; Brand C Widget", "upc": "012345678905"}
        p.update(over)
        return p

    def full_entry(self, topic="best-widget-for-tests", asin="B0ABCD1234", **over):
        """A filled entry exactly as seed + resolve write it."""
        e = {"topic": topic, "title": "Best Widgets for Test Pets",
             "keyword": "best widget for pets",
             "name": "Acme Widget for Dogs, Medium", "asin": asin,
             "affiliate_url": f"https://www.amazon.com/dp/{asin}?tag=happypetdc-20",
             "image": self.GOOD_IMAGE, "species": "dog", "category": "toys",
             "format": "roundup", "topical_sheet": "HAPPYPET_SHEET_ID_TOYS",
             "stars": 4.6, "price": "19.99", "runners_up": "Brand B Widget",
             "chewy_url": None, "chewy_price": None, "chewy_stock": None,
             "chewy_rating": None, "amazon_search_query": "widget for dogs"}
        e.update(over)
        return e

    def run_cli(self, *argv):
        return io_capture(lambda: self.cli.main([f"--products={self.head}", *argv]))

    def check(self, new_entries):
        base = json.loads(self.base.read_text(encoding="utf-8"))
        self.head.write_text(json.dumps(base + new_entries), encoding="utf-8")
        return self.run_cli("check", "--base", str(self.base))

    # ---- topic validation -----------------------------------------------------

    def test_good_topic_passes(self):
        self.assertEqual(self.cli.topic_problems(self.topic(), set()), [])

    def test_only_the_22_bare_topics_are_accepted(self):
        for legacy in ("dog-health", "cat-beds", "pet-grooming", "dog-gear"):
            self.assertTrue(self.cli.topic_problems(self.topic(category=legacy), set()), legacy)
        for bad in ("Toys", "toys ", "", None, "gear"):
            self.assertTrue(self.cli.topic_problems(self.topic(category=bad), set()), bad)
        self.assertEqual(self.cli.topic_problems(self.topic(category="flea-tick"), set()), [])

    def test_bad_slug_shapes_are_refused(self):
        for slug in ("widget", "best-Widget", "best-a--b", "best-x-", "best-x\n",
                     "best-x/y", "best-" + "a" * 60, None, 7):
            self.assertTrue(self.cli.topic_problems(self.topic(topic=slug), set()), repr(slug))

    def test_collisions_in_either_slug_form(self):
        for taken in ({"best-widget-for-tests"}, {"widget-for-tests"}):
            bad = self.cli.topic_problems(self.topic(), taken)
            self.assertTrue(any("collides" in b for b in bad), taken)

    def test_text_fields_use_the_gates_rule(self):
        for title in ('Best "Widgets"', "Best <b>Widgets</b>", "Best\tWidgets",
                      " Best Widgets", "Best `Widgets`", "x" * 201, "",
                      "=HYPERLINK(1)", "+Widgets", "-Widgets", "@Widgets"):
            self.assertTrue(self.cli.topic_problems(self.topic(title=title), set()), repr(title))

    def test_species_and_sheet_are_closed_sets(self):
        self.assertTrue(self.cli.topic_problems(self.topic(species="dogs"), set()))
        self.assertTrue(self.cli.topic_problems(
            self.topic(topical_sheet="HAPPYPET_SHEET_ID_DOGS"), set()))

    def test_taken_slugs_has_each_source_in_both_forms(self):
        taken = self.cli.taken_slugs(json.loads(self.base.read_text(encoding="utf-8")))
        self.assertEqual(taken, {
            "best-published-post", "published-post", "best-drafted-post", "drafted-post",
            "best-queued-one", "queued-one", "best-queued-two", "queued-two",
            "best-only-pending", "only-pending", "best-only-sent", "only-sent",
            "best-only-fired", "only-fired", "bare-post"})

    # ---- product validation ---------------------------------------------------

    def test_good_product_passes(self):
        self.assertEqual(self.cli.product_problems(self.product()), [])

    def test_image_forms_the_producer_accepts_but_the_gate_refuses(self):
        import refill_products as rp
        for image in (self.GOOD_IMAGE + "?x=.jpg",
                      "HTTPS://M.MEDIA-AMAZON.COM/images/I/71abcXYZ._AC_SX425_.jpg"):
            self.assertTrue(rp.validate_candidate(self.product(image=image)), image)
            self.assertTrue(self.cli.product_problems(self.product(image=image)), image)

    def test_bad_product_fields(self):
        for over in ({"asin": "B12345678"}, {"image": "https://images-na.ssl-images-amazon.com/x.jpg"},
                     {"name": "Sponsored Ad - Widget"}, {"price": "$19.99"}, {"price": 19.99},
                     {"stars": True}, {"stars": 5.1}, {"stars": "4.5"}, {"upc": "01234567890X"},
                     {"runners_up": "A\nB"}, {"name": "=Widget"}):
            self.assertTrue(self.cli.product_problems(self.product(**over)), over)
        self.assertTrue(self.cli.product_problems(None))

    # ---- check: every seed/resolve rule on every new entry --------------------

    def test_check_passes_a_canonical_new_entry(self):
        rc, out = self.check([self.full_entry()])
        self.assertEqual(rc, 0, out)

    def test_check_holds_planted_faults(self):
        cases = {
            "published post": self.full_entry(topic="best-published-post"),
            "draft": self.full_entry(topic="best-drafted-post"),
            "pin file": self.full_entry(topic="best-only-sent"),
            "fired pin": self.full_entry(topic="best-only-fired"),
            "bare-form collision": self.full_entry(topic="best-bare-post"),
            "missing topical_sheet": {k: v for k, v in self.full_entry().items()
                                      if k != "topical_sheet"},
            "missing amazon_search_query": {k: v for k, v in self.full_entry().items()
                                            if k != "amazon_search_query"},
            "missing chewy_stock": {k: v for k, v in self.full_entry().items()
                                    if k != "chewy_stock"},
            "species": self.full_entry(species="dogs"),
            "legacy category": self.full_entry(category="dog-toys"),
            "title text": self.full_entry(title="=Widgets"),
            "price": self.full_entry(price=19.99),
            "format": self.full_entry(format="listicle"),
            "name-matched chewy url": self.full_entry(chewy_url=(
                "https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=1112910"
                "&u=https%3A%2F%2Fwww.chewy.com%2Fbeggin%2Fdp%2F1112910")),
            "chewy price": self.full_entry(chewy_price="9.99"),
        }
        for label, entry in cases.items():
            rc, out = self.check([entry])
            self.assertEqual(rc, 1, f"{label}: {out}")

    def test_the_gate_alone_passes_the_name_matched_chewy_link(self):
        # Why the local rule exists: the gate allows any chewy.sjv.io link.
        g = _load_automerge_gate()
        entry = self.full_entry(chewy_url=(
            "https://chewy.sjv.io/c/7160344/3054490/32975?prodsku=1112910"
            "&u=https%3A%2F%2Fwww.chewy.com%2Fbeggin%2Fdp%2F1112910"))
        base = self.base.read_text(encoding="utf-8")
        head = json.dumps(json.loads(base) + [entry])
        self.assertEqual(g.refill_products_problems(head, base), [])
        rc, out = self.check([entry])
        self.assertIn("chewy_url is set", out)

    def test_check_holds_a_duplicate_slug_and_a_duplicate_asin(self):
        rc, out = self.check([self.full_entry(), self.full_entry(asin="B0WXYZ5678")])
        self.assertEqual(rc, 1)
        self.assertIn("collides", out)
        rc, out = self.check([self.full_entry(),
                              self.full_entry(topic="best-other-widget")])
        self.assertEqual(rc, 1)
        self.assertIn("appears more than once", out)

    def test_queued_entries_are_exempt(self):
        # The fixture's queued entries would fail several new-entry rules (their
        # slugs are "taken" by themselves); check must not judge them.
        rc, out = self.check([])
        self.assertNotIn("queued-one", out)

    def test_check_reads_bad_files_cleanly(self):
        self.base.write_text("{not json", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("check", "--base", str(self.base))
        self.assertIn("base unreadable", str(cm.exception.code))
        with self.assertRaises(SystemExit):
            self.run_cli("check", "--base", str(self.base.parent / "missing.json"))

    # ---- end to end -----------------------------------------------------------

    def test_seed_resolve_check_round_trip(self):
        plan = {"abort": None, "topics": [
            self.topic(product=self.product()),
            self.topic(topic="best-other-widget", product=self.product(
                asin="B0WXYZ5678", image=self.GOOD_IMAGE + "?bad=.jpg")),
            self.topic(topic="best-published-post", product=self.product(asin="B0ZZZZ9999")),
        ]}
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        base = json.loads(self.base.read_text(encoding="utf-8"))
        self.assertEqual(self.run_cli("seed", "--plan", str(self.plan))[0], 0)
        seeded = json.loads(self.head.read_text(encoding="utf-8"))
        self.assertEqual([e["topic"] for e in seeded[2:]],
                         ["best-widget-for-tests", "best-other-widget"])
        self.assertEqual(self.run_cli("check", "--base", str(self.base))[0], 1)
        result = self.base.parent / "result.json"
        import refill_products as rp
        with patch.object(rp, "chewy_enrich", side_effect=AssertionError("chewy ran")):
            rc, _ = self.run_cli("resolve", "--plan", str(self.plan), "--base",
                                 str(self.base), "--result", str(result))
        self.assertEqual(rc, 0)
        out = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual(out["filled"], ["best-widget-for-tests"])
        self.assertEqual([x["topic"] for x in out["dropped"]], ["best-other-widget"])
        final = json.loads(self.head.read_text(encoding="utf-8"))
        self.assertEqual(final[:2], base)
        self.assertEqual(len(final), 3)
        self.assertEqual(final[2]["affiliate_url"],
                         "https://www.amazon.com/dp/B0ABCD1234?tag=happypetdc-20")
        self.assertIsNone(final[2]["chewy_url"])
        rc, out = self.run_cli("check", "--base", str(self.base))
        self.assertEqual(rc, 0, out)

    def test_check_holds_a_leaked_partner_tag(self):
        import refill_products as rp
        self.plan.write_text(json.dumps({"topics": [self.topic(product=self.product())]}),
                             encoding="utf-8")
        self.run_cli("seed", "--plan", str(self.plan))
        with patch.object(rp, "AFFILIATE_TAG", "pawpicks04-20"):
            self.run_cli("resolve", "--plan", str(self.plan), "--base", str(self.base))
        rc, out = self.run_cli("check", "--base", str(self.base))
        self.assertEqual(rc, 1)
        self.assertIn("affiliate_url is not the canonical link", out)

    def test_resolve_never_touches_a_queued_entry(self):
        # Even a queued PLACEHOLDER on the branch point, named by the plan with no
        # product, is neither filled nor dropped: dropping it would delete queue.
        import refill_products as rp
        held = rp.build_entry({k: v for k, v in self.topic(topic="best-held-in-base").items()
                               if k != "product"})
        queue = json.loads(self.base.read_text(encoding="utf-8")) + [held]
        for p in (self.base, self.head):
            p.write_text(json.dumps(queue), encoding="utf-8")
        self.plan.write_text(json.dumps(
            {"topics": [self.topic(topic="best-held-in-base", product=None)]}), encoding="utf-8")
        self.assertEqual(self.run_cli("seed", "--plan", str(self.plan))[0], 1)
        self.assertEqual(self.run_cli("resolve", "--plan", str(self.plan),
                                      "--base", str(self.base))[0], 1)
        self.assertEqual(json.loads(self.head.read_text(encoding="utf-8")), queue)

    def test_an_aborted_plan_seeds_nothing(self):
        self.plan.write_text(json.dumps({"abort": "SiteStripe bar not visible",
                                         "topics": [self.topic()]}), encoding="utf-8")
        before = self.head.read_text(encoding="utf-8")
        self.assertEqual(self.run_cli("seed", "--plan", str(self.plan))[0], 1)
        self.assertEqual(self.head.read_text(encoding="utf-8"), before)

    def test_a_plan_with_a_bom_still_loads(self):
        self.plan.write_bytes(b"\xef\xbb\xbf" + json.dumps({"topics": []}).encode())
        self.assertEqual(self.cli.load_plan(self.plan), {"topics": []})

    def test_context_hands_over_the_plans_untaken_seeds_in_plan_order(self):
        d = self.base.parent
        (d / "_data").mkdir()
        seed = {"keyword": "k w", "species": "dog", "category": "toys",
                "topical_sheet": "HAPPYPET_SHEET_ID_TOYS", "amazon_search_query": "q"}
        (d / "_data" / "publish_plan.json").write_text(json.dumps({"order": [], "seeds": [
            dict(seed, topic="best-published-post"),       # taken: published
            dict(seed, topic="best-seed-one"),
            dict(seed, topic="best-queued-two"),           # taken: queued
            dict(seed, topic="best-seed-two"),
            dict(seed, topic="best-seed-three")]}), encoding="utf-8")
        out = d / "ctx.json"
        self.run_cli("context", "--out", str(out), "--batch", "2")
        ctx = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual([s["topic"] for s in ctx["seeds"]], ["best-seed-one", "best-seed-two"])
        self.assertRegex(ctx["today"], r"^\d{4}-\d{2}-\d{2}$")

    def test_context_lists_every_seed_slug_as_taken(self):
        """Seeds arrive only through `seeds`: every seed slug, beyond this batch
        too, is listed as taken so the session never plans one itself."""
        d = self.base.parent
        (d / "_data").mkdir()
        seed = {"keyword": "k w", "species": "dog", "category": "toys",
                "topical_sheet": "HAPPYPET_SHEET_ID_TOYS", "amazon_search_query": "q"}
        (d / "_data" / "publish_plan.json").write_text(json.dumps({"seeds": [
            dict(seed, topic="best-seed-one"), dict(seed, topic="best-seed-two")]}),
            encoding="utf-8")
        out = d / "ctx.json"
        self.run_cli("context", "--out", str(out), "--batch", "1")
        ctx = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual([s["topic"] for s in ctx["seeds"]], ["best-seed-one"])
        self.assertIn("best-seed-one", ctx["taken_slugs"])
        self.assertIn("best-seed-two", ctx["taken_slugs"])

    def test_an_unreadable_plan_stops_the_context_naming_the_file(self):
        d = self.base.parent
        (d / "_data").mkdir()
        (d / "_data" / "publish_plan.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("context", "--out", str(d / "ctx.json"))
        self.assertIn("publish_plan.json", str(cm.exception))
        (d / "_data" / "publish_plan.json").write_text('{"seeds": {}}', encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("context", "--out", str(d / "ctx.json"))
        self.assertIn("publish_plan.json", str(cm.exception))

    def test_the_real_first_batch_of_seeds_survives_seed_resolve_and_check(self):
        """The 10-25 run's four seeds (two share `treats`), each with a title the
        session writes and a product it reads, through the real seed, resolve and
        check commands: all four land, nothing dropped, the check passes."""
        real = json.loads((REPO / "_data" / "publish_plan.json").read_text(encoding="utf-8"))
        topics = [dict(s, title=f"Seed Title {i}", product=self.product(asin=f"B0SEEDS00{i}"))
                  for i, s in enumerate(real["seeds"][:4])]
        self.assertEqual([t["category"] for t in topics][:2], ["treats", "treats"])
        self.plan.write_text(json.dumps({"abort": None, "topics": topics}), encoding="utf-8")
        self.assertEqual(self.run_cli("seed", "--plan", str(self.plan), "--max", "4")[0], 0)
        rc, out = self.run_cli("resolve", "--plan", str(self.plan), "--base", str(self.base))
        self.assertEqual(rc, 0, out)
        self.assertIn("filled 4, dropped 0", out)
        rc, out = self.run_cli("check", "--base", str(self.base))
        self.assertEqual(rc, 0, out)

    def test_context_stops_offering_a_seed_after_its_publish_by(self):
        d = self.base.parent
        (d / "_data").mkdir()
        seed = {"keyword": "k w", "species": "dog", "category": "toys",
                "topical_sheet": "HAPPYPET_SHEET_ID_TOYS", "amazon_search_query": "q"}
        (d / "_data" / "publish_plan.json").write_text(json.dumps({"seeds": [
            dict(seed, topic="best-seed-early", publish_by="2026-11-02"),
            dict(seed, topic="best-seed-late", publish_by="2026-12-31")]}), encoding="utf-8")
        import refill_products
        for today, offered in (("2026-11-02", ["best-seed-early", "best-seed-late"]),
                               ("2026-11-03", ["best-seed-late"])):
            with self.subTest(today=today), \
                 patch.object(refill_products, "eastern_date", lambda: today):
                out = d / "ctx.json"
                self.run_cli("context", "--out", str(out), "--batch", "4")
                ctx = json.loads(out.read_text(encoding="utf-8"))
                self.assertEqual([s["topic"] for s in ctx["seeds"]], offered)
                self.assertEqual(ctx["today"], today)
                self.assertIn("best-seed-early", ctx["taken_slugs"])   # still reserved

    def _plan_two_seeds(self):
        d = self.base.parent
        (d / "_data").mkdir(exist_ok=True)
        seed = {"species": "dog", "category": "toys", "topical_sheet": "HAPPYPET_SHEET_ID_TOYS",
                "publish_by": "2026-11-30"}
        (d / "_data" / "publish_plan.json").write_text(json.dumps({"seeds": [
            dict(seed, topic="best-seed-one", keyword="seed one for small dogs",
                 amazon_search_query="seed one for small dogs"),
            dict(seed, topic="best-seed-two", keyword="seed two for small dogs",
                 amazon_search_query="seed two for small dogs")]}), encoding="utf-8")
        return {"best-seed-one": self.topic(topic="best-seed-one", keyword="seed one for small dogs",
                                            amazon_search_query="seed one for small dogs"),
                "best-seed-two": self.topic(topic="best-seed-two", keyword="seed two for small dogs",
                                            amazon_search_query="seed two for small dogs")}

    def _seed(self, topics, max_):
        import refill_products
        self.plan.write_text(json.dumps({"abort": None, "topics": topics}), encoding="utf-8")
        with patch.object(refill_products, "eastern_date", lambda: "2026-11-01"):
            return self.run_cli("seed", "--plan", str(self.plan), "--max", str(max_))

    def test_seed_takes_a_handed_seed_and_rejects_one_it_was_not_handed(self):
        seeds = self._plan_two_seeds()
        # batch 1: context hands best-seed-one only.
        rc, out = self._seed([seeds["best-seed-one"]], 1)
        self.assertEqual(rc, 0, out)
        self.assertIn("seeded 1: best-seed-one", out)
        self.head.write_text(self.base.read_text(encoding="utf-8"), encoding="utf-8")
        rc, out = self._seed([seeds["best-seed-two"]], 1)
        self.assertEqual(rc, 1, out)
        self.assertIn("publish-plan seed this run was not handed", out)

    def test_seed_rejects_a_seed_that_differs_from_the_plan(self):
        seeds = self._plan_two_seeds()
        for field, value in (("keyword", "seed one toys"), ("species", "cat"), ("category", "beds")):
            with self.subTest(field=field):
                self.head.write_text(self.base.read_text(encoding="utf-8"), encoding="utf-8")
                rc, out = self._seed([dict(seeds["best-seed-one"], **{field: value})], 2)
                self.assertEqual(rc, 1, out)
                self.assertIn(f"differs from the publish plan in: {field}", out)

    def test_seed_rejects_an_expired_seed(self):
        import refill_products
        seeds = self._plan_two_seeds()
        self.plan.write_text(json.dumps({"abort": None, "topics": [seeds["best-seed-one"]]}),
                             encoding="utf-8")
        with patch.object(refill_products, "eastern_date", lambda: "2026-12-01"):
            rc, out = self.run_cli("seed", "--plan", str(self.plan), "--max", "2")
        self.assertEqual(rc, 1, out)
        self.assertIn("not handed", out)

    def test_seed_rejects_a_bare_best_x_head_term_for_a_self_planned_topic(self):
        self._plan_two_seeds()
        for kw, ok in (("best dog toys", False), ("best freeze dried dog treats", False),
                       ("best dog toys for small dogs", True), ("dog toys for small dogs", True)):
            with self.subTest(keyword=kw):
                self.head.write_text(self.base.read_text(encoding="utf-8"), encoding="utf-8")
                rc, out = self._seed([self.topic(keyword=kw)], 4)
                self.assertEqual(rc, 0 if ok else 1, out)
                self.assertEqual("head term" in out, not ok, out)

    def test_bare_head_term_definition(self):
        cases = {"best dog toys": True, "Best  Cat Litter Mat": True, "best": True,
                 "best freeze dried dog treats": True,
                 "best dog toys for small dogs": False, "best cat tree under 100": False,
                 "cat advent calendar with treats": False, "bestselling dog toys": False,
                 "": False, None: False}
        for kw, bare in cases.items():
            with self.subTest(keyword=kw):
                self.assertEqual(self.cli.is_bare_head_term(kw), bare)

    def test_check_holds_a_seed_entry_that_differs_or_expired_and_a_bare_head_term(self):
        import refill_products
        self._plan_two_seeds()
        good_seed = self.full_entry(topic="best-seed-one", keyword="seed one for small dogs",
                                    amazon_search_query="seed one for small dogs",
                                    asin="B0SEEDCHK1")
        cases = [("good seed", "2026-11-01", good_seed, True),
                 ("changed category", "2026-11-01", dict(good_seed, category="beds"), False),
                 ("expired", "2026-12-01", good_seed, False),
                 ("bare head term", "2026-11-01",
                  self.full_entry(topic="best-dog-toy-thing", keyword="best dog toys",
                                  asin="B0SEEDCHK2"), False),
                 ("long tail", "2026-11-01",
                  self.full_entry(topic="best-dog-toy-thing", keyword="best dog toys for small dogs",
                                  asin="B0SEEDCHK3"), True)]
        for label, today, entry, ok in cases:
            with self.subTest(case=label), \
                 patch.object(refill_products, "eastern_date", lambda: today):
                rc, out = self.check([entry])
                self.assertEqual(rc, 0 if ok else 1, out)

    def _check_on(self, entries, *flags, today="2026-11-01"):
        import refill_products
        base = json.loads(self.base.read_text(encoding="utf-8"))
        self.head.write_text(json.dumps(base + entries), encoding="utf-8")
        with patch.object(refill_products, "eastern_date", lambda: today):
            return self.run_cli("check", "--base", str(self.base), *flags)

    def _seed_entry(self, n):
        if n not in (1, 2):
            raise ValueError(f"_plan_two_seeds has seeds 1 and 2, not {n}")
        words = ("one", "two")[n - 1]
        return self.full_entry(topic=f"best-seed-{words}", keyword=f"seed {words} for small dogs",
                               amazon_search_query=f"seed {words} for small dogs",
                               asin=f"B0SEEDMAX{n}")

    def test_check_holds_a_seed_beyond_the_batch_when_seed_was_bypassed(self):
        """Defense in depth: a plan-seed entry that `seed --max 1` would refuse
        ("not handed") is held by `check --max 1` too, even when the entry is a
        complete one that never went through `seed`."""
        self._plan_two_seeds()
        second = self._seed_entry(2)
        rc, out = self._check_on([second], "--max", "1")
        self.assertEqual(rc, 1, out)
        self.assertIn("publish-plan seed this run was not handed", out)
        for flags in (("--max", "2"), ()):
            with self.subTest(flags=flags):
                rc, out = self._check_on([second], *flags)
                self.assertEqual(rc, 0, out)

    def test_check_holds_more_new_entries_than_the_batch(self):
        """`seed` reads at most --max planned topics, so a branch with more new
        entries than that did not come through it."""
        self._plan_two_seeds()
        both = [self._seed_entry(1), self._seed_entry(2)]
        rc, out = self._check_on(both, "--max", "1")
        self.assertEqual(rc, 1, out)
        self.assertIn("2 new entries, more than --max 1", out)
        self.assertEqual(self._check_on(both, "--max", "2")[0], 0)

    def test_check_and_seed_agree_on_every_seed_at_every_batch(self):
        """The rule is one rule: for each batch and each plan seed, `seed`
        takes the topic exactly when `check` passes the finished entry."""
        seeds = self._plan_two_seeds()
        for batch in (1, 2):
            for n, slug in ((1, "best-seed-one"), (2, "best-seed-two")):
                with self.subTest(batch=batch, seed=slug):
                    self.head.write_text(self.base.read_text(encoding="utf-8"), encoding="utf-8")
                    seed_rc, _ = self._seed([seeds[slug]], batch)
                    check_rc, out = self._check_on([self._seed_entry(n)], "--max", str(batch))
                    self.assertEqual(check_rc == 0, seed_rc == 0, out)
                    self.assertEqual(seed_rc == 0, n <= batch)

    def test_check_takes_the_same_batch_flag_and_default_as_seed(self):
        ap = self.cli.build_parser()
        seed_default = ap.parse_args(["seed", "--plan", "p"]).max
        self.assertEqual(ap.parse_args(["check", "--base", "b"]).max, seed_default)
        self.assertEqual(ap.parse_args(["check", "--base", "b", "--max", "3"]).max, 3)

    def test_context_without_a_plan_has_no_seeds(self):
        out = self.base.parent / "ctx.json"
        self.run_cli("context", "--out", str(out), "--batch", "6")
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["seeds"], [])

    def test_context_lists_topics_and_taken_slugs(self):
        out = self.base.parent / "ctx.json"
        self.run_cli("context", "--out", str(out), "--batch", "6")
        ctx = json.loads(out.read_text(encoding="utf-8"))
        import categories
        self.assertEqual(ctx["categories"], list(categories.TOPICS))
        self.assertEqual(ctx["batch"], 6)
        self.assertIn("best-published-post", ctx["taken_slugs"])
        self.assertEqual([q["topic"] for q in ctx["queued"]], list(self.QUEUED))


def io_capture(fn):
    """(return value, captured stdout) of fn()."""
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn()
    return rc, buf.getvalue()


class TestRefillWatchdog(unittest.TestCase):
    """refill_watchdog.py -- the dead-man's switch keyed on queue depth, sharing
    queue_alert's depth function and sender with push_pins_to_sheets."""

    def setUp(self):
        import refill_watchdog
        import queue_alert
        self.w = refill_watchdog
        self.qa = queue_alert

    def test_verdict_table(self):
        cases = [  # (publishable depth, open refill PRs or None=unreadable, alerts?)
            (0, [], True), (0, [7], True), (1, [], True), (1, [7], True),
            (2, [], False), (3, [7], False), (5, None, True), (0, None, True)]
        for depth, prs, alerts in cases:
            self.assertEqual(self.w.verdict(depth, prs) is not None, alerts, (depth, prs))

    def test_an_open_pr_is_named_not_a_silencer(self):
        self.assertIn("#7, #9", self.w.verdict(1, [7, 9]))

    def test_unreadable_pr_lists_fail_closed(self):
        for raw in ("", None, "unreadable", "-1", "1", "[1.0]", "[true]", "[-3]", "[0]",
                    "²", "[²]", "{}", "[1,", " "):
            self.assertIsNone(self.w.parse_open_prs(raw), repr(raw))
        self.assertEqual(self.w.parse_open_prs("[]"), [])
        self.assertEqual(self.w.parse_open_prs("[12,13]\n"), [12, 13])

    def test_held_placeholders_are_not_depth(self):
        good = dict(SAMPLE_PRODUCT)
        held = dict(SAMPLE_PRODUCT, asin="NEEDS_ASIN", image="NEEDS_IMAGE")
        products = {"best-a": good, "best-b": held, "best-c": good}
        self.assertEqual(self.qa.publishable_unpublished(products, {"best-c"}), ["best-a"])

    def test_push_pins_counts_with_the_same_function(self):
        import push_pins_to_sheets as pk
        with patch.object(self.qa, "publishable_unpublished", return_value=["a", "b"]):
            self.assertEqual(pk.count_unpublished(), 2)

    def _main(self, env, products=None, with_output=True):
        """Run main() with SMTP mocked and mail credentials SET, so a send would
        happen if any email path were left. Returns (rc, stdout, SMTP mock, the
        GITHUB_OUTPUT values parsed the way the runner reads them, or None)."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "github_output"
            env = {"GMAIL_SMTP_USER": "u", "GMAIL_ACCOUNT": "u", "GMAIL_APP_PASSWORD": "x",
                   "GITHUB_OUTPUT": str(out) if with_output else "", **env}
            with patch.dict(os.environ, env), \
                 patch.object(self.qa, "publishable_unpublished", return_value=products or []), \
                 patch.object(self.qa.smtplib, "SMTP") as mock_smtp:
                rc, stdout = io_capture(self.w.main)
            outputs = _parse_github_output(out.read_text(encoding="utf-8")) if out.exists() else None
        return rc, stdout, mock_smtp, outputs

    def test_an_alert_is_written_for_the_issue_step_and_exits_red(self):
        rc, out, smtp, outputs = self._main({"OPEN_REFILL_PRS": "[42]"}, products=["best-a"])
        self.assertEqual(rc, 1)
        self.assertIn("ALERT", out)
        self.assertEqual(outputs["summary"], "the topic queue is low")
        self.assertIn("Refill watchdog: only 1 publishable topic(s) queued; "
                      "refill PR(s) open but not merged: #42.", outputs["body"])
        self.assertIn("/pull/42", outputs["body"])
        self.assertIn("Run a refill, or merge the open refill PR.", outputs["body"])
        self.assertIn("Close this issue once the queue is refilled.", outputs["body"])
        self.assertEqual(sorted(outputs), ["body", "summary"])
        smtp.assert_not_called()   # one alert path: no email, even with credentials set

    def test_the_summary_carries_no_count_so_reruns_share_a_title(self):
        summaries = {self._main({"OPEN_REFILL_PRS": prs}, products=products)[3]["summary"]
                     for prs, products in (("[]", []), ("[]", ["a"]), ("[42]", ["a"]), ("[7,9]", []))}
        self.assertEqual(summaries, {"the topic queue is low"})
        unreadable = {self._main({"OPEN_REFILL_PRS": "x"}, products=products)[3]["summary"]
                      for products in ([], ["a", "b", "c"])}
        self.assertEqual(unreadable, {"open refill PRs could not be read"})

    def test_an_unreadable_pr_list_gets_advice_for_that_cause(self):
        """The fault is the PR listing, not the queue, so the advice is to fix the
        listing and re-run, never "close once the queue is refilled"."""
        _, _, _, outputs = self._main({"OPEN_REFILL_PRS": "unreadable"}, products=["a", "b", "c"])
        body = outputs["body"]
        self.assertIn("open refill PRs could not be read; 3 publishable topic(s) queued", body)
        self.assertIn("\"Count open refill PRs\" step could not list", body)
        self.assertIn("re-run this workflow", body)
        self.assertIn("Close this issue once a watchdog run passes.", body)
        self.assertNotIn("queue is refilled", body)
        self.assertNotIn("merge the open refill PR", body)

    def test_an_alert_without_github_output_still_exits_red(self):
        rc, out, smtp, outputs = self._main({"OPEN_REFILL_PRS": "[]"}, with_output=False)
        self.assertEqual(rc, 1)
        self.assertIn("ALERT", out)
        self.assertIsNone(outputs)
        smtp.assert_not_called()

    def test_a_non_ascii_digit_count_alerts_instead_of_crashing(self):
        rc, out, _, outputs = self._main({"OPEN_REFILL_PRS": "²"}, products=["a", "b", "c"])
        self.assertEqual(rc, 1)
        self.assertIn("could not be read", out)
        self.assertEqual(outputs["summary"], "open refill PRs could not be read")

    def test_healthy_queue_exits_green_and_sends_nothing(self):
        rc, _, smtp, outputs = self._main({"OPEN_REFILL_PRS": "[]"}, products=["a", "b"])
        self.assertEqual(rc, 0)
        self.assertIsNone(outputs)
        smtp.assert_not_called()

    def test_workflow_writes_only_issues_and_passes_values_through_env(self):
        wf = (REPO / ".github" / "workflows" / "refill-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn("permissions: {}", wf)
        self.assertEqual(_job_permissions(wf, "watchdog"),
                         {"contents": "read", "pull-requests": "read", "issues": "write"})
        self.assertEqual(re.findall(r"[\w-]+:\s*write", wf), ["issues: write"])
        self.assertIn("persist-credentials: false", wf)
        self.assertIn("open=unreadable", wf)
        self.assertIn("map(.number) | tojson", wf)
        # Every ${{ }} expression is an env: value, never text inside a run: script.
        for line in wf.splitlines():
            if "${{" in line:
                self.assertRegex(line, r"^\s+[A-Z_]+:\s+\$\{\{ [A-Za-z._]+ \}\}$", line)


# ---------------------------------------------------------------------------
# Related-reviews block (_includes/related-reviews.html, traffic plan move 1)
# ---------------------------------------------------------------------------
def _jekyll_absolute_url(val):
    """The test suite's one stand-in for Jekyll's absolute_url. Jekyll's
    url_filters.rb: "return input if Addressable::URI.parse(input.to_s).absolute?"
    -- an already-absolute URL passes through untouched, only a path (with its
    leading slash) gets site.url prepended. baseurl is '' on this site."""
    if re.match(r"[a-z][a-z0-9+.-]*://", str(val), re.I):
        return val
    return "https://happypetproductreviews.com" + str(val)


class _MiniLiquid:
    """Renders small pieces of the site's Liquid without Ruby: the
    related-reviews include, the social-image include (the one image rule) and
    the default layout's <title> tag.

    HONEST LIMIT: there is no Ruby or Jekyll on the build box, so this is not
    Jekyll. It is a small Liquid subset -- the tags and filters those pieces
    use, with Liquid's own truthiness (only nil and false are falsy) and its
    flat assign scope. Anything outside the subset raises instead of guessing,
    so a template cannot quietly grow past what this checks. The real render
    is build-check.yml's `jekyll build` on the PR; these tests guard the
    logic against the real _posts on every CI run.
    """

    TOKEN = re.compile(r"(\{%-?.*?-?%\}|\{\{-?.*?-?\}\})", re.S)

    class _Break(Exception):
        pass

    def __init__(self, source):
        source = re.sub(r"\{%-?\s*comment\s*-?%\}.*?\{%-?\s*endcomment\s*-?%\}", "", source, flags=re.S)
        parts = self.TOKEN.split(source)
        # Whitespace control: {%- strips the text before it, -%} the text after.
        for i in range(1, len(parts), 2):
            if parts[i][2] == "-":
                parts[i - 1] = parts[i - 1].rstrip()
            if parts[i][-3] == "-":
                parts[i + 1] = parts[i + 1].lstrip()
        self.tokens = parts
        self.pos = 0
        self.tree = self._parse(())

    # -- parsing --------------------------------------------------------------
    def _parse(self, stop):
        nodes = []
        while self.pos < len(self.tokens):
            tok = self.tokens[self.pos]
            self.pos += 1
            if not tok.startswith(("{%", "{{")):
                if tok:
                    nodes.append(("text", tok))
                continue
            inner = tok[2:-2].strip("-").strip()
            if tok.startswith("{{"):
                nodes.append(("out", inner))
                continue
            word, _, rest = inner.partition(" ")
            if word in stop:
                self.pos -= 1
                return nodes
            if word == "assign":
                name, _, expr = rest.partition("=")
                nodes.append(("assign", name.strip(), expr.strip()))
            elif word == "capture":
                body = self._parse(("endcapture",))
                self._expect("endcapture")
                nodes.append(("capture", rest.strip(), body))
            elif word == "for":
                m = re.fullmatch(r"(\w+) in (.+)", rest.strip())
                body = self._parse(("endfor",))
                self._expect("endfor")
                nodes.append(("for", m.group(1), m.group(2).strip(), body))
            elif word in ("if", "unless"):
                branches, cond = [], rest
                while True:
                    body = self._parse(("elsif", "else", "endif", "endunless"))
                    branches.append((cond, body))
                    end = self.tokens[self.pos][2:-2].strip("-").strip()
                    self.pos += 1
                    kw, _, cond = end.partition(" ")
                    if kw == "else":
                        cond = None
                    elif kw in ("endif", "endunless"):
                        break
                nodes.append((word, branches))
            elif word == "break":
                nodes.append(("break",))
            else:
                raise AssertionError(f"unhandled Liquid tag {word!r}")
        if stop:
            raise AssertionError(f"unclosed block, expected {stop}")
        return nodes

    def _expect(self, word):
        tok = self.tokens[self.pos][2:-2].strip("-").strip()
        assert tok == word, f"expected {word}, got {tok}"
        self.pos += 1

    # -- evaluation -----------------------------------------------------------
    def render(self, ctx):
        self.ctx = dict(ctx)
        return self._run(self.tree)

    def _run(self, nodes):
        out = []
        for node in nodes:
            kind = node[0]
            if kind == "text":
                out.append(node[1])
            elif kind == "out":
                val = self._expr(node[1])
                out.append("" if val is None else str(val))
            elif kind == "assign":
                self.ctx[node[1]] = self._expr(node[2])
            elif kind == "capture":
                self.ctx[node[1]] = self._run(node[2])
            elif kind == "for":
                for item in self._expr(node[2]) or []:
                    self.ctx[node[1]] = item
                    try:
                        out.append(self._run(node[3]))
                    except self._Break as stop:
                        out.append(stop.args[0])
                        break
            elif kind in ("if", "unless"):
                for cond, body in node[1]:
                    hit = cond is None or self._cond(cond)
                    if kind == "unless" and cond is not None:
                        hit = not hit
                    if hit:
                        out.append(self._run(body))
                        break
            elif kind == "break":
                raise self._Break("".join(out))
        return "".join(out)

    def _cond(self, text):
        # Liquid has no parentheses and evaluates or/and right to left; the
        # include only ever chains one kind, where the order cannot matter.
        if " or " in text:
            assert " and " not in text, "mixed and/or is outside the subset"
            return any(self._cond(t) for t in text.split(" or "))
        if " and " in text:
            return all(self._cond(t) for t in text.split(" and "))
        m = re.fullmatch(r"(.+?)\s*(==|!=|>=|<=|>|<|\scontains\s)\s*(.+)", text.strip())
        if not m:
            val = self._value(text.strip())
            return val is not None and val is not False
        a, op, b = self._value(m.group(1)), m.group(2).strip(), self._value(m.group(3))
        if op == "contains":
            return a is not None and b is not None and str(b) in a
        return {"==": a == b, "!=": a != b, ">=": a >= b, "<=": a <= b,
                ">": a > b, "<": a < b}[op]

    def _value(self, token):
        token = token.strip()
        if token[:1] in ("'", '"'):
            return token[1:-1]
        if re.fullmatch(r"-?\d+", token):
            return int(token)
        m = re.fullmatch(r"\((\d+)\.\.(\d+)\)", token)
        if m:
            return list(range(int(m.group(1)), int(m.group(2)) + 1))
        if token in ("true", "false", "nil"):
            return {"true": True, "false": False, "nil": None}[token]
        val = self.ctx
        for key in token.split("."):
            if isinstance(val, dict):
                val = val.get(key)
            elif isinstance(val, (list, str)) and key == "size":
                val = len(val)
            elif isinstance(val, list) and key == "first":
                val = val[0] if val else None
            else:
                return None
        return val

    def _expr(self, text):
        parts = [p.strip() for p in re.split(r"\|(?=(?:[^\"']|[\"'][^\"']*[\"'])*$)", text)]
        val = self._value(parts[0])
        for filt in parts[1:]:
            name, _, argtext = filt.partition(":")
            args = [self._value(a) for a in argtext.split(",")] if argtext.strip() else []
            name = name.strip()
            if name == "first":
                val = val[0] if val else None
            elif name == "append":
                val = ("" if val is None else str(val)) + str(args[0])
            elif name == "prepend":
                val = str(args[0]) + ("" if val is None else str(val))
            elif name == "plus":
                val = int(val) + int(args[0])
            elif name == "where":
                val = [x for x in (val or []) if x.get(args[0]) == args[1]]
            elif name == "default":
                val = val if val not in (None, False, "") else args[0]
            elif name == "absolute_url":
                val = _jekyll_absolute_url(val)
            elif name == "date":
                # Only the strftime codes the layouts use; %-d is Ruby's
                # unpadded day, which Python's strftime lacks on Windows.
                # The format is one quoted argument that may hold commas.
                import datetime
                day = datetime.date.fromisoformat(str(val)[:10])
                fmt = self._value(argtext).replace("%-d", str(day.day))
                assert not re.search(r"%[^BYmd]", fmt), f"unhandled date format {argtext!r}"
                val = day.strftime(fmt)
            elif name == "escape":
                val = ("" if val is None else str(val)).replace("&", "&amp;").replace(
                    "<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")
            else:
                raise AssertionError(f"unhandled Liquid filter {name!r}")
        return val


class TestRelatedReviews(unittest.TestCase):
    """Every post page links 3-4 other reviews: same topic first, then the same
    animal, then the newest. Rendered from the include's real text against the
    real _posts front matter, in the order Jekyll hands site.posts to Liquid
    (Document#<=> sorts by date then path; site.posts is that, reversed)."""

    INCLUDE = REPO / "_includes" / "related-reviews.html"
    MAX_LINKS = 4

    @classmethod
    def setUpClass(cls):
        cls.source = cls.INCLUDE.read_text(encoding="utf-8")
        posts = []
        for p in sorted((REPO / "_posts").glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.md")):
            fm = {}
            for line in p.read_text(encoding="utf-8").split("---", 2)[1].splitlines():
                m = re.match(r"^([a-z_]+):\s*(.+)$", line)
                if m:
                    fm[m.group(1)] = m.group(2).strip().strip('"')
            cats = [c.strip() for c in fm["categories"].strip("[]").split(",")]
            slug = p.stem[11:]
            posts.append({"title": fm["title"], "species": fm.get("species"),
                          "image": fm.get("image"), "categories": cats,
                          "date": p.stem[:10], "path": f"_posts/{p.name}", "slug": slug,
                          "url": "/" + "/".join(cats) + "/" + slug + "/"})
        posts.sort(key=lambda d: (d["date"], d["path"]))
        cls.posts = list(reversed(posts))
        cls.by_url = {d["url"]: d for d in cls.posts}
        thumbs = sorted((REPO / "assets" / "images" / "thumbs").glob("*.jpg"))
        topics = json.loads((REPO / "_data" / "categories.json").read_text(encoding="utf-8"))
        cls.site = {"posts": cls.posts, "baseurl": "",
                    "static_files": [{"path": "/assets/images/thumbs/" + t.name} for t in thumbs],
                    "data": {"categories": topics}}
        cls.rendered = {d["url"]: _MiniLiquid(cls.source).render({"site": cls.site, "page": d})
                        for d in cls.posts}

    def _links(self, url):
        html = self.rendered[url]
        return re.findall(r'<a [^>]*href="([^"]+)"', html)

    def test_every_post_gets_three_or_four_related_links(self):
        self.assertGreater(len(self.posts), self.MAX_LINKS)
        for url in self.by_url:
            with self.subTest(post=url):
                links = self._links(url)
                self.assertGreaterEqual(len(links), 3)
                self.assertLessEqual(len(links), self.MAX_LINKS)

    def test_no_post_links_to_itself(self):
        for url in self.by_url:
            with self.subTest(post=url):
                self.assertNotIn(url, self._links(url))

    def test_every_target_is_a_real_post_and_none_repeats(self):
        for url in self.by_url:
            links = self._links(url)
            with self.subTest(post=url):
                self.assertEqual(len(links), len(set(links)), "a review is linked twice")
                for href in links:
                    self.assertIn(href, self.by_url, f"{href} is not a post URL")
                    self.assertTrue((REPO / self.by_url[href]["path"]).is_file())

    def test_the_topics_with_a_single_post_still_get_links(self):
        """collars, training, flea-tick and treats have one post each, so the
        same-topic tier is empty for them and the fallback carries the block."""
        counts = {}
        for d in self.posts:
            counts[d["categories"][0]] = counts.get(d["categories"][0], 0) + 1
        lonely = [d["url"] for d in self.posts if counts[d["categories"][0]] == 1]
        self.assertTrue(lonely, "fixture drift: no single-post topic left to exercise the fallback")
        for url in lonely:
            with self.subTest(post=url):
                self.assertEqual(len(self._links(url)), self.MAX_LINKS)

    def test_tiers_fill_in_order_topic_then_animal_then_newest(self):
        """Expected picks come from the plan's rule applied by hand here, not
        from the include's own logic."""
        def animal(page, other):
            return "both" in (page["species"], other["species"]) or page["species"] == other["species"]
        for page in self.posts:
            others = [d for d in self.posts if d["url"] != page["url"]]
            want = [d for d in others if d["categories"][0] == page["categories"][0]]
            want += [d for d in others if d not in want and animal(page, d)]
            want += [d for d in others if d not in want]
            with self.subTest(post=page["url"]):
                self.assertEqual(self._links(page["url"]),
                                 [d["url"] for d in want[:self.MAX_LINKS]])

    def test_output_is_deterministic(self):
        for name in ("sample", "shuffle", "site.time", "now", "random"):
            self.assertNotIn(name, self.source)
        page = self.posts[0]
        again = _MiniLiquid(self.source).render({"site": self.site, "page": page})
        self.assertEqual(again, self.rendered[page["url"]])

    def test_block_has_the_placeholder_heading(self):
        for url, html in self.rendered.items():
            with self.subTest(post=url):
                self.assertIn("More reviews you might like", html)

    def test_post_layout_includes_it_between_the_review_and_the_buy_bar(self):
        layout = (REPO / "_layouts" / "post.html").read_text(encoding="utf-8")
        tag = layout.find("{%- include related-reviews.html -%}")
        self.assertNotEqual(tag, -1, "post.html does not include related-reviews.html")
        self.assertGreater(tag, layout.find("{{ content }}"))
        self.assertLess(tag, layout.find('<div class="buybar">'))


def _scratch_site_with_posts(tmp, posts_per_topic, hub_copy=None, drafts=()):
    """A scratch site for hub_pages: dated posts per topic, DRAFT posts that do
    not count yet, hubs/ holding a stub toys hub, and, given hub_copy,
    _data/hub_copy.json."""
    (tmp / "_posts").mkdir()
    (tmp / "hubs").mkdir()
    (tmp / "hubs" / "toys.md").write_text("---\nlayout: hub\n---\n", encoding="utf-8")
    for topic, n in posts_per_topic.items():
        for i in range(n):
            (tmp / "_posts" / f"2026-01-{i + 1:02d}-best-{topic}-{i}.md").write_text(
                f"---\ncategories: [{topic}]\n---\n", encoding="utf-8")
    for topic in drafts:
        (tmp / "_posts" / f"DRAFT-best-{topic}-new.md").write_text(
            f"---\ncategories: [{topic}]\n---\n", encoding="utf-8")
    if hub_copy is not None:
        (tmp / "_data").mkdir(exist_ok=True)
        (tmp / "_data" / "hub_copy.json").write_text(json.dumps(hub_copy), encoding="utf-8")


def _bash_for_workflow_steps():
    """(bash, "") able to run publish.yml's steps here, or (None, why not).
    The steps need bash 4+ (mapfile) and git; WSL's System32 bash runs in a
    Linux VM that cannot see this interpreter, so it does not count."""
    import shutil
    import subprocess
    git = shutil.which("git")
    if not git:
        return None, "no git on PATH"
    # Git for Windows keeps bash.exe beside its own install root.
    candidates = [shutil.which("bash")] + [str(p / "bin" / "bash.exe")
                                           for p in list(Path(git).resolve().parents)[:3]]
    for bash in candidates:
        if not bash or not Path(bash).exists():
            continue
        if any(part in bash.lower() for part in ("system32", "windowsapps")):
            continue
        try:
            out = subprocess.run([bash, "--noprofile", "--norc", "-c", 'echo "${BASH_VERSINFO[0]}"'],
                                 capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if out.isdigit() and int(out) >= 4:
            return bash, ""
    return None, "no bash 4+ (publish.yml uses mapfile) outside WSL"


def _git_fixture_repo(tmp):
    """Make tmp a git repo with everything in it committed, line endings as
    written. Returns git(*args), which returns git's stdout."""
    import subprocess

    def git(*args):
        return subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "user.name=t",
                               "-c", "user.email=t@example.invalid", *args],
                              cwd=tmp, capture_output=True, text=True, check=True).stdout
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "fixture")
    return git


def _publish_date_script():
    """publish.yml's dating step as the runner gets it on a cap-1 day, with
    `python3` pointed at this interpreter."""
    import shlex
    _, script = _named_step((WORKFLOWS / "publish.yml").read_text(encoding="utf-8"),
                            "Find and date unpublished posts")
    return (script.replace("${{ steps.cap.outputs.cap }}", "1")
            .replace("python3 ", shlex.quote(Path(sys.executable).as_posix()) + " "))


# The hub layout's intro and how-we-choose markup, as rendered, per front-matter field.
HUB_FIELD_MARKUP = {"intro": '<p class="standfirst">{}</p>',
                    "how_we_choose": '<p class="hub-method"><b>How we choose:</b> {}</p>'}


def _hub_front_matter_strings(text):
    """The quoted `key: "value"` lines of a hub file's front matter. Read by
    hand because validate_published_chewy_links.parse_frontmatter strips quotes
    off both ends loosely and ignores escapes, so a stray backslash or inner
    quote would pass through it unseen. This reader matches the whole quoted
    scalar and refuses an escape or inner quote, so the value is exactly the
    double-quoted YAML scalar Jekyll reads, with nothing to unescape."""
    block = text.split("---\n")[1]
    out = {}
    for line in block.splitlines():
        m = re.fullmatch(r'(\w+): "(.*)"', line)
        if m:
            assert not re.search(r'["\\]', m.group(2)), f"{line!r} has an escape or a quote"
            out[m.group(1)] = m.group(2)
    return out


def _render_hub_field(field, hub_file_text):
    """The hub layout's own `if page.<field>` block, rendered by _MiniLiquid
    from a hub file's front matter, stripped."""
    layout = (REPO / "_layouts" / "hub.html").read_text(encoding="utf-8")
    m = re.search(r"\{%-?\s*if page\." + field + r"\s*-?%\}.*?\{%-?\s*endif\s*-?%\}",
                  layout, re.S)
    assert m, f"hub.html has no `if page.{field}` block"
    return _MiniLiquid(m.group(0)).render({"page": _hub_front_matter_strings(hub_file_text)}).strip()


class TestTopicHubs(unittest.TestCase):
    """Topic hubs (Director-approved 2026-10-13): hubs/<topic>.md, one layout
    (_layouts/hub.html) that lists every post in the topic, the post breadcrumb
    pointing at the topic's hub, and hub_pages.py, which publish.yml runs so a
    topic that reaches HUB_MIN posts gets its hub in the same publish
    (Director-approved 2026-10-14). The listing itself is Liquid (where_exp,
    which _MiniLiquid does not model); build-check.yml's jekyll build is its
    real render."""

    @classmethod
    def setUpClass(cls):
        from validate_published_chewy_links import parse_frontmatter
        cls.hubs = {p.stem: parse_frontmatter(p) for p in (REPO / "hubs").glob("*.md")}

    def test_every_hub_has_its_front_matter(self):
        import hub_pages
        for slug, fm in self.hubs.items():
            with self.subTest(hub=slug):
                self.assertEqual(fm["layout"], "hub")
                self.assertEqual(fm["hub_topic"], slug)
                self.assertEqual(fm["permalink"], f"/{slug}/")
                for key in hub_pages.COPY_FIELDS:
                    self.assertTrue(fm.get(key), key)
                self.assertNotIn("sitemap", fm)   # jekyll-sitemap lists every hub

    # The next two together pin the hubs to exactly the topics with HUB_MIN or
    # more posts; each direction has its own cause and its own fix.
    def test_no_topic_reached_hub_min_without_a_hub(self):
        import hub_pages
        self.assertEqual(hub_pages.missing_hubs(), [],
                         "a topic reached HUB_MIN posts with no hub; publish.yml writes "
                         "it in the same publish, so it got there another way. Run "
                         "python hub_pages.py")

    def test_every_hub_topic_has_hub_min_or_more_posts(self):
        import hub_pages
        counts = hub_pages.topic_counts()
        for slug in self.hubs:
            with self.subTest(hub=slug):
                self.assertGreaterEqual(counts[slug], hub_pages.HUB_MIN,
                                        "a hub for a topic under HUB_MIN posts: a post was "
                                        "removed or recategorized")

    def test_every_hub_is_written_from_the_copy_file(self):
        """D-2: the copy file is the one source of hub copy. Each hub file is
        exactly what hub_pages writes from it, so copy edited in a hub file
        alone, or in the data file alone, goes red here."""
        import hub_pages
        hub_copy = hub_pages.load_copy()
        for slug in self.hubs:
            with self.subTest(hub=slug):
                self.assertEqual((REPO / "hubs" / f"{slug}.md").read_text(encoding="utf-8"),
                                 hub_pages.hub_text(slug, hub_copy),
                                 f"hubs/{slug}.md differs from {hub_pages.COPY_FILE}; run "
                                 "python hub_pages.py --sync")

    def test_each_hubs_rendered_intro_and_method_are_its_copy_file_entry(self):
        """D-2, from the other end: the layout's own intro and how-we-choose
        markup, rendered from each hub file's front matter, shows exactly the
        text in _data/hub_copy.json. Expected text is the data file's literal,
        not hub_pages.hub_text, so a writer bug cannot cancel itself out."""
        copy = json.loads((REPO / "_data" / "hub_copy.json").read_text(encoding="utf-8"))
        self.assertTrue(self.hubs)
        for field, markup in HUB_FIELD_MARKUP.items():
            for slug in self.hubs:
                with self.subTest(hub=slug, field=field):
                    self.assertIn(slug, copy)
                    hub_file = (REPO / "hubs" / f"{slug}.md").read_text(encoding="utf-8")
                    self.assertEqual(_render_hub_field(field, hub_file),
                                     markup.format(copy[slug][field]))

    def test_the_rendered_copy_check_catches_copy_that_drifted(self):
        """Prove the check above can fail: a hub whose intro was edited in the
        hub file alone, by one character, renders text the data file lacks."""
        copy = json.loads((REPO / "_data" / "hub_copy.json").read_text(encoding="utf-8"))
        slug = sorted(self.hubs)[0]
        intro = copy[slug]["intro"]
        hub_file = (REPO / "hubs" / f"{slug}.md").read_text(encoding="utf-8")
        self.assertIn(intro, hub_file)
        rendered = _render_hub_field("intro", hub_file.replace(intro, intro + "!"))
        self.assertEqual(rendered, HUB_FIELD_MARKUP["intro"].format(intro + "!"))
        self.assertNotEqual(rendered, HUB_FIELD_MARKUP["intro"].format(intro))

    def test_the_copy_file_has_complete_copy_for_every_topic(self):
        """So the publish never meets a topic without copy. Plain one-line text
        (no double quotes, backslashes or newlines), so every reader of the hub
        front matter, ours included, reads it exactly."""
        import categories
        import hub_pages
        hub_copy = hub_pages.load_copy()
        self.assertEqual(sorted(hub_copy), sorted(categories.TOPICS))
        for slug, entry in hub_copy.items():
            with self.subTest(topic=slug):
                self.assertEqual(sorted(entry), sorted(hub_pages.COPY_FIELDS))
                self.assertEqual(hub_pages.copy_problems(slug, hub_copy), [])
                for field, text in entry.items():
                    self.assertFalse(set(text) & set('"\\\n'), field)
                    self.assertEqual(text, text.strip(), field)

    def test_scarletts_copy_for_the_new_topics(self):
        """Spot-pins from Scarlett's 2026-10-14 copy file, typed in here as an
        independent record, not read back from the data file."""
        import hub_pages
        hub_copy = hub_pages.load_copy()
        self.assertEqual(hub_copy["treats"]["title"], "Dog and Cat Treats: Picks by Purpose")
        self.assertEqual(hub_copy["tech"]["title"], "Pet Tech: Cameras and GPS Trackers")
        self.assertEqual(hub_copy["scratching"]["title"], "Cat Scratchers and Cat Trees")
        self.assertTrue(hub_copy["treats"]["intro"].startswith(
            "A treat does a job, whether that job is rewarding a sit, hiding a pill"))

    def test_no_hub_url_collides_with_a_page_post_or_redirect(self):
        taken = {}
        for p in list(REPO.glob("*.md")) + list((REPO / "_posts").glob("2*.md")):
            text = p.read_text(encoding="utf-8")
            fm = text.split("---", 2)[1] if text.startswith("---") else ""
            for m in re.finditer(r"^permalink: *(\S+)", fm, re.M):
                taken[m.group(1)] = p.name
            for m in re.finditer(r"^  - (/\S*)$", fm, re.M):   # redirect_from entries
                taken[m.group(1)] = p.name
        for slug in self.hubs:
            with self.subTest(hub=slug):
                self.assertNotIn(f"/{slug}/", taken)

    def test_the_layout_lists_the_topics_posts(self):
        layout = (REPO / "_layouts" / "hub.html").read_text(encoding="utf-8")
        self.assertIn('site.posts | where_exp: "p", "p.categories.first == page.hub_topic"', layout)
        self.assertIn("{% include post-list.html posts=hub_posts %}", layout)

    def test_every_listing_uses_the_one_tiering_include(self):
        """home, /cats/, /dogs/ and hubs list posts through post-list.html, which
        keeps the existing tiers (lead, two wide, eager through 7)."""
        include = (REPO / "_includes" / "post-list.html").read_text(encoding="utf-8")
        for tier in ('forloop.index == 1 %}', 'forloop.index <= 3 %}', 'forloop.index <= 7 %}'):
            self.assertIn(tier, include)
        for rel in ("_layouts/home.html", "cats.md", "dogs.md", "_layouts/hub.html"):
            with self.subTest(page=rel):
                text = (REPO / rel).read_text(encoding="utf-8")
                self.assertIn("{% include post-list.html posts=", text)
                self.assertNotIn("forloop.index", text)

    def test_a_post_breadcrumb_points_at_its_topic_hub_when_one_exists(self):
        post = (REPO / "_layouts" / "post.html").read_text(encoding="utf-8")
        crumb = re.search(r'<nav class="crumb".*?</nav>', post, re.S).group(0)
        pages = [{"hub_topic": s, "url": f"/{s}/"} for s in self.hubs] + [{"url": "/cats/"}]
        for category, href in (("toys", "/toys/"), ("litter", "/litter/"), ("tech", "/#reviews")):
            with self.subTest(category=category):
                html = _MiniLiquid(crumb).render({"site": {"baseurl": "", "pages": pages},
                                                  "category": category, "category_label": "L"})
                self.assertIn(f'<a href="{href}">L</a>', html)

    def _main(self, hub_pages, tmp, *argv):
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = hub_pages.main(list(argv), repo=tmp)
        return rc, out.getvalue(), err.getvalue()

    def test_hub_pages_writes_a_hub_only_for_a_topic_that_reached_hub_min(self):
        import hub_pages
        n = hub_pages.HUB_MIN
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _scratch_site_with_posts(tmp, {"toys": n, "dental": n, "tech": n - 1}, drafts=["tech"],
                                     hub_copy={"dental": {"title": "Dental Gear", "description": "Desc.",
                                                          "intro": "Intro.", "how_we_choose": "Method."}})
            self.assertEqual(hub_pages.missing_hubs(tmp), ["dental"])   # drafts do not count
            self.assertEqual(self._main(hub_pages, tmp)[0], 0)
            self.assertEqual((tmp / "hubs" / "dental.md").read_text(encoding="utf-8"),
                             '---\nlayout: hub\nhub_topic: dental\npermalink: /dental/\n'
                             'title: "Dental Gear"\ndescription: "Desc."\nintro: "Intro."\n'
                             'how_we_choose: "Method."\n---\n')
            self.assertEqual((tmp / "hubs" / "toys.md").read_text(encoding="utf-8"),
                             "---\nlayout: hub\n---\n", "an existing hub is left alone")
            self.assertEqual(hub_pages.missing_hubs(tmp), [])

    def test_hub_pages_writes_no_hub_for_a_topic_without_copy(self):
        import hub_pages
        n = hub_pages.HUB_MIN
        full = {f: f.upper() for f in hub_pages.COPY_FIELDS}
        for label, hub_copy, missing in (
                ("no entry", {}, ", ".join(hub_pages.COPY_FIELDS)),
                ("empty intro", {"dental": dict(full, intro=" ")}, "intro"),
                ("one field only", {"dental": {"title": "T"}}, ", ".join(hub_pages.COPY_FIELDS[1:]))):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                _scratch_site_with_posts(tmp, {"dental": n, "toys": n}, hub_copy=hub_copy)
                rc, _, err = self._main(hub_pages, tmp)
                self.assertEqual(rc, 1)
                self.assertIn(f"ERROR dental: no {missing} in {hub_pages.COPY_FILE}\n", err)
                self.assertEqual(sorted(p.name for p in (tmp / "hubs").glob("*.md")), ["toys.md"])

    def test_sync_rewrites_only_hubs_that_differ_from_the_copy_file(self):
        import hub_pages
        n = hub_pages.HUB_MIN
        copy_of = lambda t: {f: f"{t} {f}" for f in hub_pages.COPY_FIELDS}
        hub_copy = {t: copy_of(t) for t in ("toys", "beds", "dental")}
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _scratch_site_with_posts(tmp, {"toys": n, "beds": n, "dental": n}, hub_copy=hub_copy)
            (tmp / "hubs" / "beds.md").write_text(hub_pages.hub_text("beds", hub_copy), encoding="utf-8")
            rc, out, _ = self._main(hub_pages, tmp, "--sync", "--dry-run")
            self.assertEqual((rc, out), (0, "would rewrite hubs/toys.md\n"))
            self.assertEqual((tmp / "hubs" / "toys.md").read_text(encoding="utf-8"),
                             "---\nlayout: hub\n---\n")
            rc, out, _ = self._main(hub_pages, tmp, "--sync")
            self.assertEqual((rc, out), (0, "rewrote hubs/toys.md\n"))
            self.assertIn('title: "toys title"\n', (tmp / "hubs" / "toys.md").read_text(encoding="utf-8"))
            self.assertFalse((tmp / "hubs" / "dental.md").exists(), "--sync writes no new hub")
            self.assertEqual(self._main(hub_pages, tmp, "--sync")[1],
                             f"every hub matches {hub_pages.COPY_FILE}\n")

    def test_sync_rewrites_nothing_when_a_hub_has_no_copy(self):
        import hub_pages
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _scratch_site_with_posts(tmp, {}, hub_copy={})
            rc, _, err = self._main(hub_pages, tmp, "--sync")
            self.assertEqual(rc, 1)
            self.assertIn("ERROR toys: no ", err)
            self.assertEqual((tmp / "hubs" / "toys.md").read_text(encoding="utf-8"),
                             "---\nlayout: hub\n---\n")


# Exactly what publish.yml's `permissions:` holds as of 2026-10-14: contents for
# the publish commit, issues for the alert issues, actions to dispatch deploy.yml.
PUBLISH_PERMISSIONS = {"contents": "write", "issues": "write", "actions": "write"}


def _workflow_permissions(text):
    """(top-level permissions mapping, count of `permissions` keys at any
    indent). Reads the whole block: blank and comment lines inside it do not
    end it, so a key after one is still counted."""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if re.match(r"permissions\s*:", ln)]
    anywhere = len(re.findall(r"^\s*permissions\s*:", text, re.M))
    if len(starts) != 1 or lines[starts[0]].rstrip() != "permissions:":
        return None, anywhere
    perms = {}
    for ln in lines[starts[0] + 1:]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if not ln[0].isspace():
            break
        m = re.fullmatch(r"\s+([\w-]+):\s*(\S+?)(?:\s+#.*)?", ln)
        if not m:
            return None, anywhere
        perms[m.group(1)] = m.group(2)
    return perms, anywhere


class TestPublishWorkflowPins(unittest.TestCase):
    """What publish.yml's token may do, and what no workflow may run. Both are
    pinned as literals, so a widening or a new caller is a failing test and a
    reviewed change, not a quiet edit."""

    WORKFLOWS = REPO / ".github" / "workflows"

    def test_publish_permissions_are_pinned(self):
        text = (self.WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
        perms, anywhere = _workflow_permissions(text)
        self.assertEqual(perms, PUBLISH_PERMISSIONS,
                         "publish.yml's permissions: block changed; widening the token "
                         "is a reviewed change, so update this pin in the same PR")
        self.assertEqual(anywhere, 1, "a second permissions: key (a job-level block) "
                                      "can widen what the top-level block pins")

    def test_the_permissions_pin_reads_a_widening_however_it_is_written(self):
        """Attack the reader: each of these is a widening or an unreadable form
        and has to come out different from the pinned mapping."""
        base = ("permissions:\n  contents: write\n  issues: write\n"
                "  actions: write   # dispatch deploy.yml\n\nconcurrency:\n  group: g\n")
        self.assertEqual(_workflow_permissions(base), (PUBLISH_PERMISSIONS, 1))
        for label, text in (
                ("new key", base.replace("  issues: write\n", "  issues: write\n  pull-requests: write\n")),
                ("new key after a blank line",
                 base.replace("  actions: write   # dispatch deploy.yml\n",
                              "  actions: write\n\n  # comment\n\n  packages: write\n")),
                ("value widened", base.replace("contents: write", "contents: admin")),
                ("key dropped", base.replace("  issues: write\n", "")),
                ("write-all", base.replace("permissions:\n  contents: write\n  issues: write\n"
                                           "  actions: write   # dispatch deploy.yml\n",
                                           "permissions: write-all\n")),
                ("job-level block", base + "jobs:\n  publish:\n    permissions:\n      contents: write\n")):
            with self.subTest(case=label):
                self.assertNotEqual(_workflow_permissions(text), (PUBLISH_PERMISSIONS, 1))

    def test_no_workflow_runs_hub_pages_sync(self):
        """`hub_pages.py --sync` rewrites existing hubs from the copy file. It is
        hand-run, after editing the copy, and no workflow may run it."""
        files = [p for p in self.WORKFLOWS.rglob("*") if p.is_file()]
        self.assertIn("publish.yml", [p.name for p in files])
        for p in files:
            with self.subTest(workflow=p.name):
                # Deliberate over-match: hub_pages matches `--sync` exactly, so a case
                # variant would not sync, but failing on one costs nothing and guards
                # a future case-insensitive parser.
                self.assertNotIn("--sync", p.read_text(encoding="utf-8").lower())


class TestFooterHubLinks(unittest.TestCase):
    """The footer's topic links go to the topic hubs. They used to be
    /search/?q= queries, which depended on the search filter's species logic.
    A hub publish.yml adds later is not required here: that would turn CI red
    on main after an unattended publish."""

    SITE_PAGES = {"/", "/dogs/", "/cats/", "/search/", "/about/", "/contact/",
                  "/privacy-policy/"}

    @classmethod
    def setUpClass(cls):
        from validate_published_chewy_links import parse_frontmatter
        layout = (REPO / "_layouts" / "default.html").read_text(encoding="utf-8")
        footer = re.search(r'<footer class="colophon">.*?</footer>', layout, re.S)
        assert footer, "default.html has no colophon footer"
        cls.links = re.findall(r'<a href="\{\{ site\.baseurl \}\}([^"]*)">([^<]*)</a>',
                               footer.group(0))
        cls.hubs = {fm["permalink"]: fm["hub_topic"] for fm in
                    (parse_frontmatter(p) for p in (REPO / "hubs").glob("*.md"))}
        topics = json.loads((REPO / "_data" / "categories.json").read_text(encoding="utf-8"))
        cls.labels = {t["slug"]: t["label"] for t in topics["topics"]}

    def test_every_footer_link_is_a_hub_or_a_site_page(self):
        self.assertGreater(len(self.links), len(self.SITE_PAGES))
        for href, _ in self.links:
            with self.subTest(href=href):
                self.assertTrue(href in self.hubs or href in self.SITE_PAGES, href)

    def test_hub_links_use_the_topic_label_and_appear_once(self):
        hub_links = [(href, label) for href, label in self.links if href in self.hubs]
        self.assertGreaterEqual(len(hub_links), 8)
        self.assertEqual(len({h for h, _ in hub_links}), len(hub_links), "a hub is linked twice")
        for href, label in hub_links:
            with self.subTest(href=href):
                self.assertEqual(label, self.labels[self.hubs[href]])

    def test_no_search_query_links_remain(self):
        layout = (REPO / "_layouts" / "default.html").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'href="[^"]*\?q=[^"]*"', layout), [])


class TestPublishWritesTheHub(unittest.TestCase):
    """Acceptance (Director-approved 2026-10-14): the publish that takes a topic
    to HUB_MIN posts commits the topic's hub, with Scarlett's copy. Runs
    publish.yml's own dating, hub and issue steps, in bash, on a scratch git
    repo, with `gh` stubbed by a shell function that records its arguments."""

    HUB_STEP = "Write the hub for a topic that reached 3 posts"
    ISSUE_STEP = "Hub without copy — create GitHub issue"

    @classmethod
    def setUpClass(cls):
        cls.workflow = (REPO / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    def _step(self, name):
        m = re.search(r"\n      - name: " + re.escape(name) + r"\n((?:        (?!run:).*\n)*)"
                      r"        run: \|\n((?:          .*\n|\n)*)", self.workflow)
        self.assertIsNotNone(m, name)
        return m.group(1), textwrap.dedent(m.group(2))

    def test_the_hub_step_runs_after_dating_and_before_the_commit(self):
        names = re.findall(r"^      - name: (.+)$", self.workflow, re.M)
        self.assertLess(names.index("Find and date unpublished posts"), names.index(self.HUB_STEP))
        self.assertLess(names.index(self.HUB_STEP), names.index("Commit and push"))
        head, script = self._step(self.HUB_STEP)
        self.assertIn("id: hub\n", head)
        self.assertIn("if: steps.stage.outputs.nothing_to_publish == 'false'", head)
        self.assertNotIn("secrets.", head + script)
        self.assertNotIn("env:", head)
        # E-1: a failed hub step has to stop the publish, so the step can never
        # be allowed to fail, and neither can the job around it.
        self.assertNotIn("continue-on-error", head)
        job_head = self.workflow[self.workflow.index("\n  publish:\n"):self.workflow.index("\n    steps:\n")]
        self.assertNotIn("continue-on-error", job_head)

    def test_a_failed_hub_step_opens_an_issue_with_the_existing_token(self):
        names = re.findall(r"^      - name: (.+)$", self.workflow, re.M)
        self.assertEqual(names.index(self.ISSUE_STEP), names.index(self.HUB_STEP) + 1)
        head, script = self._step(self.ISSUE_STEP)
        self.assertIn("id: hub_issue\n", head)
        self.assertIn("if: failure() && steps.hub.outcome == 'failure'\n", head)
        self.assertEqual(re.findall(r"secrets\.\w+", head + script), ["secrets.GITHUB_TOKEN"])
        self.assertIn("gh issue create", script)

    GH_CALL_END = "---END-OF-CALL---"

    def _publish(self, tmp, earlier_posts, hub_copy, open_issues=(), list_fails=False):
        """Run the dating and hub steps; if they fail, run the issue step, as
        its `if: failure()` would. `gh` is a shell function that records every
        call's args; `gh issue list` answers with `open_issues` (the open
        issues' titles, one per line, as `--jq '.[].title'` prints them), or
        fails when `list_fails`. Returns (steps run, issue run or None, staged
        paths, the list of gh calls, each a list of args)."""
        import shlex
        import shutil
        import subprocess
        import hub_pages
        bash, why = _bash_for_workflow_steps()
        if not bash:
            self.skipTest(why)
        for rel in ("hub_pages.py", "categories.py", "_data/categories.json"):
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, tmp / rel)
        _scratch_site_with_posts(tmp, {"treats": earlier_posts}, hub_copy=hub_copy,
                                 drafts=["treats"])
        (tmp / "runner_temp").mkdir()
        git = _git_fixture_repo(tmp)
        python = shlex.quote(Path(sys.executable).as_posix())
        _, hub_step = self._step(self.HUB_STEP)
        _, issue_step = self._step(self.ISSUE_STEP)
        env = dict(os.environ, GITHUB_OUTPUT=(tmp / "github_output").as_posix(),
                   RUNNER_TEMP=(tmp / "runner_temp").as_posix(),
                   GH_ARGS=(tmp / "gh_args").as_posix(),
                   OPEN_ISSUES="\n".join(open_issues), LIST_FAILS="1" if list_fails else "",
                   GIT_CONFIG_PARAMETERS="'core.autocrlf=false'")

        def run(script):
            return subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                                  cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                                  env=env)
        steps = run(_publish_date_script() + hub_step.replace("python3 ", python + " "))
        issue = None
        if steps.returncode != 0:
            stub = ('gh() {\n'
                    '  printf "%s\\n" "$@" >> "$GH_ARGS"; echo "' + self.GH_CALL_END + '" >> "$GH_ARGS"\n'
                    '  if [ "$1 $2" = "issue list" ]; then\n'
                    '    [ -n "$LIST_FAILS" ] && return 1\n'
                    '    [ -n "$OPEN_ISSUES" ] && printf "%s\\n" "$OPEN_ISSUES"\n'
                    '  fi\n'
                    '  return 0\n}\n')
            issue = run(stub + issue_step)
        raw = ((tmp / "gh_args").read_text(encoding="utf-8")
               if (tmp / "gh_args").exists() else "")
        calls = [chunk.split("\n")[:-1] if chunk.endswith("\n") else chunk.split("\n")
                 for chunk in raw.split(self.GH_CALL_END + "\n") if chunk]
        self.assertEqual(hub_pages.HUB_MIN, 3, "the step names say 3")
        return steps, issue, git("diff", "--cached", "--name-only").split(), calls

    def test_the_publish_of_a_third_treats_post_commits_the_treats_hub(self):
        import hub_pages
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            steps, issue, staged, calls = self._publish(tmp, hub_pages.HUB_MIN - 1,
                                                        hub_pages.load_copy())
            self.assertEqual(steps.returncode, 0, steps.stdout + steps.stderr)
            self.assertEqual((issue, calls), (None, []))
            self.assertIn("hubs/treats.md", staged)
            self.assertTrue(any(s.startswith("_posts/2") and s.endswith("-best-treats-new.md")
                                for s in staged), staged)
            hub = (tmp / "hubs" / "treats.md").read_text(encoding="utf-8")
            self.assertTrue(hub.startswith("---\nlayout: hub\nhub_topic: treats\n"
                                           "permalink: /treats/\n"), hub)
            self.assertIn('title: "Dog and Cat Treats: Picks by Purpose"\n', hub)
            self.assertIn('intro: "A treat does a job, whether that job is rewarding a sit, '
                          'hiding a pill or filling a stocking.', hub)
            self.assertIn('how_we_choose: "Every pick is compared on its ingredient list, '
                          'owner ratings and what reviews say again and again about how pets '
                          'take to it."\n', hub)

    def test_the_publish_of_a_second_treats_post_writes_no_hub(self):
        import hub_pages
        with tempfile.TemporaryDirectory() as d:
            steps, issue, staged, _ = self._publish(Path(d), hub_pages.HUB_MIN - 2,
                                                    hub_pages.load_copy())
            self.assertEqual(steps.returncode, 0, steps.stdout + steps.stderr)
            self.assertIsNone(issue)
            self.assertFalse([s for s in staged if s.startswith("hubs/")], staged)

    def test_a_third_post_without_copy_fails_and_opens_an_issue_naming_it(self):
        import hub_pages
        hub_copy = hub_pages.load_copy()
        hub_copy["treats"] = {"title": hub_copy["treats"]["title"]}
        missing = ", ".join(hub_pages.COPY_FIELDS[1:])
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            steps, issue, staged, calls = self._publish(tmp, hub_pages.HUB_MIN - 1, hub_copy)
            self.assertNotEqual(steps.returncode, 0)
            self.assertIn(f"ERROR treats: no {missing} in {hub_pages.COPY_FILE}", steps.stderr)
            self.assertFalse((tmp / "hubs" / "treats.md").exists())
            self.assertFalse([s for s in staged if s.startswith("hubs/")], staged)
            self.assertEqual(issue.returncode, 0, issue.stdout + issue.stderr)
            creates = self._creates(calls)
            self.assertEqual(len(creates), 1, calls)
            gh_args = creates[0]
            title = gh_args[gh_args.index("--title") + 1]
            self.assertEqual(title, self.BLOCKED_TITLE)
            body = "\n".join(gh_args[gh_args.index("--body") + 1:gh_args.index("--assignee")])
            self.assertIn(f"ERROR treats: no {missing} in {hub_pages.COPY_FILE}", body)
            self.assertIn("nothing was published", body)

    @property
    def BLOCKED_TITLE(self):
        """The dated title the step opens today. The day comes from the same
        `TZ=America/New_York date` the step runs: what is under test here is the
        exact-title dedup; the ET conversion has its own tests (_AlertIssueStep)."""
        import subprocess
        bash, why = _bash_for_workflow_steps()
        if not bash:
            self.skipTest(why)
        day = subprocess.run([bash, "--noprofile", "--norc", "-c", "TZ=America/New_York date +%F"],
                             capture_output=True, text=True, env=dict(os.environ, TZ="UTC"),
                             check=True).stdout.strip()
        return f"⚠️ Publish blocked on {day}: no hub copy for treats"

    @staticmethod
    def _creates(calls):
        return [c for c in calls if c[:2] == ["issue", "create"]]

    def _blocked_run(self, **kw):
        """A blocked publish (treats at its third post, no copy) with the
        given open issues; returns (issue step run, gh calls)."""
        import hub_pages
        hub_copy = hub_pages.load_copy()
        hub_copy["treats"] = {"title": hub_copy["treats"]["title"]}
        with tempfile.TemporaryDirectory() as d:
            steps, issue, _, calls = self._publish(Path(d), hub_pages.HUB_MIN - 1, hub_copy, **kw)
        self.assertNotEqual(steps.returncode, 0)
        return issue, calls

    def test_a_blocked_publish_finds_its_open_issue_before_opening_one(self):
        """The same block, run after run, is one issue: the step lists the open
        issues first, under the token it already has."""
        issue, calls = self._blocked_run()
        self.assertEqual(issue.returncode, 0, issue.stdout + issue.stderr)
        self.assertEqual(calls[0][:2], ["issue", "list"], calls)
        listing = calls[0]
        self.assertEqual(listing[listing.index("--repo") + 1], "DMoneyOH/HappyPet")
        self.assertEqual(listing[listing.index("--state") + 1], "open")
        self.assertIn("--json", listing)
        self.assertEqual(len(self._creates(calls)), 1, calls)

    def test_a_blocked_publish_opens_no_second_issue_while_one_is_open(self):
        issue, calls = self._blocked_run(open_issues=["Unrelated open issue", self.BLOCKED_TITLE])
        self.assertEqual(issue.returncode, 0, issue.stdout + issue.stderr)
        self.assertEqual(self._creates(calls), [], calls)
        self.assertEqual([c[:2] for c in calls], [["issue", "list"]], calls)
        self.assertIn("already open", issue.stdout)

    def test_only_an_open_issue_for_the_same_topics_stops_a_new_one(self):
        """Exact title, so a different topic set, a longer one that merely
        contains this topic, and another alert all still get their own issue."""
        for label, titles in (
                ("different topic", [self.BLOCKED_TITLE.replace("treats", "tech")]),
                ("superset of topics", [self.BLOCKED_TITLE + ",tech"]),
                ("prefix of the title", [self.BLOCKED_TITLE[:-len("treats")]]),
                ("another day", [re.sub(r"\d{4}-\d{2}-\d{2}", "2026-01-01", self.BLOCKED_TITLE)]),
                ("undated, as before 2026-10-15", ["⚠️ Publish blocked: no hub copy for treats"]),
                ("same words, other alert", ["Queue low: no hub copy for treats"]),
                ("case differs", [self.BLOCKED_TITLE.lower()]),
                ("no open issues", [])):
            with self.subTest(case=label):
                issue, calls = self._blocked_run(open_issues=titles)
                self.assertEqual(issue.returncode, 0, issue.stdout + issue.stderr)
                self.assertEqual(len(self._creates(calls)), 1, calls)

    def test_a_failed_issue_listing_still_opens_the_issue(self):
        """An alert that could not check for a duplicate is worth a duplicate;
        it is not worth silence."""
        issue, calls = self._blocked_run(open_issues=[self.BLOCKED_TITLE], list_fails=True)
        self.assertEqual(issue.returncode, 0, issue.stdout + issue.stderr)
        self.assertEqual(len(self._creates(calls)), 1, calls)

    def test_the_issue_step_stays_inside_the_publish_token(self):
        """Dedup adds `gh issue list` to the step, which the token's
        existing `issues: write` covers, so the permissions block is unchanged."""
        head, script = self._step(self.ISSUE_STEP)
        self.assertEqual(re.findall(r"secrets\.\w+", head + script), ["secrets.GITHUB_TOKEN"])
        self.assertEqual(_workflow_permissions(self.workflow)[0], PUBLISH_PERMISSIONS)
        self.assertEqual(sorted(set(re.findall(r"\bgh (\w+ \w+)", script))),
                         ["issue create", "issue list"])


class TestPublishDatingStep(unittest.TestCase):
    """publish.yml's "Find and date unpublished posts" step, run in bash on a
    scratch git repo. Its slugs go to GITHUB_OUTPUT and into later steps'
    scripts, so a DRAFT name that is not a post slug stops the publish. So
    does a pin JSON that does not parse: its pin would fail after the post
    went out."""

    def _date(self, tmp, pin=None, drafts=("treats",), index_only=()):
        """Run the step on a scratch site with one dated treats post and a
        DRAFT-best-<topic>-new.md per topic in `drafts`. `pin` is the text of
        best-treats-new's pin JSON. `index_only` names DRAFT paths put in git's
        index only, for names this file system cannot hold. Returns
        (process, GITHUB_OUTPUT lines)."""
        _scratch_site_with_posts(tmp, {"treats": 1}, drafts=drafts)
        if pin is not None:
            (tmp / "_pin_queue").mkdir()
            (tmp / "_pin_queue" / "best-treats-new.json").write_text(pin, encoding="utf-8")
        git = _git_fixture_repo(tmp)
        for name in index_only:
            blob = git("hash-object", "-w", "_posts/2026-01-01-best-treats-0.md").strip()
            # Git for Windows refuses such a name unless told it need not
            # hold on NTFS; the runner's Linux git takes it as is.
            git("-c", "core.protectNTFS=false",
                "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
        output = tmp / "github_output"
        proc, _ = _run_alert_step(self, _publish_date_script(),
                                  {"GITHUB_OUTPUT": output.as_posix(),
                                   "GIT_CONFIG_PARAMETERS": "'core.autocrlf=false'"}, tmp)
        lines = output.read_text(encoding="utf-8").splitlines() if output.exists() else []
        return proc, lines

    def test_the_step_writes_its_four_outputs_and_nothing_else(self):
        """A newline inside a pin JSON's article_url is valid JSON, and it adds
        no line to GITHUB_OUTPUT."""
        pin = json.dumps({"article_url": "https://example.invalid/a/\nslugs=injected"})
        with tempfile.TemporaryDirectory() as d:
            proc, lines = self._date(Path(d), pin=pin)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(lines, ["nothing_to_publish=false", "slugs=best-treats-new",
                                 "publish_count=1", "remaining=0"])

    def test_a_pin_json_that_does_not_parse_stops_the_publish(self):
        for label, pin in (("not JSON", "{not json"),
                           ("not an object", "[]"),
                           ("article_url not text", json.dumps({"article_url": 5}))):
            with self.subTest(pin=label), tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                proc, lines = self._date(tmp, pin=pin)
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertIn("::error::_pin_queue/best-treats-new.json", proc.stdout)
                self.assertEqual(lines, ["nothing_to_publish=false"])
                self.assertTrue((tmp / "_posts" / "DRAFT-best-treats-new.md").exists())

    def test_a_draft_name_that_is_not_a_post_slug_stops_the_publish(self):
        """Checked before the post is moved, so nothing is dated. git ls-files
        C-quotes a name holding a newline or a non-ASCII letter, and the
        quote marks fail the check too."""
        for label, kw in (("command substitution", {"drafts": ("$(touch pwned)",)}),
                          ("backticks", {"drafts": ("`touch pwned`",)}),
                          ("a space", {"drafts": ("dog treats",)}),
                          ("uppercase", {"drafts": ("Treats",)}),
                          ("non-ASCII, C-quoted", {"drafts": ("tr\u00e9ats",)}),
                          ("a newline, C-quoted",
                           {"drafts": (), "index_only": ("_posts/DRAFT-best-a\nslugs=x.md",)})):
            with self.subTest(name=label), tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                proc, lines = self._date(tmp, **kw)
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertIn("is not DRAFT-<post slug>.md", proc.stdout)
                self.assertEqual(lines, ["nothing_to_publish=false"])
                posts = [p.name for p in (tmp / "_posts").iterdir()]
                self.assertTrue(all(n.startswith(("2026-01-", "DRAFT-")) for n in posts), posts)


class TestPublishedRootIsAllowlisted(unittest.TestCase):
    """Jekyll publishes every root file and folder that is not underscore- or
    dot-prefixed and not in _config.yml's `exclude:`. The github-pages gem also
    renders a front-matter-less .md as a page. That is how HANDOFF*.md,
    CLAUDE.md, docs/ (plans, specs, old handoffs) and pytest.ini ended up live
    on the site and in sitemap.xml.

    So the check is an allowlist, not a list of known leaks: anything new at
    the root fails here until it is either excluded or added to PUBLIC on
    purpose. Matching mirrors Jekyll's EntryFilter for root names: an fnmatch
    glob or a plain prefix."""

    PUBLIC = {
        "index.md", "about.md", "cats.md", "dogs.md", "contact.md",
        "privacy-policy.md", "search.md", "search.json", "robots.txt",
        "favicon.ico", "favicon-16x16.png", "favicon-32x32.png",
        "apple-touch-icon.png", "assets",
        "hubs",  # topic hub pages; each sets its own /<topic>/ permalink
        "9b8b0de3f2497b8a12b58d36b78a4f2e.txt",  # IndexNow key file (public by design)
    }

    @classmethod
    def setUpClass(cls):
        config = (REPO / "_config.yml").read_text(encoding="utf-8")
        # The indented block under `exclude:`; YAML comment lines inside it are skipped.
        block = re.search(r"^exclude:[ \t]*\r?\n((?:[ \t]+.*\r?\n)+)", config, re.M)
        assert block, "_config.yml has no exclude: list"
        cls.exclude = [m.strip().strip('"\'')
                       for m in re.findall(r"^[ \t]+- (.*)$", block.group(1), re.M)]
        assert "README.md" in cls.exclude and "vendor" in cls.exclude, cls.exclude

    def _excluded(self, name):
        from fnmatch import fnmatchcase
        return any(fnmatchcase(name, pat) or name.startswith(pat) for pat in self.exclude)

    def test_every_published_root_entry_is_on_the_allowlist(self):
        published = sorted(p.name for p in REPO.iterdir()
                           if not p.name.startswith(("_", ".")) and not self._excluded(p.name))
        self.assertEqual([n for n in published if n not in self.PUBLIC], [],
                         "publishes to the live site: exclude it in _config.yml or add it to PUBLIC")

    def test_the_known_internal_files_are_excluded(self):
        for name in ("HANDOFF.md", "HANDOFF-archive-2026-07-20-1606.md", "CLAUDE.md",
                     "docs", "pytest.ini"):
            with self.subTest(name=name):
                self.assertTrue(self._excluded(name), f"{name} would be published")

    def test_no_real_page_is_excluded(self):
        for name in sorted(self.PUBLIC):
            with self.subTest(name=name):
                self.assertTrue((REPO / name).exists(), f"{name} listed in PUBLIC but missing")
                self.assertFalse(self._excluded(name), f"{name} is a site page and must publish")


class TestMergeRedirects(unittest.TestCase):
    """Batch B's three merges (Scarlett spec 3a-3c): each survivor's
    redirect_from carries its own old path plus the retired post's live
    permalink and every redirect the retired post had, so no old URL 404s."""

    SURVIVORS = {
        "2026-08-31-best-cat-window-hammocks.md": [
            "/cat-beds/best-cat-window-hammocks/", "/beds/best-cat-window-perch/",
            "/cat-gear/best-cat-window-perch/", "/cat-beds/best-cat-window-perch/"],
        "2026-04-07-best-interactive-dog-toys.md": [
            "/dog-toys/best-interactive-dog-toys/", "/toys/best-dog-puzzle-toys/",
            "/dog-gear/best-dog-puzzle-toys/", "/dog-toys/best-dog-puzzle-toys/"],
        "2026-10-01-best-calming-diffusers-pets.md": [
            "/dog-health/best-calming-diffusers-pets/", "/pet-health/best-calming-diffusers-pets/",
            "/calming/best-cat-calming-products/", "/cat-health/best-cat-calming-products/"],
    }
    RETIRED = ("best-cat-window-perch", "best-dog-puzzle-toys", "best-cat-calming-products")

    def test_each_survivor_carries_every_merge_redirect(self):
        for name, expected in self.SURVIVORS.items():
            with self.subTest(post=name):
                fm = (REPO / "_posts" / name).read_text(encoding="utf-8").split("---", 2)[1]
                block = re.search(r"^redirect_from:\n((?:  - .*\n)+)", fm, re.M)
                self.assertIsNotNone(block, "no redirect_from")
                self.assertEqual(re.findall(r"^  - (\S+)$", block.group(1), re.M), expected)

    def test_no_retired_post_is_still_published(self):
        for slug in self.RETIRED:
            with self.subTest(slug=slug):
                self.assertEqual(list((REPO / "_posts").glob(f"*-{slug}.md")), [])


class TestSitemapInputs(unittest.TestCase):
    """What the repo can prove about sitemap.xml. jekyll-sitemap writes it at
    build time and there is no Ruby on the dev box, so these pin the inputs: the
    plugin is on, robots.txt points at it, no post opts out, /search/ does, and
    no redirect stub shares a canonical post URL. Search Console 2026-10-15:
    34 URLs "Discovered - currently not indexed" and "No referring sitemaps
    detected" on a post URL -- the file existed; it had not been submitted."""

    SITEMAP = "https://happypetproductreviews.com/sitemap.xml"
    # Search Console's own examples of the 34, copied from its report.
    GSC_EXAMPLES = (
        "/beds/best-cat-beds/", "/beds/best-dog-beds-large-breeds/",
        "/calming/best-calming-diffusers-pets/", "/carriers/best-cat-carrier-backpacks/",
        "/collars/best-dog-collars-small-breeds/",
    )

    @staticmethod
    def _posts():
        return [p for p in sorted((REPO / "_posts").glob("*.md")) if not p.stem.startswith("DRAFT-")]

    def _canonical(self):
        return {f"/{_post_first_category(p)}/{p.stem.split('-', 3)[3]}/": p for p in self._posts()}

    def test_robots_txt_names_the_sitemap_and_blocks_no_post(self):
        lines = [l.strip() for l in (REPO / "robots.txt").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([l for l in lines if l.lower().startswith("sitemap:")],
                         [f"Sitemap: {self.SITEMAP}"])
        url = re.search(r"^url: *'?([^'\s]+)'?", (REPO / "_config.yml").read_text(encoding="utf-8"), re.M).group(1)
        self.assertEqual(f"{url}/sitemap.xml", self.SITEMAP)
        for l in lines:
            if l.lower().startswith("disallow:") and l.split(":", 1)[1].strip():
                path = l.split(":", 1)[1].strip()
                for blocked in list(self._canonical()) + ["/sitemap.xml"]:
                    self.assertFalse(blocked.startswith(path), f"robots.txt {l!r} blocks {blocked}")

    def test_the_sitemap_plugin_is_installed_and_enabled(self):
        cfg = (REPO / "_config.yml").read_text(encoding="utf-8")
        self.assertRegex(cfg, r"(?m)^\s*-\s*jekyll-sitemap\s*$")
        self.assertRegex((REPO / "Gemfile").read_text(encoding="utf-8"), r"gem ['\"]jekyll-sitemap['\"]")
        self.assertRegex((REPO / "Gemfile.lock").read_text(encoding="utf-8"), r"(?m)^  jekyll-sitemap \(")

    def test_no_post_opts_out_of_the_sitemap_or_moves_its_url(self):
        from validate_published_chewy_links import parse_frontmatter
        for p in self._posts():
            fm = parse_frontmatter(p)
            for key in ("sitemap", "published", "permalink"):
                with self.subTest(post=p.name, key=key):
                    self.assertNotIn(key, fm)

    def test_search_page_is_kept_out_of_the_sitemap(self):
        from validate_published_chewy_links import parse_frontmatter
        self.assertEqual(parse_frontmatter(REPO / "search.md").get("sitemap"), "false")
        for name in ("index.md", "about.md", "cats.md", "dogs.md", "contact.md", "privacy-policy.md"):
            with self.subTest(page=name):
                self.assertNotIn("sitemap", parse_frontmatter(REPO / name))

    def test_every_post_has_its_own_url_and_no_stub_shadows_one(self):
        canonical = self._canonical()
        self.assertEqual(len(canonical), len(self._posts()), "two posts share one URL")
        claimed = {}
        for p in self._posts():
            for old in _post_redirect_paths(p):
                self.assertNotIn(old, canonical, f"{p.name}: stub {old} would overwrite a post")
                self.assertNotIn(old, claimed, f"{old} is a stub of both {claimed.get(old)} and {p.name}")
                claimed[old] = p.name

    def test_search_console_examples_are_published_posts(self):
        canonical = self._canonical()
        for url in self.GSC_EXAMPLES:
            with self.subTest(url=url):
                self.assertIn(url, canonical)


class TestUpdatedDate(unittest.TestCase):
    """A post prints "Updated <date>" beside "Reviewed <date>" only when its
    front matter sets last_modified_at, the field jekyll-sitemap reads for
    <lastmod>. It is set by hand for a substantive edit, so nothing automated
    may write it: a date that moves on every pipeline commit is noise."""

    FIELD = "last_modified_at"

    def _date_line(self, page):
        layout = (REPO / "_layouts" / "post.html").read_text(encoding="utf-8")
        m = re.search(r'<p class="piece-date">.*?</p>', layout)
        self.assertIsNotNone(m, "post.html has no piece-date line")
        return _MiniLiquid(m.group(0)).render({"page": page})

    def test_reviewed_only_when_the_field_is_unset(self):
        self.assertEqual(self._date_line({"date": "2026-10-05"}),
                         '<p class="piece-date">Reviewed October 5, 2026</p>')

    def test_updated_prints_beside_reviewed_when_set(self):
        self.assertEqual(
            self._date_line({"date": "2026-10-05", self.FIELD: "2026-10-20"}),
            '<p class="piece-date">Reviewed October 5, 2026 &middot; Updated October 20, 2026</p>')

    def test_no_pipeline_script_workflow_or_routine_writes_the_field(self):
        sources = [p for p in REPO.glob("*.py") if not p.name.startswith("test_")]
        sources += list((REPO / "scripts").rglob("*.py"))
        sources += list((REPO / ".github" / "workflows").glob("*.yml"))
        sources += list((REPO / ".claude").rglob("*.md"))
        sources.append(REPO / "_config.yml")   # a front-matter default would set it on every post
        self.assertGreater(len(sources), 20, "the scan found too few files to mean anything")
        for path in sources:
            with self.subTest(path=path.relative_to(REPO).as_posix()):
                self.assertNotIn(self.FIELD, path.read_text(encoding="utf-8"))

    def test_generated_front_matter_does_not_carry_it(self):
        import generate_posts as gp
        fm = gp.front_matter("Best X", "best x", "https://amzn.to/3TestABC", "best-x",
                             "dog", "toys", "A plain description.", "https://img/x.jpg",
                             "https://img/pin.jpg", product_short="X")
        self.assertNotIn(self.FIELD, fm)

    def test_no_plugin_sets_it_from_git_history(self):
        """jekyll-last-modified-at fills the field from each file's last commit,
        which is exactly the every-commit date this rule exists to avoid."""
        for name in ("Gemfile", "Gemfile.lock", "_config.yml"):
            with self.subTest(file=name):
                self.assertNotIn("jekyll-last-modified-at",
                                 (REPO / name).read_text(encoding="utf-8"))

    def test_a_post_that_sets_it_uses_a_date_on_or_after_its_review(self):
        from validate_published_chewy_links import parse_frontmatter
        import datetime
        for p in sorted((REPO / "_posts").glob("[0-9]*.md")):
            value = parse_frontmatter(p).get(self.FIELD)
            if value is None:
                continue
            with self.subTest(post=p.name):
                updated = datetime.date.fromisoformat(str(value).strip("'\"")[:10])
                self.assertGreaterEqual(updated, datetime.date.fromisoformat(p.name[:10]))


class TestOldPermalinksStillResolve(unittest.TestCase):
    """For ~4 minutes on 2026-04-06 (6eba5a4 until cea0382) the ten original
    posts were all published at /pet-accessories/<slug>/, and the sitemap listed
    them then. Search Console reports /pet-accessories/best-puppy-training-pads/
    as a 404 (last crawled Jul 29). Its post lives at /training/<slug>/;
    /dog-training/<slug>/ is a redirect stub to it, not the live page. Both sides
    are literals: the old path as Google has it, the current URL as the site
    serves it."""

    OLD_TO_CURRENT = {
        "/pet-accessories/best-puppy-training-pads/": "/training/best-puppy-training-pads/",
        "/pet-accessories/best-automatic-cat-feeder/": "/feeding/best-automatic-cat-feeder/",
        "/pet-accessories/best-cat-carrier-travel/": "/carriers/best-cat-carrier-travel/",
        "/pet-accessories/best-cat-litter-odor-control/": "/litter/best-cat-litter-odor-control/",
        "/pet-accessories/best-cat-scratching-posts/": "/scratching/best-cat-scratching-posts/",
        "/pet-accessories/best-dog-beds-large-breeds/": "/beds/best-dog-beds-large-breeds/",
        "/pet-accessories/best-dog-collars-small-breeds/": "/collars/best-dog-collars-small-breeds/",
        "/pet-accessories/best-dog-toys-aggressive-chewers/": "/toys/best-dog-toys-aggressive-chewers/",
        "/pet-accessories/best-no-pull-dog-harness/": "/harnesses/best-no-pull-dog-harness/",
        "/pet-accessories/best-pet-water-fountain/": "/feeding/best-pet-water-fountain/",
    }

    def test_each_old_pet_accessories_url_redirects_to_its_post(self):
        for old, current in self.OLD_TO_CURRENT.items():
            with self.subTest(old=old):
                slug = current.strip("/").split("/")[1]
                post = _post_file_for_slug(slug)
                self.assertIsNotNone(post, f"no published post for {slug}")
                self.assertEqual(f"/{_post_first_category(post)}/{slug}/", current)
                self.assertIn(old, _post_redirect_paths(post))


class TestApril4UrlsStillResolve(unittest.TestCase):
    """On 2026-04-04 the ten original posts first went up with the species in the
    URL (dee2491: /cat-carriers/cat/<slug>/), lost it the same day (c436cfb:
    /cat-carriers/<slug>/) and the scratching-post page spent a stretch of that
    day at /cat-toys/<slug>/ before moving to /cat-scratching/ (62b2bb2). Eleven
    old paths, none redirected until now. Both sides are literals: the old path
    as the git history has it, the current URL as the site serves it."""

    OLD_TO_CURRENT = {
        "/cat-feeders/cat/best-automatic-cat-feeder/": "/feeding/best-automatic-cat-feeder/",
        "/cat-carriers/cat/best-cat-carrier-travel/": "/carriers/best-cat-carrier-travel/",
        "/cat-litter/cat/best-cat-litter-odor-control/": "/litter/best-cat-litter-odor-control/",
        "/cat-toys/cat/best-cat-scratching-posts/": "/scratching/best-cat-scratching-posts/",
        "/cat-toys/best-cat-scratching-posts/": "/scratching/best-cat-scratching-posts/",
        "/dog-beds/dog/best-dog-beds-large-breeds/": "/beds/best-dog-beds-large-breeds/",
        "/dog-collars/dog/best-dog-collars-small-breeds/": "/collars/best-dog-collars-small-breeds/",
        "/dog-toys/dog/best-dog-toys-aggressive-chewers/": "/toys/best-dog-toys-aggressive-chewers/",
        "/dog-harnesses/dog/best-no-pull-dog-harness/": "/harnesses/best-no-pull-dog-harness/",
        "/pet-feeding/both/best-pet-water-fountain/": "/feeding/best-pet-water-fountain/",
        "/dog-training/dog/best-puppy-training-pads/": "/training/best-puppy-training-pads/",
    }

    def test_each_april_4_url_redirects_to_its_post(self):
        self.assertEqual(len(self.OLD_TO_CURRENT), 11)
        for old, current in self.OLD_TO_CURRENT.items():
            with self.subTest(old=old):
                slug = current.strip("/").split("/")[1]
                post = _post_file_for_slug(slug)
                self.assertIsNotNone(post, f"no published post for {slug}")
                self.assertEqual(f"/{_post_first_category(post)}/{slug}/", current)
                self.assertIn(old, _post_redirect_paths(post))


class TestTitleSuffix(unittest.TestCase):
    """Every page title ends "| HappyPet Reviews" (Director, 2026-10-13), added
    once by default.html, so no page's own title may carry a suffix of its own.
    A title that already names the site (the home page's) renders as written:
    the brand once, never twice (Maeve-main, 2026-10-14). The brand is the
    site's name, "Happy Pet Product Reviews", or "HappyPet": an ordinary
    phrase such as "Happy Pets" is not the brand and keeps the suffix."""

    BRAND = re.compile(r"Happy Pet Product Reviews|HappyPet")

    @classmethod
    def setUpClass(cls):
        layout = (REPO / "_layouts" / "default.html").read_text(encoding="utf-8")
        cls.tag = re.search(r"<title>.*?</title>", layout).group(0)

    def _title(self, page):
        return _MiniLiquid(self.tag).render({"page": page, "site": {"title": "Happy Pet Product Reviews"}})

    @staticmethod
    def _site_pages():
        """(name, title, url) of every published root page and every post: the
        root pages are the published-root allowlist's .md entries."""
        out = []
        pages = [REPO / n for n in sorted(TestPublishedRootIsAllowlisted.PUBLIC) if n.endswith(".md")]
        for p in pages + sorted((REPO / "_posts").glob("2*.md")) + sorted((REPO / "hubs").glob("*.md")):
            fm = p.read_text(encoding="utf-8").split("---", 2)[1]
            title = re.search(r"^title: *(.+)$", fm, re.M)
            link = re.search(r"^permalink: *(\S+)", fm, re.M)
            url = link.group(1) if link else ("/" if p.name == "index.md" else "/x/")
            out.append((p.name, title.group(1).strip().strip('"\'') if title else None, url))
        return out

    def test_the_layout_appends_the_approved_suffix(self):
        self.assertEqual(self._title({"title": "Best Cat Litter", "url": "/litter/best-cat-litter/"}),
                         "<title>Best Cat Litter | HappyPet Reviews</title>")
        self.assertEqual(self._title({"title": "Cat Product Reviews", "url": "/cats/"}),
                         "<title>Cat Product Reviews | HappyPet Reviews</title>")

    def test_the_home_page_names_the_brand_once(self):
        index = (REPO / "index.md").read_text(encoding="utf-8")
        title = re.search(r"^title: *(.+)$", index, re.M).group(1).strip()
        rendered = self._title({"title": title, "url": "/"})
        self.assertEqual(rendered, f"<title>{title}</title>")
        self.assertEqual(len(self.BRAND.findall(rendered)), 1, rendered)

    def test_a_page_without_a_title_gets_the_site_name(self):
        self.assertEqual(self._title({"url": "/x/"}), "<title>Happy Pet Product Reviews</title>")

    def test_no_page_title_carries_its_own_suffix(self):
        suffixed = [name for name, title, _ in self._site_pages()
                    if title and re.search(r"\|\s*Happy\s*Pet", title, re.I)]
        self.assertEqual(suffixed, [])

    def test_every_published_title_names_the_brand_exactly_once(self):
        """The guard for about.md's old title ("About Happy Pet Product Reviews"
        would have rendered the brand twice) and any title like it."""
        for name, title, url in self._site_pages():
            with self.subTest(page=name):
                rendered = self._title({"title": title, "url": url})
                self.assertEqual(len(self.BRAND.findall(rendered)), 1, rendered)

    def test_happy_pets_in_a_title_is_not_the_brand(self):
        title = "Fresh Home, Happy Pets: Top Odor Eliminators for Pet Stains"
        self.assertEqual(self._title({"title": title, "url": "/cleaning/x/"}),
                         f"<title>{title} | HappyPet Reviews</title>")

    def test_a_title_that_already_names_the_brand_gets_no_suffix(self):
        for title in ("About Happy Pet Product Reviews", "HappyPet Picks"):
            with self.subTest(title=title):
                self.assertEqual(self._title({"title": title, "url": "/about/"}),
                                 f"<title>{title}</title>")


class TestSocialImage(unittest.TestCase):
    """og:image is the PRODUCT picture, never the Pinterest pin.

    The pin is a 1000x1500 portrait; Facebook crops or distorts a 2:3 image in a
    link preview (Director, 2026-10-13). _includes/social-image.html picks the
    self-hosted thumbnail when the post has one, else the post's own `image`,
    else the 1200x630 brand card -- the same pick as the buy panel in post.html.
    Rendered through _MiniLiquid (a Liquid subset, not Jekyll; build-check.yml's
    `jekyll build` is the real render) against every real post. Expected values
    are computed from the filesystem and front matter, not from the include.
    """

    SITE = "https://happypetproductreviews.com"
    INCLUDE = REPO / "_includes" / "social-image.html"

    @classmethod
    def setUpClass(cls):
        cls.source = cls.INCLUDE.read_text(encoding="utf-8")
        cls.thumbs = {t.stem for t in (REPO / "assets" / "images" / "thumbs").glob("*.jpg")}
        cls.static_files = [{"path": f"/assets/images/thumbs/{s}.jpg"} for s in sorted(cls.thumbs)]
        from validate_published_chewy_links import parse_frontmatter
        cls.posts = []
        for p in sorted((REPO / "_posts").glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.md")):
            fm = parse_frontmatter(p)
            cls.posts.append({"slug": p.stem[11:], "image": fm.get("image"),
                              "pin_image": fm.get("pin_image")})

    def _render(self, page):
        return _MiniLiquid(self.source).render(
            {"site": {"baseurl": "", "static_files": self.static_files}, "page": page})

    def test_the_fixture_is_real(self):
        self.assertGreaterEqual(len(self.posts), 60)
        self.assertTrue(self.thumbs, "no thumbnails found")
        self.assertTrue(any(p["pin_image"] for p in self.posts), "no post has a pin_image")
        # Not required: a post without a thumbnail. A thumbnail backfill may cover
        # every post; the no-thumbnail branch is held by the synthetic pages below.

    def test_every_post_gets_its_thumbnail_or_its_own_image(self):
        for post in self.posts:
            with self.subTest(slug=post["slug"]):
                expected = (f"{self.SITE}/assets/images/thumbs/{post['slug']}.jpg"
                            if post["slug"] in self.thumbs else post["image"])
                self.assertEqual(self._render(post), expected)

    def test_no_post_ever_gets_a_pin(self):
        for post in self.posts:
            with self.subTest(slug=post["slug"]):
                out = self._render(post)
                self.assertNotIn("/assets/images/pins/", out)
                if post["pin_image"]:
                    self.assertNotEqual(out, post["pin_image"])
                    self.assertNotEqual(out, post["pin_image"].split("?")[0])

    def test_a_post_with_a_pin_and_no_image_still_does_not_get_the_pin(self):
        out = self._render({"slug": "no-such-post", "pin_image": self.SITE + "/assets/images/pins/x.jpg"})
        self.assertEqual(out, self.SITE + "/assets/images/og-image.png")

    def test_a_page_with_nothing_gets_the_landscape_brand_card(self):
        for page in ({}, {"slug": None}, {"image": ""}):
            with self.subTest(page=page):
                self.assertEqual(self._render(page), self.SITE + "/assets/images/og-image.png")

    def test_the_output_is_a_bare_url_with_no_whitespace(self):
        for post in self.posts[:5] + [{}]:
            out = self._render(post)
            self.assertEqual(out, out.strip())
            self.assertNotIn("\n", out)

    def test_the_brand_card_and_thumbnails_are_not_portrait(self):
        """The reason the pin is out: nothing the include can emit may be 2:3."""
        import generate_pin_images as g
        if not g.PIL_AVAILABLE:
            self.skipTest("Pillow not installed")
        from PIL import Image
        self.assertEqual(Image.open(REPO / "assets" / "images" / "og-image.png").size, (1200, 630))
        for stem in sorted(self.thumbs):
            with self.subTest(thumb=stem):
                w, h = Image.open(REPO / "assets" / "images" / "thumbs" / f"{stem}.jpg").size
                self.assertGreaterEqual(w, h)

    def test_the_facebook_sheet_rule_is_the_include_rule(self):
        """push_pins_to_sheets.product_image_url mirrors this include. Both run
        on every real post, then on synthetic posts covering each branch: a
        thumbnail, an absolute image, a relative image (absolute_url), single
        quotes, and nothing at all."""
        import shutil
        import tempfile
        import push_pins_to_sheets as pps
        from validate_published_chewy_links import parse_frontmatter
        for post in self.posts:
            with self.subTest(slug=post["slug"]):
                self.assertEqual(pps.product_image_url(post["slug"]), self._render(post))
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "_posts").mkdir()
        (tmp / "assets" / "images" / "thumbs").mkdir(parents=True)
        (tmp / "assets" / "images" / "thumbs" / "has-thumb.jpg").write_bytes(b"jpg")
        cases = {"has-thumb": 'image: "https://m.media-amazon.com/images/I/a.jpg"',
                 "absolute": 'image: "https://m.media-amazon.com/images/I/b.jpg"',
                 "relative": 'image: "/assets/images/c.jpg"',
                 "single-quoted": "image: '/assets/images/d.jpg'",
                 "nothing": 'pin_image: "/assets/images/pins/e.jpg"'}
        for slug, line in cases.items():
            (tmp / "_posts" / f"2026-10-05-{slug}.md").write_text(
                f"---\nlayout: post\n{line}\n---\nBody.\n", encoding="utf-8")
        static = [{"path": "/assets/images/thumbs/has-thumb.jpg"}]
        with patch.object(pps, "REPO_DIR", tmp):
            for slug in cases:
                with self.subTest(case=slug):
                    fm = parse_frontmatter(tmp / "_posts" / f"2026-10-05-{slug}.md")
                    liquid = _MiniLiquid(self.source).render(
                        {"site": {"baseurl": "", "static_files": static},
                         "page": {"slug": slug, "image": fm.get("image")}})
                    self.assertEqual(pps.product_image_url(slug), liquid)
                    self.assertTrue(liquid.startswith(self.SITE + "/") or
                                    liquid.startswith("https://m.media-amazon.com/"), liquid)

    def test_the_buy_panel_takes_its_image_from_the_include(self):
        post = (REPO / "_layouts" / "post.html").read_text(encoding="utf-8")
        self.assertIn("{%- include social-image.html -%}", post)
        self.assertNotIn("static_files", post, "post.html has a second copy of the image rule")

    def test_the_buy_panels_hero_image_is_the_includes_pick(self):
        """post.html's hero_image block, rendered with the include's own text
        spliced in where the include tag sits: the thumbnail (with baseurl), else
        the post's image, else nothing -- never the pin."""
        post = (REPO / "_layouts" / "post.html").read_text(encoding="utf-8")
        start = post.index("{%- capture social_image -%}")
        block = post[start:post.index("{%- endif -%}", start) + len("{%- endif -%}")]
        block = block.replace("{%- include social-image.html -%}", self.source)
        pin = self.SITE + "/assets/images/pins/x.jpg"
        cases = [({"slug": "has-thumb", "image": "https://m.media-amazon.com/images/I/a.jpg",
                   "pin_image": pin}, "/assets/images/thumbs/has-thumb.jpg"),
                 ({"slug": "no-thumb", "image": "https://m.media-amazon.com/images/I/b.jpg",
                   "pin_image": pin}, "https://m.media-amazon.com/images/I/b.jpg"),
                 ({"slug": "pin-only", "pin_image": pin}, None)]
        for page, hero in cases:
            with self.subTest(slug=page["slug"]):
                ml = _MiniLiquid(block)
                ml.render({"site": {"baseurl": "", "static_files": [
                    {"path": "/assets/images/thumbs/has-thumb.jpg"}]}, "page": page})
                self.assertEqual(ml.ctx.get("hero_image"), hero)

    def test_a_bom_or_a_nested_image_key_reads_like_jekyll(self):
        """Jekyll ignores a UTF-8 BOM and reads only top-level keys as page
        variables, so the Facebook image does too."""
        import tempfile
        import push_pins_to_sheets as pps
        amazon = "https://m.media-amazon.com/images/I/bom.jpg"
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "_posts").mkdir()
            (tmp / "_posts" / "2026-10-05-with-bom.md").write_bytes(
                b"\xef\xbb\xbf" + f'---\nlayout: post\nimage: "{amazon}"\n---\n'.encode())
            (tmp / "_posts" / "2026-10-05-nested.md").write_text(
                '---\nlayout: post\nseo:\n  image: "https://m.media-amazon.com/images/I/n.jpg"\n---\n',
                encoding="utf-8")
            with patch.object(pps, "REPO_DIR", tmp):
                self.assertEqual(pps.product_image_url("with-bom"), amazon)
                self.assertEqual(pps.product_image_url("nested"),
                                 self.SITE + "/assets/images/og-image.png")

    def test_default_layout_uses_the_include_and_never_pin_image(self):
        layout = (REPO / "_layouts" / "default.html").read_text(encoding="utf-8")
        tags = re.findall(r'<meta property="og:image"[^>]*>', layout)
        self.assertEqual(len(tags), 1, tags)
        self.assertIn("{% include social-image.html %}", tags[0])
        self.assertNotIn("pin_image", tags[0])

    def test_pinterest_keeps_its_own_pin_image(self):
        post = (REPO / "_layouts" / "post.html").read_text(encoding="utf-8")
        self.assertRegex(post, r"pinterest\.com/pin/create/button/\?[^\"]*media=\{\{ page\.pin_image")


# ---------------------------------------------------------------------------
# Alert issues (Director-approved 2026-10-15): every workflow alert is one
# deduplicated GitHub issue, and GitHub's own notification email carries it.
# No workflow sends mail. Each issue step runs here in bash with `gh` stubbed by
# a shell function that records its arguments, as TestPublishWritesTheHub does.
# ---------------------------------------------------------------------------
WORKFLOWS = REPO / ".github" / "workflows"
GH_CALL_END = "---END-OF-CALL---"
# `gh issue list` answers with $OPEN_ISSUES (one title per line, as
# `--jq '.[].title'` prints them) or fails when $LIST_FAILS; `gh issue create`
# fails when $CREATE_FAILS. Every call's args land in $GH_ARGS.
GH_STUB = ('gh() {\n'
           '  printf "%s\\n" "$@" >> "$GH_ARGS"; echo "' + GH_CALL_END + '" >> "$GH_ARGS"\n'
           '  if [ "$1 $2" = "issue list" ]; then\n'
           '    [ -n "$LIST_FAILS" ] && return 1\n'
           '    [ -n "$OPEN_ISSUES" ] && printf "%s\\n" "$OPEN_ISSUES"\n'
           '  fi\n'
           '  if [ "$1 $2" = "issue create" ] && [ -n "$CREATE_FAILS" ]; then return 1; fi\n'
           '  return 0\n}\n')
RUN_URL_EXPR = "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"


def _named_step(workflow_text, name):
    """(head, script) of the step called `name`: head is the lines between its
    name and its `run: |`, script the dedented run block. None if absent."""
    m = re.search(r"\n      - name: " + re.escape(name) + r"\n((?:        (?!run:).*\n)*)"
                  r"        run: \|\n((?:          .*\n|\n)*)", workflow_text)
    return (m.group(1), textwrap.dedent(m.group(2))) if m else None


def _job_permissions(text, job):
    """The `permissions:` mapping of job `job` (a 2-space key under jobs:), or
    None when the job is absent, has none, or holds anything but plain keys."""
    lines = text.splitlines()
    if f"  {job}:" not in lines:
        return None
    block = []
    for ln in lines[lines.index(f"  {job}:") + 1:]:
        if ln.strip() and not ln.startswith("   "):
            break
        block.append(ln)
    starts = [i for i, ln in enumerate(block) if re.match(r"\s*permissions\s*:", ln)]
    if len(starts) != 1 or block[starts[0]].rstrip() != "    permissions:":
        return None
    perms = {}
    for ln in block[starts[0] + 1:]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if not ln.startswith("      "):
            break
        m = re.fullmatch(r"      ([\w-]+):\s*(\S+?)(?:\s+#.*)?", ln)
        if not m:
            return None
        perms[m.group(1)] = m.group(2)
    return perms


def _parse_github_output(text):
    """GITHUB_OUTPUT as the runner reads it: `key=value` lines and
    `key<<DELIM` ... `DELIM` blocks."""
    out, lines, i = {}, text.split("\n"), 0
    while i < len(lines):
        line, i = lines[i], i + 1
        if not line:
            continue
        m = re.fullmatch(r"([\w-]+)<<(\S+)", line)
        if m:
            end = lines.index(m.group(2), i)
            out[m.group(1)], i = "\n".join(lines[i:end]), end + 1
        else:
            key, value = line.split("=", 1)
            out[key] = value
    return out


def _run_alert_step(testcase, script, env, cwd, open_issues=(), list_fails=False,
                    create_fails=False):
    """Run a step's script in bash the way the runner does (-eo pipefail)
    with `gh` stubbed. Returns (completed process, gh calls as lists of args)."""
    import subprocess
    bash, why = _bash_for_workflow_steps()
    if not bash:
        testcase.skipTest(why)
    gh_args = Path(cwd) / "gh_args"
    # TZ=UTC as on the runner, so a step that forgets TZ=America/New_York gets UTC
    # here too rather than this machine's local zone.
    full_env = dict(os.environ, TZ="UTC", GH_ARGS=gh_args.as_posix(),
                    OPEN_ISSUES="\n".join(open_issues), LIST_FAILS="1" if list_fails else "",
                    CREATE_FAILS="1" if create_fails else "", **env)
    proc = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", GH_STUB + script],
                          cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=full_env)
    raw = gh_args.read_text(encoding="utf-8") if gh_args.exists() else ""
    calls = [chunk.split("\n")[:-1] if chunk.endswith("\n") else chunk.split("\n")
             for chunk in raw.split(GH_CALL_END + "\n") if chunk]
    return proc, calls


def _eastern_now_or_skip(testcase):
    """Now in America/New_York from Python's tz database, after checking the
    step's bash can convert to it too; skips where either cannot (Git Bash on
    Windows ships no zoneinfo). The CI runner has both."""
    import subprocess
    bash, why = _bash_for_workflow_steps()
    if not bash:
        testcase.skipTest(why)
    probe = subprocess.run([bash, "--noprofile", "--norc", "-c", "TZ=America/New_York date -d @0 +%Z"],
                           capture_output=True, text=True).stdout.strip()
    if probe != "EST":
        testcase.skipTest(f"this bash has no America/New_York zone (got {probe!r})")
    try:
        from zoneinfo import ZoneInfo
        import datetime
        return datetime.datetime.now(ZoneInfo("America/New_York"))
    except Exception as exc:  # no tz database for Python here
        testcase.skipTest(f"no America/New_York in Python's tz database: {exc}")


class _AlertIssueStep:
    """Checks every alert issue step shares. A subclass names the workflow and
    step and supplies one failing scenario; these run it."""

    WORKFLOW = STEP = None
    TOKEN_EXPRS = ["secrets.GITHUB_TOKEN"]
    RUN_URL = "https://github.com/DMoneyOH/HappyPet/actions/runs/4242"
    RUN_URL_IN_ENV = True
    ISSUE_REPO = "DMoneyOH/HappyPet"
    TITLE_RE = None        # the title the scenario opens
    TIME_LABEL = "Reported: "
    TITLE_DATE_IS_EASTERN = True
    CREATE_FAILURE_IS_FATAL = True   # False where a failed create must only warn

    @classmethod
    def setUpClass(cls):
        cls.workflow = (WORKFLOWS / cls.WORKFLOW).read_text(encoding="utf-8")
        found = _named_step(cls.workflow, cls.STEP)
        cls.head, cls.script = found if found else (None, None)
        if cls.script:
            # An inline `python3 -c` runs this interpreter, as the hub tests do.
            import shlex
            cls.script = cls.script.replace("python3 ", shlex.quote(Path(sys.executable).as_posix()) + " ")

    def scenario_env(self):
        return {}

    def prepare(self, tmp):
        pass

    def run_step(self, open_issues=(), list_fails=False, create_fails=False, **env):
        self.assertIsNotNone(self.script, f"{self.WORKFLOW} has no step {self.STEP!r}")
        with tempfile.TemporaryDirectory() as d:
            self.prepare(Path(d))
            return _run_alert_step(self, self.script,
                                   {"RUN_URL": self.RUN_URL, **self.scenario_env(), **env},
                                   d, open_issues, list_fails, create_fails)

    @staticmethod
    def creates(calls):
        return [c for c in calls if c[:2] == ["issue", "create"]]

    @staticmethod
    def arg(call, flag):
        return call[call.index(flag) + 1]

    @staticmethod
    def body(call):
        return "\n".join(call[call.index("--body") + 1:call.index("--assignee")])

    def opened(self, **kw):
        """Run the scenario; assert it opened exactly one issue; (title, body)."""
        proc, calls = self.run_step(**kw)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        creates = self.creates(calls)
        self.assertEqual(len(creates), 1, calls)
        return self.arg(creates[0], "--title"), self.body(creates[0])

    # -- static ---------------------------------------------------------------
    def test_the_step_uses_only_the_token_and_keeps_expressions_out_of_bash(self):
        self.assertIsNotNone(self.script, f"{self.WORKFLOW} has no step {self.STEP!r}")
        self.assertEqual(re.findall(r"(?:secrets\.\w+|github\.token)", self.head + self.script),
                         self.TOKEN_EXPRS)
        # Values reach bash through env:, never as ${{ }} text inside the script.
        self.assertNotIn("${{", self.script)
        self.assertEqual(sorted(set(re.findall(r"\bgh (\w+ \w+)", self.script))),
                         ["issue create", "issue list"])

    def test_the_workflow_sends_no_email(self):
        for needle in ("GMAIL_", "smtplib", "smtp."):
            self.assertNotIn(needle, self.workflow.lower() if needle == "smtp." else self.workflow)

    def test_the_run_link_comes_from_the_run_context(self):
        if not self.RUN_URL_IN_ENV:
            self.skipTest("this step's body carries the run link from its checker")
        self.assertIn("          RUN_URL: " + RUN_URL_EXPR + "\n", self.head)

    # -- behaviour --------------------------------------------------------------
    def test_a_failure_opens_one_issue_naming_the_run(self):
        proc, calls = self.run_step()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        listing = calls[0]
        self.assertEqual(listing[:2], ["issue", "list"], calls)
        self.assertEqual(self.arg(listing, "--repo"), self.ISSUE_REPO)
        self.assertEqual(self.arg(listing, "--state"), "open")
        self.assertIn("--json", listing)
        creates = self.creates(calls)
        self.assertEqual(len(creates), 1, calls)
        self.assertEqual(self.arg(creates[0], "--repo"), self.ISSUE_REPO)
        self.assertEqual(self.arg(creates[0], "--assignee"), "DMoneyOH")
        self.assertRegex(self.arg(creates[0], "--title"), self.TITLE_RE)
        body = self.body(creates[0])
        self.assertIn(self.RUN_URL, body)
        self.assertIn(self.TIME_LABEL, body)
        self.assertIn(f"_Auto-generated by {self.WORKFLOW}_", body)

    def test_an_open_issue_with_the_same_title_stops_a_second(self):
        title, _ = self.opened()
        proc, calls = self.run_step(open_issues=["Unrelated open issue", title])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual([c[:2] for c in calls], [["issue", "list"]], calls)
        self.assertIn("already open", proc.stdout)

    def test_only_the_exact_title_stops_a_new_issue(self):
        title, _ = self.opened()
        for label, titles in (("prefix", [title[:-1]]), ("longer", [title + " (2)"]),
                              ("case differs", [title.lower()]), ("quoted", [f"Re: {title}"]),
                              ("none open", [])):
            with self.subTest(case=label):
                self.opened(open_issues=titles)

    def test_a_failed_listing_still_opens_the_issue(self):
        """An alert that could not check for a duplicate is worth a duplicate;
        it is not worth silence."""
        title, _ = self.opened()
        self.opened(open_issues=[title], list_fails=True)

    def test_an_issue_that_cannot_be_opened_turns_the_step_red(self):
        """Red, so GitHub's failed-run notice still reaches someone. Where the
        step must never stop its job, a failed create warns in the log instead."""
        proc, calls = self.run_step(create_fails=True)
        self.assertEqual(len(self.creates(calls)), 1, calls)
        if self.CREATE_FAILURE_IS_FATAL:
            self.assertNotEqual(proc.returncode, 0)
        else:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("::warning::gh issue create failed", proc.stdout)

    def test_the_title_date_and_the_time_are_eastern(self):
        now = _eastern_now_or_skip(self)
        title, body = self.opened()
        line = next(ln for ln in body.splitlines() if ln.startswith(self.TIME_LABEL))
        self.assertRegex(line, r" (?:1[0-2]|[1-9]):[0-5]\d [AP]M E[SD]T$")
        self.assertIn(now.strftime("%Z"), line)
        if self.TITLE_DATE_IS_EASTERN:
            self.assertIn(now.strftime("%Y-%m-%d"), title)

    def test_the_title_date_is_taken_in_eastern_time(self):
        """The check above tells ET from UTC only between 20:00 and midnight ET,
        and only where bash has the zone, so the date source is pinned as written."""
        if not self.TITLE_DATE_IS_EASTERN:
            self.skipTest("the title carries the slot's UTC day")
        self.assertIn("DAY=$(TZ=America/New_York date +%F)\n", self.script)


class TestSlotWatchdog(_AlertIssueStep, unittest.TestCase):
    """slot-watchdog.yml: 4 hours after a Mon/Thu publish slot, no post dated
    that UTC day on main opens one issue for the day."""

    WORKFLOW = "slot-watchdog.yml"
    STEP = "Missed slot — create GitHub issue"
    TITLE_RE = r"^⚠️ Missed publish slot: no post on main for Thu 2026-10-15$"
    TIME_LABEL = "Checked: "
    TITLE_DATE_IS_EASTERN = False   # the slot's own UTC day, not today's
    POSTS = ("2026-10-12-best-a.md", "2026-10-16-best-b.md", "DRAFT-best-c.md",
             "DRAFT-2026-10-15-best-e.md")   # names the day but is not a dated post

    def scenario_env(self):
        return {"SLOT_DATE": "2026-10-15", "REPO_URL": "https://github.com/DMoneyOH/HappyPet"}

    def prepare(self, tmp):
        (tmp / "_posts").mkdir()
        for name in self.POSTS:
            (tmp / "_posts" / name).write_text("---\n---\n", encoding="utf-8")

    def test_it_runs_four_hours_after_each_publish_slot(self):
        publish = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"- cron: '([^']*)'", publish), ["0 12 * * 1,4"])
        self.assertEqual(re.findall(r"- cron: '([^']*)'", self.workflow), ["0 16 * * 1,4"])
        self.assertIn("\n  workflow_dispatch:\n", self.workflow)

    def test_it_reads_main_and_may_only_open_issues(self):
        self.assertEqual(_workflow_permissions(self.workflow), ({"contents": "read", "issues": "write"}, 1))
        self.assertIn("uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4\n", self.workflow)
        self.assertIn("          ref: main\n", self.workflow)
        self.assertIn("          persist-credentials: false\n", self.workflow)
        self.assertIn("          SLOT_DATE: ${{ inputs.date }}\n", self.head)

    def test_a_filled_slot_opens_nothing(self):
        for day, post in (("2026-10-15", "2026-10-15-best-d.md"), ("2026-10-12", None)):
            with self.subTest(day=day):
                with tempfile.TemporaryDirectory() as d:
                    self.prepare(Path(d))
                    if post:
                        (Path(d) / "_posts" / post).write_text("x", encoding="utf-8")
                    proc, calls = _run_alert_step(self, self.script,
                                                  {**self.scenario_env(), "SLOT_DATE": day,
                                                   "RUN_URL": self.RUN_URL}, d)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(calls, [])
                self.assertIn("filled", proc.stdout)

    def test_drafts_and_other_days_do_not_fill_the_slot(self):
        title, body = self.opened()
        self.assertIn("main has no `_posts/2026-10-15-*.md`", body)
        self.assertIn("stage1/", body)
        self.assertIn("/actions/workflows/publish.yml", body)

    def test_a_missed_monday_is_named_monday(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "_posts").mkdir()
            proc, calls = _run_alert_step(self, self.script, {**self.scenario_env(),
                                          "SLOT_DATE": "2026-10-12", "RUN_URL": self.RUN_URL}, d)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual([self.arg(c, "--title") for c in self.creates(calls)],
                         ["⚠️ Missed publish slot: no post on main for Mon 2026-10-12"])

    def test_a_day_without_a_slot_opens_nothing(self):
        for day in ("2026-10-13", "2026-10-14", "2026-10-16", "2026-10-17", "2026-10-18"):
            with self.subTest(day=day):
                proc, calls = self.run_step(SLOT_DATE=day)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(calls, [])
                self.assertIn("not a publish day", proc.stdout)

    def test_a_malformed_date_fails_without_opening_anything(self):
        for day in ("2026-02-30", "15/10/2026", "2026-10-15; touch pwned", "$(touch pwned)",
                    "2026-10-15\n2026-10-12", "next thursday", "2026-10-15 +7 days", " 2026-10-15"):
            with self.subTest(day=day):
                with tempfile.TemporaryDirectory() as d:
                    self.prepare(Path(d))
                    proc, calls = _run_alert_step(self, self.script, {**self.scenario_env(),
                                                  "SLOT_DATE": day, "RUN_URL": self.RUN_URL}, d)
                    self.assertFalse((Path(d) / "pwned").exists())
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(calls, [])

    def test_a_blank_date_checks_today_in_utc(self):
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        proc, calls = self.run_step(SLOT_DATE="")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(today, proc.stdout + "".join(self.arg(c, "--title") for c in self.creates(calls)))

    def test_the_slot_time_is_given_in_eastern_time(self):
        """12:00 UTC is 8:00 AM EDT in October and 7:00 AM EST in December."""
        _eastern_now_or_skip(self)
        for day, slot in (("2026-10-15", "(12:00 UTC, 8:00 AM EDT)"),
                          ("2026-12-03", "(12:00 UTC, 7:00 AM EST)")):
            with self.subTest(day=day):
                _, body = self.opened(SLOT_DATE=day)
                self.assertIn(slot, body)


class TestPublishFailureIssue(_AlertIssueStep, unittest.TestCase):
    """publish.yml: a failed publish opens one issue a day, except when the hub
    step failed, which already opened its own."""

    WORKFLOW = "publish.yml"
    STEP = "Publish failed — create GitHub issue"
    TITLE_RE = r"^⚠️ Publish failed on \d{4}-\d{2}-\d{2}$"

    def scenario_env(self):
        return {"TRIGGER": "push", "PUSHED": "skipped"}

    def test_it_fires_on_failure_unless_the_hub_issue_already_did(self):
        """Skipped only when the hub-copy issue step succeeded (filed or found its
        issue). A hub block whose own issue failed to open still gets reported."""
        self.assertIn("        if: failure() && steps.hub_issue.outcome != 'success'\n", self.head)
        hub_head, _ = _named_step(self.workflow, "Hub without copy — create GitHub issue")
        self.assertIn("        id: hub_issue\n", hub_head)
        names = re.findall(r"^      - name: (.+)$", self.workflow, re.M)
        self.assertEqual(names[-1], self.STEP)
        self.assertIn("          PUSHED: ${{ steps.push.outcome }}\n", self.head)
        self.assertIn("          TRIGGER: ${{ github.event_name }}\n", self.head)
        push_head, _ = _named_step(self.workflow, "Commit and push")
        self.assertIn("        id: push\n", push_head)

    def test_the_token_is_not_widened(self):
        self.assertEqual(_workflow_permissions(self.workflow), (PUBLISH_PERMISSIONS, 1))

    def test_the_body_says_whether_the_post_reached_main(self):
        for pushed, says, never in (("skipped", "Nothing was published", "the post is published"),
                                    ("failure", "Nothing was published", "the post is published"),
                                    ("", "Nothing was published", "the post is published"),
                                    ("success", "the post is published", "Nothing was published")):
            with self.subTest(pushed=pushed):
                _, body = self.opened(PUSHED=pushed)
                self.assertIn(says, body)
                self.assertNotIn(never, body)
                self.assertIn("Trigger: push", body)
                self.assertIn("job `publish`", body)


class TestDeployFailureIssue(_AlertIssueStep, unittest.TestCase):
    """deploy.yml: a report job after every other job opens one issue naming
    the job that failed. Only that job may write issues."""

    WORKFLOW = "deploy.yml"
    STEP = "Deploy failed — create GitHub issue"
    TITLE_RE = r"^⚠️ Deploy failed on \d{4}-\d{2}-\d{2}: build$"

    def scenario_env(self):
        return {"TRIGGER": "push", "BUILD": "failure", "DEPLOY": "skipped", "PINS": "skipped"}

    def test_the_report_job_follows_every_job_and_fires_on_failure(self):
        jobs_text = self.workflow[self.workflow.index("\njobs:\n"):]
        jobs = re.findall(r"^  ([\w-]+):$", jobs_text, re.M)
        self.assertEqual(jobs, ["build", "deploy", "trigger-pins", "indexnow", "report-failure"])
        report = jobs_text[jobs_text.index("\n  report-failure:\n"):]
        # indexnow is left out on purpose: a failed IndexNow submit is not a failed deploy.
        self.assertIn("\n    needs: [build, deploy, trigger-pins]\n", report)
        self.assertIn("\n    if: failure()\n", report)
        self.assertNotIn("uses:", report)   # no checkout: it reads nothing
        for var, job in (("BUILD", "build"), ("DEPLOY", "deploy"), ("PINS", "trigger-pins")):
            self.assertIn(f"          {var}: ${{{{ needs.{job}.result }}}}\n", self.head)

    def test_only_the_report_job_gains_issues_write(self):
        self.assertEqual(_workflow_permissions(self.workflow),
                         ({"contents": "read", "pages": "write", "id-token": "write"}, 5))
        self.assertEqual(_job_permissions(self.workflow, "report-failure"), {"issues": "write"})
        self.assertEqual(_job_permissions(self.workflow, "deploy"),
                         {"pages": "write", "id-token": "write"})
        self.assertEqual(_job_permissions(self.workflow, "trigger-pins"),
                         {"contents": "read", "actions": "write"})
        self.assertEqual(_job_permissions(self.workflow, "indexnow"), {"contents": "read"})
        self.assertIsNone(_job_permissions(self.workflow, "build"))
        self.assertEqual(self.workflow.count("issues: write"), 1)

    def test_the_issue_names_each_failed_job_and_what_to_do(self):
        for results, suffix, says, never in (
                (("failure", "skipped", "skipped"), "build", "Jekyll build failed", "pin.yml by hand"),
                (("success", "failure", "skipped"), "deploy", "GitHub Pages did not take it", "Jekyll"),
                (("success", "success", "failure"), "trigger-pins", "Run pin.yml by hand", "Jekyll"),
                (("success", "success", "success"), "unknown job", "Open the run", "Jekyll")):
            with self.subTest(results=results):
                title, body = self.opened(**dict(zip(("BUILD", "DEPLOY", "PINS"), results)))
                self.assertTrue(title.endswith(": " + suffix), title)
                self.assertIn(says, body)
                self.assertNotIn(never, body)
                self.assertIn(f"failed in: {suffix}.", body)


class TestPinFailureIssue(_AlertIssueStep, unittest.TestCase):
    """pin.yml: a failed pin run opens one issue a day per slug list, in place
    of the old Gmail alert."""

    WORKFLOW = "pin.yml"
    STEP = "Alert on failure"
    TITLE_RE = r"^⚠️ Pins failed on \d{4}-\d{2}-\d{2}: best-a,best-b$"

    def scenario_env(self):
        return {"SLUGS": "best-a,best-b", "LIVE": "true", "POSTED": "failure"}

    def test_the_token_is_pinned(self):
        self.assertEqual(_workflow_permissions(self.workflow),
                         ({"contents": "write", "issues": "write"}, 1))
        self.assertIn("        if: failure() && steps.resolve.outputs.nothing_to_pin != 'true'\n", self.head)
        self.assertIn("          SLUGS: ${{ steps.resolve.outputs.slugs }}\n", self.head)

    def test_the_body_says_which_stage_failed(self):
        for env, says in (({"LIVE": "false", "POSTED": ""}, "not live after 30 minutes"),
                          ({"LIVE": "true", "POSTED": "failure"}, "post_pins.py reported a failed"),
                          ({"LIVE": "true", "POSTED": "success"}, "the run log names it")):
            with self.subTest(env=env):
                _, body = self.opened(**env)
                self.assertIn(says, body)
                self.assertIn("failed for: best-a,best-b.", body)

    def test_slugs_are_data_not_shell(self):
        with tempfile.TemporaryDirectory() as d:
            hostile = "best-a,$(touch pwned),`touch pwned2`;touch pwned3"
            proc, calls = _run_alert_step(self, self.script, {**self.scenario_env(),
                                          "SLUGS": hostile, "RUN_URL": self.RUN_URL}, d)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["gh_args"])
        self.assertTrue(self.arg(self.creates(calls)[0], "--title").endswith(": " + hostile))

    def test_no_slugs_still_names_the_failure(self):
        title, body = self.opened(SLUGS="")
        self.assertTrue(title.endswith(": no slugs resolved"), title)


class TestPinResolveSlugs(unittest.TestCase):
    """pin.yml's "Resolve slugs" step. Its list goes to GITHUB_OUTPUT and into
    later steps' scripts, so whitespace in it is dropped, as deploy.yml drops
    it when it dispatches, and a list of anything but post slugs stops the run."""

    def _resolve(self, pending=None, dispatched=""):
        """Run the step with `pending` (bytes) as _pin_queue/.pending-slugs, if
        given, and `dispatched` as the workflow_dispatch input, which the step
        reads from env. Returns (process, GITHUB_OUTPUT lines, names of the
        files left in the working directory)."""
        head, script = _named_step((WORKFLOWS / "pin.yml").read_text(encoding="utf-8"),
                                   "Resolve slugs")
        # deploy.yml dispatches .pending-slugs' content as the input, so the
        # input must reach the step as data, never as script text.
        self.assertIn("          INPUT_SLUGS: ${{ github.event.inputs.slugs }}\n", head)
        self.assertNotIn("${{", script)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            if pending is not None:
                (tmp / "_pin_queue").mkdir()
                (tmp / "_pin_queue" / ".pending-slugs").write_bytes(pending)
            output = tmp / "github_output"
            proc, _ = _run_alert_step(
                self, script, {"GITHUB_OUTPUT": output.as_posix(), "INPUT_SLUGS": dispatched}, tmp)
            lines = output.read_text(encoding="utf-8").splitlines() if output.exists() else []
            files = sorted(p.name for p in tmp.iterdir())
        return proc, lines, files

    def test_a_slug_list_resolves_without_its_whitespace(self):
        for label, kw in (("pending file", {"pending": b"best-a,best-b\n"}),
                          ("CRLF pending file", {"pending": b"best-a,best-b\r\n"}),
                          ("dispatch input", {"dispatched": "best-a, best-b"}),
                          ("input over file", {"pending": b"best-c\n",
                                               "dispatched": "best-a,best-b"})):
            with self.subTest(source=label):
                proc, lines, _ = self._resolve(**kw)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(lines, ["slugs=best-a,best-b", "nothing_to_pin=false"])

    def test_no_slugs_means_nothing_to_pin(self):
        for label, kw in (("no file", {}), ("empty file", {"pending": b""}),
                          ("blank file", {"pending": b" \r\n\n"})):
            with self.subTest(source=label):
                proc, lines, _ = self._resolve(**kw)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(lines, ["slugs=", "nothing_to_pin=true"])

    def test_a_list_of_anything_but_post_slugs_stops_the_run(self):
        """A newline cannot add an output line, and shell text runs nowhere:
        not here, and not in the later steps' scripts."""
        hostile = 'best-a";touch${IFS}pwned;echo"'
        for label, kw in (("a newline before an output", {"pending": b"best-a\nnothing_to_pin=true\n"}),
                          ("a newline before a second slugs", {"pending": b"best-a\nslugs=best-z\n"}),
                          ("command substitution", {"pending": b"best-a,$(touch pwned)\n"}),
                          ("a quote", {"pending": b'best-a"; touch pwned; echo "\n'}),
                          ("a quote, dispatched as deploy.yml does", {"dispatched": hostile})):
            with self.subTest(source=label):
                proc, lines, files = self._resolve(**kw)
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertIn("::error::", proc.stdout)
                self.assertEqual(lines, [])
                self.assertNotIn("pwned", files)


class TestFacebookTokenIssue(_AlertIssueStep, unittest.TestCase):
    """refresh-facebook-token.yml: a failed monthly check opens one issue."""

    WORKFLOW = "refresh-facebook-token.yml"
    STEP = "Alert on token failure"
    TITLE_RE = r"^⚠️ Facebook Page token check failed on \d{4}-\d{2}-\d{2}$"

    def test_the_token_may_only_open_issues(self):
        self.assertEqual(_workflow_permissions(self.workflow), ({"issues": "write"}, 1))
        self.assertIn("        if: failure()\n", self.head)

    def test_the_body_says_what_to_do(self):
        _, body = self.opened()
        self.assertIn("FACEBOOK_PAGE_TOKEN", body)
        self.assertIn("/me/accounts", body)


class TestRefillWatchdogIssue(_AlertIssueStep, unittest.TestCase):
    """refill-watchdog.yml: refill_watchdog.py's alert, opened as an issue by
    the next step from the summary and body the script writes."""

    WORKFLOW = "refill-watchdog.yml"
    STEP = "Queue low — create GitHub issue"
    TOKEN_EXPRS = ["github.token"]
    RUN_URL_IN_ENV = False
    TITLE_RE = r"^⚠️ Refill watchdog \d{4}-\d{2}-\d{2}: the topic queue is low$"

    def checker_outputs(self, prs="[42]", products=("best-a",)):
        """Run refill_watchdog.main() for real and read back what it wrote to
        GITHUB_OUTPUT, as the runner would hand it to the next step."""
        import refill_watchdog
        import queue_alert
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "github_output"
            env = {"OPEN_REFILL_PRS": prs, "GITHUB_OUTPUT": str(out),
                   "GITHUB_REPOSITORY": self.ISSUE_REPO, "GITHUB_RUN_ID": "4242"}
            with patch.dict(os.environ, env), \
                 patch.object(queue_alert, "publishable_unpublished", return_value=list(products)):
                rc, _ = io_capture(refill_watchdog.main)
            self.assertEqual(rc, 1)
            return _parse_github_output(out.read_text(encoding="utf-8"))

    def scenario_env(self):
        outputs = self.checker_outputs()
        return {"REPO": self.ISSUE_REPO, "SUMMARY": outputs["summary"], "BODY": outputs["body"]}

    def test_the_step_fires_on_any_failure_of_the_job(self):
        self.assertIn("        if: failure()\n", self.head)
        check_head = self.workflow[self.workflow.index("Check queue depth"):self.workflow.index(self.STEP)]
        self.assertIn("        id: check\n", check_head)
        self.assertIn("          SUMMARY: ${{ steps.check.outputs.summary }}\n", self.head)
        self.assertIn("          BODY: ${{ steps.check.outputs.body }}\n", self.head)

    def test_the_issue_carries_the_checkers_body(self):
        _, body = self.opened()
        self.assertIn("Refill watchdog: only 1 publishable topic(s) queued", body)
        self.assertIn("https://github.com/DMoneyOH/HappyPet/pull/42", body)
        self.assertIn("Close this issue once the queue is refilled.", body)

    def test_a_checker_that_died_still_opens_an_issue(self):
        title, body = self.opened(SUMMARY="", BODY="")
        self.assertTrue(title.endswith(": the queue check itself failed"), title)
        self.assertIn("failed before it could report the queue depth", body)
        self.assertIn("re-run the workflow. Close this issue once a watchdog run passes.", body)
        self.assertIn("Run: ", body)
        self.assertNotIn("queue is refilled", body)

    def test_an_unreadable_pr_list_opens_an_issue_with_its_own_advice(self):
        outputs = self.checker_outputs(prs="unreadable", products=("a", "b", "c"))
        title, body = self.opened(SUMMARY=outputs["summary"], BODY=outputs["body"])
        self.assertTrue(title.endswith(": open refill PRs could not be read"), title)
        self.assertIn("Close this issue once a watchdog run passes.", body)
        self.assertNotIn("queue is refilled", body)

    def test_a_same_day_rerun_with_a_new_count_finds_the_open_issue(self):
        """Depth 1 then depth 0 the same day is one issue: the count is in the
        body, never the title."""
        first, _ = self.opened()
        later = self.checker_outputs(prs="[]", products=())
        proc, calls = self.run_step(open_issues=[first], SUMMARY=later["summary"], BODY=later["body"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.creates(calls), [], calls)


class TestStalePendingSlugsIssue(_AlertIssueStep, unittest.TestCase):
    """publish.yml's missed-pins guard: a .pending-slugs older than 24 hours
    opens one issue a day. It must never stop the publish, so a failed create
    only warns."""

    WORKFLOW = "publish.yml"
    STEP = "Check for stale pending-slugs (missed pins guard)"
    TITLE_RE = r"^⚠️ Missed pins on \d{4}-\d{2}-\d{2}: stale \.pending-slugs$"
    CREATE_FAILURE_IS_FATAL = False
    AGE_HOURS = 30

    def prepare(self, tmp):
        """A repo whose .pending-slugs was committed AGE_HOURS ago."""
        import subprocess
        import time
        (tmp / "_pin_queue").mkdir()
        (tmp / "_pin_queue" / ".pending-slugs").write_text("best-a,best-b\n", encoding="utf-8")
        when = f"@{int(time.time()) - self.AGE_HOURS * 3600} +0000"
        env = dict(os.environ, GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                           cwd=tmp, env=env, check=True, capture_output=True)

    def test_the_trigger_and_token_are_unchanged(self):
        self.assertIn('if [ "$AGE" -gt 86400 ]; then', self.script)
        self.assertNotIn("        if:", self.head)
        self.assertEqual(_workflow_permissions(self.workflow), (PUBLISH_PERMISSIONS, 1))

    def test_the_body_names_the_slugs_and_their_age(self):
        _, body = self.opened()
        self.assertIn("is 30 hours old", body)
        self.assertIn("may never have been posted: best-a,best-b", body)
        self.assertIn("run pin.yml by hand with these slugs", body)

    def test_a_fresh_or_absent_file_opens_nothing(self):
        for label, hours in (("fresh", 2), ("absent", None)):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as d:
                self.AGE_HOURS = hours or 1
                self.prepare(Path(d))
                if hours is None:
                    (Path(d) / "_pin_queue" / ".pending-slugs").unlink()
                proc, calls = _run_alert_step(self, self.script, {"RUN_URL": self.RUN_URL}, d)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(calls, [])


class TestHubCopyIssueFormat(_AlertIssueStep, unittest.TestCase):
    """publish.yml's hub-copy block issue, in the shared alert format.
    TestPublishWritesTheHub runs it end to end after a real blocked publish."""

    WORKFLOW = "publish.yml"
    STEP = "Hub without copy — create GitHub issue"
    TITLE_RE = r"^⚠️ Publish blocked on \d{4}-\d{2}-\d{2}: no hub copy for treats$"
    ERRORS = "ERROR treats: no intro, how_we_choose in _data/hub_copy.json"

    def scenario_env(self):
        return {"RUNNER_TEMP": "."}

    def prepare(self, tmp):
        (tmp / "hub_pages.err").write_text(self.ERRORS + "\n", encoding="utf-8")

    def test_the_body_carries_the_error_and_the_fix(self):
        _, body = self.opened()
        self.assertIn(self.ERRORS, body)
        self.assertIn("nothing was published", body)
        self.assertIn("add the copy to _data/hub_copy.json, then re-run publish.yml", body)


class TestQueueLowIssue(_AlertIssueStep, unittest.TestCase):
    """publish.yml's queue-low issue: 3 or fewer unprocessed topics after a
    publish opens one issue a day. The count is in the body, not the title."""

    WORKFLOW = "publish.yml"
    STEP = "Queue low — create GitHub issue"
    TITLE_RE = r"^⚠️ Queue low on \d{4}-\d{2}-\d{2}: products\.json is running out of topics$"
    TOPICS = ("best-a", "best-b")

    def prepare(self, tmp):
        (tmp / "_posts").mkdir()
        (tmp / "_posts" / "2026-10-01-best-done.md").write_text("x", encoding="utf-8")
        products = [{"topic": t} for t in ("best-done", *self.TOPICS)]
        (tmp / "products.json").write_text(json.dumps(products), encoding="utf-8")

    def test_the_trigger_and_token_are_unchanged(self):
        self.assertIn("        if: steps.stage.outputs.nothing_to_publish == 'false'\n", self.head)
        self.assertIn('if [ "$QUEUE_REMAINING" -le 3 ]; then', self.script)
        self.assertEqual(_workflow_permissions(self.workflow), (PUBLISH_PERMISSIONS, 1))

    def test_the_count_is_in_the_body_and_a_new_count_dedups(self):
        title, body = self.opened()
        self.assertIn("only **2** unprocessed topic(s) remain", body)
        self.assertIn("run a refill", body)
        self.TOPICS = ("best-a",)
        proc, calls = self.run_step(open_issues=[title])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.creates(calls), [], calls)

    def test_a_healthy_queue_opens_nothing(self):
        self.TOPICS = ("best-a", "best-b", "best-c", "best-d")
        proc, calls = self.run_step()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(calls, [])


class TestChewyValidationIssue(_AlertIssueStep, unittest.TestCase):
    """chewy_validate.yml: flagged links open one issue a day. A failed create
    only warns, so a fix run still commits."""

    WORKFLOW = "chewy_validate.yml"
    STEP = "Create GitHub issue on mismatch"
    TITLE_RE = r"^⚠️ Chewy link problems on \d{4}-\d{2}-\d{2}$"
    CREATE_FAILURE_IS_FATAL = False
    REPORT = ("Chewy link validation found problems on 2026-10-11\n\n"
              "- **best-a** [MISMATCH]: wrong brand\n\n"
              "MISMATCH = wrong-brand link (safe to --fix). ERROR = lookup/API failure "
              "(fix creds/outage first, do NOT --fix).")

    def scenario_env(self):
        """ISSUE_BODY as the workflow's own "Build issue body" step writes it to
        GITHUB_ENV, from a validation report with one flagged link."""
        issue_body = self._github_env([
            {"slug": "best-a", "status": "MISMATCH", "detail": "wrong brand"},
            {"slug": "best-b", "status": "OK", "detail": ""}])["ISSUE_BODY"]
        return {"REPO": self.ISSUE_REPO, "ISSUE_BODY": issue_body}

    def _github_env(self, results):
        """GITHUB_ENV as the "Build issue body" step writes it from a validation
        report holding `results`, parsed the way the runner reads it."""
        import shlex
        import subprocess
        bash, why = _bash_for_workflow_steps()
        if not bash:
            self.skipTest(why)
        _, build = _named_step(self.workflow, "Build issue body")
        build = build.replace("python3 ", shlex.quote(Path(sys.executable).as_posix()) + " ")
        with tempfile.TemporaryDirectory() as d:
            report = {"date": "2026-10-11", "results": results}
            (Path(d) / "chewy_link_validation_2026-10-11.json").write_text(json.dumps(report), encoding="utf-8")
            env_file = Path(d) / "github_env"
            subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", build], cwd=d,
                           env=dict(os.environ, GITHUB_ENV=env_file.as_posix()), check=True,
                           capture_output=True)
            return _parse_github_output(env_file.read_text(encoding="utf-8"))

    def test_a_detail_cannot_end_the_body_and_set_a_variable(self):
        """A report line is data: a detail line reading BODY_EOF does not close
        ISSUE_BODY, so the line after it sets no environment variable."""
        detail = "x\nBODY_EOF\nEVIL=1"
        env = self._github_env([{"slug": "best-a", "status": "ERROR", "detail": detail}])
        self.assertEqual(list(env), ["ISSUE_BODY"])
        self.assertIn(detail, env["ISSUE_BODY"])

    def test_the_trigger_and_token_are_unchanged(self):
        self.assertIn("        if: steps.validate.outputs.validation_failed == 'true'\n", self.head)
        self.assertEqual(_workflow_permissions(self.workflow),
                         ({"contents": "write", "issues": "write"}, 1))
        self.assertIn("          REPO: ${{ github.repository }}\n", self.head)

    def test_the_body_carries_the_report_and_the_fix(self):
        _, body = self.opened()
        self.assertTrue(body.startswith(self.REPORT), body)
        self.assertIn("with fix set to true", body)
        self.assertEqual(body.count("_Auto-generated by chewy_validate.yml_"), 1)


class TestOneAlertPath(unittest.TestCase):
    """No workflow mails an alert; the opt-in preflight test email and the held
    legacy Stage 1 are the only workflows left holding the GMAIL_* secrets."""

    def test_only_preflight_and_legacy_generate_name_gmail(self):
        holders = sorted(p.name for p in WORKFLOWS.glob("*.yml")
                         if "GMAIL_" in p.read_text(encoding="utf-8")
                         or "smtplib" in p.read_text(encoding="utf-8"))
        self.assertEqual(holders, ["generate.yml", "preflight.yml"])


class TestIndexNow(unittest.TestCase):
    """indexnow.py: which live URLs a push changed, and the one POST that
    reports them. Expected URLs are literals, never recomputed by the module's
    own rule."""

    KEY = "9b8b0de3f2497b8a12b58d36b78a4f2e"

    @classmethod
    def setUpClass(cls):
        import indexnow
        cls.ix = indexnow

    def _post(self, front_matter):
        return "---\n" + front_matter + "\n---\n\nBody text.\n"

    def test_a_published_post_maps_to_its_live_permalink(self):
        for name, url in (
                ("2026-10-05-best-snuffle-mats-dogs.md",
                 "https://happypetproductreviews.com/toys/best-snuffle-mats-dogs/"),
                ("2026-10-19-new-kitten-checklist.md",
                 "https://happypetproductreviews.com/food/new-kitten-checklist/"),
                ("2026-08-27-best-dog-ramps.md",
                 "https://happypetproductreviews.com/health/best-dog-ramps/")):
            with self.subTest(post=name):
                text = (REPO / "_posts" / name).read_text(encoding="utf-8")
                self.assertEqual(self.ix.url_for(f"_posts/{name}", text), url)

    def test_every_post_agrees_with_the_pipelines_category_reader(self):
        import categories
        posts = sorted((REPO / "_posts").glob("2*.md"))
        self.assertGreater(len(posts), 50)
        for p in posts:
            with self.subTest(post=p.name):
                slug = p.stem.split("-", 3)[3]
                self.assertEqual(
                    self.ix.url_for(f"_posts/{p.name}", p.read_text(encoding="utf-8")),
                    f"https://happypetproductreviews.com/{categories.post_category(p)}/{slug}/")

    def test_jekylls_permalink_rule_edge_cases(self):
        path = "_posts/2026-11-02-best-dog-thing.md"
        for fm, url in (
                ('categories: ["toys"]', "/toys/best-dog-thing/"),
                ("categories: [Dogs, Health]", "/dogs/health/best-dog-thing/"),
                ("title: No category", "/best-dog-thing/"),
                ("categories: [toys]\npermalink: /custom/path/", "/custom/path/")):
            with self.subTest(front_matter=fm):
                self.assertEqual(self.ix.url_for(path, self._post(fm)),
                                 "https://happypetproductreviews.com" + url)
        crlf = "---\r\ncategories: [beds]\r\n---\r\nBody\r\n"
        self.assertEqual(self.ix.url_for(path, crlf),
                         "https://happypetproductreviews.com/beds/best-dog-thing/")
        for fm in ("categories: [toys]\npublished: false", "categories: [toys]\nsitemap: false"):
            with self.subTest(front_matter=fm):
                self.assertIsNone(self.ix.url_for(path, self._post(fm)))
        self.assertIsNone(self.ix.url_for(path, "No front matter at all.\n"))

    def test_files_that_publish_no_page_map_to_nothing(self):
        text = self._post("categories: [toys]\npermalink: /x/")
        for path in ("_posts/DRAFT-best-dog-thing.md", "_pin_queue/best-dog-thing.md",
                     "assets/notes.md", "docs/superpowers/plans/plan.md", "generate_posts.py",
                     "_posts/2026-11-02-best-dog-thing.json", "_layouts/post.html"):
            with self.subTest(path=path):
                self.assertIsNone(self.ix.url_for(path, text))
        for path in ("notes.md", "hubs/new.md"):   # a page with no permalink: skipped, not guessed
            with self.subTest(path=path):
                self.assertIsNone(self.ix.url_for(path, self._post("title: Notes")))

    def test_root_pages_and_hubs_map_to_their_own_permalinks(self):
        for path, url in (("index.md", "https://happypetproductreviews.com/"),
                          ("about.md", "https://happypetproductreviews.com/about/"),
                          ("hubs/beds.md", "https://happypetproductreviews.com/beds/"),
                          ("search.md", None),    # sitemap: false
                          ("README.md", None), ("CLAUDE.md", None), ("HANDOFF.md", None)):
            with self.subTest(path=path):
                self.assertEqual(self.ix.url_for(path, (REPO / path).read_text(encoding="utf-8")), url)

    def test_only_published_pages_ever_yield_a_url(self):
        public = {n for n in TestPublishedRootIsAllowlisted.PUBLIC if n.endswith((".md", ".html"))}
        public.discard("search.md")
        candidates = [p for p in REPO.iterdir() if p.suffix in (".md", ".html")]
        candidates += sorted((REPO / "hubs").glob("*.md"))
        yielded = {p.relative_to(REPO).as_posix() for p in candidates
                   if self.ix.url_for(p.relative_to(REPO).as_posix(), p.read_text(encoding="utf-8"))}
        hubs = {f"hubs/{p.name}" for p in (REPO / "hubs").glob("*.md")}
        self.assertEqual(yielded, public | hubs)

    def _git_repo(self, tmp):
        def git(*args):
            import subprocess
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                           cwd=tmp, check=True, capture_output=True)
        return git

    def test_changed_urls_from_a_real_diff(self):
        """Modified, added, renamed and deleted posts and a page; files that are
        not pages are ignored. Deleted and renamed-away files are read at the base."""
        with tempfile.TemporaryDirectory() as tmp:
            git = self._git_repo(tmp)
            root = Path(tmp)
            (root / "_posts").mkdir()
            (root / "_pin_queue").mkdir()
            files = {
                "_posts/2026-01-01-best-kept.md": self._post("categories: [toys]"),
                "_posts/2026-01-02-best-gone.md": self._post("categories: [beds]"),
                "_posts/2026-01-03-best-old-name.md": self._post("categories: [food]\nnote: a"),
                "about.md": self._post("permalink: /about/"),
                "_pin_queue/q.md": "x\n",
            }
            for rel, text in files.items():
                (root / rel).write_text(text, encoding="utf-8", newline="\n")
            git("init", "-q")
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            (root / "_posts/2026-01-01-best-kept.md").write_text(
                self._post("categories: [toys]\nlast_modified_at: 2026-02-01"), encoding="utf-8", newline="\n")
            (root / "_posts/2026-01-02-best-gone.md").unlink()
            git("mv", "_posts/2026-01-03-best-old-name.md", "_posts/2026-01-03-best-new-name.md")
            (root / "_posts/2026-02-01-best-new.md").write_text(
                self._post("categories: [Health]"), encoding="utf-8", newline="\n")
            (root / "about.md").write_text(self._post("permalink: /about/\ntitle: About"),
                                           encoding="utf-8", newline="\n")
            (root / "_pin_queue/q.md").write_text("y\n", encoding="utf-8", newline="\n")
            git("add", "-A")
            git("commit", "-q", "-m", "head")
            with patch.object(self.ix, "REPO", root):
                urls = self.ix.changed_urls("HEAD~1", "HEAD")
        site = "https://happypetproductreviews.com"
        self.assertEqual(sorted(urls), sorted([
            f"{site}/about/",
            f"{site}/toys/best-kept/",
            f"{site}/beds/best-gone/",
            f"{site}/food/best-old-name/",
            f"{site}/food/best-new-name/",
            f"{site}/health/best-new/",
        ]))

    def test_sitemap_urls_keep_only_this_site(self):
        xml = ("<urlset><url><loc>https://happypetproductreviews.com/</loc></url>\n"
               "<url><loc>\n  https://happypetproductreviews.com/toys/best-x/\n</loc></url>"
               "<url><loc>https://happypetproductreviews.com/toys/best-x/</loc></url>"
               "<url><loc>https://example.com/toys/best-x/</loc></url>"
               "<url><loc>https://happypetproductreviews.com.evil.test/a/</loc></url></urlset>")
        self.assertEqual(self.ix.sitemap_urls(xml), [
            "https://happypetproductreviews.com/",
            "https://happypetproductreviews.com/toys/best-x/"])

    def test_the_key_file_is_the_one_key(self):
        self.assertEqual(self.ix.find_key(), self.KEY)
        self.assertEqual((REPO / f"{self.KEY}.txt").read_text(encoding="utf-8"), self.KEY)
        self.assertIn(f"{self.KEY}.txt", TestPublishedRootIsAllowlisted.PUBLIC)

    def test_the_site_matches_config(self):
        config = (REPO / "_config.yml").read_text(encoding="utf-8")
        self.assertIn("url: 'https://happypetproductreviews.com'\n", config)
        self.assertEqual(self.ix.SITE, "https://happypetproductreviews.com")

    def test_the_post_permalink_matches_config(self):
        # url_for hard-codes Jekyll's post rule; a new permalink must fail here first.
        config = (REPO / "_config.yml").read_text(encoding="utf-8")
        self.assertRegex(config, r"(?m)^permalink: /:categories/:title/\r?$")

    def test_find_key_needs_exactly_one_key_file(self):
        other = "0123456789abcdef0123456789abcdef"
        for name, files, found in (
                ("no key", {"README.md": "x", f"{other}.txt": "not its own name"}, 0),
                ("two keys", {f"{self.KEY}.txt": self.KEY, f"{other}.txt": other}, 2)):
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                for fname, text in files.items():
                    (Path(tmp) / fname).write_text(text, encoding="utf-8")
                with patch.object(self.ix, "REPO", Path(tmp)), \
                        self.assertRaises(SystemExit) as raised:
                    self.ix.find_key()
                self.assertEqual(str(raised.exception),
                                 f"indexnow: expected one key file at the repo root, found {found}")

    def test_fetch_sitemap_reads_the_live_sitemap_through_the_opener(self):
        asked = []

        def opener(url, timeout):
            asked.append(url)
            resp = MagicMock()
            resp.read.return_value = (b"<urlset><url><loc>https://happypetproductreviews.com/a/</loc>"
                                      b"</url><url><loc>https://example.com/b/</loc></url></urlset>")
            resp.__enter__.return_value = resp
            return resp

        with patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests")):
            self.assertEqual(self.ix.fetch_sitemap(opener=opener),
                             ["https://happypetproductreviews.com/a/"])
        self.assertEqual(asked, ["https://happypetproductreviews.com/sitemap.xml"])

    def test_main_sitemap_submits_the_sitemap_urls(self):
        import io
        urls = ["https://happypetproductreviews.com/", "https://happypetproductreviews.com/a/"]
        with patch.object(self.ix, "fetch_sitemap", return_value=urls), \
                patch.object(self.ix, "submit", return_value=True) as submit, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.ix.main(["sitemap"]), 0)
            submit.assert_called_once_with({
                "host": "happypetproductreviews.com", "key": self.KEY,
                "keyLocation": f"https://happypetproductreviews.com/{self.KEY}.txt",
                "urlList": urls})
            submit.reset_mock()
            self.assertEqual(self.ix.main(["sitemap", "--dry-run"]), 0)
            submit.assert_not_called()

    def test_main_sitemap_logs_an_unreadable_sitemap_and_sends_nothing(self):
        import io
        import urllib.error
        out = io.StringIO()
        with patch.object(self.ix, "fetch_sitemap", side_effect=urllib.error.URLError("unreachable")), \
                patch.object(self.ix, "submit") as submit, contextlib.redirect_stdout(out):
            self.assertEqual(self.ix.main(["sitemap"]), 0)
        submit.assert_not_called()
        self.assertEqual(out.getvalue(),
                         "indexnow: could not read the live sitemap: <urlopen error unreachable>\n")

    def test_main_logs_a_failed_git_diff_and_sends_nothing(self):
        import io
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            self._git_repo(tmp)("init", "-q")
            with patch.object(self.ix, "REPO", Path(tmp)), \
                    patch.object(self.ix, "submit") as submit, contextlib.redirect_stdout(out):
                self.assertEqual(self.ix.main(["changed", "abc1234", "def5678"]), 0)
        submit.assert_not_called()
        self.assertTrue(out.getvalue().startswith("indexnow: git could not diff abc1234..def5678: "),
                        out.getvalue())
        self.assertIn("abc1234", out.getvalue().split(": ", 2)[2])

    def test_the_payload(self):
        self.assertEqual(self.ix.build_payload(self.KEY, ["https://happypetproductreviews.com/a/"]), {
            "host": "happypetproductreviews.com",
            "key": self.KEY,
            "keyLocation": f"https://happypetproductreviews.com/{self.KEY}.txt",
            "urlList": ["https://happypetproductreviews.com/a/"]})

    def _response(self, status):
        resp = MagicMock(status=status)
        resp.read.return_value = b""
        resp.__enter__.return_value = resp
        return resp

    def test_submit_posts_once_and_treats_200_and_202_as_success(self):
        import io
        payload = self.ix.build_payload(self.KEY, ["https://happypetproductreviews.com/a/"])
        for status in (200, 202):
            with self.subTest(status=status):
                sent = []
                opener = lambda req, timeout: sent.append(req) or self._response(status)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertTrue(self.ix.submit(payload, opener=opener))
                self.assertEqual(len(sent), 1)
                self.assertEqual(sent[0].full_url, "https://api.indexnow.org/indexnow")
                self.assertEqual(sent[0].get_method(), "POST")
                self.assertEqual(sent[0].get_header("Content-type"), "application/json; charset=utf-8")
                self.assertEqual(json.loads(sent[0].data), payload)

    def test_submit_logs_every_other_answer_and_never_raises(self):
        import io
        import urllib.error
        payload = self.ix.build_payload(self.KEY, ["https://happypetproductreviews.com/a/"])

        def http_error(code):
            def opener(req, timeout):
                raise urllib.error.HTTPError(req.full_url, code, "no", {}, io.BytesIO(b"key not valid"))
            return opener

        def no_network(req, timeout):
            raise urllib.error.URLError("unreachable")

        for name, opener in (("204", lambda req, timeout: self._response(204)),
                             ("403", http_error(403)), ("422", http_error(422)),
                             ("429", http_error(429)), ("no network", no_network)):
            with self.subTest(answer=name):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertFalse(self.ix.submit(payload, opener=opener))
                self.assertIn("indexnow:", out.getvalue())

    def test_an_http_error_logs_its_status_and_body(self):
        import io
        import urllib.error
        payload = self.ix.build_payload(self.KEY, ["https://happypetproductreviews.com/a/"])
        for code in (403, 422, 429):
            def opener(req, timeout):
                raise urllib.error.HTTPError(req.full_url, code, "no", {}, io.BytesIO(b"key not valid"))
            with self.subTest(code=code):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertFalse(self.ix.submit(payload, opener=opener))
                self.assertEqual(out.getvalue(),
                                 f"indexnow: HTTP {code} (not accepted) for 1 URL(s): key not valid\n")

    def test_main_never_fails_the_job(self):
        import io
        with patch.object(self.ix, "changed_urls", return_value=["https://happypetproductreviews.com/a/"]), \
                patch.object(self.ix, "submit", return_value=False) as submit, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.ix.main(["changed", "abc", "def"]), 0)
            submit.assert_called_once()
            submit.reset_mock()
            self.assertEqual(self.ix.main(["changed", "abc", "def", "--dry-run"]), 0)
            submit.assert_not_called()
            self.assertEqual(self.ix.main(["changed", "0" * 40, "def"]), 0)   # first push: no base
            submit.assert_not_called()

    def test_the_sitemap_workflow_is_a_dry_run_unless_told_otherwise(self):
        wf = (WORKFLOWS / "indexnow.yml").read_text(encoding="utf-8")
        self.assertIn("      dry_run:\n", wf)
        self.assertIn("        default: true\n", wf)
        self.assertIn('          if [ "$DRY_RUN" = "false" ]; then\n'
                      "            python3 indexnow.py sitemap\n"
                      "          else\n"
                      "            python3 indexnow.py sitemap --dry-run\n", wf)
        self.assertEqual(_workflow_permissions(wf), ({"contents": "read"}, 1))

    def test_the_deploy_job_runs_after_the_deploy_on_pushes_only(self):
        wf = (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
        job = wf[wf.index("\n  indexnow:\n"):wf.index("\n  report-failure:\n")]
        for line in ("    needs: deploy\n", "    if: github.event_name == 'push'\n",
                     "    continue-on-error: true\n", "          fetch-depth: 0\n",
                     "          BEFORE: ${{ github.event.before }}\n",
                     '        run: python3 indexnow.py changed "$BEFORE" "$GITHUB_SHA"\n'):
            with self.subTest(line=line):
                self.assertIn(line, job)


if __name__ == "__main__":
    unittest.main(verbosity=2)
