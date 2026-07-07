"""
Live cash position from Starling (the business current account).

Far fresher than Xero (which only reconciles on the accountant's ~6-week cycle):
this is the real balance, the money earmarked in Spaces/pots (VAT, tax, bonuses),
committed monthly outgoings (standing orders), and — if the token has
transaction:read — actual cash in/out over the last 30 days.

Needs a Starling Personal Access Token in STARLING_ACCESS_TOKEN with scopes:
account:read, balance:read, savings-goal:read, standing-order:read
(transaction:read is optional but powers the real net-cash figure).
"""
import os, datetime, requests

BASE = "https://api.starlingbank.com"

# Feed sources that are NOT trading income/expense — money moved between the
# business's own accounts/savings pots. Excluded from cash-in/out figures so a
# transfer from savings (e.g. funding the April studio move) isn't counted as
# revenue. NOTE: transfers in from a personal/other account still can't be told
# apart from customer income here — true revenue comes from GoCardless/Stripe.
_NON_TRADING_SOURCES = {"INTERNAL_TRANSFER"}


def _get(session, path, params=None):
    r = session.get(f"{BASE}{path}", params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def _pounds(money):
    """Starling amounts are {minorUnits, currency}; return pounds as float."""
    if not isinstance(money, dict):
        return 0.0
    return round((money.get("minorUnits") or 0) / 100.0, 2)


def _monthly_equivalent(so):
    """Normalise a standing order to a monthly £ figure by its recurrence."""
    amt = _pounds(so.get("amount"))
    rec = so.get("standingOrderRecurrence") or {}
    freq = (rec.get("frequency") or "MONTHLY").upper()
    interval = rec.get("interval") or 1
    if freq == "WEEKLY":
        return amt * 52 / 12 / interval
    if freq == "YEARLY":
        return amt / 12 / interval
    return amt / interval   # MONTHLY (or unknown → treat as monthly)


def _month_starts(months):
    """Return [(YYYY-MM, start_dt, end_dt), …] for the last `months` calendar months."""
    now = datetime.datetime.now(datetime.timezone.utc)
    y, m = now.year, now.month
    out = []
    for _ in range(months):
        start = datetime.datetime(y, m, 1, tzinfo=datetime.timezone.utc)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        end = datetime.datetime(ny, nm, 1, tzinfo=datetime.timezone.utc)
        out.append((f"{y:04d}-{m:02d}", start, min(end, now)))
        m -= 1
        if m == 0:
            m = 12; y -= 1
    return list(reversed(out))   # oldest → newest


def _pretty_cat(cat):
    """Starling spending categories are SHOUTY_SNAKE; make them readable."""
    name = (cat or "GENERAL").replace("_", " ").title()
    return {"Diy": "DIY", "Tv": "TV", "Atm": "ATM"}.get(name, name)


# Months (newest first) that carry a full per-transaction list in data.json so
# the dashboard can drill into every category and re-categorise. Older months
# keep just the category totals to hold file size down.
_TX_DETAIL_MONTHS = 7


def _monthly_series(session, acc_uid, cat_uid, months=13):
    """Monthly revenue (money IN), expenses (money OUT), profit, a full
    per-category breakdown, and (recent months) every transaction from the
    Starling feed. One query per month keeps ranges small/reliable. Needs
    transaction:read; months whose query fails (e.g. history beyond the
    token's reach) are skipped rather than sinking the whole series."""
    series = []
    month_list = _month_starts(months)
    detail_from = len(month_list) - _TX_DETAIL_MONTHS   # last N months get tx lists
    for idx, (label, start, end) in enumerate(month_list):
        frm = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        to  = end.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        try:
            feed = _get(session, f"/api/v2/feed/account/{acc_uid}/category/{cat_uid}/transactions-between",
                        {"minTransactionTimestamp": frm, "maxTransactionTimestamp": to})
        except Exception as ex:
            print(f"[starling] monthly feed unavailable for {label}: {ex}")
            continue
        ins = outs = 0.0
        cats_in, cats_out = {}, {}
        txs = []
        for it in feed.get("feedItems", []):
            if it.get("status") == "DECLINED":
                continue
            internal = it.get("source") in _NON_TRADING_SOURCES   # own-money moves (Spaces etc.)
            amt = _pounds(it.get("amount"))
            cat = _pretty_cat(it.get("spendingCategory"))
            if idx >= detail_from:
                txs.append({
                    "uid":       it.get("feedItemUid"),
                    "date":      (it.get("transactionTime") or "")[:10],
                    "name":      it.get("counterPartyName") or it.get("reference") or "—",
                    "ref":       (it.get("reference") or "")[:60],
                    "amount":    amt,
                    "direction": it.get("direction"),
                    "category":  cat,
                    "internal":  internal,   # excluded from totals (savings/own-account transfer)
                })
            if internal:
                continue
            if it.get("direction") == "IN":
                ins += amt
                cats_in[cat] = cats_in.get(cat, 0.0) + amt
            elif it.get("direction") == "OUT":
                outs += amt
                cats_out[cat] = cats_out.get(cat, 0.0) + amt
        rev, exp = round(ins, 2), round(outs, 2)
        profit = round(rev - exp, 2)
        entry = {
            "month":      label,
            "revenue":    rev,
            "expenses":   exp,
            "profit":     profit,
            "profit_pct": round(profit / rev * 100, 1) if rev else None,
            "categories": {
                "in":  sorted(([{"name": k, "amount": round(v, 2)} for k, v in cats_in.items()]),  key=lambda x: -x["amount"]),
                "out": sorted(([{"name": k, "amount": round(v, 2)} for k, v in cats_out.items()]), key=lambda x: -x["amount"]),
            },
        }
        if idx >= detail_from:
            entry["transactions"] = sorted(txs, key=lambda t: t["date"], reverse=True)
        series.append(entry)
    return series


def run():
    token = os.environ.get("STARLING_ACCESS_TOKEN")
    if not token:
        return {"configured": False}

    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    try:
        accounts = _get(s, "/api/v2/accounts").get("accounts", [])
        if not accounts:
            return {"configured": True, "error": "no accounts"}
        acc = accounts[0]
        acc_uid = acc["accountUid"]
        cat_uid = acc.get("defaultCategory")

        bal = _get(s, f"/api/v2/accounts/{acc_uid}/balance")
        cash      = _pounds(bal.get("effectiveBalance"))
        available = _pounds(bal.get("availableToSpend"))

        # Reserves / pots (VAT, corp tax, bonuses …) — earmarked money
        reserves = []
        try:
            goals = _get(s, f"/api/v2/account/{acc_uid}/savings-goals").get("savingsGoalList", [])
            for g in goals:
                reserves.append({"name": g.get("name", "Pot"),
                                 "amount": _pounds(g.get("totalSaved"))})
        except Exception as ex:
            print(f"[starling] savings-goals error: {ex}")
        reserves.sort(key=lambda x: -x["amount"])
        earmarked = round(sum(r["amount"] for r in reserves), 2)

        # Committed monthly outgoings (standing orders)
        committed_monthly = 0.0
        standing = []
        try:
            so = _get(s, f"/api/v2/payments/local/account/{acc_uid}/category/{cat_uid}/standing-orders")
            for o in so.get("standingOrders", []):
                m = _monthly_equivalent(o)
                committed_monthly += m
                standing.append({"reference": o.get("reference", ""),
                                 "monthly": round(m, 2)})
        except Exception as ex:
            print(f"[starling] standing-orders error: {ex}")
        standing.sort(key=lambda x: -x["monthly"])

        # Actual cash in/out over last 30 days (needs transaction:read)
        cash_in_30 = cash_out_30 = None
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            frm = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            to  = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            feed = _get(s, f"/api/v2/feed/account/{acc_uid}/category/{cat_uid}/transactions-between",
                        {"minTransactionTimestamp": frm, "maxTransactionTimestamp": to})
            ins = outs = 0.0
            for it in feed.get("feedItems", []):
                if it.get("status") == "DECLINED":
                    continue
                if it.get("source") in _NON_TRADING_SOURCES:
                    continue
                amt = _pounds(it.get("amount"))
                if it.get("direction") == "IN":
                    ins += amt
                elif it.get("direction") == "OUT":
                    outs += amt
            cash_in_30, cash_out_30 = round(ins, 2), round(outs, 2)
        except Exception as ex:
            print(f"[starling] transaction feed unavailable (scope?): {ex}")

        net_30 = round(cash_in_30 - cash_out_30, 2) if cash_in_30 is not None else None

        # Monthly revenue/expenses/profit history (replaces the manual KPI sheet)
        monthly = _monthly_series(s, acc_uid, cat_uid) if cash_in_30 is not None else []

        # In Starling, money in Spaces/pots is held SEPARATELY from the main
        # balance, so working cash = main balance, and total = main + pots.
        return {
            "configured":        True,
            "monthly":           monthly,
            "account_name":      acc.get("name", "Main"),
            "cash_position":     cash,          # working cash (main balance, excl. pots)
            "available":         available,
            "reserves":          reserves,      # pots (earmarked for tax/VAT/bonuses)
            "earmarked":         earmarked,
            "total_cash":        round(cash + earmarked, 2),
            "committed_monthly": round(committed_monthly, 2),
            "standing_orders":   standing,
            "cash_in_30":        cash_in_30,
            "cash_out_30":       cash_out_30,
            "net_30":            net_30,
            "as_of":             datetime.date.today().isoformat(),
        }
    except Exception as ex:
        print(f"[starling] error: {ex}")
        return {"configured": True, "error": str(ex)}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
