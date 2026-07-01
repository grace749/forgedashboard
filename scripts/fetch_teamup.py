"""Fetch membership snapshot from TeamUp (goteamup.com) using M2M token."""
import os, json, requests
from datetime import date, timedelta

TEAMUP_API_KEY = os.environ["TEAMUP_API_KEY"]
BASE = "https://goteamup.com/api/v2"
HEADERS = {"Authorization": f"Token {TEAMUP_API_KEY}"}


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
        p = None  # next URL already has params baked in
    return results


def run():
    active = get_all("customermemberships", {"status": "active"})

    customer_ids = {m["customer"] for m in active}
    total = len(customer_ids)
    recurring = sum(1 for m in active if m.get("payment_subscription"))
    trial = sum(1 for m in active if "trial" in m.get("name", "").lower())

    month_start = date.today().replace(day=1).isoformat()
    cancelled = get_all("customermemberships", {"status": "cancelled", "updated__gte": month_start})
    cancellations = len({m["customer"] for m in cancelled})

    week_ago = (date.today() - timedelta(days=7)).isoformat()
    new_joins = get_all("customermemberships", {"created__gte": week_ago})
    weekly_leads = len({m["customer"] for m in new_joins})

    return {
        "total_members": total,
        "recurring": recurring,
        "trial": trial,
        "cancellations_this_month": cancellations,
        "weekly_leads": weekly_leads,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
