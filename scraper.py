#!/usr/bin/env python3
"""
Netlify Analytics Scraper

Hoe het delta-systeem werkt:
  Pageviews:  Netlify toont de afgelopen 7 dagen. Elke dag halen we
              gisteren op als een nieuw dagrecord. Al opgeslagen data
              wordt overgeslagen — volledig idempotent.
  Formulieren: We vergelijken submission_count (Netlify) met ons opgeslagen
               totaal. Alleen het verschil (delta) wordt opgehaald en per
               datum verdeeld.

Vereiste omgevingsvariabele: NETLIFY_TOKEN
"""

import os
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

NETLIFY_TOKEN = os.environ.get("NETLIFY_TOKEN", "")
API_BASE = "https://api.netlify.com/api/v1"
ANALYTICS_BASE = "https://analytics.services.netlify.com/v2"
DATA_DIR = Path(__file__).parent / "data"

# Alleen sites met Netlify Analytics ingeschakeld (de sites met een ster)
ANALYTICS_SITES = {
    "woonstroom-aanbod-landing",
    "woonstroom-woningwaarde-landing",
    "woonstroom-interesse-landing",
    "woonstroom-interesse",
    "woonstroom-woningwaarde",
    "woonstroom-aanbod",
}


def _headers() -> dict:
    return {"Authorization": f"Bearer {NETLIFY_TOKEN}"}


def netlify_get(url: str, params: dict | None = None) -> requests.Response:
    return requests.get(url, headers=_headers(), params=params, timeout=30)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Netlify API helpers
# ---------------------------------------------------------------------------


def get_sites() -> list:
    sites, page = [], 1
    while True:
        r = netlify_get(f"{API_BASE}/sites", {"per_page": 100, "page": page})
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        sites.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return sites


def _analytics_ms_params(target: date) -> tuple[int, int]:
    from_dt = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
    return int(from_dt.timestamp() * 1000), int((from_dt + timedelta(days=1)).timestamp() * 1000)


def _parse_data_array(items: list) -> int:
    if not items:
        return 0
    if isinstance(items[0], (list, tuple)):
        return sum(int(p[1]) for p in items)
    return sum(item.get("count", item.get("quantity", item.get("pageviews", 0))) for item in items)


def fetch_pageviews_for_day(site_id: str, target: date) -> int | None:
    from_ms, to_ms = _analytics_ms_params(target)
    r = netlify_get(
        f"{ANALYTICS_BASE}/{site_id}/pageviews",
        {"from": from_ms, "to": to_ms, "timezone": "+0000", "resolution": "day"},
    )
    if r.status_code in (402, 404, 422):
        return None
    if r.status_code != 200:
        print(f"    Fout {r.status_code}: {r.text[:200]}")
        return None
    d = r.json()
    if isinstance(d, dict) and "data" in d:
        return _parse_data_array(d["data"])
    if isinstance(d, dict) and "total" in d:
        return int(d["total"])
    return 0


def fetch_nl_pageviews_for_day(site_id: str, target: date) -> int:
    """Haalt NL-specifieke pageviews op via het landen-ranking endpoint."""
    from_ms, to_ms = _analytics_ms_params(target)
    r = netlify_get(
        f"{ANALYTICS_BASE}/{site_id}/ranking/countries",
        {"from": from_ms, "to": to_ms, "timezone": "+0000", "limit": 100},
    )
    if r.status_code != 200:
        return 0
    d = r.json()
    items = d.get("data", []) if isinstance(d, dict) else []
    for item in items:
        if isinstance(item, dict):
            code = item.get("path", item.get("country", item.get("code", "")))
            if code in ("NL", "Netherlands"):
                return int(item.get("count", item.get("pageviews", 0)))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            if item[0] in ("NL", "Netherlands"):
                return int(item[1])
    return 0


def fetch_visitors_for_day(site_id: str, target: date) -> int:
    """Haalt het totaal unieke bezoekers op voor één dag."""
    from_ms, to_ms = _analytics_ms_params(target)
    r = netlify_get(
        f"{ANALYTICS_BASE}/{site_id}/visitors",
        {"from": from_ms, "to": to_ms, "timezone": "+0000", "resolution": "day"},
    )
    if r.status_code != 200:
        return 0
    d = r.json()
    if isinstance(d, dict) and "data" in d:
        return _parse_data_array(d["data"])
    if isinstance(d, dict) and "total" in d:
        return int(d["total"])
    return 0


def get_forms(site_id: str) -> list:
    r = netlify_get(f"{API_BASE}/sites/{site_id}/forms")
    if r.status_code in (404, 422):
        return []
    r.raise_for_status()
    return r.json() or []


def get_form_submissions(form_id: str, per_page: int = 100, page: int = 1) -> list:
    r = netlify_get(
        f"{API_BASE}/forms/{form_id}/submissions",
        {"per_page": per_page, "page": page},
    )
    if r.status_code in (404, 422):
        return []
    r.raise_for_status()
    return r.json() or []


# ---------------------------------------------------------------------------
# Pageviews update
# ---------------------------------------------------------------------------


def update_pageviews() -> None:
    path = DATA_DIR / "pageviews.json"
    data = load_json(path)
    data.setdefault("sites", [])

    sites_idx = {s["id"]: s for s in data["sites"]}
    today = datetime.now(timezone.utc).date()

    for site in get_sites():
        sid = site["id"]
        name = site.get("name", sid)
        url = site.get("ssl_url") or site.get("url", "")

        if name not in ANALYTICS_SITES:
            continue

        print(f"  {name}")

        entry = sites_idx.setdefault(
            sid,
            {"id": sid, "name": name, "url": url, "total_pageviews": 0, "daily": []},
        )
        entry["name"] = name
        entry["url"] = url

        # Sla bestaande data op als dict voor snelle lookup
        have = {d["date"]: d for d in entry["daily"]}
        analytics_available = True

        for days_ago in range(0, 7):
            target = today - timedelta(days=days_ago)
            ds = target.isoformat()

            # Sla over als compleet opgeslagen (tenzij vandaag/gisteren)
            existing = have.get(ds, {})
            if days_ago >= 2 and "pageviews_nl" in existing and "visitors" in existing:
                continue
            if not analytics_available:
                break

            count = fetch_pageviews_for_day(sid, target)
            if count is None:
                print(f"    → analytics niet beschikbaar voor {name}")
                analytics_available = False
                break

            nl_count = fetch_nl_pageviews_for_day(sid, target)
            visitors = fetch_visitors_for_day(sid, target)

            label = " (vandaag)" if days_ago == 0 else ""
            print(f"    {ds}: {count} pageviews, {nl_count} NL, {visitors} bezoekers{label}")

            have[ds] = {"date": ds, "pageviews": count, "pageviews_nl": nl_count, "visitors": visitors}

        entry["daily"] = sorted(have.values(), key=lambda x: x["date"])
        entry["total_pageviews"] = sum(d["pageviews"] for d in entry["daily"])
        entry["total_pageviews_nl"] = sum(d.get("pageviews_nl", 0) for d in entry["daily"])
        entry["total_visitors"] = sum(d.get("visitors", 0) for d in entry["daily"])

    data["sites"] = list(sites_idx.values())
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_json(path, data)
    print(f"  ✓ {len(data['sites'])} website(s) opgeslagen")


# ---------------------------------------------------------------------------
# Forms update
# ---------------------------------------------------------------------------


def update_forms() -> None:
    path = DATA_DIR / "forms.json"
    data = load_json(path)
    data.setdefault("forms", [])

    forms_idx = {f["id"]: f for f in data["forms"]}

    for site in get_sites():
        sid = site["id"]
        sname = site.get("name", sid)

        if sname not in ANALYTICS_SITES:
            continue

        forms = get_forms(sid)
        if not forms:
            continue

        for form in forms:
            fid = form["id"]
            fname = form.get("name", fid)
            netlify_total = int(form.get("submission_count", 0) or 0)

            entry = forms_idx.setdefault(
                fid,
                {
                    "id": fid,
                    "name": fname,
                    "site_id": sid,
                    "site_name": sname,
                    "total_submissions": 0,
                    "last_submission_date": None,
                    "daily": [],
                },
            )
            entry["name"] = fname
            entry["site_name"] = sname

            stored_total = int(entry.get("total_submissions", 0) or 0)
            delta = netlify_total - stored_total

            if delta <= 0:
                print(f"  {sname} / {fname}: geen nieuwe verzendingen")
                continue

            # Begrens de eerste ophaal bij 1000 om de API niet te overbelasten
            fetch_limit = min(delta, 1000)
            if delta > 1000:
                print(f"  {sname} / {fname}: {delta} nieuw (max 1000 opgehaald)")
            else:
                print(f"  {sname} / {fname}: {delta} nieuwe verzending(en)")

            subs, page = [], 1
            while len(subs) < fetch_limit:
                batch = get_form_submissions(fid, per_page=100, page=page)
                if not batch:
                    break
                subs.extend(batch)
                if len(batch) < 100:
                    break
                page += 1

            subs = subs[:fetch_limit]

            # Groepeer per datum
            by_date: dict[str, int] = defaultdict(int)
            latest: str | None = None
            for sub in subs:
                dt = (sub.get("created_at") or "")[:10]
                if dt:
                    by_date[dt] += 1
                    if not latest or dt > latest:
                        latest = dt

            # Samenvoegen met bestaande dagtelling
            dmap = {d["date"]: d["count"] for d in entry["daily"]}
            for dt, cnt in by_date.items():
                dmap[dt] = dmap.get(dt, 0) + cnt

            entry["daily"] = sorted(
                [{"date": k, "count": v} for k, v in dmap.items()],
                key=lambda x: x["date"],
            )
            entry["total_submissions"] = netlify_total
            if latest:
                entry["last_submission_date"] = latest

    data["forms"] = list(forms_idx.values())
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_json(path, data)
    print(f"  ✓ {len(data['forms'])} formulier(en) opgeslagen")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if not NETLIFY_TOKEN:
        print("Fout: NETLIFY_TOKEN omgevingsvariabele niet ingesteld.")
        print("Stel in via: export NETLIFY_TOKEN=<jouw-token>")
        sys.exit(1)

    print(
        f"Netlify Analytics Scraper — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n"
    )
    print("Paginaweergaven ophalen:")
    update_pageviews()
    print("\nFormulieren ophalen:")
    update_forms()
    print("\nKlaar!")


if __name__ == "__main__":
    main()
