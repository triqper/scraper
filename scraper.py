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


def fetch_pageviews_for_day(site_id: str, target: date) -> int | None:
    """Geeft het totaal pageviews voor één dag terug, of None als analytics niet beschikbaar."""
    from_dt = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
    to_dt = from_dt + timedelta(days=1)
    r = netlify_get(
        f"{ANALYTICS_BASE}/sites/{site_id}/pageviews",
        {
            "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timezone": "UTC",
        },
    )
    if r.status_code in (402, 404, 422):
        return None
    r.raise_for_status()
    d = r.json()
    if isinstance(d, dict):
        if "total" in d:
            return int(d["total"])
        if "data" in d and isinstance(d["data"], list):
            return sum(
                item.get("count", item.get("quantity", item.get("pageviews", 0)))
                for item in d["data"]
            )
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
        print(f"  {name}")

        entry = sites_idx.setdefault(
            sid,
            {"id": sid, "name": name, "url": url, "total_pageviews": 0, "daily": []},
        )
        entry["name"] = name
        entry["url"] = url

        have = {d["date"] for d in entry["daily"]}
        analytics_available = True

        # Netlify bewaart 7 dagen; we proberen elke ontbrekende dag op te halen
        for days_ago in range(1, 8):
            target = today - timedelta(days=days_ago)
            ds = target.isoformat()
            if ds in have or not analytics_available:
                continue

            count = fetch_pageviews_for_day(sid, target)
            if count is None:
                print(f"    → analytics niet beschikbaar voor {name}")
                analytics_available = False
            else:
                print(f"    {ds}: {count} pageviews")
                entry["daily"].append({"date": ds, "pageviews": count})

        entry["daily"].sort(key=lambda x: x["date"])
        entry["total_pageviews"] = sum(d["pageviews"] for d in entry["daily"])

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
