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

    return {
        "total_members": total,
        "recurring": recurring,
        "trial": trial,
        "new_this_month": new_this_month,
        "cancellations_this_month": cancellations,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
