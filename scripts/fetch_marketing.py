"""Fetch ad spend, leads and sales from the Costing & Profit Indicator sheet."""
import os, json, re
from datetime import date
import ai
from google.oauth2 import service_account
from googleapiclient.discovery import build

ADS_SYSTEM = (
    "You are a senior paid social advertising expert (Meta/Instagram & Facebook ads) "
    "advising The Forge, a women's-only fitness gym in Belfast that runs lead-gen ads "
    "for its 6-week trial. Analyse the last month's ad performance vs prior months and "
    "give sharp, specific, actionable advice: what the trend in leads / cost-per-lead / "
    "close rate is telling us, creative and targeting angles to test, budget guidance, "
    "and the single biggest lever to pull now. Reference the actual numbers. "
    "Structure as: **Insights** (2-3 bullets on what the data shows) then **Actions** "
    "(3-4 specific things to do). Plain UK English, no preamble, no disclaimers."
)


def _ads_advice(summary, monthly):
    if not summary:
        return ""
    table = " | ".join(
        f"{m['month']}: spend £{m['spend']:.0f}, {m['leads']} leads, "
        f"£{(m['spend']/m['leads']):.0f}/lead, {m['sales']} sales"
        for m in monthly if m.get("leads")
    )
    text = ai.generate(
        ADS_SYSTEM,
        f"The Forge — Meta ads performance.\nLatest month ({summary['period']}): "
        f"£{summary['ad_spend']:.0f} spend, {summary['leads']} leads, "
        f"£{summary['cost_per_lead']:.0f} cost/lead, {summary['sales']} sales "
        f"({summary['close_rate']:.0f}% close), £{summary['income_from_ads']:.0f} income, "
        f"£{summary['profit']:.0f} profit.\nMonth by month: {table}\n\n"
        "As our paid-social expert, what's happening and what should we do next month?",
        max_tokens=500,
    )
    return text or (
        "**Insights**\n"
        "• Lead volume is the thing to watch month-to-month — a rising cost-per-lead "
        "usually means creative fatigue or too-narrow targeting.\n"
        "• Close rate matters as much as lead count: cheap leads that don't convert "
        "waste spend.\n"
        "**Actions**\n"
        "• Refresh 2-3 new ad creatives (real member results, UGC-style video) to beat fatigue.\n"
        "• Test a broader lookalike audience alongside your best-performing interest set.\n"
        "• Hold budget steady and judge on cost-per-trial-started, not raw leads.\n"
        "• Tighten the lead follow-up so more leads become booked trials."
    )

SPREADSHEET_ID = "1eQNAtON9ThPPr-IhwRMT-zfyrS9yRgjXlbUTboslha0"
TARGET_SHEET   = "Costing & Profit Indicator"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "october": 10, "november": 11, "december": 12,
}

# Row indices (0-based) for each metric
ROW_WEEK      = 1   # Week labels
ROW_AD_SPEND  = 6   # Ad Spend
ROW_LEADS     = 7   # Number Of Leads
ROW_SALES     = 14  # Number of Sales (from ads)
ROW_INCOME    = 15  # Total Income (from ads)
ROW_PROFIT    = 17  # Profit/Loss


def parse_currency(val):
    if not val or val in ("-", "#DIV/0!"):
        return 0.0
    return float(re.sub(r"[£,\s]", "", val) or 0)


def parse_int(val):
    if not val or val in ("-", "#DIV/0!"):
        return 0
    try:
        return int(re.sub(r"[,\s]", "", val))
    except ValueError:
        return 0


def week_month(label):
    """Return the month number the week mainly falls in, based on the end date mention."""
    label = label.lower()
    months_found = [MONTH_MAP[m] for m in MONTH_MAP if m in label]
    return months_found[-1] if months_found else None


def run():
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    service = build("sheets", "v4", credentials=creds)

    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{TARGET_SHEET}'!A1:Z50",
    ).execute()

    rows = result.get("values", [])
    if not rows:
        return {}

    # Pad all rows to same length
    max_cols = max(len(r) for r in rows)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]

    week_row   = rows[ROW_WEEK]
    spend_row  = rows[ROW_AD_SPEND]
    leads_row  = rows[ROW_LEADS]
    sales_row  = rows[ROW_SALES]
    income_row = rows[ROW_INCOME]
    profit_row = rows[ROW_PROFIT] if len(rows) > ROW_PROFIT else []

    today = date.today()
    current_month = today.month

    # Group all weeks by month
    month_data = {}  # {month_int: {spend, leads, sales, income, profit}}

    for col in range(1, max_cols - 2):  # skip Total/Average cols at end
        label = week_row[col] if col < len(week_row) else ""
        if not label:
            continue
        m = week_month(label)
        if not m:
            continue

        spend  = parse_currency(spend_row[col]  if col < len(spend_row)  else "")
        leads  = parse_int(leads_row[col]        if col < len(leads_row)  else "")
        sales  = parse_int(sales_row[col]        if col < len(sales_row)  else "")
        income = parse_currency(income_row[col]  if col < len(income_row) else "")
        profit = parse_currency(profit_row[col]  if col < len(profit_row) else "")

        if m not in month_data:
            month_data[m] = {"spend": 0.0, "leads": 0, "sales": 0, "income": 0.0, "profit": 0.0}
        month_data[m]["spend"]  += spend
        month_data[m]["leads"]  += leads
        month_data[m]["sales"]  += sales
        month_data[m]["income"] += income
        month_data[m]["profit"] += profit

    # Build monthly breakdown sorted chronologically
    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    monthly = []
    for m in sorted(month_data.keys()):
        d = month_data[m]
        monthly.append({
            "month":  month_names[m - 1],
            "spend":  round(d["spend"], 2),
            "leads":  d["leads"],
            "sales":  d["sales"],
            "income": round(d["income"], 2),
            "profit": round(d["profit"], 2),
        })

    # Lifetime totals
    lifetime_spend  = sum(d["spend"]  for d in month_data.values())
    lifetime_leads  = sum(d["leads"]  for d in month_data.values())
    lifetime_sales  = sum(d["sales"]  for d in month_data.values())
    lifetime_income = sum(d["income"] for d in month_data.values())
    lifetime_profit = sum(d["profit"] for d in month_data.values())

    # Current month summary (fall back to last available month)
    cur = month_data.get(current_month) or month_data.get(max(month_data.keys()), {})
    period_m = current_month if current_month in month_data else max(month_data.keys())
    period = date(today.year, period_m, 1).strftime("%B %Y")
    if period_m != current_month:
        period += " (latest)"

    cost_per_lead = round(cur["spend"] / cur["leads"], 2) if cur.get("leads") else None
    close_rate    = round(cur["sales"] / cur["leads"] * 100, 1) if cur.get("leads") else None

    summary = {
        "period":           period,
        "ad_spend":         round(cur.get("spend", 0), 2),
        "leads":            cur.get("leads", 0),
        "sales":            cur.get("sales", 0),
        "income_from_ads":  round(cur.get("income", 0), 2),
        "profit":           round(cur.get("profit", 0), 2),
        "cost_per_lead":    cost_per_lead,
        "close_rate":       close_rate,
        "monthly":          monthly,
        "lifetime": {
            "spend":  round(lifetime_spend, 2),
            "leads":  lifetime_leads,
            "sales":  lifetime_sales,
            "income": round(lifetime_income, 2),
            "profit": round(lifetime_profit, 2),
        },
    }
    summary["advice"] = _ads_advice(summary, monthly)
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
