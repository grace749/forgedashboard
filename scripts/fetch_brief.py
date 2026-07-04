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
import os, json, re, base64, email as email_lib
from datetime import datetime, date, timezone, timedelta
from email.mime.text import MIMEText

import ai
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


def _day_label(start_raw, today):
    """'Today' / 'Tomorrow' / weekday for an event start."""
    try:
        d = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).date() if "T" in start_raw \
            else date.fromisoformat(start_raw[:10])
    except Exception:
        return ""
    delta = (d - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return d.strftime("%A")


def _calendar_prep(events):
    """One AI call: a short 'what to prep' note per event (or blank if none)."""
    real = [e for e in events if e.get("title")]
    if not real:
        return
    lines = "\n".join(
        f"{i+1}. {e['day']} {e['time']} — {e['title']}"
        + (f" [{e['description'][:90]}]" if e.get("description") else "")
        for i, e in enumerate(real)
    )
    text = ai.generate(
        "You are the assistant to Grace, owner of The Forge, a women's gym in Belfast. "
        "For each calendar event, give a ONE-line note on what she needs to prepare to "
        "be ready (materials to bring/print, who to brief, questions to prep, things to "
        "book/confirm, data to review). If it's a routine block or a class she just "
        "coaches with nothing to prep, reply exactly '-'. Under 16 words each.",
        f"Events over the next 2 days:\n{lines}\n\n"
        f"Return exactly one line per event, numbered 1..{len(real)} in the same order, "
        "as '<number>. <prep note or ->'.",
        max_tokens=450,
    )
    prep = {}
    for ln in (text or "").splitlines():
        m = re.match(r"\s*(\d+)[.)]\s*(.+)", ln)
        if m:
            idx, note = int(m.group(1)) - 1, m.group(2).strip()
            if 0 <= idx < len(real) and note not in ("-", "—", ""):
                prep[idx] = note
    for i, e in enumerate(real):
        e["prep"] = prep.get(i, "")


def fetch_calendar():
    if not SA_JSON:
        return {"error": "GOOGLE_SERVICE_ACCOUNT_JSON not set"}
    try:
        svc   = _calendar_service()
        now   = datetime.now(timezone.utc)
        today = date.today()
        # From now through the end of the day-after-tomorrow (the next 2 days)
        end   = today + timedelta(days=2)
        t_min = now.isoformat()
        t_max = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc).isoformat()
        result = svc.events().list(
            calendarId=CALENDAR_ID,
            timeMin=t_min,
            timeMax=t_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=40,
        ).execute()

        events = []
        for e in result.get("items", []):
            start = e.get("start", {})
            start_raw = start.get("dateTime", start.get("date", ""))
            others = [
                a.get("displayName") or a.get("email", "")
                for a in e.get("attendees", [])
                if not a.get("self")
            ]
            events.append({
                "title":       e.get("summary", "Untitled"),
                "time":        _fmt_time(start_raw),
                "start_raw":   start_raw,
                "day":         _day_label(start_raw, today),
                "attendees":   others[:4],
                "description": (e.get("description") or "")[:200].strip(),
                "location":    e.get("location", ""),
                "prep":        "",
            })
        _calendar_prep(events)
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


# Senders that are definitely automated — never needs a reply
_NOISE_SENDER = re.compile(
    r'noreply|no-reply|donotreply|automated|bounce|mailer-daemon|'
    r'notification@|notifications@|billing\.|'
    r'github\.com|captions\.ai|velites|tripcatcher|'
    r'myzone|temu@|etsy\.com|o2\.com|'
    r'@forgefemalefitness\.co',   # Grace's own automated system emails
    re.I
)

# Subjects that are definitely automated noise
_NOISE_SUBJECT = re.compile(
    r'unsubscribe|loyalty points|reward|your bill is ready|'
    r'pages build|workflow run|run failed|github action|'
    r'appointment confirmation|new member lifestyle|t-shirt size|'
    r'well75 registration|refer a friend|member of the month',
    re.I
)


def _is_noise(sender, subject, snippet=""):
    return bool(_NOISE_SENDER.search(sender) or _NOISE_SUBJECT.search(subject))


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
        threads_result = svc.users().threads().list(userId="me", q=q, maxResults=100).execute()
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

            if _is_noise(last_from, subject):
                continue

            # For threads with multiple messages, use original subject
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

        # Create AI draft replies for emails needing attention (max 8, last 48h only)
        recent = [e for e in emails if e.get("msg_count", 1) <= 5][:8]
        for e in recent:
            try:
                draft_id = _create_draft_reply(svc, e)
                if draft_id:
                    e["draft_id"] = draft_id
            except Exception:
                pass

        return emails[:10]
    except Exception as ex:
        return {"error": str(ex)}


def _get_email_body(svc, msg_id):
    """Fetch the plain text body of an email."""
    try:
        msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        payload = msg.get("payload", {})

        def extract_text(part):
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            for p in part.get("parts", []):
                result = extract_text(p)
                if result:
                    return result
            return ""

        return extract_text(payload)[:2000]
    except Exception:
        return ""


def _draft_exists_for_thread(svc, thread_id):
    """Check if a draft already exists for this thread to avoid duplicates."""
    try:
        drafts = svc.users().drafts().list(userId="me").execute()
        for d in drafts.get("drafts", []):
            draft = svc.users().drafts().get(userId="me", id=d["id"], format="metadata",
                metadataHeaders=["Subject"]).execute()
            msg = draft.get("message", {})
            if msg.get("threadId") == thread_id:
                return True
    except Exception:
        pass
    return False


def _generate_reply(sender_name, subject, body):
    """Use Anthropic API to write a suggested reply. Falls back to a template."""
    import urllib.request
    ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    if ANTHROPIC_KEY:
        try:
            payload = json.dumps({
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "messages": [{
                    "role": "user",
                    "content": (
                        f"You are Grace Smith, owner of The Forge — a women's fitness gym in Belfast. "
                        f"Write a short, warm, professional reply to this email. "
                        f"Keep it brief (3-5 sentences max). Don't use filler phrases like 'I hope this finds you well'. "
                        f"Sign off as Grace.\n\n"
                        f"From: {sender_name}\n"
                        f"Subject: {subject}\n\n"
                        f"{body[:1000]}"
                    )
                }]
            }).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read())
                return result["content"][0]["text"]
        except Exception as ex:
            print(f"[brief] Anthropic API error for '{subject}': {ex}")

    # Fallback: blank draft so Grace can still reply quickly from the thread
    return f"Hi {sender_name.split()[0]},\n\n\n\nThanks,\nGrace"


def _create_draft_reply(svc, email_data):
    """Create a Gmail draft reply for an email thread."""
    thread_id = email_data.get("thread_id")
    if not thread_id:
        return None

    # Don't create duplicate drafts
    if _draft_exists_for_thread(svc, thread_id):
        return None

    body_text = _get_email_body(svc, email_data["id"])
    reply_text = _generate_reply(
        email_data.get("from", ""),
        email_data.get("subject", ""),
        body_text
    )
    if not reply_text:
        return None

    # Build reply email
    subject = email_data.get("subject", "")
    re_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    sender_raw = email_data.get("from_raw", "")

    msg = MIMEText(reply_text)
    msg["To"] = sender_raw
    msg["From"] = "grace@theforge.pt"
    msg["Subject"] = re_subject
    msg["In-Reply-To"] = email_data.get("id", "")
    msg["References"] = email_data.get("id", "")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    draft = svc.users().drafts().create(userId="me", body={
        "message": {
            "threadId": thread_id,
            "raw": raw,
        }
    }).execute()

    return draft.get("id")


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
            if _is_noise(sender, subject):
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


# ── Member forms from email (check-ins + lifestyle goals) ───────────────────

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# our own / automated addresses that aren't the member
_OWN_EMAIL = re.compile(r"theforge\.pt|forgefemalefitness|noreply|no-reply|"
                        r"typeform|jotform|google|mailer|notification", re.I)


def _emails_in(text):
    return {e.lower() for e in _EMAIL_RE.findall(text or "") if not _OWN_EMAIL.search(e)}


def _email_date(hdrs):
    try:
        return email_lib.utils.parsedate_to_datetime(hdrs.get("Date", "")).date().isoformat()
    except Exception:
        return None


def _scan_form_emails(svc, subject_query, max_results=500):
    """Return [(member_email, iso_date, body), …] for emails matching a subject."""
    res = svc.users().messages().list(
        userId="me", q=f'subject:"{subject_query}" newer_than:400d',
        maxResults=max_results).execute()
    out = []
    for msg in res.get("messages", []):
        m = svc.users().messages().get(
            userId="me", id=msg["id"], format="full").execute()
        hdrs = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
        iso  = _email_date(hdrs)
        body = _get_email_body(svc, msg["id"])
        # member email can be the sender, the reply-to, or in the body
        candidates = _emails_in(hdrs.get("From", "")) | _emails_in(hdrs.get("Reply-To", "")) | _emails_in(body)
        for e in candidates:
            out.append((e, iso, body))
    return out


def fetch_checkins():
    """Map member email -> last check-in date, from 'Client Reflection' emails."""
    svc = _gmail_service()
    if not svc:
        return {"error": "Gmail not configured"}
    try:
        by_email = {}
        for email_addr, iso, _body in _scan_form_emails(svc, "Client Reflection"):
            if not iso:
                continue
            if email_addr not in by_email or iso > by_email[email_addr]:
                by_email[email_addr] = iso
        return {"by_email": by_email, "count": len(by_email)}
    except Exception as ex:
        return {"error": str(ex)}


def _extract_goal(body):
    """Pull the member's goal out of a lifestyle-form email body."""
    if not body:
        return ""
    # look for a goal question label and grab the answer after it
    m = re.search(r"(?:goal|hoping to achieve|what.{0,30}achieve|why.{0,20}join)"
                  r"[^\n:?]*[:?]\s*(.+)", body, re.I)
    if not m:
        return ""
    goal = re.sub(r"\s+", " ", m.group(1)).strip()
    # Cut off trailing form metadata that runs into the answer on one line
    goal = re.split(r"\b(?:Timezone|Submission Date|Submitted|GMT[+\-]|"
                    r"Europe/|IP Address|Form Name|Page URL|http)\b",
                    goal, maxsplit=1)[0].strip(" -–·.")
    return goal[:200]


def fetch_lifestyle_goals():
    """Map member email -> goal, from 'New Member Lifestyle Form' emails."""
    svc = _gmail_service()
    if not svc:
        return {"error": "Gmail not configured"}
    try:
        by_email = {}   # email -> {goal, date}
        for email_addr, iso, body in _scan_form_emails(svc, "New Member Lifestyle Form"):
            goal = _extract_goal(body)
            if not goal:
                continue
            if email_addr not in by_email or (iso or "") > (by_email[email_addr]["date"] or ""):
                by_email[email_addr] = {"goal": goal, "date": iso}
        return {"by_email": {k: v["goal"] for k, v in by_email.items()}, "count": len(by_email)}
    except Exception as ex:
        return {"error": str(ex)}


# ── Assemble ───────────────────────────────────────────────────────────────

def run():
    today = date.today()

    calendar  = fetch_calendar()
    urgent    = fetch_gmail_urgent()
    important = fetch_gmail_important()
    enquiries = fetch_gmail_enquiries()
    checkins  = fetch_checkins()
    goals     = fetch_lifestyle_goals()

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
        "checkins":      checkins,
        "goals":         goals,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
