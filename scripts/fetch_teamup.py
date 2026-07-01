"""Fetch membership snapshot from TeamUp (goteamup.com) using M2M token."""
import os, json, requests
from datetime import date, timedelta

TEAMUP_API_KEY = os.environ["TEAMUP_API_KEY"]
BASE = "https://goteamup.com/api/v1"
HEADERS = {"Authorization": f"Token {TEAMUP_API_KEY}"}


def get_all(endpoint, params=None):
    results = []
    url = f"{BASE}/{endpoint}/"
    p = params or {}
    while url:
        r = requests.get(url, headers=HEADERS, params=p)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        url = data.get("next")
        p = {}
    return results


def run():
    # All active customers
    active = get_all("customers", {"status": "active"})
    total = len(active)

    # Trials — customers with trial status
    trial = sum(1 for c in active if "trial" in str(c.get("status", "")).lower())

    # Recurring — customers with a direct debit / recurring payment
    recurring = sum(1 for c in active if c.get("has_direct_debit") or c.get("is_recurring"))

    # Cancellations this month
    month_start = date.today().replace(day=1).isoformat()
    cancelled = get_all("customers", {
        "status": "cancelled",
        "updated__gte": month_start,
    })
    cancellations = len(cancelled)

    # New customers in last 7 days
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    new_customers = get_all("customers", {"created__gte": week_ago})
    weekly_leads = len(new_customers)

    return {
        "total_members": total,
        "recurring": recurring,
        "trial": trial,
        "cancellations_this_month": cancellations,
        "weekly_leads": weekly_leads,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
