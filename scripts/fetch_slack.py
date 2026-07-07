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
import os, re, time, json, urllib.parse, urllib.request

SLACK_API = "https://slack.com/api/"
GRACE_ID = os.environ.get("SLACK_USER_ID", "U05P1R84NKS")
WINDOW_HOURS = 48         # only show DMs / mentions from the last 48 hours


def _is_noise_message(msg):
    """Skip Slack system, bot/automated, and emoji/one-word messages."""
    if msg.get("subtype"):        # channel_join, invitation accepted, bot_message…
        return True
    if msg.get("bot_id") or msg.get("app_id"):   # automated (e.g. Zapier)
        return True
    text = (msg.get("text") or "")
    low = text.lower()
    if ("accepted your invitation" in low or "has joined" in low
            or "joined via" in low or "invite link" in low
            or "set up your notification" in low):
        return True
    # automated member messages sent via Zapier (e.g. body composition results)
    if "body composition" in low or "inbody" in low or "zapier" in low:
        return True
    # strip :emoji: and whitespace — if almost nothing's left, it's a reaction/ack
    stripped = re.sub(r":[a-z0-9_+'-]+:", "", text).strip()
    if len(stripped) < 4:
        return True
    # short thank-you / sign-off that closes the conversation (no reply needed)
    if len(stripped) <= 35 and re.search(r"\b(thanks?|thank you|great|brilliant|perfect|ok|okay)\b", low):
        return True
    return False


def _call(method, token, **params):
    url = SLACK_API + method + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _grace_replied_after(token, channel_id, after_ts, thread_ts=None):
    """True if Grace has posted in this channel/thread AFTER the given message."""
    try:
        if thread_ts:
            r = _call("conversations.replies", token, channel=channel_id,
                      ts=thread_ts, oldest=after_ts, limit=50)
        else:
            r = _call("conversations.history", token, channel=channel_id,
                      oldest=after_ts, limit=30)
        for m in r.get("messages", []):
            if float(m.get("ts", 0)) > float(after_ts) and m.get("user") == GRACE_ID:
                return True
    except Exception as ex:
        print(f"[slack] reply-check error: {ex}")
    return False


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


def _all_users(token):
    """Map normalised full name → Slack user id (for deep-linking a DM to a member)."""
    users, cursor = {}, ""
    for _ in range(25):
        try:
            r = _call("users.list", token, limit=200, cursor=cursor)
        except Exception:
            break
        if not r.get("ok"):
            break
        for m in r.get("members", []):
            if m.get("deleted") or m.get("is_bot") or m.get("id") == "USLACKBOT":
                continue
            p = m.get("profile", {})
            nm = " ".join((m.get("real_name") or p.get("real_name") or p.get("display_name") or "").lower().split())
            if nm:
                users.setdefault(nm, m["id"])
        cursor = (r.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            break
    return users


def run():
    token = os.environ.get("SLACK_USER_TOKEN", "")
    if not token:
        print("[slack] SLACK_USER_TOKEN not set — skipping")
        return {"configured": False, "unreplied_dms": [], "mentions": []}

    now = time.time()
    window_start = now - WINDOW_HOURS * 3600   # only the last 48 hours

    # ── Unreplied DMs (last 48h) ────────────────────────────────
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
            # Genuine DM I still need to reply to, within the last 48h:
            # last message is from the other person, recent, not automated/emoji.
            if (last.get("user") and last["user"] != GRACE_ID
                    and ts >= window_start
                    and not _is_noise_message(last)):
                # If Grace replied in a thread on that last message, it's handled
                if (last.get("thread_ts") or last.get("reply_count")) and \
                        _grace_replied_after(token, im["id"], last["ts"],
                                             last.get("thread_ts") or last["ts"]):
                    continue
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
                    sort="timestamp", sort_dir="desc", count=100)
        matches = (res.get("messages") or {}).get("matches", [])
        for m in matches:
            ts = float(m.get("ts", 0))
            if ts < now - WINDOW_HOURS * 3600:   # only the last 48 hours
                continue
            if _is_noise_message(m):             # skip bot/automated mentions
                continue
            if m.get("user") == GRACE_ID:        # her own message mentioning herself
                continue
            chan_id = (m.get("channel") or {}).get("id", "")
            # Skip if Grace has already replied after this mention (in-channel or in-thread)
            if chan_id and _grace_replied_after(token, chan_id, m.get("ts", 0), m.get("thread_ts")):
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

    # Workspace team id — app_redirect deep-links open a blank page without it
    team_id = ""
    try:
        auth = _call("auth.test", token)
        if auth.get("ok"):
            team_id = auth.get("team_id", "")
    except Exception as ex:
        print(f"[slack] auth.test error: {ex}")

    return {
        "configured": True,
        "team_id": team_id,
        "unreplied_dms": unreplied,
        "mentions": mentions,
        "users": _all_users(token),   # name → id, for DM deep-links from a member profile
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
