"""Fetch membership snapshot from TeamUp (goteamup.com) using M2M token."""
import os, json, requests
from datetime import date, timedelta

TEAMUP_API_KEY = os.environ["TEAMUP_API_KEY"]
BASE = "https://goteamup.com/api/v2"
HEADERS = {"Authorization": f"Token {TEAMUP_API_KEY}"}

TRIAL_NAMES = ["6 week jumpstart"]
RECURRING_NAMES = ["elevate", "evolve", "empower"]


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


def run():
    today = date.today()
    month_start = today.replace(day=1).isoformat()

    # All active memberships
    active = get_all("customermemberships", {"status": "active"})

    total = len({m["customer"] for m in active})
    recurring = len({m["customer"] for m in active
                     if m.get("name", "").strip().lower() in RECURRING_NAMES})
    trial = len({m["customer"] for m in active
                 if m.get("name", "").strip().lower() in TRIAL_NAMES})

    # New joins this month — filter by start_date in Python
    new_this_month = len({
        m["customer"] for m in active
        if m.get("start_date", "") >= month_start
    })

    # Members who have requested cancellation (still active but leaving)
    cancellations = len({m["customer"] for m in active if m.get("is_set_for_cancellation")})

    # Paused memberships
    on_hold = get_all("customermemberships", {"status": "hold"})
    paused = len({m["customer"] for m in on_hold})

    # Count unique customers per membership type
    type_counts = {}
    for m in active:
        name = m.get("name", "Unknown").strip()
        cid = m["customer"]
        if name not in type_counts:
            type_counts[name] = set()
        type_counts[name].add(cid)
    breakdown = sorted(
        [{"name": k, "count": len(v)} for k, v in type_counts.items()],
        key=lambda x: -x["count"]
    )
    # Filter out internal/dummy entries
    EXCLUDE = ["dummy membership", "body composition scan"]
    breakdown = [b for b in breakdown if b["name"].lower() not in EXCLUDE]

    return {
        "total_members": total,
        "recurring": recurring,
        "trial": trial,
        "new_this_month": new_this_month,
        "cancellations_this_month": cancellations,
        "paused": paused,
        "breakdown": breakdown,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
