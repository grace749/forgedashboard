"""Fetch 30-day action plan from the private Google Sheet, with AI strategy advice per task."""
import os, json
import ai
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1Cztbi-zVqFvpZ48q-aAIMBZWSeeQjUM8abHRI98b6iY"
TARGET_SHEET = "30 Action Plan"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

EXCLUDE_COLS = ["risk", "resources", "insert", "(insert"]
KEEP_COLS = ["30 day action plan", "owner", "deadline", "completed", "done"]

SYSTEM_PROMPT = (
    "You are a sharp business growth strategist advising Grace Smith, owner of The Forge — "
    "a women's-only fitness gym in Belfast. Grace is ambitious, results-driven, and wants "
    "practical, specific advice she can act on today. Keep responses to 3-5 punchy bullet points. "
    "No fluff. Focus on the HOW, not the what."
)


def _fallback_advice(action):
    """A practical execution framework used when the AI call is unavailable,
    so clicking a task always shows something useful."""
    return (
        f"• Break it down: list the 3-4 concrete sub-tasks needed to deliver “{action[:80]}”.\n"
        "• Assign an owner and a deadline to each sub-task so nothing stalls.\n"
        "• Define what 'done' looks like — a measurable outcome you can point to.\n"
        "• Do the smallest first step this week to build momentum.\n"
        "• Review progress at your weekly check-in and adjust."
    )


def _generate_advice(action):
    text = ai.generate(
        SYSTEM_PROMPT,
        f"How should Grace execute this specific growth action for The Forge? "
        f"Give concrete, tailored advice for THIS task — not generic project-management "
        f"steps. Reference what the task actually involves:\n\n\"{action}\"",
        max_tokens=380,
    )
    return text or _fallback_advice(action)


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

    header_idx = next(
        (i for i, row in enumerate(rows) if any("owner" in str(c).lower() for c in row)),
        0
    )

    raw_headers = rows[header_idx]

    keep_indices = []
    keep_names = []
    for i, h in enumerate(raw_headers):
        hl = h.strip().lower()
        if any(ex in hl for ex in EXCLUDE_COLS):
            continue
        matched_keep = next((k for k in KEEP_COLS if k in hl), None)
        if matched_keep is not None:
            keep_indices.append(i)
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
        action_val = padded[0].strip() if padded else ""
        if not action_val:
            continue
        owner_val    = padded[keep_indices[1]].strip() if len(keep_indices) > 1 else ""
        deadline_val = padded[keep_indices[2]].strip() if len(keep_indices) > 2 else ""
        if not owner_val and not deadline_val:
            continue
        entry = {keep_names[j]: padded[keep_indices[j]] for j in range(len(keep_indices))}
        entry["advice"] = _generate_advice(action_val)
        kpis.append(entry)

    return {"kpis": kpis}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
