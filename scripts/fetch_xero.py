"""Fetch financial KPIs from Xero using OAuth2 with stored refresh token."""
import os, json, requests
from datetime import date, timedelta

CLIENT_ID = os.environ["XERO_CLIENT_ID"]
CLIENT_SECRET = os.environ["XERO_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["XERO_REFRESH_TOKEN"]
TOKEN_URL = "https://identity.xero.com/connect/token"
BASE = "https://api.xero.com/api.xro/2.0"

# GitHub Actions writes the new refresh token back as a repo secret via the GH API
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GITHUB_REPOSITORY")


def refresh_access_token():
    r = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    })
    r.raise_for_status()
    data = r.json()
    return data["access_token"], data["refresh_token"]


def get_tenant_id(access_token):
    r = requests.get("https://api.xero.com/connections", headers={"Authorization": f"Bearer {access_token}"})
    r.raise_for_status()
    return r.json()[0]["tenantId"]


def get_report(access_token, tenant_id, report_name, params=None):
    r = requests.get(
        f"{BASE}/Reports/{report_name}",
        headers={"Authorization": f"Bearer {access_token}", "Xero-tenant-id": tenant_id, "Accept": "application/json"},
        params=params or {},
    )
    r.raise_for_status()
    return r.json()


def extract_pl_value(report, account_name):
    """Pull a line value from a Profit & Loss report by account name."""
    for section in report.get("Reports", [{}])[0].get("Rows", []):
        for row in section.get("Rows", []):
            cells = row.get("Cells", [])
            if cells and cells[0].get("Value", "").lower() == account_name.lower():
                try:
                    return float(cells[1]["Value"].replace(",", ""))
                except (IndexError, ValueError, KeyError):
                    return None
    return None


def update_github_secret(secret_name, secret_value):
    """Rotate the refresh token stored as a GitHub Actions secret."""
    if not GH_TOKEN or not GH_REPO:
        return
    import base64, nacl.encoding, nacl.public

    pub_r = requests.get(
        f"https://api.github.com/repos/{GH_REPO}/actions/secrets/public-key",
        headers={"Authorization": f"token {GH_TOKEN}"},
    )
    pub_r.raise_for_status()
    key_data = pub_r.json()
    public_key = nacl.public.PublicKey(key_data["key"].encode(), nacl.encoding.Base64Encoder())
    sealed = nacl.public.SealedBox(public_key).encrypt(secret_value.encode())
    encrypted = base64.b64encode(sealed).decode()

    requests.put(
        f"https://api.github.com/repos/{GH_REPO}/actions/secrets/{secret_name}",
        headers={"Authorization": f"token {GH_TOKEN}"},
        json={"encrypted_value": encrypted, "key_id": key_data["key_id"]},
    ).raise_for_status()


def run():
    access_token, new_refresh_token = refresh_access_token()

    # Rotate the stored refresh token so the next run still works
    if new_refresh_token != REFRESH_TOKEN:
        update_github_secret("XERO_REFRESH_TOKEN", new_refresh_token)

    tenant_id = get_tenant_id(access_token)

    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    pl = get_report(access_token, tenant_id, "ProfitAndLoss", {  # requires accounting.reports.profitandloss.read
        "fromDate": last_month_start.isoformat(),
        "toDate": last_month_end.isoformat(),
    })

    return {
        "period": last_month_start.strftime("%B %Y"),
        "revenue": extract_pl_value(pl, "Total Income"),
        "direct_wages": extract_pl_value(pl, "Direct Wages"),
        "rent": extract_pl_value(pl, "Rent"),
        "marketing": extract_pl_value(pl, "Advertising & Marketing"),
        "cleaning": extract_pl_value(pl, "Cleaning"),
        "it_software": extract_pl_value(pl, "IT Software and Consumables"),
        "electricity": extract_pl_value(pl, "Light, Power, Heating"),
        "telephone": extract_pl_value(pl, "Telephone & Internet"),
        "general_expenses": extract_pl_value(pl, "General Expenses"),
        "total_expenses": extract_pl_value(pl, "Total Operating Expenses"),
        "gross_profit": extract_pl_value(pl, "Gross Profit"),
        "net_profit": extract_pl_value(pl, "Net Profit"),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
