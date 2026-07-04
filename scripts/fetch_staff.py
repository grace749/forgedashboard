"""
Staff page — per coach: which classes they teach (from TeamUp), their role, and
an AI team-utilisation review (where staff are under/over-used, coverage gaps).

Roles come from the TeamUp instructor description for now; when Grace shares the
coach-tasks KPI sheet, responsibilities can be enriched from it.
"""
import os, time, requests
from datetime import date, timedelta
from collections import Counter
import ai

TEAMUP_API_KEY = os.environ["TEAMUP_API_KEY"]
BASE = "https://goteamup.com/api/v2"
HEADERS = {"Authorization": f"Token {TEAMUP_API_KEY}"}

STAFF_SYSTEM = (
    "You are a fitness studio operations manager for The Forge, a women's-only gym "
    "in Belfast. Looking at how the coaching team's time was spread across classes "
    "and 1:1s LAST MONTH, give practical suggestions on using the team better: where "
    "a coach is under-used or over-loaded, gaps in coverage (e.g. a popular class "
    "leaning on one person), and how to balance the timetable. 4-6 short bullet "
    "points, plain UK English, no preamble."
)


def _get(url, params=None, max_retries=5):
    delay = 2
    for _ in range(max_retries):
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", delay)))
            delay = min(delay * 2, 30)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def _get_all(endpoint, params=None):
    results, url, p = [], f"{BASE}/{endpoint}", dict(params or {})
    while url:
        data = _get(url, p).json()
        results.extend(data.get("results", []))
        url, p = data.get("next"), None
    return results


def run():
    instructors = _get_all("instructors", {"page_size": 50})
    # Previous full calendar month
    today = date.today()
    first_this = today.replace(day=1)
    last_prev  = first_this - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    since, until = first_prev.isoformat(), last_prev.isoformat()
    period = first_prev.strftime("%B %Y")

    coaches = []
    for ins in instructors:
        events = _get_all("events", {
            "instructors": ins["id"],
            "starts_at_gte": since,
            "starts_at_lte": until,
            "page_size": 100,
        })
        counts = Counter()
        for e in events:
            if e.get("status") == "cancelled":
                continue
            nm = (e.get("name") or "").strip()
            if nm:
                counts[nm] += 1
        coaches.append({
            "name":     ins.get("name", ""),
            "role":     ins.get("description") or "",
            "picture":  ins.get("picture_url") or "",
            "sessions": sum(counts.values()),
            "classes":  [{"name": n, "count": c} for n, c in counts.most_common()],
        })

    coaches.sort(key=lambda c: -c["sessions"])

    def _cls(c):
        return ", ".join(f"{x['name']} x{x['count']}" for x in c["classes"][:6])
    summary = "; ".join(
        f"{c['name']} ({c['role']}): {c['sessions']} sessions in {period} — {_cls(c)}"
        for c in coaches if c["sessions"]
    )
    advice = ai.generate(
        STAFF_SYSTEM,
        f"Coaching team last month ({period}):\n{summary}\n\n"
        "How should Grace use the team better — where are people under-used, "
        "over-loaded, or is coverage too dependent on one coach?",
        max_tokens=420,
    ) or (
        "**Team utilisation**\n"
        "• Check whether any single coach is carrying most of the popular classes — "
        "cross-train a second coach so you're not exposed if they're off.\n"
        "• If a coach has low session numbers, give them more classes or a clear "
        "secondary role (nutrition, onboarding, follow-ups).\n"
        "• Balance 1:1 PT load across JoJo and Eilis so neither is a bottleneck.\n"
        "• Match your busiest class times to your strongest coaches."
    )

    return {"coaches": coaches, "advice": advice, "period": period}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
