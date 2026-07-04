"""
Fetch SOP Mission Control — which SOPs are built, grouped by cadence.
Sheet: https://docs.google.com/spreadsheets/d/1CiahjhZohT64jPvds8JWNbSVgJJu1FAQ3Ae1-o-Wq1E
Tab:   "SOP's"

A row is a real SOP task when it has a task name (col A) and a Type (col B).
It counts as BUILT when the SOP column (col C) is filled in.
Section headers (Daily/Weekly/Monthly/Quarterly/Yearly/Coach HQ's) group them.
"""
import os, json
import ai
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


COO_SYSTEM = (
    "You are the Fractional COO for The Forge, a women's-only fitness gym in Belfast. "
    "You continuously evaluate the SOPs against how the business actually runs. "
    "Structure your answer under exactly these three headings (use **bold** headings):\n"
    "**Gaps — no SOP yet**: parts of the business with no documented process "
    "(think across onboarding, retention/win-back, coaching standards, finance, "
    "marketing, staffing, complaints/incidents, health & safety).\n"
    "**Needs updating**: existing SOPs likely to be stale or incomplete, and what's "
    "missing from them.\n"
    "**Build next**: the 2-3 highest-leverage SOPs to create now, in priority order, "
    "with a one-line note on how to structure each.\n"
    "Be sharp and specific to a fitness studio. Short bullet points under each heading, "
    "no preamble, no fluff."
)


def _coo_fallback(stats, not_built, sections):
    parts = ["**Gaps — no SOP yet**"]
    parts.append("• Likely undocumented: complaints/incident handling, health & safety, "
                 "member win-back after cancellation, and a data/finance close routine.")
    parts.append("**Needs updating**")
    weakest = min((t for t in stats.get("by_type", [])),
                  key=lambda t: (t["built"] / t["total"]) if t["total"] else 1, default=None)
    if weakest and weakest["built"] < weakest["total"]:
        parts.append(f"• {weakest['type']} has the lowest coverage "
                     f"({weakest['built']}/{weakest['total']}) — review those SOPs for gaps.")
    parts.append("• Add a quarterly review date to each SOP so they don't go stale as the team grows.")
    parts.append("**Build next**")
    if not_built:
        parts.append("• Finish the started-but-unbuilt SOPs: " +
                     ", ".join(it["task"] for it in not_built) + ".")
    parts.append("• Map SOPs to the member journey (enquiry → trial → onboarding → retention → "
                 "win-back) and make sure each stage has one clear owner.")
    return "\n".join(parts)


def _coo_evaluation(stats, not_built, sections):
    """Fractional COO review of SOP coverage — AI with a rule-based fallback."""
    by_area = ", ".join(f"{t['type']} {t['built']}/{t['total']}"
                        for t in stats.get("by_type", []))
    not_built_names = ", ".join(it["task"] for it in not_built) or "none"
    section_names = ", ".join(s["name"] for s in sections)
    summary = (
        f"SOPs built: {stats['built']} of {stats['total']} ({stats['pct_built']}%). "
        f"By area: {by_area}. Not built yet: {not_built_names}. Sections: {section_names}."
    )
    text = ai.generate(
        COO_SYSTEM,
        f"Here's our current SOP status:\n{summary}\n\n"
        "As our Fractional COO, what processes/SOPs should we organise or build next, "
        "and how should we structure them?",
        max_tokens=420,
    )
    return text or _coo_fallback(stats, not_built, sections)


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

    stats = {
        "total":     len(all_items),
        "built":     len(built),
        "not_built": len(all_items) - len(built),
        "pct_built": round(len(built) / len(all_items) * 100) if all_items else 0,
        "by_type":   [{"type": k, **v} for k, v in sorted(by_type.items())],
    }
    not_built_list = [it for it in all_items if not it["built"]]

    return {
        "sections": sections,
        "stats": stats,
        "not_built_list": not_built_list,
        "coo_evaluation": _coo_evaluation(stats, not_built_list, sections),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
