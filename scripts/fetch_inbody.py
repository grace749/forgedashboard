"""
Fetch InBody scan data from Lookin'Body Web (gbr.lookinbody.com).

Lookin'Body is InBody's admin backend. There's no public API, so we log in with
the admin credentials and use the built-in data export
(/SetupExportDataInBody/SetupExportDataWithColumn), which returns real member
names plus scan metrics (weight, skeletal muscle mass, body fat %, BMI) — one
row per scan. We group by member to get their latest scan, the change since the
previous scan, and when the next one is due.

Env:
  INBODY_LOGIN_ID   Lookin'Body admin login id
  INBODY_PASSWORD   Lookin'Body admin password
"""
import os, re, datetime
from datetime import date
import requests

BASE = "https://gbr.lookinbody.com"
SCAN_INTERVAL_DAYS = 42   # recommended every ~6 weeks

# Columns to export: (table, field, header)
EXPORT_COLS = [
    ("USER_INFO1_TBL", "NAME",      "Name"),
    ("USER_INFO1_TBL", "USER_ID",   "ID"),
    ("BCA_TBL",        "DATETIMES", "TestDate"),
    ("BCA_TBL",        "WT",        "Weight"),
    ("MFA_TBL",        "SMM",       "SMM"),
    ("MFA_TBL",        "PBF",       "PBF"),
    ("MFA_TBL",        "BMI",       "BMI"),
]


def _login(session):
    login_id = os.environ["INBODY_LOGIN_ID"]
    password = os.environ["INBODY_PASSWORD"]
    session.get(BASE + "/", timeout=30)
    r = session.post(
        BASE + "/Login/LoginProcess",
        headers={"X-Requested-With": "XMLHttpRequest"},
        data={"LoginID": login_id, "LoginPW": password, "Type": "ADMIN",
              "IsForceLogin": "true", "IP": "", "BrowserType": "Mozilla/5.0"},
        timeout=30,
    )
    code = r.json().get("Data", {}).get("Code")
    if code:
        # Code is already URL-encoded — append raw so it isn't double-encoded.
        session.get(BASE + "/BaseForm/Index?code=" + code, timeout=30)


def _fetch_uids(session, page_size=200, max_pages=20):
    uids = []
    for page in range(max_pages):
        start = page * page_size + 1
        end   = (page + 1) * page_size
        body = (f"startPage={start}&endPage={end}"
                "&SearchOption%5BName%5D=&SearchOption%5BSortAsecding%5D=false"
                "&SearchOption%5BSortColName%5D=InBodyTestDate")
        r = session.post(BASE + "/MemberList/GetUserData",
                         headers={"X-Requested-With": "XMLHttpRequest",
                                  "Content-Type": "application/x-www-form-urlencoded"},
                         data=body, timeout=30)
        d = r.json().get("Data", {})
        batch = d.get("Data", []) or []
        uids.extend([m["UID"] for m in batch if m.get("UID")])
        if start + len(batch) - 1 >= (d.get("TotalCount") or 0) or not batch:
            break
    return uids


def _export_scans(session, uids):
    data = {"StartDate": "2019-01-01", "EndDate": "2100-01-01", "DownloadType": "0"}
    data["LUIDS[]"] = uids
    for i, (tbl, field, name) in enumerate(EXPORT_COLS):
        data[f"Columns[{i}][TABLENAME]"] = tbl
        data[f"Columns[{i}][FieldName]"] = field
        data[f"Columns[{i}][Name]"] = name
    r = session.post(BASE + "/SetupExportDataInBody/SetupExportDataWithColumn",
                     data=data, timeout=120)
    return r.json().get("Data", "")


def _parse_export(xml_text):
    """Parse the SpreadsheetML export into a list of row dicts, honouring
    ss:Index (empty cells are skipped in the XML)."""
    rows = []
    for row_xml in re.findall(r"<Row[^>]*>(.*?)</Row>", xml_text, re.S):
        cells = {}
        idx = 0
        for cell_xml in re.findall(r"<Cell([^>]*)>(.*?)</Cell>", row_xml, re.S):
            attrs, inner = cell_xml
            m = re.search(r'ss:Index="(\d+)"', attrs)
            if m:
                idx = int(m.group(1))
            else:
                idx += 1
            dm = re.search(r"<Data[^>]*>(.*?)</Data>", inner, re.S)
            val = re.sub(r"<[^>]+>", "", dm.group(1)).strip() if dm else ""
            cells[idx] = val
        rows.append(cells)
    if not rows:
        return []
    # Return each data row as a positional list matching EXPORT_COLS order.
    # (The header row is dropped; export headers are numbered like "1. Name".)
    n = len(EXPORT_COLS)
    return [[r.get(i + 1, "") for i in range(n)] for r in rows[1:]]


def _parse_test_date(s):
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            pass
    return None


def _num(s):
    try:
        return round(float(s), 1)
    except (ValueError, TypeError):
        return None


def run():
    if not os.environ.get("INBODY_LOGIN_ID") or not os.environ.get("INBODY_PASSWORD"):
        print("[inbody] INBODY_LOGIN_ID / INBODY_PASSWORD not set — skipping")
        return {"scans": [], "total": 0, "configured": False}

    session = requests.Session()
    _login(session)
    uids = _fetch_uids(session)
    if not uids:
        return {"scans": [], "total": 0, "configured": True}

    rows = _parse_export(session and _export_scans(session, uids))

    # Group scans by member. Columns are positional (see EXPORT_COLS):
    # 0 Name, 1 ID, 2 TestDate, 3 Weight, 4 SMM, 5 PBF, 6 BMI
    by_member = {}
    for r in rows:
        name = (r[0] or "").strip()
        if name in ("", "-"):
            name = (r[1] or "").strip()   # fall back to InBody ID
        d = _parse_test_date(r[2] if len(r) > 2 else "")
        if not name or not d:
            continue
        entry = {
            "date":   d,
            "weight": _num(r[3] if len(r) > 3 else None),
            "smm":    _num(r[4] if len(r) > 4 else None),
            "pbf":    _num(r[5] if len(r) > 5 else None),
            "bmi":    _num(r[6] if len(r) > 6 else None),
        }
        by_member.setdefault(name, []).append(entry)

    today = date.today()
    scans = []
    for name, entries in by_member.items():
        entries.sort(key=lambda e: e["date"])
        latest = entries[-1]
        prev = entries[-2] if len(entries) > 1 else None
        next_due = latest["date"] + datetime.timedelta(days=SCAN_INTERVAL_DAYS)

        def change(k):
            if prev and latest[k] is not None and prev[k] is not None:
                return round(latest[k] - prev[k], 1)
            return None

        scans.append({
            "name":        name,
            "last_scan":   latest["date"].isoformat(),
            "scan_count":  len(entries),
            "weight":      latest["weight"],
            "smm":         latest["smm"],
            "pbf":         latest["pbf"],
            "bmi":         latest["bmi"],
            "weight_change": change("weight"),
            "smm_change":    change("smm"),
            "pbf_change":    change("pbf"),
            "next_due":    next_due.isoformat(),
            "days_to_due": (next_due - today).days,
            "overdue":     (next_due - today).days < 0,
        })

    scans.sort(key=lambda s: s["last_scan"], reverse=True)
    return {"scans": scans, "total": len(scans), "configured": True}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
