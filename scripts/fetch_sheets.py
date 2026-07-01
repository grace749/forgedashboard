"""Fetch 90-day growth sprint KPIs from the private Google Sheet."""
import os, json
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1Cztbi-zVqFvpZ48q-aAIMBZWSeeQjUM8abHRI98b6iY"
RANGE = "'30 Action Plan'!A1:Z200"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def run():
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    creds_info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    service = build("sheets", "v4", credentials=creds)

    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=RANGE,
    ).execute()

    rows = result.get("values", [])
    if not rows:
        return {"kpis": []}

    # Assume row 0 = headers, subsequent rows = data
    headers = rows[0]
    kpis = []
    for row in rows[1:]:
        padded = row + [""] * (len(headers) - len(row))
        kpis.append(dict(zip(headers, padded)))

    return {"kpis": kpis}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
