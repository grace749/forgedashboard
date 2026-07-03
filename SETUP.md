# Forge Dashboard — Setup Guide

Step-by-step for the bits that need you to add credentials or connect a service.
Everything here is one-time setup.

---

## A. Adding a secret in GitHub (you'll do this a few times)

A "secret" is a password the automated job uses. It's stored safely and never
shown in the code.

1. Go to your repo on GitHub: **github.com/grace749/forgedashboard**
2. Click **Settings** (top menu, far right).
3. Left sidebar → **Secrets and variables** → **Actions**.
4. Click the green **New repository secret** button.
5. **Name** = the exact name in CAPITALS (e.g. `SLACK_USER_TOKEN`).
   **Secret** = the value you're pasting.
6. Click **Add secret**. Done — repeat for each one.

To make a change take effect immediately: **Actions** tab → **Refresh Dashboard
Data** → **Run workflow**.

---

## B. Slack panel

Needs a Slack token so the dashboard can see your DMs and mentions.

1. Go to **api.slack.com/apps** → **Create New App** → **From scratch**.
2. Name it "Forge Dashboard", pick your workspace, **Create App**.
3. Left sidebar → **OAuth & Permissions**.
4. Scroll to **User Token Scopes** (NOT bot scopes) → **Add an OAuth Scope** and
   add these four: `im:read`, `im:history`, `users:read`, `search:read`.
5. Scroll up → **Install to Workspace** → **Allow**.
6. Copy the **User OAuth Token** (starts with `xoxp-`).
7. In GitHub, add a secret (section A) named **`SLACK_USER_TOKEN`**, value = that token.

That's it — the Slack panel on the Home page will fill in on the next refresh.

---

## C. InBody scan list

Needs your Lookin'Body login so the dashboard can read scan dates.

1. Add two GitHub secrets (section A):
   - **`INBODY_LOGIN_ID`** = `grace001`
   - **`INBODY_PASSWORD`** = your Lookin'Body password
2. Refresh the workflow.

(Note: the InBody admin feed still hides real member names even with masking off,
so names may show as usernames. That's a separate fix I'm working on.)

---

## D. WhatsApp / webchat enquiries via GoHighLevel (Systemize)

This makes inbound WhatsApp and website-chat messages show up in your daily brief.
It works by having Zapier copy each new message into a Google Sheet the dashboard
reads.

### D1. Create the sheet
1. Open the Google Sheet the dashboard already uses for GHL
   (or make a new one). Add a tab named exactly **`GHL Conversations`**.
2. Put these headers in row 1, one per column:
   `Date | Name | Channel | Message`
3. Click **Share** (top right) → paste this address → give **Viewer** access →
   **Send**:
   ```
   forge-dashboard@forge-dashboard-473821.iam.gserviceaccount.com
   ```

### D2. Set up the Zap
1. Go to **zapier.com** → **Create Zap**.
2. **Trigger**: search **GoHighLevel** (or "LeadConnector") → event
   **"New Inbound Message"** → connect your account.
3. **Action**: search **Google Sheets** → event **"Create Spreadsheet Row"**.
4. Pick your spreadsheet and the **GHL Conversations** tab.
5. Map the columns:
   - Date → the message's *Date/Created* field
   - Name → the *Contact Name*
   - Channel → the *Message Type* (WhatsApp / SMS / etc.)
   - Message → the *Message Body*
6. **Publish** the Zap.

Once messages start flowing in, they appear in the Home page daily brief under
"WhatsApp & webchat".

---

## E. Member check-ins (already working, no setup)

Check-ins are read automatically from your Gmail — any email with the subject
**"Client Reflection"**. They fill the "Last check-in" column in the Member
Directory. If names don't match, tell Claude what one of those emails looks like.
