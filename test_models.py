#!/usr/bin/env python3
"""Test which Gemini models respond cleanly for use as reviewer."""
import urllib.request, json, os, time
from pathlib import Path

# Load env
try:
    from dotenv import load_dotenv
    load_dotenv(Path.home() / ".env")
except ImportError:
    for line in (Path.home() / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"'))

API_KEY = os.environ.get("GEMINI_API_KEY", "")
URL     = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

# Models to test: different generation from generator (gemini-2.5-flash)
CANDIDATES = [
    "gemini-2.5-flash-lite",   # same gen, lite variant — different weights, higher RPM
    "gemini-2.0-flash-lite",   # prior gen, lite — most different from 2.5-flash
    "gemini-2.0-flash",        # prior gen full
]

for model in CANDIDATES:
    print(f"\nTesting {model}...")
    payload = json.dumps({
        "model": f"models/{model}",
        "messages": [{"role": "user", "content": "Reply with only the word OK"}],
        "max_tokens": 10,
        "temperature": 0.0,
    }).encode()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    try:
        req = urllib.request.Request(URL, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            reply = data["choices"][0]["message"]["content"].strip()
            print(f"  PASS — response: '{reply}'")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:150]
        print(f"  FAIL — HTTP {e.code}: {body}")
    except Exception as e:
        print(f"  FAIL — {e}")
    time.sleep(15)  # respect RPM between tests

print("\nDone.")
