"""
Real revenue + failed charges from Stripe (card payments / one-offs).

Like GoCardless, Stripe only shows genuine customer payments — a clean revenue
source. Returns monthly succeeded revenue and recent failed charges.

Needs STRIPE_ACCESS_TOKEN (a restricted read key, rk_live_…) in the environment.
"""
import os, datetime, requests

BASE = "https://api.stripe.com/v1"


def run():
    key = os.environ.get("STRIPE_ACCESS_TOKEN")
    if not key:
        return {"configured": False}
    auth = (key, "")
    since = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=400)).timestamp())
    try:
        charges, starting_after = [], None
        for _ in range(120):
            params = {"limit": 100, "created[gte]": since}
            if starting_after:
                params["starting_after"] = starting_after
            r = requests.get(f"{BASE}/charges", auth=auth, params=params, timeout=30)
            r.raise_for_status()
            j = r.json()
            batch = j.get("data", [])
            charges.extend(batch)
            if not j.get("has_more") or not batch:
                break
            starting_after = batch[-1]["id"]

        monthly = {}
        for c in charges:
            ts = c.get("created")
            if not ts:
                continue
            ym = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m")
            amt = (c.get("amount") or 0) / 100.0
            b = monthly.setdefault(ym, {"collected": 0.0, "count": 0})
            if c.get("status") == "succeeded" and not c.get("refunded"):
                b["collected"] += amt
                b["count"] += 1
        monthly_list = [{"month": k, "collected": round(v["collected"], 2), "payments": v["count"]}
                        for k, v in sorted(monthly.items())]

        # Recent failed charges (last 45 days)
        cutoff = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)).timestamp())
        fail_list = []
        for c in sorted([c for c in charges if c.get("status") == "failed" and (c.get("created") or 0) >= cutoff],
                        key=lambda x: x.get("created", 0), reverse=True)[:25]:
            bd = c.get("billing_details") or {}
            fail_list.append({
                "name":   bd.get("name") or (c.get("receipt_email") or "A customer"),
                "amount": round((c.get("amount") or 0) / 100.0, 2),
                "date":   datetime.datetime.fromtimestamp(c["created"], datetime.timezone.utc).date().isoformat(),
                "reason": c.get("failure_message") or "",
            })

        failed_count = sum(1 for c in charges if c.get("status") == "failed" and (c.get("created") or 0) >= cutoff)
        return {"configured": True, "source": "Stripe",
                "monthly": monthly_list, "failed": fail_list, "failed_count": failed_count}
    except Exception as ex:
        print(f"[stripe] error: {ex}")
        return {"configured": True, "error": str(ex)}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
