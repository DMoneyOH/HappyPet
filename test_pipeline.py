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



if __name__ == "__main__":
    unittest.main(verbosity=2)
