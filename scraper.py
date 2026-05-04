#!/usr/bin/env python3
"""
Netlify Analytics Scraper — delta vanaf nulpunt

Bij de eerste run (of na een reset) wordt data/zero_point.json aangemaakt
met de huidige stand per site. Alle latere runs trekken die baseline eraf,
zodat het dashboard alleen toont wat er NA het nulpunt is bijgekomen.

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


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _day_ms(d: date) -> int:
    return _ms(datetime(d.year, d.month, d.day, tzinfo=timezone.utc))


def fetch_pageviews_daily(site_id: str, from_date: date, to_date: date) -> list[dict]:
    r = netlify_get(
        f"{ANALYTICS_BASE}/{site_id}/pageviews",
        {"from": _day_ms(from_date), "to": _day_ms(to_date), "timezone": "+0000", "resolution": "day"},
    )
    if r.status_code in (402, 404, 422):
        return []
    if r.status_code != 200:
        print(f"    Fout pageviews {r.status_code}: {r.text[:200]}")
        return []
    return _parse_timeseries(r.json(), "pageviews")


def fetch_visitors_daily(site_id: str, from_date: date, to_date: date) -> list[dict]:
    r = netlify_get(
        f"{ANALYTICS_BASE}/{site_id}/visitors",
        {"from": _day_ms(from_date), "to": _day_ms(to_date), "timezone": "+0000", "resolution": "day"},
    )
    if r.status_code != 200:
        return []
    return _parse_timeseries(r.json(), "visitors")


def fetch_pageviews_hourly(site_id: str, from_dt: datetime, to_dt: datetime) -> list[dict]:
    r = netlify_get(
        f"{ANALYTICS_BASE}/{site_id}/pageviews",
        {"from": _ms(from_dt), "to": _ms(to_dt), "timezone": "+0000", "resolution": "hour"},
    )
    if r.status_code in (402, 404, 422):
        return []
    if r.status_code != 200:
        return []
    return _parse_timeseries_hourly(r.json(), "pageviews")


def fetch_visitors_hourly(site_id: str, from_dt: datetime, to_dt: datetime) -> list[dict]:
    r = netlify_get(
        f"{ANALYTICS_BASE}/{site_id}/visitors",
        {"from": _ms(from_dt), "to": _ms(to_dt), "timezone": "+0000", "resolution": "hour"},
    )
    if r.status_code != 200:
        return []
    return _parse_timeseries_hourly(r.json(), "visitors")


def _parse_timeseries(raw: dict | list, key: str) -> list[dict]:
    items = raw.get("data", []) if isinstance(raw, dict) else []
    result = []
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            day = datetime.fromtimestamp(item[0] / 1000, tz=timezone.utc).date().isoformat()
            result.append({"date": day, key: int(item[1])})
        elif isinstance(item, dict):
            day = (item.get("date") or item.get("timestamp") or "")[:10]
            if day:
                result.append({"date": day, key: int(item.get("count", item.get(key, 0)))})
    return result


def _parse_timeseries_hourly(raw: dict | list, key: str) -> list[dict]:
    items = raw.get("data", []) if isinstance(raw, dict) else []
    result = []
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            dt = datetime.fromtimestamp(item[0] / 1000, tz=timezone.utc)
            hour_str = dt.strftime("%Y-%m-%dT%H:00:00+00:00")
            result.append({"hour": hour_str, key: int(item[1])})
        elif isinstance(item, dict):
            hour_str = (item.get("timestamp") or item.get("hour") or "")
            if hour_str:
                result.append({"hour": hour_str[:19] + "+00:00", key: int(item.get("count", item.get(key, 0)))})
    return result


def fetch_top_countries(site_id: str, from_date: date, to_date: date, limit: int = 20) -> list[dict]:
    r = netlify_get(
        f"{ANALYTICS_BASE}/{site_id}/ranking/countries",
        {"from": _day_ms(from_date), "to": _day_ms(to_date), "timezone": "+0000", "limit": limit},
    )
    if r.status_code != 200:
        return []
    items = (r.json().get("data", []) if isinstance(r.json(), dict) else [])
    result = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("country_name") or item.get("resource") or item.get("name") or "Onbekend"
            count = int(item.get("count", item.get("pageviews", item.get("quantity", 0))))
            result.append({"country": name, "pageviews": count})
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            result.append({"country": str(item[0]), "pageviews": int(item[1])})
    return sorted(result, key=lambda x: x["pageviews"], reverse=True)


def fetch_top_sources(site_id: str, from_date: date, to_date: date, limit: int = 20) -> list[dict]:
    r = netlify_get(
        f"{ANALYTICS_BASE}/{site_id}/ranking/sources",
        {"from": _day_ms(from_date), "to": _day_ms(to_date), "timezone": "+0000", "limit": limit},
    )
    if r.status_code != 200:
        print(f"    [sources {r.status_code}]")
        return []
    items = (r.json().get("data", []) if isinstance(r.json(), dict) else [])
    result = []
    for item in items:
        if isinstance(item, dict):
            source = item.get("resource") or item.get("source") or item.get("path") or item.get("name") or None
            source = source if source else "Direct traffic"
            count = int(item.get("count", item.get("referrals", item.get("quantity", 0))))
            result.append({"source": source, "referrals": count})
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            result.append({"source": str(item[0]) or "Direct traffic", "referrals": int(item[1])})
    return sorted(result, key=lambda x: x["referrals"], reverse=True)


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
# Zero-point: eenmalige baseline
# ---------------------------------------------------------------------------


def initialize_zero_point() -> dict:
    """
    Leg de huidige Netlify-stand vast als nulpunt.
    Alles wat hierna binnenkomt wordt als delta getoond.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    # Uurdata start bij het VOLGENDE volle uur zodat we geen halve-uur-correctie nodig hebben
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    zp: dict = {
        "created_at": now.isoformat(),
        "date": today.isoformat(),
        "hour": next_hour.isoformat(),
        "sites": {},
    }

    for site in get_sites():
        sid = site["id"]
        name = site.get("name", sid)
        if name not in ANALYTICS_SITES:
            continue

        print(f"  {name}")

        pv_today       = fetch_pageviews_daily(sid, today, today + timedelta(days=1))
        vis_today      = fetch_visitors_daily(sid, today, today + timedelta(days=1))
        countries_today = fetch_top_countries(sid, today, today + timedelta(days=1))
        sources_today   = fetch_top_sources(sid, today, today + timedelta(days=1))

        baseline_pv  = next((d["pageviews"] for d in pv_today  if d["date"] == today.isoformat()), 0)
        baseline_vis = next((d["visitors"]  for d in vis_today if d["date"] == today.isoformat()), 0)

        zp["sites"][sid] = {
            "name": name,
            "today_pageviews": baseline_pv,
            "today_visitors":  baseline_vis,
            "today_countries": {c["country"]: c["pageviews"] for c in countries_today},
            "today_sources":   {s["source"]:  s["referrals"] for s in sources_today},
        }
        print(f"    baseline: {baseline_pv} pv, {baseline_vis} viz — uurdata start {next_hour.strftime('%H:%M')} UTC")

    save_json(DATA_DIR / "zero_point.json", zp)
    print(f"\n  ✓ Nulpunt vastgelegd op {now.strftime('%Y-%m-%d %H:%M')} UTC")
    return zp


# ---------------------------------------------------------------------------
# Analytics update (delta t.o.v. nulpunt)
# ---------------------------------------------------------------------------


def update_analytics(zero_point: dict) -> None:
    path = DATA_DIR / "analytics.json"
    data = load_json(path)
    data.setdefault("sites", [])
    data["zero_point"] = zero_point["created_at"]

    sites_idx = {s["id"]: s for s in data["sites"]}
    zp_date = date.fromisoformat(zero_point["date"])
    today   = datetime.now(timezone.utc).date()
    now_dt  = datetime.now(timezone.utc)

    # Uurdata-startpunt (datetime object)
    zp_hour_str = zero_point.get("hour")
    zp_hour_dt  = datetime.fromisoformat(zp_hour_str) if zp_hour_str else None

    for site in get_sites():
        sid  = site["id"]
        name = site.get("name", sid)
        url  = site.get("ssl_url") or site.get("url", "")

        if name not in ANALYTICS_SITES:
            continue

        print(f"  {name}")

        entry = sites_idx.setdefault(sid, {
            "id": sid, "name": name, "url": url,
            "total_pageviews": 0, "total_visitors": 0,
            "daily": [], "hourly": [],
            "top_countries": [], "top_sources": [],
        })
        entry["name"] = name
        entry["url"]  = url

        zp_site          = zero_point["sites"].get(sid, {})
        baseline_pv      = zp_site.get("today_pageviews", 0)
        baseline_vis     = zp_site.get("today_visitors", 0)
        baseline_countries: dict[str, int] = zp_site.get("today_countries", {})
        baseline_sources:   dict[str, int] = zp_site.get("today_sources", {})

        # ── Dagelijkse data ──────────────────────────────────────────
        pv_list  = fetch_pageviews_daily(sid, zp_date, today + timedelta(days=1))
        vis_list = fetch_visitors_daily(sid, zp_date, today + timedelta(days=1))

        if not pv_list and not vis_list:
            print(f"    → analytics niet beschikbaar, overgeslagen")
        else:
            pv_map  = {d["date"]: d["pageviews"] for d in pv_list}
            vis_map = {d["date"]: d["visitors"]  for d in vis_list}
            daily   = []
            for ds in sorted(set(pv_map) | set(vis_map)):
                pv  = pv_map.get(ds, 0)
                vis = vis_map.get(ds, 0)
                if ds == zero_point["date"]:
                    pv  = max(0, pv  - baseline_pv)
                    vis = max(0, vis - baseline_vis)
                daily.append({"date": ds, "pageviews": pv, "visitors": vis})
            entry["daily"] = daily

        # ── Uurlijkse data (start bij volgend heel uur na nulpunt) ──
        if zp_hour_dt and zp_hour_dt < now_dt:
            pv_hourly  = fetch_pageviews_hourly(sid, zp_hour_dt, now_dt)
            vis_hourly = fetch_visitors_hourly(sid, zp_hour_dt, now_dt)
            pv_h  = {h["hour"]: h["pageviews"] for h in pv_hourly}
            vis_h = {h["hour"]: h["visitors"]  for h in vis_hourly}
            all_hours = sorted(set(pv_h) | set(vis_h))
            entry["hourly"] = [
                {"hour": h, "pageviews": pv_h.get(h, 0), "visitors": vis_h.get(h, 0)}
                for h in all_hours
            ]

        # ── Top landen ───────────────────────────────────────────────
        countries = fetch_top_countries(sid, zp_date, today + timedelta(days=1))
        country_delta: dict[str, int] = {}
        for c in countries:
            net = max(0, c["pageviews"] - baseline_countries.get(c["country"], 0))
            if net > 0:
                country_delta[c["country"]] = net
        entry["top_countries"] = sorted(
            [{"country": k, "pageviews": v} for k, v in country_delta.items()],
            key=lambda x: x["pageviews"], reverse=True,
        )

        # ── Top bronnen ──────────────────────────────────────────────
        sources = fetch_top_sources(sid, zp_date, today + timedelta(days=1))
        source_delta: dict[str, int] = {}
        for s in sources:
            net = max(0, s["referrals"] - baseline_sources.get(s["source"], 0))
            if net > 0:
                source_delta[s["source"]] = net
        entry["top_sources"] = sorted(
            [{"source": k, "referrals": v} for k, v in source_delta.items()],
            key=lambda x: x["referrals"], reverse=True,
        )

        entry["total_pageviews"] = sum(d["pageviews"] for d in entry["daily"])
        entry["total_visitors"]  = sum(d["visitors"]  for d in entry["daily"])
        print(f"    {entry['total_pageviews']} pv, {entry['total_visitors']} viz — "
              f"{len(entry.get('hourly', []))} uurpunten")

    data["sites"] = list(sites_idx.values())
    data["last_updated"] = now_dt.isoformat()
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
        sid   = site["id"]
        sname = site.get("name", sid)

        if sname not in ANALYTICS_SITES:
            continue

        forms = get_forms(sid)
        if not forms:
            continue

        for form in forms:
            fid          = form["id"]
            fname        = form.get("name", fid)
            netlify_total = int(form.get("submission_count", 0) or 0)

            entry = forms_idx.setdefault(fid, {
                "id": fid, "name": fname,
                "site_id": sid, "site_name": sname,
                "total_submissions": 0,
                "last_submission_date": None,
                "daily": [],
            })
            entry["name"]      = fname
            entry["site_name"] = sname

            stored_total = int(entry.get("total_submissions", 0) or 0)
            delta        = netlify_total - stored_total

            if delta <= 0:
                print(f"  {sname} / {fname}: geen nieuwe verzendingen")
                continue

            fetch_limit = min(delta, 1000)
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

            by_date: dict[str, int] = defaultdict(int)
            latest: str | None = None
            for sub in subs:
                dt = (sub.get("created_at") or "")[:10]
                if dt:
                    by_date[dt] += 1
                    if not latest or dt > latest:
                        latest = dt

            dmap = {d["date"]: d["count"] for d in entry["daily"]}
            for dt, cnt in by_date.items():
                dmap[dt] = dmap.get(dt, 0) + cnt

            entry["daily"] = sorted(
                [{"date": k, "count": v} for k, v in dmap.items()],
                key=lambda x: x["date"],
            )
            entry["total_submissions"]    = netlify_total
            if latest:
                entry["last_submission_date"] = latest

    data["forms"]        = list(forms_idx.values())
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_json(path, data)
    print(f"  ✓ {len(data['forms'])} formulier(en) opgeslagen")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if not NETLIFY_TOKEN:
        print("Fout: NETLIFY_TOKEN omgevingsvariabele niet ingesteld.")
        sys.exit(1)

    print(f"Netlify Analytics Scraper — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n")

    zp_path = DATA_DIR / "zero_point.json"
    if zp_path.exists():
        zero_point = load_json(zp_path)
        print(f"Nulpunt: {zero_point['created_at'][:16].replace('T', ' ')} UTC\n")
    else:
        print("Geen nulpunt gevonden — nulpunt initialiseren:")
        zero_point = initialize_zero_point()
        save_json(DATA_DIR / "analytics.json", {
            "zero_point":   zero_point["created_at"],
            "last_updated": zero_point["created_at"],
            "sites": [],
        })
        print()

    print("Analytics ophalen (delta sinds nulpunt):")
    update_analytics(zero_point)
    print("\nFormulieren ophalen:")
    update_forms()
    print("\nKlaar!")


if __name__ == "__main__":
    main()
