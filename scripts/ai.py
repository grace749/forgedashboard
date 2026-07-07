"""
Shared Anthropic helper for all dashboard AI agents.

Tries the current Haiku model, falls back to an older widely-available one if the
account doesn't have access, and logs the exact HTTP error so failures are
diagnosable in the workflow logs. Returns "" on failure (callers supply a
rule-based fallback).
"""
import os, json, time, hashlib, urllib.request, urllib.error
from pathlib import Path

# Tried in order — newest first, then a broadly-available fallback.
# NOTE: fallback to Sonnet removed — it's ~12x the price of Haiku; if Haiku is
# unavailable we return "" and callers use their rule-based fallback instead.
MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-3-5-haiku-20241022",
]

# ── Cost control: cache each agent's answer so repeated refreshes DON'T re-hit
# the API. Keyed by the system prompt (stable per agent) so within the TTL every
# refresh reuses the same advice — cutting spend from (agents × refreshes/day)
# to (agents × once/day). Cache lives in data/ so it persists between runs.
_CACHE_FILE = Path(__file__).parent.parent / "data" / "ai_cache.json"
_TTL_SECONDS = int(os.environ.get("AI_CACHE_HOURS", "20")) * 3600


def _load_cache():
    try:
        return json.loads(_CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(cache):
    try:
        _CACHE_FILE.write_text(json.dumps(cache))
    except Exception as ex:
        print(f"[ai] cache save failed: {ex}")


def generate(system, user, max_tokens=400, timeout=40):
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("[ai] ANTHROPIC_API_KEY not set — using fallback text")
        return ""

    cache = _load_cache()
    ckey = hashlib.sha256(f"{system}|{max_tokens}".encode()).hexdigest()[:20]
    hit = cache.get(ckey)
    if hit and (time.time() - hit.get("ts", 0)) < _TTL_SECONDS:
        print("[ai] cache hit — skipping API call")
        return hit.get("text", "")

    for model in MODELS:
        try:
            payload = json.dumps({
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = json.loads(resp.read())["content"][0]["text"].strip()
                if text:
                    cache[ckey] = {"text": text, "ts": time.time()}
                    _save_cache(cache)
                    return text
        except urllib.error.HTTPError as ex:
            body = ex.read().decode(errors="ignore")[:400]
            print(f"[ai] HTTP {ex.code} on model {model}: {body}")
            # Auth/credit problems won't be fixed by trying another model.
            if ex.code in (401, 403, 429):
                break
        except Exception as ex:
            print(f"[ai] error on model {model}: {ex}")
    return ""
