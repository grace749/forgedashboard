"""Orchestrator: runs all fetchers and writes data/data.json."""
import json, os, traceback
from datetime import datetime, timezone
from pathlib import Path

import fetch_asana
import fetch_brief
import fetch_ghl          # direct GoHighLevel API (all channels: WhatsApp/webchat/SMS/Instagram/Facebook)
import fetch_ghl_sheet    # Zapier-fed sheet fallback
import fetch_inbody
import fetch_jumpstart
import fetch_kpi
import fetch_slack
import fetch_sop
import fetch_staff
import fetch_teamup
import fetch_xero
import fetch_starling
import fetch_sheets
import fetch_marketing

OUTPUT = Path(__file__).parent.parent / "data" / "data.json"


def safe_run(name, fn):
    try:
        return fn()
    except Exception:
        print(f"[{name}] FAILED:")
        traceback.print_exc()
        return None


def fetch_ghl_leads():
    """Prefer the direct GoHighLevel API (includes Instagram & Facebook DMs).
    Fall back to the Zapier-fed sheet if the API errors or returns nothing."""
    api = safe_run("ghl-api", fetch_ghl.run)
    if isinstance(api, list) and api:
        print(f"[ghl] using live API ({len(api)} conversations)")
        return api
    sheet = safe_run("ghl-sheet", fetch_ghl_sheet.run)
    if isinstance(sheet, list) and sheet:
        print(f"[ghl] using Zapier sheet ({len(sheet)} conversations)")
        return sheet
    # neither had data — return whichever is a list (so the tab isn't broken)
    return api if isinstance(api, list) else (sheet if isinstance(sheet, list) else [])


data = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "apps_script_url": os.environ.get("APPS_SCRIPT_URL", ""),
    "asana_script_url": os.environ.get("ASANA_SCRIPT_URL", ""),
    "atrisk_url": os.environ.get("ATRISK_SCRIPT_URL", ""),
    "brief":     safe_run("brief",     fetch_brief.run),
    "ghl":       fetch_ghl_leads(),
    "jumpstart": safe_run("jumpstart", fetch_jumpstart.run),
    "inbody": safe_run("inbody", fetch_inbody.run),
    "sop": safe_run("sop", fetch_sop.run),
    "slack": safe_run("slack", fetch_slack.run),
    "kpi": safe_run("kpi", fetch_kpi.run),
    "staff": safe_run("staff", fetch_staff.run),
    "asana": safe_run("asana", fetch_asana.run),
    "teamup": safe_run("teamup", fetch_teamup.run),
    "xero": safe_run("xero", fetch_xero.run),
    "starling": safe_run("starling", fetch_starling.run),
    "growth_sprint": safe_run("sheets", fetch_sheets.run),
    "marketing": safe_run("marketing", fetch_marketing.run),
}

OUTPUT.write_text(json.dumps(data, indent=2))
print(f"Wrote {OUTPUT}")
