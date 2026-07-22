"""
Connect JoJo's Gmail to the coach dashboard — ONE-TIME setup.

Run this on a computer with a web browser (e.g. Grace's laptop) with JoJo there
to sign in. It opens a Google sign-in window; JoJo signs in and clicks Allow;
the script then prints ONE value to add as a GitHub secret. That's it.

  1. (If needed) install the helper:  pip install google-auth-oauthlib
  2. Run:  python scripts/setup_jojo_gmail.py
  3. It asks for the Client ID and Client Secret — get them from
     Google Cloud Console → APIs & Services → Credentials → your OAuth 2.0
     Client ID (the same one already used for the dashboard).
  4. A browser opens — JoJo signs in as jojo@theforge.pt and clicks Allow.
     (If Google says her email isn't an approved tester: add jojo@theforge.pt
      under OAuth consent screen → Test users, then run this again.)
  5. Copy the printed value into GitHub → repo Settings → Secrets and variables →
     Actions → New repository secret,  named  GMAIL_REFRESH_TOKEN_COACH.
  6. Run the "Refresh Dashboard Data" workflow — JoJo's inbox card fills in.
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Read-only access to her inbox is all the dashboard needs.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

CLIENT_ID = (os.environ.get("GMAIL_CLIENT_ID")
             or input("Paste GMAIL_CLIENT_ID (Google Cloud Console → Credentials): ").strip())
CLIENT_SECRET = (os.environ.get("GMAIL_CLIENT_SECRET")
                 or input("Paste GMAIL_CLIENT_SECRET: ").strip())

client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    }
}

print("\nA browser window will open — sign in as JoJo (jojo@theforge.pt) and click Allow.\n")
flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
creds = flow.run_local_server(port=0)

print("\n" + "=" * 62)
print("SUCCESS — add this ONE GitHub secret in the forgedashboard repo:")
print("(Settings -> Secrets and variables -> Actions -> New repository secret)")
print("=" * 62)
print("\n   Name:   GMAIL_REFRESH_TOKEN_COACH")
print(f"\n   Value:  {creds.refresh_token}\n")
print("Then run the 'Refresh Dashboard Data' workflow and JoJo's inbox appears.")
