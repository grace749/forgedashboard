"""
One-off: discover the correct Lookin'Body export field codes for the extended
InBody metrics (visceral fat, protein, mineral, InBody age, hydration).

The export API identifies each column by an internal (TABLE, FIELD) code that the
Lookin'Body UI never shows. This logs in (same creds as fetch_inbody), then for a
short list of candidate codes per metric, exports that ONE column against a sample
of members and reports which candidate actually returns numbers. Whatever it
prints as "USE …" is what we plug into fetch_inbody.EXTRA_COLS.

Run it from GitHub Actions (workflow: "Discover InBody Columns") so it uses the
stored INBODY_LOGIN_ID / INBODY_PASSWORD secrets — no local setup needed.
"""
import os
import requests
import fetch_inbody as fi

# Candidate (TABLE, FIELD) codes to try for each metric, best guess first.
CANDIDATES = {
    "Hydration / Total Body Water": [
        ("BCA_TBL", "TBW"), ("BCA_TBL", "TBW_WT"), ("MFA_TBL", "TBW"),
    ],
    "Protein": [
        ("BCA_TBL", "PROTEIN"), ("BCA_TBL", "PROTEIN_WT"), ("BCA_TBL", "PROT"),
        ("BCA_TBL", "TP"), ("MFA_TBL", "PROTEIN"),
    ],
    "Mineral": [
        ("BCA_TBL", "MINERAL"), ("BCA_TBL", "MINERALS"), ("BCA_TBL", "TM"),
        ("BCA_TBL", "OSMINERAL"), ("BCA_TBL", "BONE_MINERAL"), ("MFA_TBL", "MINERAL"),
    ],
    "Visceral Fat": [
        ("MFA_TBL", "VFL"), ("BCA_TBL", "VFL"), ("MFA_TBL", "VFA"),
        ("BCA_TBL", "VFA"), ("MFA_TBL", "VISCERAL_FAT_LEVEL"), ("MFA_TBL", "VISCERAL_FAT"),
    ],
    "InBody Age": [
        ("MFA_TBL", "INBODY_AGE"), ("BCA_TBL", "INBODY_AGE"), ("MFA_TBL", "BODY_AGE"),
        ("MFA_TBL", "BODYAGE"), ("RESEARCH_TBL", "INBODY_AGE"), ("USER_INFO1_TBL", "INBODY_AGE"),
    ],
}

BASE = [
    ("USER_INFO1_TBL", "NAME", "Name"),
    ("USER_INFO1_TBL", "USER_ID", "ID"),
    ("BCA_TBL", "DATETIMES", "TestDate"),
]


def main():
    if not os.environ.get("INBODY_LOGIN_ID") or not os.environ.get("INBODY_PASSWORD"):
        print("INBODY_LOGIN_ID / INBODY_PASSWORD not set — cannot run discovery.")
        return
    session = requests.Session()
    fi._login(session)
    uids = fi._fetch_uids(session)[:40]          # a sample is plenty to detect values
    if not uids:
        print("No members returned from Lookin'Body — check the login.")
        return
    print(f"Probing against {len(uids)} sample members…\n")

    results = {}
    for metric, cands in CANDIDATES.items():
        print(f"── {metric} ──")
        found = None
        for tbl, field in cands:
            cols = BASE + [(tbl, field, field)]
            try:
                rows = fi._parse_export(fi._export_scans(session, uids, cols), len(cols))
            except Exception as ex:
                print(f"   {tbl}.{field:20s} → export ERROR ({ex})")
                continue
            vals = [r[3] for r in rows if len(r) > 3 and (r[3] or "").strip() not in ("", "-")]
            numeric = [v for v in vals if fi._num(v) is not None]
            print(f"   {tbl}.{field:20s} → {len(numeric)} numeric values"
                  + (f" (e.g. {numeric[:3]})" if numeric else " (blank)"))
            if numeric and not found:
                found = (tbl, field)
        results[metric] = found
        print(f"   ==> {'USE  ' + found[0] + ' / ' + found[1] if found else 'no candidate worked'}\n")

    print("\n================ SUMMARY (paste this back) ================")
    for metric, found in results.items():
        print(f"{metric:32s}: {found[0] + ' / ' + found[1] if found else 'NOT FOUND'}")


if __name__ == "__main__":
    main()
