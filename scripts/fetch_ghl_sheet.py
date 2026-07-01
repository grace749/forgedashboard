"""
Read GHL conversations from Google Sheet (populated by Zapier).

Sheet: "GHL Conversations" in the existing Forge spreadsheet.
Zapier writes one row per new inbound conversation/message with columns:
  Timestamp | Contact Name | Channel | Message | Conversation ID | Status

Shows conversations from last 72h where status is not "Replied".
"""
import os, json
from datetime import datetime, timezone, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1Cztbi-zVqFvpZ48q-aAIMBZWSeeQjUM8abHRI98b6iY"
SHEET_NAME     = "GHL Conversations"
SCOPES         = ["https://www.googleapis.com/auth/spreadsheets"]


def run():
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds   = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    service = build("sheets", "v4", credentials=creds)

    # Check sheet exists
    meta   = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = [s["properties"]["title"] for s in meta.get("sheets", [])]

    if SHEET_NAME not in sheets:
        return {"error": f"Sheet '{SHEET_NAME}' not found — create it and set up Zapier to write to it"}

    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1:F500",
    ).execute()

    rows = result.get("values", [])
    if len(rows) < 2:
        return []

    headers = [h.strip().lower() for h in rows[0]]

    def col(row, name):
        try:
            return row[headers.index(name)] if name in headers else ""
        except IndexError:
            return ""

    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    convos = []

    for row in rows[1:]:
        if not row:
            continue

        # Parse timestamp
        ts_raw = col(row, "timestamp")
        age_label = ""
        try:
            # Zapier typically writes ISO or MM/DD/YYYY HH:MM:SS
            for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                        "%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                        "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(ts_raw, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            else:
                dt = None

            if dt and dt < cutoff:
                continue
            if dt:
                hours = int((datetime.now(timezone.utc) - dt).total_seconds() / 3600)
                age_label = f"{hours}h ago" if hours < 24 else f"{hours // 24}d ago"
        except Exception:
            pass

        status = col(row, "status").strip().lower()
        if status in ("replied", "done", "closed"):
            continue

        name    = col(row, "contact name") or col(row, "name") or "Unknown"
        channel = col(row, "channel") or "WhatsApp"
        message = col(row, "message") or col(row, "last message") or ""
        conv_id = col(row, "conversation id") or col(row, "id") or ""

        convos.append({
            "name":    name,
            "channel": channel,
            "message": message[:200],
            "age":     age_label,
            "conv_id": conv_id,
            "unread":  0,
        })

    # Most recent first
    return list(reversed(convos))


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
