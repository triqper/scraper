# CLAUDE.md — projectinstructies voor triqper/scraper

## Git push
`git push` geeft HTTP 403 in deze omgeving (geen git-schrijftoegang).
Gebruik altijd `mcp__github__push_files` om bestanden te pushen naar:
- **owner**: `triqper`
- **repo**: `scraper`
- **branch**: `main`

> **Belangrijk:** Pushen naar `main` gaat direct naar de live omgeving op https://triqper.github.io/scraper/ via GitHub Pages. Elke push is dus meteen zichtbaar voor gebruikers.

Na elke push de lokale repo synchroniseren:
```
git fetch origin main && git reset --hard origin/main
```

## URLs

| Pagina / bestand | URL |
|---|---|
| Dashboard (index.html) | https://triqper.github.io/scraper/ |
| Leads pagina (leads.html) | https://triqper.github.io/scraper/leads.html |
| analytics.json | https://triqper.github.io/scraper/data/analytics.json |
| forms.json | https://triqper.github.io/scraper/data/forms.json |
| leads.json | https://triqper.github.io/scraper/data/leads.json |
| pageviews.json | https://triqper.github.io/scraper/data/pageviews.json |
| wc-data.json | https://triqper.github.io/scraper/data/wc-data.json |
| zero_point.json | https://triqper.github.io/scraper/data/zero_point.json |
| GitHub repo | https://github.com/triqper/scraper |

## Bestanden in de repo
- `index.html` — analytics dashboard
- `leads.html` — leads overzicht
- `scraper.py` — scraper die elk uur draait via GitHub Actions
- `data/analytics.json` — pageviews/bezoekers per site, per uur en per dag
- `data/forms.json` — aanmeldingen per formulier
- `data/leads.json` — ruwe leaddata
- `data/pageviews.json` — ruwe pageview data
- `data/wc-data.json` — WoonConnect data
- `data/zero_point.json` — nulpunt instelling
- `.github/workflows/daily-scrape.yml` — GitHub Actions workflow

## Handige context
- De scraper draait elk uur en pusht analytics.json automatisch
- `country_hourly` gebruikt **run-tijdstip** (view-tijdstip + 1 uur); gebruik `viewHourKey()` om terug te rekenen
- Testdata = voor 6 mei 2026; echte data = vanaf 6 mei 2026 09:00
