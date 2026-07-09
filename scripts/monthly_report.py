"""
Monthly report generator.

On the LAST FRIDAY of the month (or when REPORT_FORCE=1), assembles the key KPIs
across every area from the freshly built `data` dict, POSTs them to a Google Apps
Script (REPORT_SCRIPT_URL, see docs/report_apps_script.gs) which creates a
formatted Google Doc and returns its URL, then records the report in
data/reports.json (so the dashboard can list every past report, newest first).
"""
import os, json, datetime, calendar
from pathlib import Path

REPORTS_FILE = Path(__file__).parent.parent / "data" / "reports.json"


def is_last_friday(d=None):
    d = d or datetime.date.today()
    if d.weekday() != 4:                       # 4 = Friday
        return False
    return (d + datetime.timedelta(days=7)).month != d.month


def _load_reports():
    try:
        return json.loads(REPORTS_FILE.read_text())
    except Exception:
        return []


def _num(v):
    return v if isinstance(v, (int, float)) else None


# Per-page reports, in display order. Each key is also a dashboard tab id, so the
# frontend can show that page's own report card. The coach's redacted data drops
# the sensitive keys (finances/leads/ads + the combined "full"), so her per-page
# reports never contain them.
PAGE_ORDER = ["members", "retention", "jumpstart", "inbody",
              "finances", "leads", "ads", "operations"]


def assemble(data):
    """Build report sections per page: {page_key: {"heading", "lines"}}."""
    tm = data.get("teamup") or {}
    js = (data.get("jumpstart") or {}).get("stats") or {}
    ib = (data.get("inbody") or {}).get("scans") or []
    st = data.get("starling") or {}
    mk = (data.get("marketing") or {})
    ghl = data.get("ghl") or []
    ts = data.get("tshirt") or {}
    ops_events = ((data.get("events") or {}).get("events")) or []

    this_ym = datetime.date.today().strftime("%Y-%m")
    starling_months = [m for m in (st.get("monthly") or []) if m.get("month") != this_ym]
    last_full = sorted(starling_months, key=lambda m: m.get("month", ""))[-1] if starling_months else {}

    def gbp(v):
        v = _num(v)
        return ("£{:,.0f}".format(v)) if v is not None else "—"

    top_types = sorted((tm.get("breakdown") or []), key=lambda b: -(b.get("count") or 0))[:5]

    return {
        "members": {"heading": "Members", "lines": [
            f"Active members: {tm.get('total_members', '—')}",
            f"Recurring: {tm.get('recurring', '—')} · Trials: {tm.get('trial', '—')} · Paused: {tm.get('paused', '—')}",
            f"Joined this month: {tm.get('joined_this_month', '—')} · Monthly churn: {tm.get('churn_rate', '—')}%",
            "By type: " + ", ".join(f"{b['name']} {b['count']}" for b in top_types),
        ]},
        "retention": {"heading": "Retention", "lines": [
            f"Monthly churn: {tm.get('churn_rate', '—')}%",
            f"Leaving this month: {tm.get('cancellations_this_month', '—')}",
            f"Paused memberships: {tm.get('paused', '—')}",
        ]},
        "jumpstart": {"heading": "Jumpstart (trials)", "lines": [
            f"Active trials: {js.get('active_count', '—')} · Paused: {js.get('paused_count', '—')}",
            f"Conversion rate: {js.get('conv_rate', '—')}% ({js.get('converted', '—')} converted of {js.get('total_complete', '—')} completed)",
        ]},
        "inbody": {"heading": "InBody", "lines": [
            f"Members with scans: {len(ib)}",
            f"Scans overdue: {sum(1 for s in ib if s.get('overdue'))}",
        ]},
        "finances": {"heading": "Finances", "lines": [
            f"Last full month ({last_full.get('month', '—')}): in {gbp(last_full.get('revenue'))}, out {gbp(last_full.get('expenses'))}, net {gbp(last_full.get('profit'))}",
            f"Cash now: {gbp(st.get('cash_position'))} · Net last 30 days: {gbp(st.get('net_30'))}",
            f"Reserved in pots: {gbp(st.get('earmarked'))}",
        ]},
        "leads": {"heading": "Leads", "lines": [
            f"Open leads/enquiries: {len(ghl)}",
        ]},
        "ads": {"heading": "Ads", "lines": [
            f"Lifetime ad spend: {gbp((mk.get('lifetime') or {}).get('spend'))} · Leads: {(mk.get('lifetime') or {}).get('leads', '—')}",
            f"Cost per lead (lifetime): {(mk.get('lifetime') or {}).get('cpl', '—')}",
        ]},
        "operations": {"heading": "Operations", "lines": [
            f"Upcoming events: {len(ops_events)}" + (" — " + "; ".join(e.get("name", "") for e in ops_events[:4]) if ops_events else ""),
            f"T-Shirt Studio orders (in email): {ts.get('count', 0)}" + (f" · spend {gbp(ts.get('total_spend'))}" if ts.get('total_spend') else ""),
        ]},
    }


def _post_report(url, title, subtitle, sections):
    """POST one report to Apps Script → returns its Google Doc URL (or None)."""
    payload = {"title": title, "subtitle": subtitle, "sections": sections}
    try:
        import requests
        r = requests.post(url, data=json.dumps(payload), timeout=60)
        r.raise_for_status()
        res = r.json()
    except Exception as ex:
        print(f"[report] '{title}' generation failed: {ex}")
        return None
    if not res.get("ok") or not res.get("url"):
        print(f"[report] '{title}' script error: {res.get('error')}")
        return None
    return res["url"]


def maybe_generate(data, force=None):
    """Generate + record the monthly reports if it's the last Friday (or forced).

    Produces ONE Google Doc per page (Members, Retention, …) plus a combined
    "full" report covering everything the owner sees. reports.json is a dict
    keyed by page (each value a list of that page's monthly docs, newest first)
    so every tab can show its own report and the owner home shows the full one.
    """
    force = force if force is not None else os.environ.get("REPORT_FORCE") == "1"
    if not (force or is_last_friday()):
        return None
    url = os.environ.get("REPORT_SCRIPT_URL")
    if not url:
        print("[report] REPORT_SCRIPT_URL not set — skipping monthly report")
        return None

    today = datetime.date.today()
    month_label = today.strftime("%B %Y")
    ym = today.strftime("%Y-%m")
    subtitle = f"Generated {today.strftime('%A %d %B %Y')}"

    reports = _load_reports()
    if not isinstance(reports, dict):
        reports = {}   # migrate from the old flat-list format
    if reports.get("full") and any(r.get("month") == ym for r in reports["full"]) and not force:
        print(f"[report] {ym} already generated — skipping")
        return None

    sections_by_page = assemble(data)

    def record(key, title, sections):
        doc_url = _post_report(url, title, subtitle, sections)
        if not doc_url:
            return
        entry = {"month": ym, "title": title, "label": month_label,
                 "url": doc_url, "generated": today.isoformat()}
        lst = [r for r in reports.get(key, []) if r.get("month") != ym] + [entry]
        lst.sort(key=lambda r: r.get("month", ""), reverse=True)
        reports[key] = lst

    # One doc per page …
    for key in PAGE_ORDER:
        sec = sections_by_page.get(key)
        if sec:
            record(key, f"Forge Female Fitness — {sec['heading']} · {month_label}", [sec])
    # … and the full owner report covering everything.
    record("full", f"Forge Female Fitness — Monthly Report · {month_label}",
           [sections_by_page[k] for k in PAGE_ORDER if k in sections_by_page])

    REPORTS_FILE.write_text(json.dumps(reports, indent=2))
    print(f"[report] wrote {month_label} reports ({len(reports)} pages)")
    return reports.get("full", [None])[0]
