"""
Fetch InBody scan data from Lookin'Body Web (gbr.lookinbody.com).

Lookin'Body is InBody's admin backend. There's no public API, so we log in with
the admin credentials and read the member list (which carries each member's last
InBody test date). Scans are recommended every ~6 weeks, so we compute the next
due date from the last scan.

Env:
  INBODY_LOGIN_ID   Lookin'Body admin login id
  INBODY_PASSWORD   Lookin'Body admin password
"""
import os, datetime, urllib.parse
from datetime import date
import requests

BASE = "https://gbr.lookinbody.com"
SCAN_INTERVAL_DAYS = 42   # recommended every ~6 weeks


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
    data = r.json().get("Data", {})
    code = data.get("Code")
    if code:
        # The Code is already URL-encoded in the response — append it raw so
        # requests doesn't double-encode it (which invalidates the session).
        session.get(BASE + "/BaseForm/Index?code=" + code, timeout=30)
    return data


def _fetch_members(session, page_size=200, max_pages=20):
    members = []
    for page in range(max_pages):
        start = page * page_size + 1
        end   = (page + 1) * page_size
        body = (
            f"startPage={start}&endPage={end}"
            "&SearchOption%5BName%5D="
            "&SearchOption%5BSortAsecding%5D=false"
            "&SearchOption%5BSortColName%5D=InBodyTestDate"
        )
        r = session.post(
            BASE + "/MemberList/GetUserData",
            headers={"X-Requested-With": "XMLHttpRequest",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data=body, timeout=30,
        )
        d = r.json().get("Data", {})
        batch = d.get("Data", []) or []
        members.extend(batch)
        if start + len(batch) - 1 >= (d.get("TotalCount") or 0) or not batch:
            break
    return members


def _parse_dt(s):
    # LastInBodyTest looks like "20260618094107"
    if not s or len(s) < 8:
        return None
    try:
        return datetime.datetime.strptime(s[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _phone_from_uid(uid):
    # UID looks like "5895_44_07515339656"
    parts = (uid or "").split("_")
    return parts[-1] if parts else ""


def _teamup_name_map():
    """phone -> 'First Last' from TeamUp, to de-mask Lookin'Body names."""
    key = os.environ.get("TEAMUP_API_KEY", "")
    if not key:
        return {}
    names = {}
    url = "https://goteamup.com/api/v2/customers"
    headers = {"Authorization": f"Token {key}"}
    params = {"page_size": 200}
    try:
        while url:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if not r.ok:
                break
            j = r.json()
            for c in j.get("results", []):
                phone = "".join(ch for ch in (c.get("phone") or c.get("mobile") or "") if ch.isdigit())
                nm = f"{c.get('first_name','')} {c.get('last_name','')}".strip()
                if phone and nm:
                    names[phone[-9:]] = nm  # last 9 digits, ignore leading 0/44
            url = j.get("next")
            params = None
    except Exception as ex:
        print(f"[inbody] teamup name lookup failed: {ex}")
    return names


def _clean_username(user_id):
    """
    Best-effort display name from an InBody username. The admin feed masks real
    names, so usernames like '094louisefar' / 'katiemcf038' are the only handle.
    Strip the sequence digits and title-case what's left.
    """
    import re
    s = re.sub(r"\d+", "", user_id or "").strip()
    return s.title() if s else "Member"


def run():
    if not os.environ.get("INBODY_LOGIN_ID") or not os.environ.get("INBODY_PASSWORD"):
        print("[inbody] INBODY_LOGIN_ID / INBODY_PASSWORD not set — skipping")
        return {"scans": [], "total": 0, "configured": False}

    session = requests.Session()
    _login(session)
    members = _fetch_members(session)

    phone_names = _teamup_name_map()
    today = date.today()
    scans = []
    for m in members:
        last = _parse_dt(m.get("LastInBodyTest"))
        if not last:
            continue
        uid   = m.get("UID", "")
        phone = _phone_from_uid(uid)
        name  = (m.get("Name") or "").strip()
        if not name and phone:
            name = phone_names.get(phone[-9:], "")
        if not name:
            name = _clean_username(m.get("UserID"))

        next_due = last + datetime.timedelta(days=SCAN_INTERVAL_DAYS)
        scans.append({
            "name":        name,
            "last_scan":   last.isoformat(),
            "days_since":  (today - last).days,
            "next_due":    next_due.isoformat(),
            "days_to_due": (next_due - today).days,
            "overdue":     (next_due - today).days < 0,
        })

    scans.sort(key=lambda s: s["last_scan"], reverse=True)
    return {"scans": scans, "total": len(scans), "configured": True}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
