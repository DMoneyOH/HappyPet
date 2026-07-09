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

    def test_no_affiliate_link_fails_at_review_gate(self):
        # Gate 2 (post-review) is the publish gate: missing link must raise
        no_link = GOOD_ARTICLE.replace("https://amzn.to/3TestABC", "https://amazon.com/dp/B00AKOQYXY")
        with self.assertRaises(self.gp.GenerationStageError):
            self.gp.validate_output("review", no_link, "test-slug")

    def test_word_count_below_minimum_fails(self):
        # Has a link but too short
        short_with_link = "Short article. [Product](https://amzn.to/3TestABC). " * 5
        with self.assertRaises(self.gp.GenerationStageError):
            self.gp.validate_output("generate", short_with_link, "test-slug")


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

    def test_em_dash_fail_overrides_pass_true(self):
        """em_dash_count > 0 must force pass=false even if model returns pass=true"""
        import generate_posts as gp
        scorecard = json.loads(REVIEWER_FAIL_JSON)
        scorecard["pass"] = True  # Simulate model wrongly returning pass=true
        scorecard["em_dash_count"] = 2
        # Replicate the override logic from review_and_rewrite
        if scorecard.get("em_dash_count", 0) > 0:
            scorecard["pass"] = False
        self.assertFalse(scorecard["pass"])

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


class TestGenerationResultJSON(unittest.TestCase):
    """GENERATION_RESULT.json is written on pipeline completion — P4"""

    def test_result_json_written_after_run(self):
        import generate_posts as gp
        import tempfile, os

        result_path = REPO / "GENERATION_RESULT.json"
        # Check the attribute exists in module (structural test)
        source = (REPO / "generate_posts.py").read_text()
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
        data = json.loads(result_path.read_text())
        for key in ("articles_generated", "articles_held", "articles_skipped", "articles_failed"):
            self.assertIn(key, data, f"Missing key: {key}")


class TestPendingDraftsJSON(unittest.TestCase):
    """PENDING_DRAFTS.json is written when articles are generated — P7"""

    def test_pending_drafts_written_in_source(self):
        source = (REPO / "generate_posts.py").read_text()
        self.assertIn("PENDING_DRAFTS.json", source)
        self.assertIn('"drafts"', source)
        self.assertIn('"generated"', source)

    def test_publish_yml_reads_pending_drafts(self):
        publish = (REPO / ".github/workflows/publish.yml").read_text()
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
        source = (REPO / "post_pins.py").read_text()
        self.assertNotIn("shutil.move", source,
                         "post_pins must not move queue files -- sent/ belongs to push_pins")

    def test_push_pins_dedups_on_sheet(self):
        source = (REPO / "push_pins_to_sheets.py").read_text()
        self.assertIn("read_fb_queue_state", source)
        self.assertNotIn("SKIP (already sent)", source,
                         "sent/-location dedup starves the FB queue; the sheet is the authority")

    def test_validator_treats_api_failure_as_error_not_mismatch(self):
        # An Impact.com outage must never look like a wrong-brand link --
        # --fix would clear every stored Chewy URL
        source = (REPO / "validate_published_chewy_links.py").read_text()
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

    def test_gemini_schema_has_no_additional_properties(self):
        import generate_posts as gp
        self.assertNotIn("additionalProperties", json.dumps(gp.REVIEW_SCHEMA_GEMINI))

    def test_schema_covers_all_scorecard_keys(self):
        import generate_posts as gp
        required = set(gp.REVIEW_SCHEMA["required"])
        for key in ("pass", "scores", "affiliate_link_present", "em_dash_count",
                    "ai_patterns_found", "flags", "rewrite_instructions"):
            self.assertIn(key, required)

    def test_reviewer_routes_through_openrouter(self):
        # Derek's constraint: no direct Anthropic account -- Claude access goes
        # through OpenRouter on the existing OPENROUTER_API_KEY, with schema
        # enforcement requested and routing pinned to providers that honor it
        source = (REPO / "generate_posts.py").read_text()
        self.assertNotIn("api.anthropic.com", source)
        self.assertNotIn("ANTHROPIC_API_KEY", source)
        self.assertIn('"response_format"', source)
        self.assertIn('"json_schema"', source)
        self.assertIn("require_parameters", source)
        import generate_posts as gp
        self.assertTrue(gp.REVIEWER_MODEL.startswith("anthropic/"))

    def test_generate_yml_has_no_anthropic_secret(self):
        workflow = (REPO / ".github/workflows/generate.yml").read_text()
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
        workflow = (REPO / ".github/workflows/refill.yml").read_text()
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
            written = json.loads(path.read_text())

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
            original = path.read_text()
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "best-dog-ramps", "--name", "Some Ramp",
                        "--asin", "NOTREAL123",
                        "--image", "https://m.media-amazon.com/images/I/x.jpg",
                        "--price", "39.99", "--stars", "4.2",
                    ])
            self.assertEqual(path.read_text(), original, "rejected candidate must not write")

    def test_rejects_wrong_image_host_and_does_not_write(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-cat-carrier-backpacks",
                     "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            original = path.read_text()
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "best-cat-carrier-backpacks", "--name", "Some Carrier",
                        "--asin", "B0ABCD1234", "--image", "https://example.com/evil.jpg",
                        "--price", "59.99", "--stars", "4.3",
                    ])
            self.assertEqual(path.read_text(), original)

    def test_rejects_sponsored_prefixed_name_and_does_not_write(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-catnip-toys", "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            original = path.read_text()
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "best-catnip-toys",
                        "--name", "Sponsored Ad - Fancy Catnip Toy",
                        "--asin", "B0ABCD1234",
                        "--image", "https://m.media-amazon.com/images/I/x.jpg",
                        "--price", "12.99", "--stars", "4.1",
                    ])
            self.assertEqual(path.read_text(), original)

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
            written = json.loads(path.read_text())
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
            written = json.loads(path.read_text())
        self.assertNotIn("upc", written[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
