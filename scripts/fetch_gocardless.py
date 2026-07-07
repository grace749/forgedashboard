"""
Real revenue + failed payments from GoCardless (Direct Debit memberships).

GoCardless only ever shows genuine customer collections, so it's a clean revenue
source (unlike raw bank money-in). Returns monthly collected revenue and recent
failed payments (with the member's name) for the failed-payment alert.

Needs GOCARDLESS_ACCESS_TOKEN (read-only) in the environment.
"""
import os, datetime, requests

BASE = "https://api.gocardless.com"
COLLECTED = {"confirmed", "paid_out"}      # money actually taken
FAILED    = {"failed", "charged_back"}


def _headers(token):
    return {"Authorization": f"Bearer {token}",
            "GoCardless-Version": "2015-07-06",
            "Accept": "application/json"}


def _customer_name(H, payment, cache):
    """Resolve a payment → mandate → customer name (cached). GoCardless single-
    resource GETs return the object under its plural key, e.g. {"mandates": {…}}."""
    try:
        mid = (payment.get("links") or {}).get("mandate")
        if not mid:
            return "A member"
        if ("m:" + mid) not in cache:
            m = requests.get(f"{BASE}/mandates/{mid}", headers=H, timeout=20).json()
            cache["m:" + mid] = ((m.get("mandates") or {}).get("links") or {}).get("customer") or ""
        cid = cache["m:" + mid]
        if not cid:
            return "A member"
        if ("c:" + cid) not in cache:
            c = requests.get(f"{BASE}/customers/{cid}", headers=H, timeout=20).json()
            cust = c.get("customers") or {}
            cache["c:" + cid] = (f"{cust.get('given_name','')} {cust.get('family_name','')}".strip()
                                 or cust.get("company_name") or "A member")
        return cache["c:" + cid]
    except Exception:
        return "A member"


def run():
    token = os.environ.get("GOCARDLESS_ACCESS_TOKEN")
    if not token:
        return {"configured": False}
    H = _headers(token)
    since = (datetime.date.today() - datetime.timedelta(days=400)).isoformat() + "T00:00:00Z"
    try:
        payments, after = [], None
        for _ in range(80):
            params = {"limit": 500, "created_at[gte]": since}
            if after:
                params["after"] = after
            r = requests.get(f"{BASE}/payments", headers=H, params=params, timeout=30)
            r.raise_for_status()
            j = r.json()
            payments.extend(j.get("payments", []))
            after = ((j.get("meta") or {}).get("cursors") or {}).get("after")
            if not after:
                break

        monthly = {}
        for p in payments:
            d = (p.get("charge_date") or p.get("created_at") or "")[:7]
            if not d:
                continue
            amt = (p.get("amount") or 0) / 100.0
            b = monthly.setdefault(d, {"collected": 0.0, "count": 0})
            if p.get("status") in COLLECTED:
                b["collected"] += amt
                b["count"] += 1
        monthly_list = [{"month": k, "collected": round(v["collected"], 2), "payments": v["count"]}
                        for k, v in sorted(monthly.items())]

        # Recent failed payments (last 45 days) with member names
        cutoff = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
        failed = [p for p in payments if p.get("status") in FAILED and (p.get("charge_date") or "") >= cutoff]
        cache = {}
        fail_list = [{
            "name":   _customer_name(H, p, cache),
            "amount": round((p.get("amount") or 0) / 100.0, 2),
            "date":   p.get("charge_date"),
            "status": p.get("status"),
        } for p in sorted(failed, key=lambda x: x.get("charge_date", ""), reverse=True)[:25]]

        return {"configured": True, "source": "GoCardless",
                "monthly": monthly_list, "failed": fail_list, "failed_count": len(failed)}
    except Exception as ex:
        print(f"[gocardless] error: {ex}")
        return {"configured": True, "error": str(ex)}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
