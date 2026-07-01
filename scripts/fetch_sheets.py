"""Fetch 90-day growth sprint KPIs from the private Google Sheet."""
import os, json
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1Cztbi-zVqFvpZ48q-aAIMBZWSeeQjUM8abHRI98b6iY"
TARGET_SHEET = "30 Action Plan"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def run():
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    service = build("sheets", "v4", credentials=creds)

    # Get all sheet titles from metadata so we can find the exact name
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = {s["properties"]["title"]: s["properties"]["sheetId"]
              for s in meta.get("sheets", [])}

    print(f"Available sheets: {list(sheets.keys())}")

    # Match case-insensitively
    matched = next((t for t in sheets if t.strip().lower() == TARGET_SHEET.lower()), None)
    if matched is None:
        raise ValueError(f"Sheet '{TARGET_SHEET}' not found. Available: {list(sheets.keys())}")

    # Use the exact title as returned by the API, wrapped in single quotes
    safe_title = matched.replace("'", "''")
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{safe_title}'!A1:Z200",
    ).execute()

    rows = result.get("values", [])
    if not rows:
        return {"kpis": []}

    # Find the header row (contains "Owner")
    header_idx = next(
        (i for i, row in enumerate(rows) if any("owner" in str(c).lower() for c in row)),
        0
    )

    headers = rows[header_idx]
    kpis = []
    for row in rows[header_idx + 1:]:
        padded = row + [""] * (len(headers) - len(row))
        entry = dict(zip(headers, padded))
        if any(v.strip() for v in entry.values()):
            kpis.append(entry)

    return {"kpis": kpis}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
