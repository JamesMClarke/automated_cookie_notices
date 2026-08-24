# Cookie Notices Automation

Automation and analysis code for *Large-scale Evaluation of Website Cookie
Notices against Privacy and Accessibility Frameworks* — a study of cookie
notices across 3,000 Tranco-ranked websites (1k each from the top, middle,
and bottom popularity tiers), measuring UK GDPR/PECR compliance, tracker
activity, and screen-reader accessibility.

For each site the crawler drives Chrome and Brave via Playwright across
three interaction flows — pre-interaction, post-accept, and post-reject —
detecting and classifying the cookie notice (building on BannerClick's
detection vocabulary — Rasaii et al., *Exploring the Cookieverse*, PAM
2023), and capturing screenshots, HTML, network traffic,
cookies/storage, Lighthouse and WAVE accessibility audits, and (via a
custom NVDA add-on) a screen-reader transcript of the pre-interaction page.
Everything is stored in SQLite for later analysis.

## Setup

```
conda env create -f ENV.yml
conda activate cookie_notices_automation
playwright install chrome
npm install -g lighthouse
```

Also required:

- `wave.min.js` (download from https://wave.webaim.org) placed next to
  `chrome_scan.py` / `brave_scan.py`.
- Go (see `postProcessor/go.mod` for version) if you're building/running
  `postProcessor`.
- The NVDA add-on in `nvda_addon/` (Windows + NVDA only) if you want
  screen-reader transcripts — see `nvda_capture.py` for the IPC protocol it
  talks over.

## Running a scan

```
python scan.py sites.csv results.db          # Chrome + Brave, combined DB
python chrome_scan.py sites.csv results.db   # Chrome only
python brave_scan.py sites.csv results.db    # Brave only
```

Each script's module docstring has the full flag list (`--dwell`,
`--timeout`, `--no-lighthouse`, `--no-wave`, etc.) and describes the output
layout under `artifacts/`.

## Reviewing & correcting classifications

```
python reviewing/review_classifications.py results.db   # generates review.html
python reviewing/mark_classified.py results.db <scan_id> <field> <value>
python reviewing/mark_verified.py results.db <scan_id> <1|0|NULL>
python reviewing/mark_reject_verified.py results.db <scan_id> <1|0|NULL>
```

## Reporting

- `reporting/generate_summary.py` — LaTeX summary report from one or more
  crawl DBs.
- `reporting/compare_results.py` — stability analysis across repeated crawls.
- `postProcessor/` — Go tool that classifies captured network requests
  against AdGuard/EasyList filter rules and the Open Cookie Database.

## Linting

Hooks are managed with [pre-commit](https://pre-commit.com/) (ruff for
Python, gofmt/go vet for Go):

```
pre-commit install
pre-commit run --all-files   # run on demand across the whole repo
```
