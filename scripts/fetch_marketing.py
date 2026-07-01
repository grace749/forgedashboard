"""Fetch ad spend, leads and sales from the Costing & Profit Indicator sheet."""
import os, json, re
from datetime import date
from google.oauth2 import service_account
from googleapiclient.discovery import build

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

    today = date.today()
    current_month = today.month

    # Data starts at column index 1 (col B)
    month_spend = 0.0
    month_leads = 0
    month_sales = 0
    month_income = 0.0
    latest_week_label = None
    latest_spend = None
    latest_leads = None
    latest_sales = None
    weeks_found = 0

    for col in range(1, max_cols - 2):  # skip Total/Average cols at end
        label = week_row[col] if col < len(week_row) else ""
        if not label:
            continue
        m = week_month(label)
        if m != current_month:
            continue

        spend  = parse_currency(spend_row[col]  if col < len(spend_row)  else "")
        leads  = parse_int(leads_row[col]        if col < len(leads_row)  else "")
        sales  = parse_int(sales_row[col]        if col < len(sales_row)  else "")
        income = parse_currency(income_row[col]  if col < len(income_row) else "")

        month_spend  += spend
        month_leads  += leads
        month_sales  += sales
        month_income += income
        weeks_found  += 1

        # Track most recent populated week
        if spend > 0 or leads > 0 or sales > 0:
            latest_week_label = label.strip()
            latest_spend  = spend
            latest_leads  = leads
            latest_sales  = sales

    # If no data for current month yet, fall back to previous month
    if weeks_found == 0:
        prev_month = current_month - 1 if current_month > 1 else 12
        for col in range(1, max_cols - 2):
            label = week_row[col] if col < len(week_row) else ""
            if not label:
                continue
            m = week_month(label)
            if m != prev_month:
                continue
            spend  = parse_currency(spend_row[col]  if col < len(spend_row)  else "")
            leads  = parse_int(leads_row[col]        if col < len(leads_row)  else "")
            sales  = parse_int(sales_row[col]        if col < len(sales_row)  else "")
            income = parse_currency(income_row[col]  if col < len(income_row) else "")
            month_spend  += spend
            month_leads  += leads
            month_sales  += sales
            month_income += income
            if spend > 0 or leads > 0 or sales > 0:
                latest_week_label = label.strip()
                latest_spend = spend
                latest_leads = leads
                latest_sales = sales

        period = date(today.year, prev_month, 1).strftime("%B %Y") + " (prev)"
    else:
        period = today.strftime("%B %Y") + " (MTD)"

    cost_per_lead = round(month_spend / month_leads, 2) if month_leads else None
    close_rate    = round(month_sales / month_leads * 100, 1) if month_leads else None

    return {
        "period":           period,
        "ad_spend":         round(month_spend, 2),
        "leads":            month_leads,
        "sales":            month_sales,
        "income_from_ads":  round(month_income, 2),
        "cost_per_lead":    cost_per_lead,
        "close_rate":       close_rate,
        "latest_week":      latest_week_label,
        "latest_spend":     latest_spend,
        "latest_leads":     latest_leads,
        "latest_sales":     latest_sales,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
