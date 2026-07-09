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

# Only the two still-unconfirmed metrics. TBW/PROTEIN/MINERAL are already proven
# (all BCA_TBL) and enabled in fetch_inbody, so we don't re-probe them here.
# Visceral fat area (VFA) & InBody age live in InBody's "research" parameters,
# which sit in a different table than BCA/MFA — hence the wider table sweep.
CANDIDATES = {
    "Visceral Fat": [
        ("RESEARCH_TBL", "VFA"), ("RESEARCH_TBL", "VFL"), ("OBESITY_TBL", "VFL"),
        ("OBESITY_TBL", "VFA"), ("OBESITY_TBL", "VISCERAL_FAT_LEVEL"),
        ("RESEARCH_TBL", "VISCERAL_FAT_AREA"), ("MFA_TBL", "VFL"), ("BCA_TBL", "VFA"),
        ("WC_TBL", "VFL"), ("WC_TBL", "VFA"),
    ],
    "InBody Age": [
        ("RESEARCH_TBL", "INBODY_AGE"), ("RESEARCH_TBL", "BODY_AGE"), ("RESEARCH_TBL", "IB_AGE"),
        ("RESEARCH_TBL", "AGE"), ("OBESITY_TBL", "INBODY_AGE"), ("BCA_TBL", "BODY_AGE"),
        ("MFA_TBL", "IB_AGE"), ("RESEARCH_TBL", "INBODYAGE"), ("WC_TBL", "INBODY_AGE"),
    ],
}

# Endpoints that might return the authoritative column catalogue the export
# "column picker" is built from (each column's real TABLE + FIELD code).
CATALOG_URLS = [
    "/SetupExportDataInBody", "/SetupExportDataInBody/Index",
    "/SetupExportDataInBody/GetColumnList", "/SetupExportDataInBody/GetColumns",
    "/SetupExportDataInBody/GetExportColumn", "/SetupExportDataInBody/GetSetupData",
]


BASE_URL = fi.BASE


def dump_catalog(session):
    """Best-effort: fetch the export column catalogue and print every TABLE.FIELD
    it advertises, so the exact visceral-fat / InBody-age codes are visible."""
    import re
    pairs = set()
    for path in CATALOG_URLS:
        for method in ("get", "post"):
            try:
                r = getattr(session, method)(BASE_URL + path, timeout=30)
            except Exception:
                continue
            if r.status_code != 200 or not r.text:
                continue
            txt = r.text
            for a, b in re.findall(r'TABLENAME"\s*:\s*"([^"]+)"\s*,\s*"FieldName"\s*:\s*"([^"]+)"', txt):
                pairs.add((a, b))
            for b, a in re.findall(r'FieldName"\s*:\s*"([^"]+)"\s*,\s*"TABLENAME"\s*:\s*"([^"]+)"', txt):
                pairs.add((a, b))
            for a, b in re.findall(r'data-tablename="([^"]+)"[^>]*data-fieldname="([^"]+)"', txt):
                pairs.add((a, b))
    if pairs:
        print("── Column catalogue found on the server ──")
        for tbl in sorted(set(t for t, _ in pairs)):
            fields = sorted(f for t, f in pairs if t == tbl)
            print(f"   {tbl}: {', '.join(fields)}")
        hits = [f"{t}.{f}" for t, f in sorted(pairs)
                if any(k in f.upper() for k in ("VF", "VISC", "AGE"))]
        if hits:
            print("   >>> visceral-fat / age candidates:", ", ".join(hits))
    else:
        print("── No column catalogue endpoint responded (will rely on probing) ──")
    print()

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

    # First, try to read the authoritative column catalogue off the server.
    dump_catalog(session)

    # Then probe. Use a SMALL sample — big exports on the research table time out.
    uids = fi._fetch_uids(session)[:12]
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
            rows = None
            for attempt in (1, 2):                 # one retry — research table is slow
                try:
                    rows = fi._parse_export(fi._export_scans(session, uids, cols), len(cols))
                    break
                except Exception as ex:
                    if attempt == 2:
                        print(f"   {tbl}.{field:20s} → export ERROR ({ex})")
            if rows is None:
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
