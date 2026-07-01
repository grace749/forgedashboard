"""Fetch membership snapshot from TeamUp (goteamup.com) using M2M token."""
import os, json, requests
from datetime import date, timedelta

TEAMUP_API_KEY = os.environ["TEAMUP_API_KEY"]
BASE = "https://goteamup.com/api/v2"
HEADERS = {"Authorization": f"Token {TEAMUP_API_KEY}"}

TRIAL_KEYWORDS = ["trial", "emerge", "intro", "taster"]


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
    recurring = sum(1 for m in active if m.get("payment_subscription"))
    trial = sum(1 for m in active
                if any(kw in m.get("name", "").lower() for kw in TRIAL_KEYWORDS))

    # New joins this month — filter by start_date in Python
    new_this_month = len({
        m["customer"] for m in active
        if m.get("start_date", "") >= month_start
    })

    # Cancellations this month — filter cancelled by expiration_date this month
    cancelled = get_all("customermemberships", {"status": "cancelled"})
    cancellations = len({
        m["customer"] for m in cancelled
        if m.get("expiration_date", "") >= month_start
    })

    return {
        "total_members": total,
        "recurring": recurring,
        "trial": trial,
        "new_this_month": new_this_month,
        "cancellations_this_month": cancellations,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
