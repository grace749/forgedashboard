"""Fetch membership snapshot + member intelligence from TeamUp (goteamup.com)."""
import os, json, time, requests, datetime, urllib.request
import ai
from datetime import date
from collections import Counter
from pathlib import Path

# Lifetime class counts are expensive (they also pull every 'registered' record).
# Cache them and only recompute every 2 weeks — attendance milestones don't
# move fast enough to need a daily recount.
CLASS_COUNT_CACHE = Path(__file__).parent.parent / "data" / "class_counts.json"
CLASS_COUNT_MAX_AGE_DAYS = 14

TEAMUP_API_KEY = os.environ["TEAMUP_API_KEY"]
BASE = "https://goteamup.com/api/v2"
HEADERS = {"Authorization": f"Token {TEAMUP_API_KEY}"}

TRIAL_NAMES     = ["6 week jumpstart"]
RECURRING_NAMES = ["elevate", "evolve", "empower"]

EXCLUDE_FROM_CHURN = {
    "30 day beginners challenge", "emerge", "30 day challenge",
    "new you next level", "fuel forward nutrition challenge",
    "dummy membership", "body composition scan",
}
EXCLUDE_CUSTOMER_NAMES = {"grace smith", "joan smith"}
# Staff / coaches — never flag as at-risk members
AT_RISK_EXCLUDE_NAMES = {"joanne hall", "eilis kearns", "michelle mcknight"}
EXCLUDE_FROM_BREAKDOWN = {"dummy membership", "body composition scan"}

INBODY_INTERVAL_DAYS = 42   # 6 weeks between scans
MILESTONE_CLASSES    = [50, 250, 500]
MILESTONE_WINDOW     = 5    # flag if within 5 classes of a milestone


def _get(url, params=None, max_retries=5):
    """GET with retry/backoff on 429 rate limits (respects Retry-After)."""
    delay = 2
    for attempt in range(max_retries):
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", delay))
            time.sleep(wait)
            delay = min(delay * 2, 30)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def get_all(endpoint, params=None, max_results=None):
    results = []
    url = f"{BASE}/{endpoint}"
    p = dict(params or {})
    while url:
        r = _get(url, params=p)
        data = r.json()
        results.extend(data.get("results", []))
        url = data.get("next")
        p = None
        if max_results and len(results) >= max_results:
            break
    return results


def get_customer_names(customer_ids, existing=None):
    names = dict(existing or {})
    to_fetch = [cid for cid in customer_ids if cid not in names]
    for cid in to_fetch:
        try:
            r = _get(f"{BASE}/customers/{cid}")
            if r.ok:
                d = r.json()
                first = d.get("first_name", "") or ""
                last  = d.get("last_name",  "") or ""
                names[cid] = f"{first} {last}".strip() or f"Customer {cid}"
        except Exception:
            names[cid] = f"Customer {cid}"
    return names


def get_all_customer_emails():
    """Map customer_id -> lowercased email, from the customers list endpoint."""
    emails = {}
    try:
        for c in get_all("customers", {"page_size": 200}):
            if c.get("email"):
                emails[c["id"]] = c["email"].strip().lower()
    except Exception as ex:
        print(f"[teamup] email map error: {ex}")
    return emails


def members_list(ids, name_map):
    return sorted(
        [{"id": cid, "name": name_map.get(cid, f"Customer {cid}")} for cid in ids],
        key=lambda x: x["name"]
    )


# ── Attendance helpers ──────────────────────────────────────────────────────

def get_event_start(booking):
    """Extract start datetime from an enriched booking record."""
    ev = booking.get("event")
    if isinstance(ev, dict):
        return ev.get("starts_at") or ev.get("start_datetime") or ev.get("start") or ""
    return ""


def get_event_name(booking):
    ev = booking.get("event")
    if isinstance(ev, dict):
        return ev.get("name") or ev.get("title") or ""
    return ""


# IMPORTANT TeamUp API quirks (verified against live API):
#  - /attendances IGNORES event__starts_at_gte/lte date filters — it returns
#    ALL records ordered oldest-first, so a max_results cap only ever sees
#    ancient data. This silently broke at-risk/class stats.
#  - /attendances DOES honour ?status=attended (server-side filter).
#  - /events DOES honour starts_at_gte/starts_at_lte correctly.
# So: fetch ALL attended records once (for lifetime counts), fetch recent
# events via their working date filter, then intersect on event id.

def fetch_events_in_range(date_from, date_to):
    """Events whose start falls in [date_from, date_to]. Date filter works here."""
    try:
        return get_all("events", {
            "starts_at_gte": date_from,
            "starts_at_lte": date_to,
            "page_size": 500,
        })
    except Exception as ex:
        print(f"[teamup] fetch_events_in_range error: {ex}")
        return []


def fetch_all_attended():
    """
    Every attended attendance record, lifetime (~12.5k / ~26 pages). Needed for
    the lifetime class milestones (50/250/500). /attendances honours
    ?status=attended server-side, so this is ~26 requests, not a per-event crawl.
    Recent-activity views (at-risk, class stats) are derived from this same
    pull by intersecting with recent event ids — no extra fetching.
    """
    try:
        return get_all("attendances", {"status": "attended", "page_size": 500})
    except Exception as ex:
        print(f"[teamup] fetch_all_attended error: {ex}")
        return []


def fetch_all_registered():
    """Every 'registered' attendance record. Combined with attended (and minus
    upcoming bookings) this matches TeamUp's lifetime 'overall class' count."""
    try:
        return get_all("attendances", {"status": "registered", "page_size": 500})
    except Exception as ex:
        print(f"[teamup] fetch_all_registered error: {ex}")
        return []


def load_class_count_cache():
    """Return cached {customer_id: count} if fresh (<14 days), else None."""
    try:
        d = json.loads(CLASS_COUNT_CACHE.read_text())
        gen = date.fromisoformat(d["generated"])
        if (date.today() - gen).days <= CLASS_COUNT_MAX_AGE_DAYS:
            return Counter({int(k): v for k, v in d["counts"].items()})
    except Exception:
        pass
    return None


def save_class_count_cache(counts):
    try:
        CLASS_COUNT_CACHE.write_text(json.dumps({
            "generated": date.today().isoformat(),
            "counts": {str(k): v for k, v in counts.items()},
        }))
    except Exception as ex:
        print(f"[teamup] class count cache save failed: {ex}")


def compute_class_counts(all_attended_raw):
    """Full lifetime class count per customer (attended + past bookings).
    Heavy — pulls every 'registered' record. Called at most every 2 weeks."""
    today = date.today()
    all_registered = fetch_all_registered()
    upcoming = fetch_events_in_range(
        today.isoformat(), (today + datetime.timedelta(days=45)).isoformat())
    upcoming_ids = {e["id"] for e in upcoming}
    counts = Counter()
    for a in all_attended_raw:
        if a.get("customer"):
            counts[a["customer"]] += 1
    for a in all_registered:
        cid = a.get("customer")
        if cid and a.get("event") not in upcoming_ids:
            counts[cid] += 1
    return counts


# ── Class statistics ────────────────────────────────────────────────────────

# Non-class events excluded from popularity stats (they're 1:1 / admin sessions)
NON_CLASS_EVENTS = {"no sweat intro", "momentum call", "body composition scan",
                    "consultation", "induction"}
DAY_ORDER = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]


def _is_non_class(name):
    """Exclude 1:1 / admin sessions from group-class popularity stats."""
    n = (name or "").strip().lower()
    if n in NON_CLASS_EVENTS:
        return True
    if "personal training" in n or "pt session" in n or n.startswith("pt ") or n.startswith("1:") or "1:2 pt" in n:
        return True
    return False


def build_class_stats(bookings):
    """
    Class popularity from a set of attended bookings.
    Returns most AND least popular days and classes.
    """
    days_counter  = Counter()
    class_counter = Counter()
    slot_counter  = Counter()   # (weekday, hour, class) -> attendances
    slot_sessions = Counter()   # distinct event instances per slot (to average)
    slot_seen_events = {}

    def _fmt_hour(dt):
        return dt.strftime("%-I%p").lower()  # "6pm"

    for b in bookings:
        start = get_event_start(b)
        name  = (get_event_name(b) or "").strip()
        if _is_non_class(name):
            continue
        dt = None
        if start:
            try:
                dt = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
                days_counter[dt.strftime("%A")] += 1
            except Exception:
                dt = None
        if name:
            class_counter[name] += 1
        if dt is not None and name:
            slot = (dt.strftime("%A"), _fmt_hour(dt), name)
            slot_counter[slot] += 1
            ev_id = b.get("event", {}).get("id")
            key = (slot, ev_id)
            if key not in slot_seen_events:
                slot_seen_events[key] = True
                slot_sessions[slot] += 1

    days_sorted = sorted(
        [{"day": d, "count": c} for d, c in days_counter.items()],
        key=lambda x: (-x["count"], DAY_ORDER.index(x["day"]) if x["day"] in DAY_ORDER else 99)
    )
    classes_sorted = [{"name": n, "count": c} for n, c in class_counter.most_common()]

    # Class-day-time slots, with average attendance per session (fairer than raw
    # totals when a slot has run more times).
    slots = []
    for (day, time, name), total in slot_counter.items():
        sessions = slot_sessions.get((day, time, name), 1)
        slots.append({
            "day": day, "time": time, "name": name,
            "count": total,
            "sessions": sessions,
            "avg": round(total / sessions, 1),
            "label": f"{day} {time} {name}",
        })
    # Only slots that have actually run a few times (ignore one-offs)
    real_slots = [s for s in slots if s["sessions"] >= 2]
    slots_by_avg = sorted(real_slots, key=lambda s: -s["avg"])

    # For "least popular" ignore days/classes that barely run.
    max_day   = days_sorted[0]["count"]    if days_sorted    else 0
    max_class = classes_sorted[0]["count"] if classes_sorted else 0
    day_floor   = max(3, max_day   * 0.10)
    class_floor = max(5, max_class * 0.05)
    operating_days   = [d for d in days_sorted    if d["count"] >= day_floor]
    regular_classes  = [c for c in classes_sorted if c["count"] >= class_floor]

    return {
        "top_days":       days_sorted[:5],
        "bottom_days":    list(reversed(operating_days))[:5] if len(operating_days) > 1 else [],
        "top_classes":    classes_sorted[:6],
        "bottom_classes": list(reversed(regular_classes))[:6] if len(regular_classes) > 1 else [],
        "top_slots":      slots_by_avg[:6],
        "bottom_slots":   list(reversed(slots_by_avg))[:6] if len(slots_by_avg) > 1 else [],
        "total_attended": sum(class_counter.values()),
    }


def _rule_suggestion(stats):
    """Data-driven fallback suggestion when the AI call is unavailable."""
    d90 = stats.get("last_90_days", {})
    parts = []
    if d90.get("bottom_classes"):
        bc = d90["bottom_classes"][0]
        parts.append(f"“{bc['name']}” is your least-attended class over 3 months ({bc['count']}). "
                     f"Try moving it to a busier time or promoting it — or swap it for a more popular format.")
    if d90.get("bottom_days"):
        bd = d90["bottom_days"][0]
        parts.append(f"{bd['day']} is your quietest day. Consider trimming a class or running a popular format to lift attendance.")
    if d90.get("top_classes"):
        tc = d90["top_classes"][0]
        parts.append(f"“{tc['name']}” is your most popular class ({tc['count']}) — adding another slot could capture unmet demand.")
    return " ".join(parts)


def _class_suggestion(stats):
    """AI suggestion on class scheduling; falls back to a rule-based tip."""
    d90 = stats["last_90_days"]
    d30 = stats["last_30_days"]
    def pairs(items, k): return [(x[k], x["count"]) for x in items]
    summary = (
        f"Last 90 days — busiest days: {pairs(d90['top_days'][:3],'day')}; "
        f"quietest days: {pairs(d90['bottom_days'][:3],'day')}; "
        f"most popular classes: {pairs(d90['top_classes'][:5],'name')}; "
        f"least popular classes: {pairs(d90['bottom_classes'][:5],'name')}. "
        f"Last 30 days — most popular classes: {pairs(d30['top_classes'][:5],'name')}; "
        f"least popular: {pairs(d30['bottom_classes'][:5],'name')}."
    )
    text = ai.generate(
        ("You are an operations advisor for The Forge, a women's fitness gym in Belfast. "
         "From class attendance data, give 2-3 short, specific, practical suggestions to "
         "improve attendance and optimise the timetable. Plain sentences, no preamble."),
        f"Class attendance data:\n{summary}\n\nWhat should Grace do?",
        max_tokens=300,
    )
    return text or _rule_suggestion(stats)


def build_avg_tenure(cancelled, active_ids, first_seen):
    """
    Average months a member stayed, measured only for members who FULLY left
    (had a recurring membership, now no active membership). Tenure runs from
    their earliest-ever join to when their recurring membership ended, so an
    upgrade/switch isn't mistaken for churn.
    """
    spans = []
    seen  = set()
    for m in cancelled:
        cid = m.get("customer")
        if cid in active_ids or cid in seen:
            continue
        if m.get("name", "").strip().lower() not in RECURRING_NAMES:
            continue
        end   = m.get("end_date") or m.get("expiration_date")
        start = first_seen.get(cid) or m.get("start_date")
        if not end or not start:
            continue
        try:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if days > 0:
                spans.append(days / 30.44)
                seen.add(cid)
        except Exception:
            pass
    if not spans:
        return None
    return round(sum(spans) / len(spans), 1)


# ── At-risk members ─────────────────────────────────────────────────────────

AT_RISK_GRACE_DAYS = 14   # brand-new members get a grace period before at-risk


def build_at_risk(active_ids, name_map, active_memberships, recently_active_ids,
                  first_seen, ever_attended_ids):
    """
    Active members with no attendance in the last 10 days — EXCLUDING brand-new
    members who haven't had their first class yet. A member is 'too new to flag'
    if they joined within the grace period, or have no attendance on record at
    all (e.g. a fresh 6-week trial or PT signup who hasn't started).
    """
    today = date.today()
    cust_membership = {}
    for m in active_memberships:
        cid = m["customer"]
        if cid not in cust_membership:
            cust_membership[cid] = m.get("name", "")

    at_risk = []
    for cid in active_ids:
        if cid in recently_active_ids:
            continue
        name = name_map.get(cid, "")
        if not name or name.lower() in EXCLUDE_CUSTOMER_NAMES or name.lower() in AT_RISK_EXCLUDE_NAMES:
            continue
        membership = cust_membership.get(cid, "")
        if membership.lower() in EXCLUDE_FROM_CHURN or membership.lower() in EXCLUDE_FROM_BREAKDOWN:
            continue

        # Skip brand-new members who haven't started yet
        start_raw = first_seen.get(cid, "")
        if start_raw:
            try:
                if (today - date.fromisoformat(start_raw)).days <= AT_RISK_GRACE_DAYS:
                    continue
            except Exception:
                pass
        if cid not in ever_attended_ids:
            continue  # never attended a class — they haven't started, not at-risk

        at_risk.append({
            "id":         cid,
            "name":       name,
            "membership": membership,
            "days_absent": None,
            "last_seen":  None,
        })

    at_risk.sort(key=lambda x: x["name"])
    return at_risk[:25]


# ── New member milestones ───────────────────────────────────────────────────

def build_first_seen_map(all_memberships):
    """
    Each customer's earliest-ever membership start date across ALL their
    memberships. This is their true 'join date' — a member who upgrades or
    switches membership type keeps their original start, so long-standing
    members don't falsely re-trigger 'first week/month'.
    """
    first_seen = {}
    for m in all_memberships:
        cid   = m.get("customer")
        start = m.get("start_date", "")
        if not cid or not start:
            continue
        if cid not in first_seen or start < first_seen[cid]:
            first_seen[cid] = start
    return first_seen


# Tenure anniversaries to celebrate (months) — 2+ years handled as yearly after
TENURE_MONTHS = [3, 6, 9, 12, 18, 24]
TENURE_WINDOW_DAYS = 4   # celebrate within ±4 days of the anniversary


def _tenure_label(months):
    if months < 12:
        return f"{months} months"
    if months == 12:
        return "1 year"
    if months == 18:
        return "18 months"
    if months % 12 == 0:
        return f"{months // 12} years"
    return f"{months} months"


def build_first_recurring_map(all_memberships):
    """Earliest-ever recurring (Elevate/Evolve/Empower) start date per customer."""
    first_rec = {}
    for m in all_memberships:
        if m.get("name", "").strip().lower() not in RECURRING_NAMES:
            continue
        cid   = m.get("customer")
        start = m.get("start_date", "")
        if not cid or not start:
            continue
        if cid not in first_rec or start < first_rec[cid]:
            first_rec[cid] = start
    return first_rec


def build_celebrations(active, name_map, first_seen, first_recurring):
    """
    Member celebrations:
      - first_week:  jumpstart members in their first 7 days
      - new_member:  genuinely new full member — their FIRST-EVER recurring
                     membership started in the last 14 days. A member who just
                     switches membership type, or who regularly buys class packs,
                     is NOT new (their first recurring start is old / absent).
      - tenure:      near an anniversary (3/6/9/12/18 months, 2 years, then yearly)
    """
    today = date.today()
    first_week  = []
    new_members = []
    tenure      = []

    # Current membership name per customer (for jumpstart / full-member checks)
    cust_current = {}
    for m in active:
        cid = m["customer"]
        cust_current.setdefault(cid, set()).add(m.get("name", "").strip().lower())

    for cid, names in cust_current.items():
        name = name_map.get(cid, "")
        if not name or name.lower() in EXCLUDE_CUSTOMER_NAMES:
            continue

        is_jumpstart = any(t in n for n in names for t in TRIAL_NAMES)

        start_raw = first_seen.get(cid, "")
        if not start_raw:
            continue
        try:
            start   = date.fromisoformat(start_raw)
        except Exception:
            continue
        days_in = (today - start).days
        if days_in < 0:
            continue

        # First week — jumpstart only
        if is_jumpstart and days_in <= 7:
            first_week.append({"name": name, "days_in": days_in, "start_date": start_raw})
            continue

        # Genuinely new full member — first-ever recurring membership <14 days ago
        rec_start = first_recurring.get(cid, "")
        if rec_start:
            try:
                rdays = (today - date.fromisoformat(rec_start)).days
                if 0 <= rdays <= 14:
                    new_members.append({"name": name, "days_in": rdays, "start_date": rec_start})
                    continue
            except Exception:
                pass

        # Tenure anniversaries (skip anyone already celebrated as new above)
        months_milestones = list(TENURE_MONTHS)
        # add yearly anniversaries beyond 2 years (36, 48, 60, …)
        yrs = days_in // 365
        if yrs >= 3:
            months_milestones.append(yrs * 12)
        for months in months_milestones:
            anniversary = start + datetime.timedelta(days=round(months * 30.44))
            delta = (anniversary - today).days
            if abs(delta) <= TENURE_WINDOW_DAYS:
                tenure.append({
                    "name":       name,
                    "label":      _tenure_label(months),
                    "months":     months,
                    "days_until": delta,   # negative = just passed, positive = upcoming
                    "start_date": start_raw,
                })
                break

    first_week.sort(key=lambda x: x["days_in"])
    new_members.sort(key=lambda x: x["days_in"])
    tenure.sort(key=lambda x: (-x["months"], x["days_until"]))
    return {"first_week": first_week, "new_members": new_members, "tenure": tenure}


# ── Full member directory ───────────────────────────────────────────────────

# Coaches with test memberships — never show in the member directory
MEMBER_LIST_EXCLUDE = {"grace smith", "eilis kearns", "sarah lacey", "joanne hall"}


def build_member_list(active, name_map, first_seen, class_counts, momentum_calls, email_map):
    """
    One row per active FULL member (trials excluded) with: join date, current
    membership, lifetime class count, last momentum call, and email (for
    matching check-ins / goals from Gmail). Coaches' test accounts excluded.
    """
    # Latest momentum call date per member name
    last_momentum = {}
    for m in (momentum_calls or {}).get("recent", []):
        nm = (m.get("name") or "").lower()
        if nm and (nm not in last_momentum or m["date"] > last_momentum[nm]):
            last_momentum[nm] = m["date"]

    # Primary membership per customer: prefer recurring, then trial, then other
    def _primary(cid):
        names = [mm.get("name", "") for mm in active if mm["customer"] == cid]
        for n in names:
            if n.strip().lower() in RECURRING_NAMES:
                return n
        for n in names:
            if n.strip().lower() in TRIAL_NAMES:
                return n
        return next((n for n in names if n.strip().lower() not in EXCLUDE_FROM_BREAKDOWN), names[0] if names else "")

    seen = set()
    rows = []
    for m in active:
        cid = m["customer"]
        if cid in seen:
            continue
        seen.add(cid)
        name = name_map.get(cid, "")
        if not name or name.lower() in EXCLUDE_CUSTOMER_NAMES or name.lower() in MEMBER_LIST_EXCLUDE:
            continue
        membership = _primary(cid)
        ml = membership.strip().lower()
        if ml in EXCLUDE_FROM_BREAKDOWN:
            continue
        if ml in TRIAL_NAMES:       # don't list trials in the member directory
            continue
        rows.append({
            "name":          name,
            "email":         email_map.get(cid, ""),
            "join_date":     first_seen.get(cid),
            "membership":    membership,
            "class_count":   class_counts.get(cid, 0),
            "last_momentum": last_momentum.get(name.lower()),
        })
    rows.sort(key=lambda r: r["name"])
    return rows


# ── Class count milestones ──────────────────────────────────────────────────

def build_class_milestones(active_ids, name_map, class_counts):
    """Members within MILESTONE_WINDOW classes of reaching 50, 250, or 500.
    class_counts is a {customer_id: total_classes} map (attended + past bookings,
    to match TeamUp's 'overall class' number)."""
    counts = class_counts

    results = []
    for cid in active_ids:
        name = name_map.get(cid, "")
        if not name or name.lower() in EXCLUDE_CUSTOMER_NAMES:
            continue
        total = counts.get(cid, 0)
        for ms in MILESTONE_CLASSES:
            if ms - MILESTONE_WINDOW <= total < ms:
                results.append({
                    "name":           name,
                    "total_classes":  total,
                    "next_milestone": ms,
                    "classes_away":   ms - total,
                })
                break

    return sorted(results, key=lambda x: x["classes_away"])


# ── Momentum calls ──────────────────────────────────────────────────────────

def fetch_momentum_calls(name_map):
    """Events named 'momentum call' — past attendees + upcoming bookings."""
    today = date.today()
    date_past   = (today - datetime.timedelta(days=60)).isoformat()
    date_future = (today + datetime.timedelta(days=30)).isoformat()

    try:
        events = get_all("events", {
            "starts_at_gte": date_past,
            "starts_at_lte": date_future,
        })
        # Filter in Python — the name param does NOT filter server-side
        momentum_events = {
            e["id"]: e
            for e in events
            if "momentum" in (e.get("name") or "").lower()
        }
        if not momentum_events:
            return {"recent": [], "upcoming": []}

        # Only a handful of momentum events — fetch each one's attendances by id
        # rather than pulling every attendance in the window.
        attendances = []
        for ev_id in momentum_events:
            attendances.extend(get_all("attendances", {"event": ev_id, "page_size": 100}))

        recent   = []
        upcoming = []
        seen     = set()

        for a in attendances:
            ev_id = a.get("event")
            if ev_id not in momentum_events:
                continue

            ev       = momentum_events[ev_id]
            ev_start = (ev.get("starts_at") or "")[:10]
            cid      = a.get("customer")
            name     = name_map.get(cid, "") if cid else ""
            if not name and cid:
                try:
                    r = _get(f"{BASE}/customers/{cid}")
                    if r.ok:
                        d = r.json()
                        name = f"{d.get('first_name','')} {d.get('last_name','')}".strip()
                        name_map[cid] = name
                except Exception:
                    pass
            name   = name or "Unknown"
            status = a.get("status", "registered")
            key    = f"{cid}_{ev_start}"
            if key in seen:
                continue
            seen.add(key)

            if ev_start and ev_start <= today.isoformat():
                recent.append({"name": name, "date": ev_start, "status": status})
            else:
                upcoming.append({"name": name, "date": ev_start, "status": status})

        recent.sort(key=lambda x: x["date"], reverse=True)
        upcoming.sort(key=lambda x: x["date"])
        return {"recent": recent[:10], "upcoming": upcoming[:10]}

    except Exception as ex:
        print(f"[teamup] momentum_calls error: {ex}")
        return {"recent": [], "upcoming": []}


# ── InBody scans ────────────────────────────────────────────────────────────

def build_inbody_scans(all_memberships, name_map):
    """
    Body composition scan tracking via 'body composition scan' membership purchase date.
    Next due = last scan + INBODY_INTERVAL_DAYS.
    """
    scan_memberships = [
        m for m in all_memberships
        if m.get("name", "").strip().lower() == "body composition scan"
    ]

    customer_scans = {}
    for m in scan_memberships:
        cid   = m["customer"]
        start = (m.get("start_date") or m.get("created_at", ""))[:10]
        if not start:
            continue
        if cid not in customer_scans or start > customer_scans[cid]:
            customer_scans[cid] = start

    scan_ids = set(customer_scans.keys())
    name_map = get_customer_names(scan_ids, existing=name_map)

    today = date.today()
    scans = []
    for cid, last_scan_str in customer_scans.items():
        name = name_map.get(cid, f"Customer {cid}")
        if name.lower() in EXCLUDE_CUSTOMER_NAMES:
            continue
        try:
            last_scan   = date.fromisoformat(last_scan_str)
            next_due    = last_scan + datetime.timedelta(days=INBODY_INTERVAL_DAYS)
            days_to_due = (next_due - today).days
            scans.append({
                "name":        name,
                "last_scan":   last_scan_str,
                "next_due":    next_due.isoformat(),
                "days_to_due": days_to_due,
                "overdue":     days_to_due < 0,
            })
        except Exception:
            pass

    scans.sort(key=lambda x: x.get("days_to_due", 999))
    return scans


# ── Main ────────────────────────────────────────────────────────────────────

def run():
    today = date.today()
    month_start = today.replace(day=1).isoformat()

    first_of_this_month = today.replace(day=1)
    last_month_end   = first_of_this_month - datetime.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    # ── Active memberships ──────────────────────────────────────
    active = get_all("customermemberships", {"status": "active"})

    total          = len({m["customer"] for m in active})
    new_this_month = len({m["customer"] for m in active if m.get("start_date", "") >= month_start})
    leaving_ids    = {m["customer"] for m in active if m.get("is_set_for_cancellation")}

    all_active_ids = {m["customer"] for m in active}
    name_map = get_customer_names(all_active_ids)

    # ── Paused memberships ──────────────────────────────────────
    on_hold    = get_all("customermemberships", {"status": "hold"})
    paused_ids = {m["customer"] for m in on_hold}
    name_map   = get_customer_names(paused_ids, existing=name_map)

    # ── Cancelled last month ────────────────────────────────────
    cancelled_all        = get_all("customermemberships", {"status": "cancelled"})
    cancelled_last_month = [
        m for m in cancelled_all
        if last_month_start.isoformat() <= (m.get("end_date") or m.get("expiration_date") or "") <= last_month_end.isoformat()
        and m.get("name", "").strip().lower() not in EXCLUDE_FROM_CHURN
        and m["customer"] not in all_active_ids   # exclude membership switches
    ]
    cancelled_ids = {m["customer"] for m in cancelled_last_month}
    name_map      = get_customer_names(cancelled_ids, existing=name_map)

    # ── Cancellations grouped by month (last 6 months) ──────────
    six_months_ago = (first_of_this_month - datetime.timedelta(days=185)).isoformat()
    cancelled_recent = [
        m for m in cancelled_all
        if (m.get("end_date") or m.get("expiration_date") or "") >= six_months_ago
        and m.get("name", "").strip().lower() not in EXCLUDE_FROM_CHURN
    ]
    cancel_name_ids = {m["customer"] for m in cancelled_recent}
    name_map = get_customer_names(cancel_name_ids, existing=name_map)
    cancelled_by_month = {}
    for m in cancelled_recent:
        cid  = m["customer"]
        # Not a real cancellation if they still have an active membership —
        # they just switched/moved membership type.
        if cid in all_active_ids:
            continue
        nm   = name_map.get(cid, "")
        if not nm or nm.lower() in EXCLUDE_CUSTOMER_NAMES:
            continue
        end  = (m.get("end_date") or m.get("expiration_date") or "")[:10]
        if not end:
            continue
        cancelled_by_month.setdefault(end[:7], []).append({
            "name": nm, "membership": m.get("name", ""), "end": end,
        })
    cancelled_by_month = [
        {"month": mo, "members": sorted(mem, key=lambda x: x["end"], reverse=True)}
        for mo, mem in sorted(cancelled_by_month.items(), reverse=True)
    ]

    # ── Churn rate ──────────────────────────────────────────────
    churn_base_ids = {
        cid for cid in all_active_ids
        if name_map.get(cid, "").lower() not in EXCLUDE_CUSTOMER_NAMES
        and next((m.get("name","") for m in active if m["customer"]==cid), "").strip().lower() not in EXCLUDE_FROM_CHURN
    }
    churn_leaving = leaving_ids & churn_base_ids
    churn_rate    = round(len(churn_leaving) / len(churn_base_ids) * 100, 1) if churn_base_ids else 0

    # ── Breakdown by membership type ────────────────────────────
    type_members = {}
    for m in active:
        mname = m.get("name", "Unknown").strip()
        if mname.lower() in EXCLUDE_FROM_BREAKDOWN:
            continue
        cid = m["customer"]
        type_members.setdefault(mname, set()).add(cid)

    breakdown = sorted(
        [{"name": k, "count": len(v), "members": members_list(v, name_map)} for k, v in type_members.items()],
        key=lambda x: -x["count"]
    )

    recurring_ids = {m["customer"] for m in active if m.get("name","").strip().lower() in RECURRING_NAMES}
    trial_ids     = {m["customer"] for m in active if m.get("name","").strip().lower() in TRIAL_NAMES}

    # ── Real average price paid per recurring member ────────────
    recurring_prices = [
        m["billed_price"]["decimal"]
        for m in active
        if m.get("name", "").strip().lower() in RECURRING_NAMES
        and isinstance(m.get("billed_price"), dict)
        and m["billed_price"].get("decimal")
    ]
    avg_member_price = round(sum(recurring_prices) / len(recurring_prices), 2) if recurring_prices else None
    monthly_recurring_revenue = round(sum(recurring_prices), 2) if recurring_prices else None

    # ── Attendance ──────────────────────────────────────────────
    # One lifetime pull of attended records (~26 requests) serves BOTH the
    # lifetime class milestones AND the recent-activity views. Recent windows
    # are derived by intersecting with recent event ids (events date-filter
    # works); /attendances itself ignores date filters.
    date_today  = today.isoformat()
    date_90_ago = (today - datetime.timedelta(days=90)).isoformat()
    date_30_ago = (today - datetime.timedelta(days=30)).isoformat()
    date_10_ago = (today - datetime.timedelta(days=10)).isoformat()

    all_attended_raw = fetch_all_attended()

    # Class stats use a 3-month window; fetch those events once (date filter works)
    events_90     = fetch_events_in_range(date_90_ago, date_today)
    event_map_90  = {e["id"]: e for e in events_90}
    events_30_ids = {e["id"] for e in events_90 if (e.get("starts_at") or "")[:10] >= date_30_ago}
    events_10_ids = {e["id"] for e in events_90 if (e.get("starts_at") or "")[:10] >= date_10_ago}

    # ── Lifetime class counts (attended + past bookings) to match TeamUp ──
    # Cached and only recomputed every 2 weeks (the recompute pulls every
    # 'registered' record, which is slow). Fresh cache → skip the heavy fetch.
    class_counts = load_class_count_cache()
    if class_counts is None:
        print("[teamup] recomputing lifetime class counts (cache stale)")
        class_counts = compute_class_counts(all_attended_raw)
        save_class_count_cache(class_counts)
    else:
        print("[teamup] using cached lifetime class counts")

    # At-risk = active members with no attendance in the last 10 days
    recently_active_ids = {
        a["customer"] for a in all_attended_raw
        if a.get("event") in events_10_ids and a.get("customer")
    }
    # Everyone who's ever attended anything (to spot members who never started)
    ever_attended_ids = {a["customer"] for a in all_attended_raw if a.get("customer")}

    def _enrich(pred):
        return [
            {"customer": a.get("customer"),
             "event": {"id": a.get("event"),
                       "starts_at": event_map_90[a["event"]].get("starts_at", ""),
                       "name": event_map_90[a["event"]].get("name", "")}}
            for a in all_attended_raw if pred(a.get("event"))
        ]

    bookings_90 = _enrich(lambda eid: eid in event_map_90)
    bookings_30 = _enrich(lambda eid: eid in events_30_ids)

    # ── Celebrations (first week / new full member / tenure) ────
    all_memberships = active + on_hold + cancelled_all
    first_seen      = build_first_seen_map(all_memberships)
    first_recurring = build_first_recurring_map(all_memberships)
    celebrations    = build_celebrations(active, name_map, first_seen, first_recurring)

    # ── Class stats (3-month + 30-day, most & least popular) ────
    class_stats = {
        "last_90_days": build_class_stats(bookings_90),
        "last_30_days": build_class_stats(bookings_30),
    }
    class_stats["suggestion"] = _class_suggestion(class_stats)

    # ── At-risk members ─────────────────────────────────────────
    at_risk = build_at_risk(all_active_ids, name_map, active, recently_active_ids,
                            first_seen, ever_attended_ids)

    # ── Class milestones (lifetime 50/250/500) ──────────────────
    class_milestones = build_class_milestones(all_active_ids, name_map, class_counts)

    # ── Average tenure (for LTV) from cancelled recurring memberships ──
    avg_tenure_months = build_avg_tenure(cancelled_all, all_active_ids, first_seen)

    # ── Momentum calls ──────────────────────────────────────────
    momentum_calls = fetch_momentum_calls(name_map)

    # ── Full member directory ───────────────────────────────────
    email_map = get_all_customer_emails()
    member_list = build_member_list(active, name_map, first_seen, class_counts, momentum_calls, email_map)

    # ── InBody scans ────────────────────────────────────────────
    all_memberships_for_scan = active + cancelled_all
    inbody_scans = build_inbody_scans(all_memberships_for_scan, name_map)

    return {
        "total_members":            total,
        "recurring":                len(recurring_ids),
        "recurring_members":        members_list(recurring_ids, name_map),
        "trial":                    len(trial_ids),
        "trial_members":            members_list(trial_ids, name_map),
        "new_this_month":           new_this_month,
        "cancellations_this_month": len(leaving_ids),
        "leaving_members":          members_list(leaving_ids, name_map),
        "paused":                   len(paused_ids),
        "paused_members":           members_list(paused_ids, name_map),
        "cancelled_by_month":       cancelled_by_month,
        "churn_rate":               churn_rate,
        "avg_member_price":         avg_member_price,
        "monthly_recurring_revenue": monthly_recurring_revenue,
        "avg_tenure_months":        avg_tenure_months,
        "cancelled_last_month":     len([c for c in cancelled_ids if name_map.get(c,"").lower() not in EXCLUDE_CUSTOMER_NAMES]),
        "cancelled_members":        [m for m in members_list(cancelled_ids, name_map) if m["name"].lower() not in EXCLUDE_CUSTOMER_NAMES],
        "breakdown":                breakdown,
        "class_stats":              class_stats,
        "at_risk":                  at_risk,
        "celebrations":             celebrations,
        "class_milestones":         class_milestones,
        "momentum_calls":           momentum_calls,
        "member_list":              member_list,
        "inbody_scans":             inbody_scans,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
