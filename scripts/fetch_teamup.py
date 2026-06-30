"""Fetch membership snapshot from TeamUp (goteamup.com)."""
import os, json, requests
from datetime import date, timedelta

TEAMUP_API_KEY = os.environ["TEAMUP_API_KEY"]
TEAMUP_SITE_ID = os.environ["TEAMUP_SITE_ID"]
BASE = "https://api.goteamup.com/v1"
HEADERS = {"Authorization": f"Bearer {TEAMUP_API_KEY}"}


def run():
    # Active members
    r = requests.get(f"{BASE}/customers", headers=HEADERS, params={"site": TEAMUP_SITE_ID, "status": "active"})
    r.raise_for_status()
    members = r.json()

    total = members.get("count", 0)

    # Recurring (have an active subscription)
    r2 = requests.get(f"{BASE}/subscriptions", headers=HEADERS, params={"site": TEAMUP_SITE_ID, "status": "active"})
    r2.raise_for_status()
    subs = r2.json()

    recurring = subs.get("count", 0)

    # Trials — customers with trial membership type (adjust filter to match your TeamUp setup)
    r3 = requests.get(f"{BASE}/customers", headers=HEADERS, params={"site": TEAMUP_SITE_ID, "membership_type": "trial"})
    r3.raise_for_status()
    trial = r3.json().get("count", 0)

    # Cancellations this month
    month_start = date.today().replace(day=1).isoformat()
    r4 = requests.get(f"{BASE}/subscriptions", headers=HEADERS, params={
        "site": TEAMUP_SITE_ID,
        "status": "cancelled",
        "cancelled_after": month_start,
    })
    r4.raise_for_status()
    cancellations = r4.json().get("count", 0)

    # Leads this week (new sign-ups / enquiries in last 7 days)
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    r5 = requests.get(f"{BASE}/customers", headers=HEADERS, params={
        "site": TEAMUP_SITE_ID,
        "created_after": week_ago,
    })
    r5.raise_for_status()
    weekly_leads = r5.json().get("count", 0)

    return {
        "total_members": total,
        "recurring": recurring,
        "trial": trial,
        "cancellations_this_month": cancellations,
        "weekly_leads": weekly_leads,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
