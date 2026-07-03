"""
Lightweight Slack panel for the dashboard.

Flags, in red:
  - DMs to Coach Grace with no reply from her after 48 hours
  - Messages that @-mention Coach Grace (recent, still unanswered)

Not a full message history — just the things needing Grace's attention.

Env:
  SLACK_USER_TOKEN   A Slack user token (xoxp-…) for Grace's account. Needs
                     scopes: im:read, im:history, channels:history,
                     groups:history, users:read, search:read.
  SLACK_USER_ID      Grace's Slack user id (default U05P1R84NKS).
"""
import os, time, json, urllib.parse, urllib.request

SLACK_API = "https://slack.com/api/"
GRACE_ID = os.environ.get("SLACK_USER_ID", "U05P1R84NKS")
STALE_HOURS = 48


def _call(method, token, **params):
    url = SLACK_API + method + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _user_names(token, ids):
    names = {}
    for uid in ids:
        try:
            r = _call("users.info", token, user=uid)
            if r.get("ok"):
                p = r["user"]
                names[uid] = p.get("real_name") or p.get("name") or uid
        except Exception:
            names[uid] = uid
    return names


def run():
    token = os.environ.get("SLACK_USER_TOKEN", "")
    if not token:
        print("[slack] SLACK_USER_TOKEN not set — skipping")
        return {"configured": False, "unreplied_dms": [], "mentions": []}

    now = time.time()
    stale_before = now - STALE_HOURS * 3600

    # ── Unreplied DMs ───────────────────────────────────────────
    unreplied = []
    try:
        ims = _call("conversations.list", token, types="im", limit=200).get("channels", [])
        other_ids = set()
        pending = []
        for im in ims:
            hist = _call("conversations.history", token, channel=im["id"], limit=1).get("messages", [])
            if not hist:
                continue
            last = hist[0]
            ts = float(last.get("ts", 0))
            # last message is from the other person (not Grace) and older than 48h
            if last.get("user") and last["user"] != GRACE_ID and ts < stale_before:
                other_ids.add(im.get("user"))
                pending.append({
                    "user_id": im.get("user"),
                    "text": (last.get("text") or "")[:160],
                    "hours": round((now - ts) / 3600),
                })
        names = _user_names(token, other_ids)
        for p in pending:
            p["name"] = names.get(p["user_id"], p["user_id"])
        unreplied = sorted(pending, key=lambda x: -x["hours"])
    except Exception as ex:
        print(f"[slack] DM scan error: {ex}")

    # ── Mentions of Grace ───────────────────────────────────────
    mentions = []
    try:
        res = _call("search.messages", token, query=f"<@{GRACE_ID}>",
                    sort="timestamp", sort_dir="desc", count=20)
        matches = (res.get("messages") or {}).get("matches", [])
        for m in matches:
            ts = float(m.get("ts", 0))
            if ts < now - 14 * 86400:   # only last 2 weeks
                continue
            mentions.append({
                "name": (m.get("username") or m.get("user") or "Someone"),
                "channel": (m.get("channel") or {}).get("name", ""),
                "text": (m.get("text") or "")[:160],
                "hours": round((now - ts) / 3600),
                "permalink": m.get("permalink", ""),
            })
    except Exception as ex:
        print(f"[slack] mention search error: {ex}")

    return {
        "configured": True,
        "unreplied_dms": unreplied,
        "mentions": mentions,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
