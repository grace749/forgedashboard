"""
Fetch 6 Week Jumpstart tracker data from the Trial Tracker Google Sheet.
Sheet: https://docs.google.com/spreadsheets/d/1aPlFYzsJDu4lLhlQEsxdqQWqErCMSrqhmVUO48Mzpg8

Reads all cohort tabs (skips role guide), finds active + recent members.
Service account needs read access — share the sheet with the SA email.

Column structure (rows vary per cohort, headers detected dynamically):
  Member Name | Date Joined | Due to Finish | Goal | Key Notes |
  Nutrition Guide Sent | InBody Done | InBody 2 Due |
  Wk1 | Wk2 | Wk3 | Wk4 | Wk5 | Wk6 |
  Conversion Chat | Converted | Welcome Pack | Notes/Follow Up
"""
import os, json, re
from datetime import date, datetime, timedelta, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1aPlFYzsJDu4lLhlQEsxdqQWqErCMSrqhmVUO48Mzpg8"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Skip these tabs — not cohort data
SKIP_TABS = {"eilis guide", "role guide", "guide", "instructions", "template", "notes"}

# Weeks lookback to include "recent" (finished but not long ago) members
RECENT_WEEKS = 8


def _sheets_service():
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _parse_date(raw):
    """Parse dates like 4/3/2026, 04/03/2026, 15/05/2026, 2026-04-03."""
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%-d/%-m/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _is_yes(val):
    return str(val).strip().lower() in ("yes", "y", "✓", "x", "done", "sent", "true", "1")


def _is_no(val):
    return str(val).strip().lower() in ("no", "n", "false", "0")


def _get(row, idx, default=""):
    try:
        return row[idx] if idx < len(row) else default
    except Exception:
        return default


def _find_header_row(rows):
    """Find the row index containing 'Member Name' header."""
    for i, row in enumerate(rows):
        for cell in row:
            if "member name" in str(cell).lower():
                return i
    return None


def _col_index(headers, *keywords):
    """Find column index by keyword match (case-insensitive)."""
    for i, h in enumerate(headers):
        hl = h.strip().lower()
        if all(k in hl for k in keywords):
            return i
    return None


def _parse_cohort_tab(svc, sheet_title):
    """Read one cohort tab and return list of member dicts."""
    safe = sheet_title.replace("'", "''")
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{safe}'!A1:T200",
    ).execute()
    rows = result.get("values", [])
    if not rows:
        return []

    header_idx = _find_header_row(rows)
    if header_idx is None:
        return []

    headers = [str(h).strip() for h in rows[header_idx]]

    # Locate columns dynamically
    c_name      = _col_index(headers, "member name") or 0
    c_joined    = _col_index(headers, "date joined")  or 1
    c_end       = _col_index(headers, "finish")        or 2
    c_goal      = _col_index(headers, "goal")          or 3
    c_notes     = _col_index(headers, "key notes")     or 4
    c_nutrition = _col_index(headers, "nutrition")     or 5
    c_inbody1   = _col_index(headers, "inbody") if _col_index(headers, "inbody") else 6
    c_wk1       = _col_index(headers, "wk 1") or _col_index(headers, "week 1") or 8
    c_wk2       = _col_index(headers, "wk 2") or _col_index(headers, "week 2") or 9
    c_wk3       = _col_index(headers, "wk 3") or _col_index(headers, "week 3") or 10
    c_wk4       = _col_index(headers, "wk 4") or _col_index(headers, "week 4") or 11
    c_wk5       = _col_index(headers, "wk 5") or _col_index(headers, "week 5") or 12
    c_wk6       = _col_index(headers, "wk 6") or _col_index(headers, "week 6") or 13
    c_conv_chat = _col_index(headers, "conversion chat") or 14
    c_converted = _col_index(headers, "converted to") or 15
    c_welcome   = _col_index(headers, "welcome pack") or 16
    c_followup  = _col_index(headers, "follow up") or _col_index(headers, "notes") or 17

    members = []
    for row in rows[header_idx + 1:]:
        name = _get(row, c_name).strip()
        if not name or name.lower().startswith("member"):
            continue
        # Skip clearly blank rows
        if len(name) < 2:
            continue

        joined_raw = _get(row, c_joined)
        end_raw    = _get(row, c_end)

        joined = _parse_date(joined_raw)
        end    = _parse_date(end_raw)

        # Calculate end if missing (joined + 42 days)
        if joined and not end:
            end = joined + timedelta(weeks=6)

        today    = date.today()
        is_active = end is not None and end >= today
        days_left = (end - today).days if end else None
        days_in   = (today - joined).days if joined else None
        week_on   = min(6, max(1, (days_in // 7) + 1)) if days_in is not None and days_in >= 0 else None

        # Check-in status
        check_ins = [
            _is_yes(_get(row, c_wk1)),
            _is_yes(_get(row, c_wk2)),
            _is_yes(_get(row, c_wk3)),
            _is_yes(_get(row, c_wk4)),
            _is_yes(_get(row, c_wk5)),
            _is_yes(_get(row, c_wk6)),
        ]
        # How many check-ins should have happened vs did happen
        expected_checkins = week_on if week_on else 0
        completed_checkins = sum(check_ins[:expected_checkins])
        missing_checkins   = expected_checkins - completed_checkins

        converted_val = _get(row, c_converted)
        is_converted  = _is_yes(converted_val)
        not_converted = _is_no(converted_val) or "not converted" in str(converted_val).lower()

        members.append({
            "name":               name,
            "cohort":             sheet_title,
            "joined":             joined.isoformat() if joined else None,
            "end":                end.isoformat() if end else None,
            "goal":               _get(row, c_goal)[:200],
            "notes":              _get(row, c_notes)[:300],
            "nutrition_sent":     _is_yes(_get(row, c_nutrition)),
            "inbody_done":        _is_yes(_get(row, c_inbody1)),
            "check_ins":          check_ins,
            "week_on":            week_on,
            "days_left":          days_left,
            "is_active":          is_active,
            "is_converted":       is_converted,
            "not_converted":      not_converted,
            "conv_chat_done":     _is_yes(_get(row, c_conv_chat)),
            "welcome_pack":       _is_yes(_get(row, c_welcome)),
            "follow_up":          _get(row, c_followup)[:200],
            "missing_checkins":   missing_checkins,
            "completed_checkins": completed_checkins,
            "expected_checkins":  expected_checkins,
        })
    return members


def run():
    svc = _sheets_service()

    # Get all tabs
    meta   = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = meta.get("sheets", [])

    all_members = []
    for sheet in sheets:
        title = sheet["properties"]["title"]
        if title.strip().lower() in SKIP_TABS or any(k in title.lower() for k in ("guide", "template", "eilis")):
            continue
        try:
            members = _parse_cohort_tab(svc, title)
            all_members.extend(members)
        except Exception as ex:
            print(f"[jumpstart] skipping tab '{title}': {ex}")

    today = date.today()
    cutoff = today - timedelta(weeks=RECENT_WEEKS)

    active   = [m for m in all_members if m["is_active"]]
    recent   = [m for m in all_members if not m["is_active"] and m["end"] and m["end"] >= cutoff.isoformat()]
    historic = [m for m in all_members if not m["is_active"] and (not m["end"] or m["end"] < cutoff.isoformat())]

    # ── Alerts for active members ──────────────────────────────
    alerts = []
    for m in active:
        name = m["name"]
        if m["missing_checkins"] and m["missing_checkins"] > 0:
            alerts.append({"member": name, "type": "missing_checkin",
                           "detail": f"Missing {m['missing_checkins']} check-in(s) (on week {m['week_on']})"})
        if not m["nutrition_sent"]:
            alerts.append({"member": name, "type": "nutrition",
                           "detail": "Nutrition guide not sent yet"})
        if m["days_left"] is not None and m["days_left"] <= 14 and not m["conv_chat_done"]:
            alerts.append({"member": name, "type": "conversion",
                           "detail": f"{m['days_left']}d left — conversion chat not scheduled!"})

    # ── Conversion stats across recent + active cohorts ────────
    for_stats = active + recent
    total_complete = [m for m in for_stats if not m["is_active"]]
    converted      = [m for m in total_complete if m["is_converted"]]
    not_converted  = [m for m in total_complete if m["not_converted"]]
    conv_rate      = round(len(converted) / len(total_complete) * 100) if total_complete else None

    return {
        "active":        sorted(active, key=lambda m: m["end"] or ""),
        "recent":        sorted(recent, key=lambda m: m["end"] or "", reverse=True),
        "alerts":        alerts,
        "stats": {
            "active_count":  len(active),
            "conv_rate":     conv_rate,
            "converted":     len(converted),
            "not_converted": len(not_converted),
            "total_complete": len(total_complete),
        },
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
