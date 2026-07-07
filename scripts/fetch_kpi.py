"""
Fetch the monthly KPI dashboard (Starling-sourced) from Grace's KPI sheet.
Sheet: https://docs.google.com/spreadsheets/d/1hnmdTnecyLu3WQBynvRgGxor1JnEGyJG9_7O4czWC90
Tab:   "KPI Revenue"

Layout: CAPTAIN | CATEGORY | MEASURABLES | YTD/AVG | GOAL | <month cols…>
We read each measurable's goal and its latest month value, grouped by category.
Updated monthly, so "last updated" = the sheet's Drive modified time.
"""
import os, json, datetime
import ai
from google.oauth2 import service_account
from googleapiclient.discovery import build

FINANCE_SYSTEM = (
    "You are the Fractional CFO for The Forge, a women's-only fitness gym in Belfast "
    "run as a UK limited company. Your job is to grow profit month on month. Give "
    "specific, actionable advice across three areas, using the actual figures: "
    "(1) INCREASE REVENUE — pricing, upsells, retention, filling quiet classes, new "
    "revenue lines; (2) REDUCE EXPENSES — where costs look high vs revenue, supplier/"
    "staffing efficiency; (3) IMPROVE PROFITABILITY & TAX — margin targets, corporation "
    "tax, director salary/dividend split, allowable expenses, VAT threshold. "
    "Reference the numbers you're given (call out what's off-target). 5-7 short bullet "
    "points grouped by those three areas, plain UK English, no preamble, no disclaimers."
)


KEY_METRICS = ["Revenue", "Total Profit", "Profit %", "Expenses - People",
               "Expenses - Non-People", "Active Clients", "Attrition (Churn) %", "AR/M"]


def _metric(categories, name):
    for cat in categories:
        for it in cat["items"]:
            if (it.get("name") or "").lower().startswith(name.lower()):
                return it.get("value", "")
    return ""


def _finance_advice(period, months):
    """Fractional CFO — data-driven advice from month/quarter/year trends."""
    if not months:
        return _finance_fallback()

    # Build a compact month-by-month table of the key metrics (oldest→newest)
    series = list(reversed(months))   # months is newest-first
    header = "Month | " + " | ".join(KEY_METRICS)
    rows = [header]
    for m in series:
        vals = [_metric(m["categories"], k) or "—" for k in KEY_METRICS]
        rows.append(f"{m['period']} | " + " | ".join(vals))
    table = "\n".join(rows)

    latest = months[0]["period"]
    prev   = months[1]["period"] if len(months) > 1 else None
    q_note = ("Compare the most recent 3 months (a quarter) against the 3 before it. "
              if len(months) >= 6 else "")
    yoy_note = ("Where the same month a year earlier is present, comment on the "
                "year-on-year change. " if len(months) >= 12 else "")

    text = ai.generate(
        FINANCE_SYSTEM,
        f"The Forge — monthly KPIs (goals are in the KPI Tracking sheet):\n{table}\n\n"
        f"Latest complete month is {latest}"
        + (f", compare it to {prev} (month-on-month). " if prev else ". ")
        + q_note + yoy_note +
        "IMPORTANT CONTEXT: April 2026 was a STUDIO RELOCATION month — the gym moved "
        "to a new studio roughly twice the size. The large one-off fit-out cost was a "
        "PLANNED, SELF-FUNDED investment paid from savings — NOT a real trading loss or "
        "cash-flow problem. The business still holds ~£12k in cash reserves in a "
        "separate account, so it is financially healthy. Treat April purely as a "
        "successful launch of the bigger space: exclude it from expense/profit trend "
        "judgements, do NOT frame it as a loss or recommend cost-cutting because of it. "
        "The bigger studio should now support more members/revenue — factor that in.\n"
        "As our Fractional CFO, give specific, numbers-referenced moves to increase "
        "revenue, reduce expenses and improve profitability. Call out trends "
        "(improving/declining), what's off-target, and the single biggest priority.",
        max_tokens=600,
    )
    if text:
        return text
    return _finance_fallback()


def finance_advice_live(gocardless, stripe, starling, teamup):
    """Fractional CFO advice from LIVE sources — real revenue (GoCardless+Stripe),
    bank cash flow (Starling), members/churn (TeamUp) — replacing the manual sheet."""
    gc = (gocardless or {}).get("monthly") or []
    sp = (stripe or {}).get("monthly") or []
    stg = (starling or {}).get("monthly") or []
    hist = (teamup or {}).get("member_history") or []
    if not (gc or sp or stg):
        return ""

    rev = {}
    for m in gc: rev[m["month"]] = rev.get(m["month"], 0) + (m.get("collected") or 0)
    for m in sp: rev[m["month"]] = rev.get(m["month"], 0) + (m.get("collected") or 0)
    cash = {m["month"]: m for m in stg}
    mem  = {h["month"]: h for h in hist}

    import datetime as _dt
    this_month = _dt.date.today().strftime("%Y-%m")
    months = sorted({*rev, *cash, *mem})
    months = [mo for mo in months if mo != this_month][-8:]   # completed months, last 8

    rows = ["Month | Revenue(memberships+cards) | Cash in(bank) | Cash out(bank) | Net cash | Active | Churn% | Rev/member"]
    for mo in months:
        r = rev.get(mo)
        c = cash.get(mo, {})
        h = mem.get(mo, {})
        active = h.get("active")
        arm = round(r / active) if (r and active) else None
        rows.append(" | ".join([
            mo,
            f"£{round(r):,}" if r is not None else "—",
            f"£{round(c['revenue']):,}" if c.get("revenue") is not None else "—",
            f"£{round(c['expenses']):,}" if c.get("expenses") is not None else "—",
            f"£{round(c['profit']):,}" if c.get("profit") is not None else "—",
            str(active) if active is not None else "—",
            f"{h['churn']}%" if h.get("churn") is not None else "—",
            f"£{arm}" if arm is not None else "—",
        ]))
    table = "\n".join(rows)

    text = ai.generate(
        FINANCE_SYSTEM + "  You are reasoning over LIVE real-time financial data.",
        "The Forge — LIVE monthly finances (revenue from GoCardless + Stripe = real "
        "customer collections; cash in/out from the Starling business account; active "
        "members & churn from TeamUp):\n" + table + "\n\n"
        "Note: 'Cash in (bank)' includes non-trading transfers so it runs higher than "
        "real Revenue — judge trading performance on the Revenue column, and cash health "
        "on Net cash. April 2026 was a PLANNED studio relocation funded from savings — a "
        "successful launch of a bigger space, NOT a trading loss; exclude it from trend "
        "judgements and don't recommend cost-cutting because of it.\n"
        "As our Fractional CFO, compare the most recent months month-on-month (and the "
        "last 3 vs the prior 3), and give specific, numbers-referenced moves to grow "
        "revenue, reduce expenses and improve profitability. Call out trends, what's "
        "off-track, and the single biggest priority now.",
        max_tokens=600,
    )
    return text or _finance_fallback()


def _finance_fallback():
    return ("**Grow revenue**\n"
            "• Fill your quietest class slots (see Members → Class Popularity) — a promo or "
            "format change there is revenue you're already paying the coach for.\n"
            "• Review pricing vs your AR/M; even a small rise across recurring members compounds.\n"
            "**Reduce expenses**\n"
            "• Track non-people expenses against the 20-35% target — anything above needs a reason.\n"
            "• Renegotiate or cut the lowest-ROI subscriptions/suppliers this month.\n"
            "**Improve profitability & tax**\n"
            "• Set aside ~19-25% of profit for corporation tax so it isn't a year-end shock.\n"
            "• Review the director salary/dividend split with your accountant for tax efficiency.\n"
            "• Watch the rolling 12-month turnover against the £90k VAT threshold.")

SPREADSHEET_ID = "1hnmdTnecyLu3WQBynvRgGxor1JnEGyJG9_7O4czWC90"
TARGET_SHEET = "KPI Revenue"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]


def _creds():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def _last_updated(creds):
    try:
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        meta = drive.files().get(fileId=SPREADSHEET_ID, fields="modifiedTime").execute()
        return (meta.get("modifiedTime") or "")[:10]   # YYYY-MM-DD
    except Exception as ex:
        print(f"[kpi] modifiedTime lookup failed: {ex}")
        return None


def run():
    creds = _creds()
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    # Month columns are far to the right (merged blocks around AL–AU), so read wide
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"'{TARGET_SHEET}'!A1:CZ60").execute()
    rows = result.get("values", [])
    if not rows:
        return {"error": "empty sheet"}

    # header row contains "MEASURABLES"
    hidx = next((i for i, r in enumerate(rows)
                 if any("measurable" in str(c).lower() for c in r)), None)
    if hidx is None:
        return {"error": "header row not found"}
    headers = rows[hidx]

    c_cat, c_meas = 1, 2
    c_goal = next((i for i, h in enumerate(headers) if "goal" in str(h).lower()), 4)
    data_rows = rows[hidx + 1:]

    def real_count(ci):
        """Count cells with genuine values (ignore blanks and #DIV/0!/#REF! errors)."""
        n = 0
        for r in data_rows:
            v = str(r[ci]).strip() if ci < len(r) else ""
            if v and not v.startswith("#"):
                n += 1
        return n

    hdr_cols = [i for i in range(c_goal + 1, len(headers)) if str(headers[i]).strip()]
    max_real = max((real_count(i) for i in hdr_cols), default=0)
    # Latest COMPLETE month = right-most populated column that ISN'T the current
    # calendar month (which is still in progress) or a future/formula-only column.
    this_month = datetime.date.today().strftime("%B").lower()
    month_cols = [i for i in hdr_cols
                  if real_count(i) >= max(3, max_real * 0.5)
                  and this_month not in str(headers[i]).lower()]
    cur_col = month_cols[-1] if month_cols else (hdr_cols[-1] if hdr_cols else c_goal + 1)
    period = str(headers[cur_col]).strip() if cur_col < len(headers) else ""

    def cell(row, i):
        return row[i].strip() if i < len(row) and row[i] is not None else ""

    def build_categories(month_col):
        groups = {}
        for row in data_rows:
            meas = cell(row, c_meas)
            if not meas:
                continue
            cat = cell(row, c_cat) or "Other"
            val = cell(row, month_col)
            goal = cell(row, c_goal)
            if not val and not goal:
                continue
            groups.setdefault(cat, []).append({
                "name": meas.replace("\n", " ").strip(),
                "value": val,
                "goal": goal,
            })
        return [{"name": k, "items": v} for k, v in groups.items()]

    # One entry per complete month (most recent first)
    months = [{"period": str(headers[i]).strip(), "categories": build_categories(i)}
              for i in reversed(month_cols)]

    return {
        "period": period,
        "last_updated": _last_updated(creds),
        "categories": build_categories(cur_col),   # latest month (back-compat)
        "months": months,
        "advice": _finance_advice(period, months),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
