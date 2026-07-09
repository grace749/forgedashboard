"""
Read the Forge "Events Calendar" Google Sheet → a list of events for the
Operations tab. Columns: Event Name, Date & Time, Details, Marketing Materials.

The sheet must be shared (Viewer) with the service-account email (same one as
the KPI/SOP sheets). If it isn't, we return an empty list and the card shows a
"not shared" note.
"""
import os, json

SPREADSHEET_ID = "18EQd_ARn_UuC1eL_lGA8LaLToUtPsBxM_AftHRhGiB4"
SHEET_URL = "https://docs.google.com/spreadsheets/d/18EQd_ARn_UuC1eL_lGA8LaLToUtPsBxM_AftHRhGiB4/edit"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def run():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return {"events": [], "sheet_url": SHEET_URL, "configured": False}
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        vals = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="A1:Z300").execute().get("values", [])
    except Exception as ex:
        print(f"[events] could not read Events Calendar sheet — shared with the service account? {ex}")
        return {"events": [], "sheet_url": SHEET_URL, "configured": True, "error": str(ex)}

    if not vals:
        return {"events": [], "sheet_url": SHEET_URL, "configured": True}

    header = [h.strip().lower() for h in vals[0]]

    def col(row, *keys):
        for key in keys:
            for i, h in enumerate(header):
                if key in h:
                    return row[i].strip() if i < len(row) else ""
        return ""

    events = []
    for row in vals[1:]:
        if not any((c or "").strip() for c in row):
            continue
        name = col(row, "event name", "event") or (row[0].strip() if row else "")
        if not name:
            continue
        events.append({
            "name":      name,
            "when":      col(row, "date"),
            "details":   col(row, "details"),
            "marketing": col(row, "marketing"),
        })
    print(f"[events] {len(events)} events from sheet")
    return {"events": events, "sheet_url": SHEET_URL, "configured": True}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
