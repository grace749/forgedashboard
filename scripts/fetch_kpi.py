"""
Fetch the monthly KPI dashboard (Starling-sourced) from Grace's KPI sheet.
Sheet: https://docs.google.com/spreadsheets/d/1hnmdTnecyLu3WQBynvRgGxor1JnEGyJG9_7O4czWC90
Tab:   "KPI Revenue"

Layout: CAPTAIN | CATEGORY | MEASURABLES | YTD/AVG | GOAL | <month cols…>
We read each measurable's goal and its latest month value, grouped by category.
Updated monthly, so "last updated" = the sheet's Drive modified time.
"""
import os, json, datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

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

    groups = {}
    for row in rows[hidx + 1:]:
        meas = cell(row, c_meas)
        if not meas:
            continue
        cat = cell(row, c_cat) or "Other"
        val = cell(row, cur_col)
        goal = cell(row, c_goal)
        if not val and not goal:
            continue
        groups.setdefault(cat, []).append({
            "name": meas.replace("\n", " ").strip(),
            "value": val,
            "goal": goal,
        })

    return {
        "period": period,
        "last_updated": _last_updated(creds),
        "categories": [{"name": k, "items": v} for k, v in groups.items()],
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
