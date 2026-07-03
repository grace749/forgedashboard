"""
Fetch the monthly KPI dashboard (Starling-sourced) from Grace's KPI sheet.
Sheet: https://docs.google.com/spreadsheets/d/1hnmdTnecyLu3WQBynvRgGxor1JnEGyJG9_7O4czWC90
Tab:   "KPI Revenue"

Layout: CAPTAIN | CATEGORY | MEASURABLES | YTD/AVG | GOAL | <month cols…>
We read each measurable's goal and its latest month value, grouped by category.
Updated monthly, so "last updated" = the sheet's Drive modified time.
"""
import os, json
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
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"'{TARGET_SHEET}'!A1:Z60").execute()
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
    # month columns are everything after GOAL that has a header
    month_cols = [i for i in range(c_goal + 1, len(headers)) if str(headers[i]).strip()]
    cur_col = month_cols[-1] if month_cols else c_goal + 1
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
