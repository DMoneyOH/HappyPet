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

def test_gate_fails_on_em_dash():
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"; body.write_text("has — dash", encoding="utf-8")
        card = Path(td) / "card.json"
        card.write_text(json.dumps({
            "scores": {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}}),
            encoding="utf-8")
        r = run("gate", "--body", str(body), "--scorecard", str(card))
        out = json.loads(r.stdout)
        assert out["passed"] is False
        assert "em_dash_in_body" in out["flags"]

def test_review_prompt_contains_title_and_rubric():
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"; body.write_text("article body", encoding="utf-8")
        r = run("review-prompt", "--body", str(body),
                "--title", "Best Dog Mats", "--keyword", "dog mats")
        assert r.returncode == 0, r.stderr
        assert "Best Dog Mats" in r.stdout
        assert "human_voice" in r.stdout
