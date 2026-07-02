"""Fetch membership snapshot + member intelligence from TeamUp (goteamup.com)."""
import os, json, requests, datetime
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


def get_all(endpoint, params=None, max_results=None):
    results = []
    url = f"{BASE}/{endpoint}"
    p = dict(params or {})
    while url:
        r = requests.get(url, headers=HEADERS, params=p)
        r.raise_for_status()
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
            r = requests.get(f"{BASE}/customers/{cid}", headers=HEADERS)
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


def fetch_events_map(date_from, date_to):
    """Fetch events in range, return {event_id: event_dict}."""
    try:
        events = get_all("events", {
            "starts_at_gte": date_from,
            "starts_at_lte": date_to,
        })
        return {e["id"]: e for e in events}
    except Exception as ex:
        print(f"[teamup] events fetch error: {ex}")
        return {}


def fetch_attended_bookings(date_from, date_to, max_results=5000):
    """
    Return attended records enriched with event name and start time.
    Uses /attendances (correct endpoint) with event__starts_at_gte/lte filters.
    """
    try:
        event_map = fetch_events_map(date_from, date_to)

        attendances = get_all("attendances", {
            "event__starts_at_gte": date_from,
            "event__starts_at_lte": date_to,
        }, max_results=max_results)

        enriched = []
        for a in attendances:
            if a.get("status") != "attended":
                continue
            ev_id = a.get("event")
            ev    = event_map.get(ev_id, {})
            enriched.append({
                "customer":            a.get("customer"),
                "customer_membership": a.get("customer_membership"),
                "event": {
                    "id":        ev_id,
                    "starts_at": ev.get("starts_at", ""),
                    "name":      ev.get("name", ""),
                },
                "status": "attended",
            })
        return enriched
    except Exception as ex:
        print(f"[teamup] attendances error: {ex}")
        return []


def fetch_attendance_counts(date_from, date_to, max_results=20000):
    """
    Fetch raw attendances (status=attended) without event enrichment.
    Used for class milestone counts where we only need customer IDs.
    """
    try:
        attendances = get_all("attendances", {
            "event__starts_at_gte": date_from,
            "event__starts_at_lte": date_to,
        }, max_results=max_results)
        return [a for a in attendances if a.get("status") == "attended"]
    except Exception as ex:
        print(f"[teamup] attendance counts error: {ex}")
        return []


# ── Class statistics ────────────────────────────────────────────────────────

def build_class_stats(bookings):
    """Most popular class days, times, and class names from recent bookings."""
    days_counter  = Counter()
    times_counter = Counter()
    class_counter = Counter()

    for b in bookings:
        start = get_event_start(b)
        name  = get_event_name(b)
        if start:
            try:
                dt  = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
                day = dt.strftime("%A")
                hour_label = dt.strftime("%-I%p").lower()
                days_counter[day]        += 1
                times_counter[hour_label] += 1
            except Exception:
                pass
        if name:
            class_counter[name] += 1

    DAY_ORDER = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    top_days = sorted(
        [{"day": d, "count": c} for d, c in days_counter.items()],
        key=lambda x: (-x["count"], DAY_ORDER.index(x["day"]) if x["day"] in DAY_ORDER else 99)
    )

    return {
        "top_days":       top_days[:7],
        "top_times":      [{"time": t, "count": c} for t, c in times_counter.most_common(8)],
        "top_classes":    [{"name": n, "count": c} for n, c in class_counter.most_common(6)],
        "total_attended": len(bookings),
    }


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

def build_new_milestones(active, name_map):
    """
    First week: jumpstart membership started 0-7 days ago.
    First month: any membership started 28-34 days ago.
    """
    today = date.today()
    milestones = []
    seen = set()

    for m in active:
        start_raw = m.get("start_date", "")
        if not start_raw:
            continue
        cid   = m["customer"]
        if cid in seen:
            continue
        name  = name_map.get(cid, "")
        if not name or name.lower() in EXCLUDE_CUSTOMER_NAMES:
            continue
        mname = m.get("name", "").strip().lower()
        if mname in EXCLUDE_FROM_CHURN or mname in EXCLUDE_FROM_BREAKDOWN:
            continue

        is_jumpstart = any(t in mname for t in TRIAL_NAMES)

        try:
            start   = date.fromisoformat(start_raw)
            days_in = (today - start).days
            if is_jumpstart and 0 <= days_in <= 7:
                seen.add(cid)
                milestones.append({"name": name, "type": "first_week",  "days_in": days_in, "start_date": start_raw})
            elif 28 <= days_in <= 34:
                seen.add(cid)
                milestones.append({"name": name, "type": "first_month", "days_in": days_in, "start_date": start_raw})
        except Exception:
            pass

    return milestones


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

        attendances = get_all("attendances", {
            "event__starts_at_gte": date_past,
            "event__starts_at_lte": date_future,
        })

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
                    r = requests.get(f"{BASE}/customers/{cid}", headers=HEADERS)
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
        if last_month_start.isoformat() <= (m.get("end_date") or "") <= last_month_end.isoformat()
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

    # ── Recent attendance (30 days, enriched with event name/time) ─────────
    date_today  = today.isoformat()
    date_30_ago = (today - datetime.timedelta(days=30)).isoformat()
    recent_bookings = fetch_attended_bookings(date_30_ago, date_today)

    # ── At-risk: who attended in last 14 days? ──────────────────
    date_14_ago = (today - datetime.timedelta(days=14)).isoformat()
    raw_14 = fetch_attendance_counts(date_14_ago, date_today)
    recently_active_ids = {a["customer"] for a in raw_14}

    # ── All-time attendance counts for class milestones ─────────
    date_2yr_ago = (today - datetime.timedelta(days=730)).isoformat()
    all_attended_raw = fetch_attendance_counts(date_2yr_ago, date_today, max_results=20000)

    # ── New member milestones ───────────────────────────────────
    new_milestones = build_new_milestones(active, name_map)

    # ── Class stats ─────────────────────────────────────────────
    class_stats = build_class_stats(recent_bookings)

    # ── At-risk members ─────────────────────────────────────────
    at_risk = build_at_risk(all_active_ids, name_map, active, recently_active_ids)

    # ── Class milestones ────────────────────────────────────────
    class_milestones = build_class_milestones(all_active_ids, name_map, all_attended_raw)

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
        "cancelled_last_month":     len([c for c in cancelled_ids if name_map.get(c,"").lower() not in EXCLUDE_CUSTOMER_NAMES]),
        "cancelled_members":        [m for m in members_list(cancelled_ids, name_map) if m["name"].lower() not in EXCLUDE_CUSTOMER_NAMES],
        "breakdown":                breakdown,
        "class_stats":              class_stats,
        "at_risk":                  at_risk,
        "new_milestones":           new_milestones,
        "class_milestones":         class_milestones,
        "momentum_calls":           momentum_calls,
        "inbody_scans":             inbody_scans,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
