"""Fetch membership snapshot from TeamUp (goteamup.com) using M2M token."""
import os, json, requests, datetime
from datetime import date

TEAMUP_API_KEY = os.environ["TEAMUP_API_KEY"]
BASE = "https://goteamup.com/api/v2"
HEADERS = {"Authorization": f"Token {TEAMUP_API_KEY}"}

TRIAL_NAMES    = ["6 week jumpstart"]
RECURRING_NAMES = ["elevate", "evolve", "empower"]

# Exclude from churn rate & total (short programmes, not real memberships)
EXCLUDE_FROM_CHURN = {
    "30 day beginners challenge", "emerge", "30 day challenge",
    "new you next level", "fuel forward nutrition challenge",
    "dummy membership", "body composition scan",
}
# Exclude these customers from churn calculation
EXCLUDE_CUSTOMER_NAMES = {"grace smith", "joan smith"}

# Exclude from breakdown display
EXCLUDE_FROM_BREAKDOWN = {"dummy membership", "body composition scan"}


def get_all(endpoint, params=None):
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
    return results


def get_customer_names(customer_ids):
    """Fetch name for a small list of customer IDs."""
    names = {}
    for cid in customer_ids:
        try:
            r = requests.get(f"{BASE}/customers/{cid}", headers=HEADERS)
            if r.ok:
                d = r.json()
                first = d.get("first_name", "") or ""
                last  = d.get("last_name", "")  or ""
                names[cid] = f"{first} {last}".strip() or f"Customer {cid}"
        except Exception:
            names[cid] = f"Customer {cid}"
    return names


def run():
    today = date.today()
    month_start = today.replace(day=1).isoformat()

    # Previous month date range
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month - datetime.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    # ── Active memberships ──────────────────────────────────────
    active = get_all("customermemberships", {"status": "active"})

    total    = len({m["customer"] for m in active})
    recurring = len({m["customer"] for m in active
                     if m.get("name", "").strip().lower() in RECURRING_NAMES})
    trial    = len({m["customer"] for m in active
                    if m.get("name", "").strip().lower() in TRIAL_NAMES})

    new_this_month = len({
        m["customer"] for m in active
        if m.get("start_date", "") >= month_start
    })

    # ── Leaving members (set for cancellation) ──────────────────
    leaving_ids = {m["customer"] for m in active if m.get("is_set_for_cancellation")}
    leaving_names_map = get_customer_names(leaving_ids)
    leaving_members = [
        {"id": cid, "name": leaving_names_map.get(cid, f"Customer {cid}")}
        for cid in leaving_ids
    ]

    # ── Paused memberships ──────────────────────────────────────
    on_hold = get_all("customermemberships", {"status": "hold"})
    paused_ids = {m["customer"] for m in on_hold}
    paused_names_map = get_customer_names(paused_ids)
    paused_members = [
        {"id": cid, "name": paused_names_map.get(cid, f"Customer {cid}")}
        for cid in paused_ids
    ]

    # ── Churn rate (excluding short programmes & staff) ─────────
    # Fetch names only for members in churn-relevant memberships
    churn_candidate_ids = {
        m["customer"] for m in active
        if m.get("name", "").strip().lower() not in EXCLUDE_FROM_CHURN
    }
    # Fetch names to filter out Grace/Joan Smith
    churn_name_map = get_customer_names(churn_candidate_ids)
    churn_base_ids = {
        cid for cid in churn_candidate_ids
        if churn_name_map.get(cid, "").lower() not in EXCLUDE_CUSTOMER_NAMES
    }
    churn_leaving = {
        m["customer"] for m in active
        if m.get("is_set_for_cancellation")
        and m["customer"] in churn_base_ids
    }
    churn_rate = round(len(churn_leaving) / len(churn_base_ids) * 100, 1) if churn_base_ids else 0

    # ── Cancelled last month ────────────────────────────────────
    cancelled_all = get_all("customermemberships", {"status": "cancelled"})
    cancelled_last_month = [
        m for m in cancelled_all
        if last_month_start.isoformat() <= (m.get("end_date") or "") <= last_month_end.isoformat()
        and m.get("name", "").strip().lower() not in EXCLUDE_FROM_CHURN
    ]
    cancelled_ids = {m["customer"] for m in cancelled_last_month}
    cancelled_names_map = get_customer_names(cancelled_ids)
    cancelled_members = [
        {"id": cid, "name": cancelled_names_map.get(cid, f"Customer {cid}")}
        for cid in cancelled_ids
        if cancelled_names_map.get(cid, "").lower() not in EXCLUDE_CUSTOMER_NAMES
    ]

    # ── Breakdown by membership type ────────────────────────────
    type_counts = {}
    for m in active:
        name = m.get("name", "Unknown").strip()
        if name.lower() in EXCLUDE_FROM_BREAKDOWN:
            continue
        cid = m["customer"]
        type_counts.setdefault(name, set()).add(cid)
    breakdown = sorted(
        [{"name": k, "count": len(v)} for k, v in type_counts.items()],
        key=lambda x: -x["count"]
    )

    return {
        "total_members":         total,
        "recurring":             recurring,
        "trial":                 trial,
        "new_this_month":        new_this_month,
        "cancellations_this_month": len(leaving_ids),
        "leaving_members":       leaving_members,
        "paused":                len(paused_ids),
        "paused_members":        paused_members,
        "churn_rate":            churn_rate,
        "cancelled_last_month":  len(cancelled_members),
        "cancelled_members":     cancelled_members,
        "breakdown":             breakdown,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
