"""
Fetch SOP Mission Control — which SOPs are built, grouped by cadence.
Sheet: https://docs.google.com/spreadsheets/d/1CiahjhZohT64jPvds8JWNbSVgJJu1FAQ3Ae1-o-Wq1E
Tab:   "SOP's"

A row is a real SOP task when it has a task name (col A) and a Type (col B).
It counts as BUILT when the SOP column (col C) is filled in.
Section headers (Daily/Weekly/Monthly/Quarterly/Yearly/Coach HQ's) group them.
"""
import os, json
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1CiahjhZohT64jPvds8JWNbSVgJJu1FAQ3Ae1-o-Wq1E"
TARGET_SHEET = "SOP's"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

KNOWN_TYPES = {"facilities", "coaches", "marketing", "clients", "general"}
SECTION_KEYS = ["daily", "weekly", "monthly", "quarterly", "yearly", "coach hq", "for all coaches"]
SKIP_TASKS = {"task", "key operations", "sop", "type", "owner",
              "reocurring tasks & operating procedures"}


def _svc():
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _section_name(cell_a, cell_b, cell_c):
    """If this row is a section header, return its display name, else None."""
    a = cell_a.strip()
    if not a or cell_b.strip() or cell_c.strip():
        return None
    al = a.lower()
    for key in SECTION_KEYS:
        if key in al:
            return a
    return None


def run():
    svc = _svc()
    safe = TARGET_SHEET.replace("'", "''")
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{safe}'!A1:D120",
    ).execute()
    rows = result.get("values", [])

    def cell(row, i):
        return row[i].strip() if i < len(row) else ""

    sections = []
    current = None
    for row in rows:
        a, b, c = cell(row, 0), cell(row, 1), cell(row, 2)
        owner = cell(row, 3)

        sect = _section_name(a, b, c)
        if sect:
            current = {"name": sect, "items": []}
            sections.append(current)
            continue

        # a real SOP task needs a name + a recognised Type
        if not a or a.lower() in SKIP_TASKS:
            continue
        if b.lower() not in KNOWN_TYPES:
            continue

        if current is None:
            current = {"name": "General", "items": []}
            sections.append(current)

        current["items"].append({
            "task":  a,
            "type":  b,
            "sop":   c,
            "owner": owner,
            "built": bool(c),
        })

    sections = [s for s in sections if s["items"]]

    all_items = [it for s in sections for it in s["items"]]
    built = [it for it in all_items if it["built"]]
    by_type = {}
    for it in all_items:
        t = it["type"]
        d = by_type.setdefault(t, {"total": 0, "built": 0})
        d["total"] += 1
        d["built"] += 1 if it["built"] else 0

    return {
        "sections": sections,
        "stats": {
            "total":     len(all_items),
            "built":     len(built),
            "not_built": len(all_items) - len(built),
            "pct_built": round(len(built) / len(all_items) * 100) if all_items else 0,
            "by_type":   [{"type": k, **v} for k, v in sorted(by_type.items())],
        },
        "not_built_list": [it for it in all_items if not it["built"]],
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
