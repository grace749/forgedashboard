"""
Pull T-Shirt Studio orders from Gmail order-confirmation emails.

T-Shirt Studio (tshirtstudio.com) has no public API, but it emails an order
confirmation for every purchase. This scans Gmail for those emails (reusing the
same OAuth creds as fetch_brief.py) and extracts each order's date, number,
total and item summary, so the Operations → Clothing card populates itself.

Env (already wired in refresh.yml):
  GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN

Optional overrides (if T-Shirt Studio's from-address or subject wording differs):
  TSHIRT_SENDER    — sender domain/address to match     (default: tshirtstudio.com)
  TSHIRT_SUBJECT   — subject phrase that marks an order  (default: order)
  TSHIRT_LOOKBACK  — how far back to scan, Gmail syntax  (default: 730d)
"""
import os, re, base64, email as email_lib
from datetime import date

from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

GMAIL_CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")

SENDER   = os.environ.get("TSHIRT_SENDER", "tshirtstudio.com")
SUBJECT  = os.environ.get("TSHIRT_SUBJECT", "order")
LOOKBACK = os.environ.get("TSHIRT_LOOKBACK", "730d")


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


def _body_text(payload):
    """Best-effort plain-text body; falls back to stripped HTML."""
    plain, html = "", ""

    def walk(part):
        nonlocal plain, html
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if data and mime in ("text/plain", "text/html"):
            try:
                txt = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            except Exception:
                txt = ""
            if mime == "text/plain":
                plain += txt
            else:
                html += txt
        for p in part.get("parts", []):
            walk(p)

    walk(payload)
    if plain.strip():
        return plain
    # strip tags from HTML as a fallback
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    return text


def _order_no(subject, body):
    for src in (subject, body):
        m = re.search(r"order\s*(?:number|no\.?|ref(?:erence)?|#)?\s*[:#]?\s*([A-Z]{0,4}[-]?\d{4,})",
                      src, re.I)
        if m:
            return m.group(1).strip()
    return ""


def _total(body):
    """Prefer an amount next to a 'total' label; else the largest £ amount."""
    labelled = re.findall(
        r"(?:grand\s+total|order\s+total|total(?:\s+to\s+pay)?|amount\s+paid)\D{0,20}£\s?([\d,]+\.\d{2})",
        body, re.I)
    if labelled:
        return "£" + labelled[-1].replace(",", "")
    amounts = re.findall(r"£\s?([\d,]+\.\d{2})", body)
    if amounts:
        return "£" + max(amounts, key=lambda a: float(a.replace(",", ""))).replace(",", "")
    return ""


def _items(body):
    """Pull 'qty x product' style lines into a short summary."""
    out = []
    for m in re.finditer(r"(?m)^\s*(\d{1,3})\s*[x×]\s*(.{3,60}?)(?:\s+£|\s*$)", body):
        qty, name = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip(" -–·")
        if name and not re.match(r"(?i)total|subtotal|shipping|vat|discount", name):
            out.append(f"{qty}× {name}")
    return out[:6]


def _email_date(hdrs):
    try:
        return email_lib.utils.parsedate_to_datetime(hdrs.get("Date", "")).date().isoformat()
    except Exception:
        return None


def run():
    svc = _gmail_service()
    if not svc:
        return {"configured": False, "orders": [],
                "error": "Gmail not configured — add GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN"}
    try:
        q = f'from:{SENDER} subject:{SUBJECT} newer_than:{LOOKBACK}'
        res = svc.users().messages().list(userId="me", q=q, maxResults=100).execute()
        msgs = res.get("messages", [])
        orders, seen, total_spend = [], set(), 0.0
        for msg in msgs:
            m = svc.users().messages().get(userId="me", id=msg["id"], format="full").execute()
            hdrs = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
            subject = hdrs.get("Subject", "")
            body = _body_text(m.get("payload", {}))
            iso = _email_date(hdrs)
            no = _order_no(subject, body)
            key = no or msg["id"]
            if key in seen:
                continue
            seen.add(key)
            total = _total(body)
            items = _items(body)
            if total:
                try:
                    total_spend += float(total.replace("£", "").replace(",", ""))
                except ValueError:
                    pass
            title = ", ".join(items) if items else (f"Order {no}" if no else "T-Shirt Studio order")
            orders.append({
                "date": iso,
                "order_no": no,
                "total": total,
                "items": items,
                "title": (title[:80] + ("…" if len(title) > 80 else "")),
            })
        orders.sort(key=lambda o: o.get("date") or "", reverse=True)
        return {
            "configured": True,
            "orders": orders,
            "count": len(orders),
            "total_spend": round(total_spend, 2),
            "matched_query": q,
        }
    except Exception as ex:
        return {"configured": True, "orders": [], "error": str(ex)}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
