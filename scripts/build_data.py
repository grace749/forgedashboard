"""Orchestrator: runs all fetchers and writes data/data.json."""
import json, os, traceback
from datetime import datetime, timezone
from pathlib import Path

import fetch_asana
import fetch_brief
import fetch_ghl_sheet as fetch_ghl
import fetch_inbody
import fetch_jumpstart
import fetch_kpi
import fetch_slack
import fetch_sop
import fetch_staff
import fetch_teamup
import fetch_xero
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


data = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "apps_script_url": os.environ.get("APPS_SCRIPT_URL", ""),
    "asana_script_url": os.environ.get("ASANA_SCRIPT_URL", ""),
    "atrisk_url": os.environ.get("ATRISK_SCRIPT_URL", ""),
    "brief":     safe_run("brief",     fetch_brief.run),
    "ghl":       safe_run("ghl",       fetch_ghl.run),
    "jumpstart": safe_run("jumpstart", fetch_jumpstart.run),
    "inbody": safe_run("inbody", fetch_inbody.run),
    "sop": safe_run("sop", fetch_sop.run),
    "slack": safe_run("slack", fetch_slack.run),
    "kpi": safe_run("kpi", fetch_kpi.run),
    "staff": safe_run("staff", fetch_staff.run),
    "asana": safe_run("asana", fetch_asana.run),
    "teamup": safe_run("teamup", fetch_teamup.run),
    "xero": safe_run("xero", fetch_xero.run),
    "growth_sprint": safe_run("sheets", fetch_sheets.run),
    "marketing": safe_run("marketing", fetch_marketing.run),
}

OUTPUT.write_text(json.dumps(data, indent=2))
print(f"Wrote {OUTPUT}")
