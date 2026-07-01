"""Fetch 30-day action plan from the private Google Sheet."""
import os, json
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1Cztbi-zVqFvpZ48q-aAIMBZWSeeQjUM8abHRI98b6iY"
TARGET_SHEET = "30 Action Plan"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Columns to exclude (case-insensitive substring match)
EXCLUDE_COLS = ["risk"]

# Only keep these columns (in this order) — must match header names in sheet
KEEP_COLS = ["30 day action plan", "owner", "deadline", "completed"]


def run():
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    service = build("sheets", "v4", credentials=creds)

    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = {s["properties"]["title"]: s["properties"]["sheetId"]
              for s in meta.get("sheets", [])}

    matched = next((t for t in sheets if t.strip().lower() == TARGET_SHEET.lower()), None)
    if matched is None:
        raise ValueError(f"Sheet '{TARGET_SHEET}' not found. Available: {list(sheets.keys())}")

    safe_title = matched.replace("'", "''")
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{safe_title}'!A1:Z200",
    ).execute()

    rows = result.get("values", [])
    if not rows:
        return {"kpis": []}

    # Find header row (contains "Owner")
    header_idx = next(
        (i for i, row in enumerate(rows) if any("owner" in str(c).lower() for c in row)),
        0
    )

    raw_headers = rows[header_idx]

    # Determine which column indices to keep
    keep_indices = []
    keep_names = []
    for i, h in enumerate(raw_headers):
        hl = h.strip().lower()
        if any(ex in hl for ex in EXCLUDE_COLS):
            continue
        matched_keep = next((k for k in KEEP_COLS if k in hl), None)
        if matched_keep is not None:
            keep_indices.append(i)
            # Use a clean display name
            display = {
                "30 day action plan": "Action",
                "owner": "Owner",
                "deadline": "Deadline",
                "completed": "Done",
            }.get(matched_keep, h.strip())
            keep_names.append(display)

    kpis = []
    for row in rows[header_idx + 1:]:
        padded = row + [""] * (max(keep_indices, default=0) + 1 - len(row))
        # Only include rows that have an actual action item (col 0 has text)
        action_val = padded[0].strip() if padded else ""
        if not action_val:
            continue
        # Skip section header rows (no owner and no deadline)
        owner_val = padded[keep_indices[1]].strip() if len(keep_indices) > 1 else ""
        deadline_val = padded[keep_indices[2]].strip() if len(keep_indices) > 2 else ""
        if not owner_val and not deadline_val:
            continue
        entry = {keep_names[j]: padded[keep_indices[j]] for j in range(len(keep_indices))}
        kpis.append(entry)

    return {"kpis": kpis}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
