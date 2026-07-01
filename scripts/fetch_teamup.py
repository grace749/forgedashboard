"""Fetch membership snapshot from TeamUp (goteamup.com) using M2M token."""
import os, json, requests
from datetime import date, timedelta

TEAMUP_API_KEY = os.environ["TEAMUP_API_KEY"]
BASE = "https://goteamup.com/api/v2"
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
    # Active customers with a payment subscription
    all_memberships = get_all("customermemberships", {"status": "active"})

    # Unique customers with active membership
    customer_ids = set(m.get("customer") or m.get("customer_id") for m in all_memberships)
    total = len(customer_ids)

    # Customers on recurring payment plans
    recurring = sum(1 for m in all_memberships if m.get("payment_subscription"))

    # Trials
    trial = sum(1 for m in all_memberships if "trial" in str(m.get("membership_type_name", "")).lower())

    # Cancellations this month
    month_start = date.today().replace(day=1).isoformat()
    cancelled = get_all("customermemberships", {
        "status": "cancelled",
        "updated__gte": month_start,
    })
    cancellations = len(set(m.get("customer") or m.get("customer_id") for m in cancelled))

    # New joins in last 7 days
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    new_memberships = get_all("customermemberships", {"created__gte": week_ago})
    weekly_leads = len(set(m.get("customer") or m.get("customer_id") for m in new_memberships))

    return {
        "total_members": total,
        "recurring": recurring,
        "trial": trial,
        "cancellations_this_month": cancellations,
        "weekly_leads": weekly_leads,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
