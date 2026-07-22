"""
Connect JoJo's Gmail to the coach dashboard — ONE-TIME setup.

Run this on a computer with a web browser (e.g. Grace's laptop) with JoJo there
to sign in. It opens a Google sign-in window; JoJo signs in and clicks Allow;
the script then prints ONE value to add as a GitHub secret.

Google no longer shows the client secret on screen — you DOWNLOAD it instead:
  1. Google Cloud Console → APIs & Services → Credentials.
  2. Next to your OAuth 2.0 Client ID, click the download icon (⬇) → "Download JSON".
     It saves a file like  client_secret_1234....json  (usually to Downloads).
  3. (If needed) install the helper once:  pip3 install google-auth-oauthlib
  4. Run:  python3 setup_jojo_gmail.py
     It auto-finds that JSON in Downloads/Desktop/this folder. If it can't, it
     asks you to paste the full path to the file.
  5. A browser opens — JoJo signs in as jojo@theforge.pt and clicks Allow.
     (If Google says her email isn't an approved tester: add jojo@theforge.pt
      under OAuth consent screen → Test users, then run this again.)
  6. Copy the printed value into GitHub → repo Settings → Secrets and variables →
     Actions → New repository secret,  named  GMAIL_REFRESH_TOKEN_COACH.
  7. Run the "Refresh Dashboard Data" workflow — JoJo's inbox card fills in.
"""
import os
import glob
from google_auth_oauthlib.flow import InstalledAppFlow

# Read-only access to her inbox is all the dashboard needs.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def find_client_json():
    """Look for a downloaded OAuth client-secret JSON in the usual places."""
    folders = [os.getcwd(),
               os.path.expanduser("~/Downloads"),
               os.path.expanduser("~/Desktop")]
    hits = []
    for d in folders:
        hits += glob.glob(os.path.join(d, "client_secret*.json"))
        hits += glob.glob(os.path.join(d, "client_secret*.apps.googleusercontent.com.json"))
    # newest first
    hits = sorted(set(hits), key=lambda p: os.path.getmtime(p), reverse=True)
    return hits[0] if hits else None


json_path = os.environ.get("GMAIL_CLIENT_JSON") or find_client_json()
if json_path and os.path.exists(json_path):
    print(f"Using downloaded credentials file:\n  {json_path}\n")
    flow = InstalledAppFlow.from_client_secrets_file(json_path, SCOPES)
else:
    print("Couldn't find a downloaded client_secret*.json in Downloads/Desktop/this folder.")
    typed = input("Paste the full path to the downloaded .json file "
                  "(or just press Enter to type the ID/secret manually): ").strip().strip('"')
    if typed:
        flow = InstalledAppFlow.from_client_secrets_file(typed, SCOPES)
    else:
        cid = input("Client ID: ").strip()
        csec = input("Client secret: ").strip()
        flow = InstalledAppFlow.from_client_config({"installed": {
            "client_id": cid, "client_secret": csec,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        }}, SCOPES)

print("\nA browser window will open — sign in as JoJo (jojo@theforge.pt) and click Allow.\n")
creds = flow.run_local_server(port=0)

print("\n" + "=" * 62)
print("SUCCESS — add this ONE GitHub secret in the forgedashboard repo:")
print("(Settings -> Secrets and variables -> Actions -> New repository secret)")
print("=" * 62)
print("\n   Name:   GMAIL_REFRESH_TOKEN_COACH")
print(f"\n   Value:  {creds.refresh_token}\n")
print("Then run the 'Refresh Dashboard Data' workflow and JoJo's inbox appears.")
