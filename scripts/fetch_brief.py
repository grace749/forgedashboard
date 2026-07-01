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


# Subjects/senders that are never worth surfacing
_NOISE_SUBJECT = re.compile(
    r'receipt|invoice|unsubscribe|newsletter|loyalty|reward|points|'
    r'monthly update|weekly update|mileage rate|HMRC rate|'
    r'pages build|workflow run|run failed|github action|'
    r'info sent|membership info sent',
    re.I
)
_NOISE_SENDER = re.compile(
    r'noreply|no-reply|donotreply|automated|bounce|mailer-daemon|'
    r'notifications@|github\.com|captions\.ai|velites|tripcatcher|'
    r'myzone|chase servicing',
    re.I
)

# Signals that an email genuinely needs a reply or attention
_NEEDS_REPLY = re.compile(
    r'\?|please (reply|respond|confirm|let me know|get back)|'
    r'(can|could|would) you|following up|just checking|'
    r'question|query|help|issue|problem|concern|complaint|'
    r'payment (failed|issue|problem)|direct debit|'
    r'(join|interested in|enquir|information about|tell me more)',
    re.I
)
_IMPORTANT_SUBJECT = re.compile(
    r'invitation|enquir|complaint|payment|problem|issue|urgent|'
    r'cancel|injury|refund|join|interested|information|membership',
    re.I
)


def _is_noise(sender, subject, snippet):
    return bool(_NOISE_SENDER.search(sender) or _NOISE_SUBJECT.search(subject))


def _needs_attention(sender, subject, snippet):
    """True if email looks like it needs a reply or action."""
    if _is_noise(sender, subject, snippet):
        return False
    return bool(
        _IMPORTANT_SUBJECT.search(subject) or
        _NEEDS_REPLY.search(snippet)
    )


def fetch_gmail_urgent():
    """
    Emails needing a reply: looks at inbox threads where the last message
    is NOT from Grace — catches read-but-never-replied emails like Aaron
    from S Moore Motors, not just unread ones.
    """
    svc = _gmail_service()
    if not svc:
        return {"error": "Gmail not configured — add GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN secrets"}
    try:
        # Fetch inbox threads updated in last 7 days, excluding noise categories
        q = (
            "in:inbox newer_than:7d "
            "-category:promotions -category:updates -category:social "
            "NOT label:spam"
        )
        threads_result = svc.users().threads().list(userId="me", q=q, maxResults=40).execute()
        threads = threads_result.get("threads", [])

        emails = []
        for t in threads:
            thread = svc.users().threads().get(
                userId="me", id=t["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            messages = thread.get("messages", [])
            if not messages:
                continue

            # Last message in thread — if it's from Grace, she already replied
            last_msg  = messages[-1]
            last_hdrs = {h["name"]: h["value"] for h in last_msg.get("payload", {}).get("headers", [])}
            last_from = last_hdrs.get("From", "")

            # Skip if Grace sent the last message (she already replied)
            if "grace@theforge.pt" in last_from.lower():
                continue

            subject = last_hdrs.get("Subject", "(no subject)")
            snippet = last_msg.get("snippet", "")

            if _is_noise(last_from, subject, snippet):
                continue

            # For threads with multiple messages, show how many days since last received
            first_hdrs = {h["name"]: h["value"] for h in messages[0].get("payload", {}).get("headers", [])}
            original_subject = first_hdrs.get("Subject", subject)

            emails.append({
                "from":      _sender_name(last_from),
                "from_raw":  last_from,
                "subject":   original_subject,
                "snippet":   snippet[:250],
                "id":        last_msg["id"],
                "thread_id": t["id"],
                "msg_count": len(messages),
            })

        return emails[:10]
    except Exception as ex:
        return {"error": str(ex)}


def fetch_gmail_enquiries():
    """Actual people enquiring about membership or services — form submissions and direct interest."""
    svc = _gmail_service()
    if not svc:
        return {"error": "Gmail not configured"}
    try:
        # Tighter query: form submissions and direct personal enquiries only
        q = (
            "newer_than:3d ("
            "subject:enquiry OR subject:enquire OR "
            "subject:\"join the forge\" OR subject:\"interested in joining\" OR "
            "subject:\"form submission\" OR subject:\"corporate partnership\" OR "
            "subject:\"personal training\" OR subject:\"PT enquiry\""
            ")"
        )
        result = svc.users().messages().list(userId="me", q=q, maxResults=20).execute()
        seen  = set()
        leads = []
        for msg in result.get("messages", []):
            hdrs, snippet = _get_headers(svc, msg["id"])
            sender  = hdrs.get("From", "")
            subject = hdrs.get("Subject", "")
            # Skip if sender is a company/marketing list
            if _NOISE_SENDER.search(sender):
                continue
            # Skip if Grace herself sent it
            if "grace@theforge.pt" in sender.lower():
                continue
            key = f"{sender}|{subject}"
            if key in seen:
                continue
            seen.add(key)
            # Extract name from form submission snippets
            name_m    = re.search(r'(?:Full Name|Name)[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)', snippet)
            email_m   = re.search(r'(?:Email)[:\s]+([\w.+-]+@[\w.-]+)', snippet)
            path_m    = re.search(r'(?:Choose your path|path|goal)[:\s]+([^\n]{3,40})', snippet, re.I)
            leads.append({
                "name":    name_m.group(1) if name_m else _sender_name(sender),
                "email":   email_m.group(1) if email_m else "",
                "subject": subject,
                "path":    path_m.group(1).strip() if path_m else "",
                "snippet": snippet[:200],
                "id":      msg["id"],
            })
        return leads[:6]
    except Exception as ex:
        return {"error": str(ex)}


def fetch_gmail_important():
    """Starred emails or calendar invites from real people needing attention."""
    svc = _gmail_service()
    if not svc:
        return {"error": "Gmail not configured"}
    try:
        q = "is:unread (is:starred OR subject:invitation) newer_than:5d -category:promotions"
        result = svc.users().messages().list(userId="me", q=q, maxResults=15).execute()
        emails = []
        for msg in result.get("messages", []):
            hdrs, snippet = _get_headers(svc, msg["id"])
            sender  = hdrs.get("From", "")
            subject = hdrs.get("Subject", "(no subject)")
            if _is_noise(sender, subject, snippet):
                continue
            if "grace@theforge.pt" in sender.lower():
                continue
            emails.append({
                "from":    _sender_name(sender),
                "subject": subject,
                "snippet": snippet[:200],
                "id":      msg["id"],
            })
        return emails[:5]
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
