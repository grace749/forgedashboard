"""Fetch membership snapshot + member intelligence from TeamUp (goteamup.com)."""
import os, json, time, requests, datetime, urllib.request
from datetime import date
from collections import Counter

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


# ── Class statistics ────────────────────────────────────────────────────────

# Non-class events excluded from popularity stats (they're 1:1 / admin sessions)
NON_CLASS_EVENTS = {"no sweat intro", "momentum call", "body composition scan",
                    "consultation", "induction"}
DAY_ORDER = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]


def build_class_stats(bookings):
    """
    Class popularity from a set of attended bookings.
    Returns most AND least popular days and classes.
    """
    days_counter  = Counter()
    class_counter = Counter()

    for b in bookings:
        start = get_event_start(b)
        name  = (get_event_name(b) or "").strip()
        if name.lower() in NON_CLASS_EVENTS:
            continue
        if start:
            try:
                dt = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
                days_counter[dt.strftime("%A")] += 1
            except Exception:
                pass
        if name:
            class_counter[name] += 1

    days_sorted = sorted(
        [{"day": d, "count": c} for d, c in days_counter.items()],
        key=lambda x: (-x["count"], DAY_ORDER.index(x["day"]) if x["day"] in DAY_ORDER else 99)
    )
    classes_sorted = [{"name": n, "count": c} for n, c in class_counter.most_common()]

    # For "least popular" ignore days/classes that barely run (one-offs, special
    # events, a day with almost no classes) so the bottom list is meaningful.
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
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        try:
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
            payload = json.dumps({
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "system": ("You are an operations advisor for The Forge, a women's fitness gym in "
                           "Belfast. From class attendance data, give 2-3 short, specific, practical "
                           "suggestions to improve attendance and optimise the timetable. Plain "
                           "sentences, no preamble, no bullet characters."),
                "messages": [{"role": "user", "content": f"Class attendance data:\n{summary}\n\nWhat should Grace do?"}],
            }).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = json.loads(resp.read())["content"][0]["text"].strip()
                if text:
                    return text
        except Exception as ex:
            print(f"[teamup] class suggestion AI error: {ex}")
    return _rule_suggestion(stats)


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

def build_at_risk(active_ids, name_map, active_memberships, recently_active_ids):
    """Active members not seen in last 14 days."""
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
        if not name or name.lower() in EXCLUDE_CUSTOMER_NAMES:
            continue
        membership = cust_membership.get(cid, "")
        if membership.lower() in EXCLUDE_FROM_CHURN or membership.lower() in EXCLUDE_FROM_BREAKDOWN:
            continue
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


def build_celebrations(active, name_map, first_seen):
    """
    Member celebrations:
      - first_week:  jumpstart members in their first 7 days
      - new_member:  just started a full membership (Elevate/Evolve/Empower) in
                     the last 14 days
      - tenure:      near an anniversary (3/6/9/12/18 months, 2 years, then yearly)
    """
    today = date.today()
    first_week  = []
    new_members = []
    tenure      = []
    seen_new    = set()

    # Current membership name per customer (for jumpstart / full-member checks)
    cust_current = {}
    for m in active:
        cid = m["customer"]
        cust_current.setdefault(cid, set()).add(m.get("name", "").strip().lower())

    for cid, names in cust_current.items():
        name = name_map.get(cid, "")
        if not name or name.lower() in EXCLUDE_CUSTOMER_NAMES:
            continue

        is_jumpstart   = any(t in n for n in names for t in TRIAL_NAMES)
        is_full_member = any(n in RECURRING_NAMES for n in names)

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
            seen_new.add(cid)
            continue

        # Just joined as a full member (started a recurring membership recently).
        # Use the recurring membership's own start, not first_seen, so jumpstart
        # converts count from when they upgraded.
        if is_full_member:
            rec_start = min(
                (mm.get("start_date", "") for mm in active
                 if mm["customer"] == cid and mm.get("name", "").strip().lower() in RECURRING_NAMES
                 and mm.get("start_date")),
                default="",
            )
            if rec_start:
                try:
                    rdays = (today - date.fromisoformat(rec_start)).days
                    if 0 <= rdays <= 14:
                        new_members.append({"name": name, "days_in": rdays, "start_date": rec_start})
                        seen_new.add(cid)
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


# ── Class count milestones ──────────────────────────────────────────────────

def build_class_milestones(active_ids, name_map, all_attended_raw):
    """Members within MILESTONE_WINDOW classes of reaching 50, 250, or 500."""
    counts = Counter(
        a.get("customer") for a in all_attended_raw
        if a.get("customer") and a.get("status") == "attended"
    )

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
    ]
    cancelled_ids = {m["customer"] for m in cancelled_last_month}
    name_map      = get_customer_names(cancelled_ids, existing=name_map)

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

    # At-risk = active members with no attendance in the last 10 days
    recently_active_ids = {
        a["customer"] for a in all_attended_raw
        if a.get("event") in events_10_ids and a.get("customer")
    }

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
    first_seen   = build_first_seen_map(active + on_hold + cancelled_all)
    celebrations = build_celebrations(active, name_map, first_seen)

    # ── Class stats (3-month + 30-day, most & least popular) ────
    class_stats = {
        "last_90_days": build_class_stats(bookings_90),
        "last_30_days": build_class_stats(bookings_30),
    }
    class_stats["suggestion"] = _class_suggestion(class_stats)

    # ── At-risk members ─────────────────────────────────────────
    at_risk = build_at_risk(all_active_ids, name_map, active, recently_active_ids)

    # ── Class milestones (lifetime 50/250/500) ──────────────────
    class_milestones = build_class_milestones(all_active_ids, name_map, all_attended_raw)

    # ── Average tenure (for LTV) from cancelled recurring memberships ──
    avg_tenure_months = build_avg_tenure(cancelled_all, all_active_ids, first_seen)

    # ── Momentum calls ──────────────────────────────────────────
    momentum_calls = fetch_momentum_calls(name_map)

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
        "inbody_scans":             inbody_scans,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
