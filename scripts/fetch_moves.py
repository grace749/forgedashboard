"""
Build the Programming move library from The Forge's programme Google Docs.

Reads the "Q4 Program Movements" Google Doc (movement name → Instagram/YouTube
demo link, one per exercise) via the Docs API using the same service account as
the sheet fetchers, and returns a de-duplicated list of moves with an inferred
body part:

    [{ "name": "Barbell Bent-Over Row", "part": "Back",
       "video": "https://www.instagram.com/p/…", "source": "Upper 1.0" }, …]

The doc must be shared (viewer) with the service-account email — the same one
the KPI / SOP sheets are shared with. If it isn't (or creds are missing), we
return [] and the dashboard falls back to its built-in starter set.

Add more docs by extending DOC_IDS.
"""
import os, re, json

# The programme movement Google Docs (each move has an Instagram/YouTube demo
# link). All must be shared (viewer) with the service-account email.
DOC_IDS = [
    ("Q4 2026", "15MqmvBJ4eRchsyEMl9SITiFz54QDb0FgDzVnRabI3_o"),  # "Q4 Program Movements"
    ("Q3 2026", "1N7tw8Wt_Y7PebIu-KCYm5tEkNORWorb2YRd8zIJkBWA"),  # "Q3 2026"
    ("Q2 2026", "12xgn_rp3gK0uMtJKjn27wNoSTWbre0tF41WaIqHKe5k"),  # "Q2 2026"
    ("RenewYou 12-Week", "1LTBJj7Nzymrt-SAIVHguw05HbFsRKuIPa7hpPLQfUtw"),  # "RenewYou_12Week_Training_Programmes"
    ("Strongevity", "10IoijPaP7_nVdZrJRTIHrf12FlcdxnavKSAUoaO4fwk"),  # "Strongevity Programme"
]
# PDF programmes (older cycles) — text only, NO embedded demo links, so these
# contribute move NAMES (with a YouTube-search fallback), not the Forge's clips.
PDF_IDS = [
    ("Q1 2026", "1QNCCpoqVZQmnoPJ6WaayehkJVv6r5D5T"),  # "_Forge Program Q1 2026.pdf"
]
SCOPES = ["https://www.googleapis.com/auth/documents.readonly",
          "https://www.googleapis.com/auth/drive.readonly"]

# Keyword → body part. First match wins (order matters: specific before general).
_PART_RULES = [
    ("Biceps",     ["bicep", "curl", " 21", "bicep curl"]),
    ("Triceps",    ["tricep", "pushdown", "skull crusher", "kickback", "overhead extension", "titan press", "dip "]),
    ("Back",       ["row", "pulldown", "pull down", "pull-down", "pullover", "pull over", "shrug", "lat ", "ring row", "renegade", "supinated row", "good morning", "y t w", "ytw", "w raise", "band pull apart", "face pull", "jefferson"]),
    ("Chest",      ["bench press", "chest press", "fly", "flies", "push up", "push-up", "press up", "floor press", "chest"]),
    ("Shoulders",  ["shoulder press", "overhead press", "lateral raise", "front raise", "high pull", "halo", "push press", "arnold", "overhead raise", "y raise", "snatch", "clean & push", "gtoh", "goth", "shoulder raise"]),
    ("Hamstrings", ["rdl", "romanian", "hinge", "hamstring", "single leg deadlift", "good morning"]),
    ("Glutes",     ["glute", "hip thrust", "bridge", "abduction", "fire hydrant", "kickback", "hip opener", "banded lateral", "hip dip"]),
    ("Quads",      ["squat", "lunge", "step up", "step-up", "split squat", "wall sit", "leg press", "sissy", "pistol", "wall sit"]),
    ("Calves",     ["calf", "heel raise"]),
    ("Core",       ["plank", "dead bug", "deadbug", "russian twist", "wood chop", "woodchop", "crunch", "leg raise", "leg lift", "hollow", "wiper", "sit up", "sit-up", "knee tuck", "pike", "mountain climber", "bird dog", "toe tap", "windmill", "side bend", "flutter", "wall ball side", "reverse crunch", "rotation"]),
    ("Cardio",     ["ski", "row ", "rower", "c2 bike", "concept2", "bike", "assault", "sled", "run", " cal", "burpee", "battle rope", "battlerope", "erg"]),
    ("Full Body",  ["clean", "thruster", "devils press", "get up", "get-up", "complex", "atlas", "gorilla", "cluster", "power clean", "farmers", "carry", "bear crawl", "slam", "wall ball", "deadlift", "commando"]),
]

# Lines that are section/class headings, not moves.
_HEADINGS = re.compile(r"^(LOWER|UPPER|FULL BODY|ELEVATE|PLATE|CIRCUITS|CIRCUIT|MOBILITY|ENGINE|CAPACITY|BLOCK|SESSION|SUPERSET|WORKOUT|STRENGTH|COMPLEX|PARTNER|MOBILITY EMOM|MIN \d|Q4|WORKOUT MOVEMENTS)\b", re.I)
_LABELS = re.compile(r"^\s*(MAIN MOVE|ACCESSORY\s*\d*|MOVE\s*\d*|MIN\s*\d+|\d+[.)]|SUPERSET\s*\d*|PARTNER (ONE|TWO|THREE)[^:]*)\s*[:.\-]?\s*", re.I)


def _creds(creds_info):
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)


def _svc(creds):
    from googleapiclient.discovery import build
    return build("docs", "v1", credentials=creds, cache_discovery=False)


def _pdf_extract(text, source):
    """Best-effort NAME extraction from a flattened programme PDF (no links).
    Pulls numbered circuit/plate/mobility list items and MAIN MOVE/ACCESSORY
    lines; skips the rep-scheme noise."""
    moves = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(MAIN MOVE|ACCESSORY\s*\d*)\s*[:\-]\s*(.+)$", line, re.I)
        if m:
            nm = _clean_name(m.group(2))
            if _looks_like_move(nm) and len(nm.split()) <= 6:
                moves.append({"name": nm, "video": "", "source": source})
            continue
        # numbered exercise list on one line: "1 C2 BIKE 2 DB STEP OVERS 3 …"
        if re.match(r"^\d+[.)\s]", line) and len(re.findall(r"\b\d+[.)\s]", line)) >= 2:
            for seg in re.split(r"\s*\b\d+[.)\s]+", " " + line):
                nm = _clean_name(seg)
                if _looks_like_move(nm) and 1 <= len(nm.split()) <= 5 and not re.search(r"\b(rep|sec|min|round|set|level|cal)\b", nm, re.I):
                    moves.append({"name": nm, "video": "", "source": source})
    return moves


def _pdf_moves(creds):
    from googleapiclient.discovery import build
    try:
        from pypdf import PdfReader
    except Exception as ex:
        print(f"[moves] pypdf not available: {ex}")
        return []
    import io
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    out = []
    for source, fid in PDF_IDS:
        try:
            data = drive.files().get_media(fileId=fid).execute()
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
            found = _pdf_extract(text, source)
            print(f"[moves] {source} (PDF): {len(found)} name candidates")
            out.extend(found)
        except Exception as ex:
            print(f"[moves] could not read {source} PDF ({fid}) — shared with the service account? {ex}")
    return out


def _infer_part(name):
    low = " " + name.lower() + " "
    for part, kws in _PART_RULES:
        if any(k in low for k in kws):
            return part
    return "Full Body"


def _clean_name(raw):
    """Strip labels, rep counts, video refs, markdown → a clean move name."""
    n = (raw or "").strip()
    n = _LABELS.sub("", n)
    n = re.sub(r"\*+", "", n)                                   # markdown bold
    n = re.sub(r"\(?\b(slide|video|vid|move|min)\s*\d+\b\)?", "", n, flags=re.I)
    n = re.sub(r"\bimg[_ ]?index=?\d*\b", "", n, flags=re.I)
    n = re.sub(r"\b\d+\s*(reps?|cals?|m|min|sec|secs|kg|rounds?)\b.*$", "", n, flags=re.I)  # trailing rep/dose text
    n = re.sub(r"^\d+\s*[xX]\s*", "", n)                        # leading "3x"
    n = re.sub(r"^\d+\s+(?=[A-Za-z])", "", n)                   # leading rep count "15 Cable…"
    n = re.sub(r"\s*[xX]\s*\d+\b", "", n)                       # "x2"
    n = re.sub(r"https?://\S+", "", n)                          # any stray url
    n = re.sub(r"[\(\[][^)\]]*[\)\]]\s*$", "", n).strip()       # trailing parenthetical
    n = re.sub(r"\s{2,}", " ", n).strip(" -–—:·")
    # Normalise ALL-CAPS to Title Case for readability
    if n and n == n.upper():
        n = n.title()
    return n.strip()


def _looks_like_move(name):
    if not name or len(name) < 3:
        return False
    if _HEADINGS.match(name):
        return False
    if not re.search(r"[A-Za-z]{3}", name):
        return False
    if name.lower() in {"rest", "coaches choice", "off rack", "reps", "video", "movement",
                        "follow reps", "max reps", "follow coach for sets", "work in your pods",
                        "use challenging weight", "team workout", "partner workout", "pod workout",
                        "work at your own pace", "mobility to finish", "strength"}:
        return False
    return True


def _paragraph_text_and_links(para):
    """Return (full_text, [(linked_text, url)]) for a Doc paragraph."""
    text, links = "", []
    for el in para.get("elements", []):
        tr = el.get("textRun")
        if not tr:
            continue
        content = tr.get("content", "")
        text += content
        url = ((tr.get("textStyle") or {}).get("link") or {}).get("url")
        if url:
            links.append((content.strip(), url))
    return text.strip(), links


def _extract(doc, source):
    """Walk the doc, pairing each move name with its demo link."""
    moves, prev_name = [], None
    body = (doc.get("body") or {}).get("content", [])
    for block in body:
        para = block.get("paragraph")
        if not para:
            continue
        text, links = _paragraph_text_and_links(para)
        if not text:
            continue
        # bare URL sitting on its own line → belongs to the previous move name
        bare = re.match(r"^<?(https?://\S+?)>?$", text)
        if bare and prev_name:
            moves.append({"name": prev_name, "video": bare.group(1), "source": source})
            continue
        # links whose anchor text is the move name (e.g. "[Reverse Grip DB Thrusters](url)")
        used_inline = False
        for anchor, url in links:
            nm = _clean_name(anchor)
            if _looks_like_move(nm):
                moves.append({"name": nm, "video": url, "source": source})
                used_inline = True
        # otherwise treat the line as a (possibly linkless) move name and remember it
        name = _clean_name(re.sub(r"https?://\S+", "", text))
        if _looks_like_move(name):
            if links and not used_inline:            # name line that also carried a url
                moves.append({"name": name, "video": links[0][1], "source": source})
            prev_name = name
        else:
            prev_name = None
    return moves


def run():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        print("[moves] GOOGLE_SERVICE_ACCOUNT_JSON not set — skipping")
        return []
    try:
        creds = _creds(json.loads(raw))
        svc = _svc(creds)
    except Exception as ex:
        print(f"[moves] auth failed: {ex}")
        return []

    all_moves = []
    for source, doc_id in DOC_IDS:
        try:
            doc = svc.documents().get(documentId=doc_id).execute()
            found = _extract(doc, source)
            print(f"[moves] {source}: {len(found)} raw moves")
            all_moves.extend(found)
        except Exception as ex:
            print(f"[moves] could not read {source} ({doc_id}) — is it shared with the service account? {ex}")

    all_moves.extend(_pdf_moves(creds))   # older PDF programmes (names only)

    # De-dupe by normalised name, keep the first demo link seen, add body part.
    seen, out = set(), []
    for m in all_moves:
        key = m["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": m["name"], "part": _infer_part(m["name"]),
                    "video": m["video"], "source": m.get("source", "")})
    out.sort(key=lambda x: x["name"])
    print(f"[moves] {len(out)} unique moves")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
