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


def get_tasks_due_today(workspace_gid):
    today = date.today().isoformat()
    params = {
        "workspace": workspace_gid,
        "assignee": "me",
        "due_on": today,
        "opt_fields": "name,due_on,completed,permalink_url,projects.name",
    }
    r = requests.get(f"{BASE}/tasks", headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()["data"]


def run():
    workspace_gid = get_workspace_gid()
    tasks = get_tasks_due_today(workspace_gid)
    return {
        "tasks_today": [
            {
                "name": t["name"],
                "completed": t["completed"],
                "url": t.get("permalink_url"),
                "project": t["projects"][0]["name"] if t.get("projects") else None,
            }
            for t in tasks
        ]
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
