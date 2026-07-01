"""
One-time script to get a Gmail OAuth2 refresh token.
Run this ONCE locally, then save the printed values as GitHub secrets.

Steps:
  1. Go to Google Cloud Console → APIs & Services → Credentials
  2. Create an OAuth 2.0 Client ID (Desktop app type)
  3. Download the JSON and paste client_id and client_secret below (or set as env vars)
  4. Run: python scripts/setup_gmail_oauth.py
  5. A browser window opens — sign in as grace@theforge.pt and allow access
  6. Copy the printed refresh token and add it as GitHub secret GMAIL_REFRESH_TOKEN

You also need to enable the Gmail API in your Google Cloud project:
  APIs & Services → Library → Gmail API → Enable
"""
import os, json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",  # for creating drafts
]

# Fill these in or set as env vars
CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "YOUR_CLIENT_ID_HERE")
CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "YOUR_CLIENT_SECRET_HERE")

client_config = {
    "installed": {
        "client_id":                  CLIENT_ID,
        "client_secret":              CLIENT_SECRET,
        "auth_uri":                   "https://accounts.google.com/o/oauth2/auth",
        "token_uri":                  "https://oauth2.googleapis.com/token",
        "redirect_uris":              ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    }
}

flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
creds = flow.run_local_server(port=0)

print("\n" + "="*60)
print("Add these as GitHub secrets in your forgedashboard repo:")
print("="*60)
print(f"\nGMAIL_CLIENT_ID     = {CLIENT_ID}")
print(f"GMAIL_CLIENT_SECRET = {CLIENT_SECRET}")
print(f"GMAIL_REFRESH_TOKEN = {creds.refresh_token}")
print("\nDone! You can also set GOOGLE_CALENDAR_ID = grace@theforge.pt")
print("and GOOGLE_IMPERSONATE_EMAIL = grace@theforge.pt if you set up domain-wide delegation.")
