"""Fetch today's Asana tasks for the Forge workspace."""
import os, json, requests
from datetime import date

ASANA_PAT = os.environ["ASANA_PAT"]
HEADERS = {"Authorization": f"Bearer {ASANA_PAT}", "Accept": "application/json"}
BASE = "https://app.asana.com/api/1.0"


def get_workspace_gid():
    r = requests.get(f"{BASE}/workspaces", headers=HEADERS)
    r.raise_for_status()
    workspaces = r.json()["data"]
    # Use first workspace — adjust if Grace has multiple
    return workspaces[0]["gid"]


# Coaches whose own Asana tasks we also surface (for their coach dashboard).
COACH_EMAILS = ["jojo@theforge.pt"]


def _incomplete_tasks(workspace_gid, assignee):
    params = {
        "workspace": workspace_gid,
        "assignee": assignee,
        "completed": False,
        "opt_fields": "name,due_on,completed,permalink_url,projects.name,notes",
    }
    r = requests.get(f"{BASE}/tasks", headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()["data"]


def _find_user_gid(workspace_gid, email):
    r = requests.get(f"{BASE}/users", headers=HEADERS,
                     params={"workspace": workspace_gid, "opt_fields": "email"})
    r.raise_for_status()
    for u in r.json().get("data", []):
        if (u.get("email") or "").lower() == email.lower():
            return u["gid"]
    return None


def _format(tasks):
    today = date.today().isoformat()
    result = []
    for t in tasks:
        if t.get("completed"):
            continue
        due = t.get("due_on")
        result.append({
            "gid": t["gid"], "name": t["name"], "completed": False,
            "url": t.get("permalink_url"),
            "project": t["projects"][0]["name"] if t.get("projects") else None,
            "due_on": due, "overdue": bool(due and due < today),
            "notes": (t.get("notes") or "").strip(),
        })
    result.sort(key=lambda x: (0 if x["overdue"] else (1 if x["due_on"] else 2), x["due_on"] or ""))
    return result


def run():
    workspace_gid = get_workspace_gid()
    result = _format(_incomplete_tasks(workspace_gid, "me"))
    # Also each coach's own tasks (keyed by email) for their dashboard.
    coach = {}
    for email in COACH_EMAILS:
        try:
            gid = _find_user_gid(workspace_gid, email)
            if gid:
                coach[email] = _format(_incomplete_tasks(workspace_gid, gid))
                print(f"[asana] {email}: {len(coach[email])} tasks")
            else:
                print(f"[asana] {email} not found in workspace")
        except Exception as ex:
            print(f"[asana] coach {email} failed: {ex}")
    return {"tasks_today": result, "coach": coach}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
