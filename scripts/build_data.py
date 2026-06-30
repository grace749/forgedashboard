"""Orchestrator: runs all fetchers and writes data/data.json."""
import json, traceback
from datetime import datetime, timezone
from pathlib import Path

import fetch_asana
import fetch_teamup
import fetch_xero
import fetch_sheets

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
    "asana": safe_run("asana", fetch_asana.run),
    "teamup": safe_run("teamup", fetch_teamup.run),
    "xero": safe_run("xero", fetch_xero.run),
    "growth_sprint": safe_run("sheets", fetch_sheets.run),
}

OUTPUT.write_text(json.dumps(data, indent=2))
print(f"Wrote {OUTPUT}")
