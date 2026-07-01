"""Fetch membership snapshot from TeamUp (goteamup.com) using M2M token."""
import os, json, requests
from datetime import date, timedelta

TEAMUP_API_KEY = os.environ["TEAMUP_API_KEY"]
BASE = "https://goteamup.com/api/v1"
HEADERS = {"Authorization": f"Token {TEAMUP_API_KEY}"}


def get_all(endpoint, params=None):
    """Fetch all pages from a paginated endpoint."""
    results = []
    url = f"{BASE}/{endpoint}/"
    p = params or {}
    while url:
        r = requests.get(url, headers=HEADERS, params=p)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        url = data.get("next")
        p = {}  # next URL already has params baked in
    return results


def run():
    # All active customer memberships
    all_memberships = get_all("customermemberships", {"status": "active"})

    total = len(all_memberships)

    # Recurring = memberships with a recurring billing type
    recurring = sum(1 for m in all_memberships if m.get("membership_type") == "recurring"
                    or m.get("is_recurring") is True)

    # Trials
    trial = sum(1 for m in all_memberships if "trial" in str(m.get("membership", "")).lower()
                or m.get("is_trial") is True)

    # Cancellations this month
    month_start = date.today().replace(day=1).isoformat()
    cancelled = get_all("customermemberships", {
        "status": "cancelled",
        "end_date__gte": month_start,
    })
    cancellations = len(cancelled)

    # New customers in last 7 days (weekly leads)
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
