"""
Shared Anthropic helper for all dashboard AI agents.

Tries the current Haiku model, falls back to an older widely-available one if the
account doesn't have access, and logs the exact HTTP error so failures are
diagnosable in the workflow logs. Returns "" on failure (callers supply a
rule-based fallback).
"""
import os, json, urllib.request, urllib.error

# Tried in order — newest first, then a broadly-available fallback.
MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-3-5-haiku-20241022",
    "claude-3-5-sonnet-20241022",
]


def generate(system, user, max_tokens=400, timeout=40):
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("[ai] ANTHROPIC_API_KEY not set — using fallback text")
        return ""
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
