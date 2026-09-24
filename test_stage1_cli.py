import json, subprocess, sys, tempfile
from pathlib import Path

# This worktree has no .venv of its own -- tests run under the main repo's venv
# interpreter (invoked by absolute path from outside), so re-use that same
# interpreter (sys.executable) for the stage1_cli.py subprocess calls below
# rather than a relative "./.venv/Scripts/python.exe" that won't resolve here.
PY = sys.executable

def run(*args, cwd=None):
    return subprocess.run([PY, "stage1_cli.py", *args],
                          capture_output=True, text=True, cwd=cwd)

def test_gate_passes_clean_on_standard_article():
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"; body.write_text("clean body text", encoding="utf-8")
        card = Path(td) / "card.json"
        card.write_text(json.dumps({"pass": False,
            "scores": {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}}),
            encoding="utf-8")
        r = run("gate", "--body", str(body), "--scorecard", str(card))
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["passed"] is True

def test_gate_autofixes_em_dash_instead_of_holding():
    # scrub_typography converts the em dash before the gate evaluates it, so an
    # otherwise on-standard article is NOT held on a fixable em dash: it passes,
    # and the scrubbed body (what staging writes) is clean. The raw-body backstop
    # is covered separately by TestAuthoritativeGate.test_real_em_dash_in_body_fails.
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"; body.write_text("has — dash", encoding="utf-8")
        card = Path(td) / "card.json"
        card.write_text(json.dumps({
            "scores": {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}}),
            encoding="utf-8")
        r = run("gate", "--body", str(body), "--scorecard", str(card))
        out = json.loads(r.stdout)
        assert out["passed"] is True
        assert "—" not in out["scrubbed_body"]

def test_gate_flags_a_link_to_a_product_with_no_record():
    # The agent-driven path's half of the unbacked-link guard. Staging holds the
    # article either way, but a flag HERE is what lets the rewrite pass fix it
    # instead of hitting the hold. best-outdoor-dog-tie-outs' real entry links
    # amazon.com/dp/B07CXJGZY5; amzn.to/4Xy9ZkL is one of the three shortcodes
    # best-dog-backpack-carrier published for carriers that have no record.
    # These tests read the live products.json queue, so the slug must be one
    # still queued -- repoint it when this entry is retired.
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"
        body.write_text("[Petbobi tie-out](https://www.amazon.com/dp/B07CXJGZY5?tag=pawpicks04-20) tops the list. "
                        "[Invented Runner-Up](https://amzn.to/4Xy9ZkL) is second.",
                        encoding="utf-8")
        card = Path(td) / "card.json"
        card.write_text(json.dumps({"pass": True,
            "scores": {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}}),
            encoding="utf-8")
        r = run("gate", "--body", str(body), "--scorecard", str(card),
                "--slug", "best-outdoor-dog-tie-outs")
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["passed"] is False, out
        assert any("unbacked_affiliate_link_in_body" in f and "4Xy9ZkL" in f
                   for f in out["flags"]), out["flags"]


def test_gate_does_not_flag_the_entrys_own_link():
    # The inverse. A guard that fires on the one link every article is supposed
    # to carry would hold every article, and would be switched off within a day.
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"
        body.write_text("[Petbobi tie-out](https://www.amazon.com/dp/B07CXJGZY5?tag=pawpicks04-20) tops the list, and "
                        "[here it is again](https://www.amazon.com/dp/B07CXJGZY5?tag=pawpicks04-20).", encoding="utf-8")
        card = Path(td) / "card.json"
        card.write_text(json.dumps({"pass": True,
            "scores": {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}}),
            encoding="utf-8")
        r = run("gate", "--body", str(body), "--scorecard", str(card),
                "--slug", "best-outdoor-dog-tie-outs")
        out = json.loads(r.stdout)
        assert out["passed"] is True, out
        assert out["flags"] == [], out["flags"]


def test_gate_without_a_slug_behaves_exactly_as_before():
    # --slug is optional and new. An existing caller that does not pass it must
    # get the old behaviour, not a silently different verdict.
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"
        body.write_text("[Invented Runner-Up](https://amzn.to/4Xy9ZkL) is second.",
                        encoding="utf-8")
        card = Path(td) / "card.json"
        card.write_text(json.dumps({"pass": True,
            "scores": {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}}),
            encoding="utf-8")
        r = run("gate", "--body", str(body), "--scorecard", str(card))
        out = json.loads(r.stdout)
        assert out["passed"] is True, out
        assert out["flags"] == [], out["flags"]


def test_gate_rejects_an_unknown_slug_instead_of_scoring_it_clean():
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"; body.write_text("clean body text", encoding="utf-8")
        card = Path(td) / "card.json"
        card.write_text(json.dumps({"pass": True,
            "scores": {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}}),
            encoding="utf-8")
        r = run("gate", "--body", str(body), "--scorecard", str(card),
                "--slug", "best-nonexistent-topic")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert "unknown slug" in r.stderr


def test_review_prompt_contains_title_and_rubric():
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"; body.write_text("article body", encoding="utf-8")
        r = run("review-prompt", "--body", str(body),
                "--title", "Best Dog Mats", "--keyword", "dog mats")
        assert r.returncode == 0, r.stderr
        assert "Best Dog Mats" in r.stdout
        assert "human_voice" in r.stdout

def test_review_prompt_slug_injects_verified_data_instruction():
    # --slug derives title/keyword AND the verified-facts block from products.json
    # so the reviewer is told not to flag the featured product's checked figures.
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"; body.write_text("article body", encoding="utf-8")
        r = run("review-prompt", "--slug", "best-outdoor-dog-tie-outs", "--body", str(body))
        assert r.returncode == 0, r.stderr
        assert "VERIFIED PRODUCT DATA" in r.stdout
