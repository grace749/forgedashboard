"""Fetch new conversations from GoHighLevel (WhatsApp, webchat, forms, SMS)."""
import os, json
from datetime import datetime, timezone, timedelta
import requests as http

GHL_API_KEY   = os.environ.get("GHL_API_KEY", "")
LOCATION_ID   = "eJ81HuWlVVSQQQaqsZ6X"
BASE          = "https://services.leadconnectorhq.com"
HEADERS       = {
    "Authorization": f"Bearer {GHL_API_KEY}",
    "Version":       "2021-04-15",
    "Accept":        "application/json",
}

CHANNEL_LABELS = {
    "TYPE_WHATSAPP":  "WhatsApp",
    "TYPE_WEBCHAT":   "Webchat",
    "TYPE_SMS":       "SMS",
    "TYPE_EMAIL":     "Email",
    "TYPE_INSTAGRAM": "Instagram",
    "TYPE_FACEBOOK":  "Facebook",
    "TYPE_GMB":       "Google",
    "TYPE_CALL":      "Call",
}


def run():
    if not GHL_API_KEY:
        return {"error": "GHL_API_KEY not set"}
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=3)

        # Search for recent unread/open conversations
        r = http.get(
            f"{BASE}/conversations/search",
            headers=HEADERS,
            params={
                "locationId": LOCATION_ID,
                "status":     "open",
                "limit":      40,
                "sortBy":     "last_message_date",
                "sortOrder":  "desc",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        convs = data.get("conversations", [])

        leads = []
        for c in convs:
            # Filter to recent conversations
            last_msg_raw = c.get("lastMessageDate") or c.get("dateUpdated") or ""
            try:
                if last_msg_raw:
                    # GHL returns milliseconds epoch or ISO string
                    if isinstance(last_msg_raw, (int, float)):
                        last_dt = datetime.fromtimestamp(last_msg_raw / 1000, tz=timezone.utc)
                    else:
                        last_dt = datetime.fromisoformat(last_msg_raw.replace("Z", "+00:00"))
                    if last_dt < cutoff:
                        continue
                    age_hours = int((datetime.now(timezone.utc) - last_dt).total_seconds() / 3600)
                    age_label = f"{age_hours}h ago" if age_hours < 24 else f"{age_hours // 24}d ago"
                else:
                    age_label = ""
            except Exception:
                age_label = ""

            # Skip if Grace sent the last message (already responded)
            last_msg_direction = c.get("lastMessageDirection", "")
            if last_msg_direction == "outbound":
                continue

            channel_type = c.get("type", "")
            channel      = CHANNEL_LABELS.get(channel_type, channel_type.replace("TYPE_", "").title())

            contact_name  = c.get("contactName") or c.get("fullName") or "Unknown"
            last_msg_body = (c.get("lastMessage") or c.get("lastMessageBody") or "")[:200]
            unread        = c.get("unreadCount", 0)

            leads.append({
                "name":      contact_name,
                "channel":   channel,
                "message":   last_msg_body,
                "age":       age_label,
                "unread":    unread,
                "conv_id":   c.get("id", ""),
                "contact_id": c.get("contactId", ""),
            })

        return leads

    except Exception as ex:
        return {"error": str(ex)}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
