"""
Fetch daily morning brief: Calendar, Gmail, TeamUp alerts, Starling balance.

Secrets required in GitHub Actions:
  GOOGLE_SERVICE_ACCOUNT_JSON  — existing SA (needs Calendar scope)
  GOOGLE_CALENDAR_ID           — calendar ID, e.g. grace@theforge.pt
  GOOGLE_IMPERSONATE_EMAIL     — email to impersonate (domain-wide delegation)
                                 OR leave blank if calendar is shared with SA
  GMAIL_CLIENT_ID              — OAuth2 client ID (from Google Cloud Console)
  GMAIL_CLIENT_SECRET          — OAuth2 client secret
  GMAIL_REFRESH_TOKEN          — long-lived refresh token (run setup_gmail_oauth.py once)
"""
import os, json, re
from datetime import datetime, date, timezone

from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

# ── env ────────────────────────────────────────────────────────────────────
SA_JSON             = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
CALENDAR_ID         = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
IMPERSONATE_EMAIL   = os.environ.get("GOOGLE_IMPERSONATE_EMAIL", "")
GMAIL_CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")
# ── Calendar ───────────────────────────────────────────────────────────────

def _calendar_service():
    info  = json.loads(SA_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"]
    )
    if IMPERSONATE_EMAIL:
        creds = creds.with_subject(IMPERSONATE_EMAIL)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _fmt_time(raw):
    """Return '9:00am', 'all day', etc."""
    if not raw:
        return ""
    if "T" in raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.strftime("%-I:%M%p").lower()
        except Exception:
            return raw
    return "all day"


def fetch_calendar():
    if not SA_JSON:
        return {"error": "GOOGLE_SERVICE_ACCOUNT_JSON not set"}
    try:
        svc   = _calendar_service()
        today = date.today()
        t_min = datetime(today.year, today.month, today.day, 0,  0,  0,  tzinfo=timezone.utc).isoformat()
        t_max = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc).isoformat()
        result = svc.events().list(
            calendarId=CALENDAR_ID,
            timeMin=t_min,
            timeMax=t_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()

        events = []
        for e in result.get("items", []):
            start = e.get("start", {})
            others = [
                a.get("displayName") or a.get("email", "")
                for a in e.get("attendees", [])
                if not a.get("self")
            ]
            events.append({
                "title":       e.get("summary", "Untitled"),
                "time":        _fmt_time(start.get("dateTime", start.get("date", ""))),
                "start_raw":   start.get("dateTime", start.get("date", "")),
                "attendees":   others[:4],
                "description": (e.get("description") or "")[:200].strip(),
                "location":    e.get("location", ""),
            })
        return events
    except Exception as ex:
        return {"error": str(ex)}


# ── Gmail ──────────────────────────────────────────────────────────────────

def _gmail_service():
    if not all([GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN]):
        return None
    from google.oauth2.credentials import Credentials
    creds = Credentials(
        token=None,
        refresh_token=GMAIL_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GMAIL_CLIENT_ID,
        client_secret=GMAIL_CLIENT_SECRET,
    )
    creds.refresh(GoogleRequest())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _get_headers(svc, msg_id):
    m = svc.users().messages().get(
        userId="me", id=msg_id, format="metadata",
        metadataHeaders=["From", "Subject", "Date"]
    ).execute()
    hdrs = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
    return hdrs, m.get("snippet", "")


def _sender_name(from_header):
    """Extract display name from 'Name <email>' or return email."""
    m = re.match(r'^"?([^"<]+)"?\s*<', from_header)
    return m.group(1).strip() if m else from_header.split("@")[0]


def fetch_gmail_urgent():
    """Unread emails from real humans in last 24h needing a reply today."""
    svc = _gmail_service()
    if not svc:
        return {"error": "Gmail not configured — add GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN secrets"}
    try:
        # Exclude automated senders, promotions, updates
        q = (
            "is:unread newer_than:1d "
            "-from:noreply@ -from:no-reply@ -from:notifications@ "
            "-from:donotreply@ -from:automated@ "
            "-category:promotions -category:updates -category:social "
            "NOT label:spam"
        )
        result = svc.users().messages().list(userId="me", q=q, maxResults=15).execute()
        emails = []
        for msg in result.get("messages", []):
            hdrs, snippet = _get_headers(svc, msg["id"])
            sender = hdrs.get("From", "")
            # Skip obvious automation by checking common no-reply patterns
            if re.search(r'noreply|no-reply|donotreply|automated|bounce|mailer-daemon', sender, re.I):
                continue
            emails.append({
                "from":    _sender_name(sender),
                "from_raw": sender,
                "subject": hdrs.get("Subject", "(no subject)"),
                "snippet": snippet[:250],
                "id":      msg["id"],
            })
        return emails
    except Exception as ex:
        return {"error": str(ex)}


def fetch_gmail_enquiries():
    """New enquiry emails from last 48h (enquiry forms, membership interest)."""
    svc = _gmail_service()
    if not svc:
        return {"error": "Gmail not configured"}
    try:
        q = "newer_than:2d (subject:enquiry OR subject:enquire OR subject:membership OR subject:\"interested in\" OR subject:\"join the forge\")"
        result = svc.users().messages().list(userId="me", q=q, maxResults=20).execute()
        seen   = set()
        leads  = []
        for msg in result.get("messages", []):
            hdrs, snippet = _get_headers(svc, msg["id"])
            sender  = _sender_name(hdrs.get("From", ""))
            subject = hdrs.get("Subject", "")
            key     = f"{sender}|{subject}"
            if key in seen:
                continue
            seen.add(key)
            # Extract name/interest from snippet if possible
            name_m     = re.search(r'[Nn]ame[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)', snippet)
            interest_m = re.search(r'[Ii]nterested in[:\s]+([^\n,\.]{3,60})', snippet)
            leads.append({
                "name":     name_m.group(1) if name_m else sender,
                "subject":  subject,
                "interest": interest_m.group(1).strip() if interest_m else "",
                "snippet":  snippet[:200],
                "id":       msg["id"],
            })
        return leads
    except Exception as ex:
        return {"error": str(ex)}


def fetch_gmail_important():
    """Important but not urgent — starred or high-priority unread."""
    svc = _gmail_service()
    if not svc:
        return {"error": "Gmail not configured"}
    try:
        q = "is:unread (is:starred OR is:important) newer_than:3d -category:promotions"
        result = svc.users().messages().list(userId="me", q=q, maxResults=10).execute()
        emails = []
        for msg in result.get("messages", []):
            hdrs, snippet = _get_headers(svc, msg["id"])
            sender = hdrs.get("From", "")
            if re.search(r'noreply|no-reply|donotreply', sender, re.I):
                continue
            emails.append({
                "from":    _sender_name(sender),
                "subject": hdrs.get("Subject", "(no subject)"),
                "snippet": snippet[:200],
                "id":      msg["id"],
            })
        return emails
    except Exception as ex:
        return {"error": str(ex)}


# ── Assemble ───────────────────────────────────────────────────────────────

def run():
    today = date.today()

    calendar  = fetch_calendar()
    urgent    = fetch_gmail_urgent()
    important = fetch_gmail_important()
    enquiries = fetch_gmail_enquiries()

    event_count   = len(calendar) if isinstance(calendar, list) else 0
    urgent_count  = len(urgent)   if isinstance(urgent, list)   else 0
    enquiry_count = len(enquiries) if isinstance(enquiries, list) else 0

    return {
        "date":          today.isoformat(),
        "event_count":   event_count,
        "urgent_count":  urgent_count,
        "enquiry_count": enquiry_count,
        "calendar":      calendar,
        "urgent":        urgent,
        "important":     important,
        "enquiries":     enquiries,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
