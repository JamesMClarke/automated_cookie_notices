"""
brave_shields_test.py
Counts requests blocked by Brave Shields, runs WAVE accessibility injection,
and runs a Lighthouse accessibility audit for each URL in a CSV file.
Results are saved to SQLite. Screenshots and HTML snapshots are saved per site.

For each URL the script:
  1. Navigates and records the HTTP status code.
  2. If error (4xx/5xx/nav failure): screenshots, records error, moves on.
  3. If success: dwells for `dwell` seconds to catch late trackers, then:
       a. Injects WAVE and records accessibility statistics.
       b. Runs Lighthouse accessibility audit via Node subprocess.
       c. Takes a full-page screenshot and saves HTML.

Output layout:
    results.db
    artifacts/
        <scan_id>_<domain>/
            screenshot.png
            page.html
            lighthouse.json     raw Lighthouse report (if available)
            brave_lighthouse.png      extracted Lighthouse full-page screenshot (best effort)

Prerequisites:
    pip install playwright pytest-playwright
    npm install -g lighthouse          # for Lighthouse audits
    Place wave.min.js next to this script (download from https://wave.webaim.org)

Usage:
    python brave_shields_test.py sites.csv results.db
    python brave_shields_test.py sites.csv results.db --dwell 10 --timeout 30
    python brave_shields_test.py sites.csv results.db --no-lighthouse
    python brave_shields_test.py sites.csv results.db --no-wave

Notes:
    - Set BRAVE_PATH below to match your OS.
    - Brave must NOT be running before the script starts.
    - Delete .brave_golden_profile/ to force a profile rebuild.
"""

import argparse
import asyncio
import base64
import json
import re
import shutil
import sqlite3
import sys

# On Windows, npm CLIs are installed as .cmd files which CreateProcess won't
# resolve without the shell — use the .cmd suffix directly instead.
_LH_CMD = "lighthouse.cmd" if sys.platform == "win32" else "lighthouse"
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

from nvda_capture import capture_nvda_transcript, restart_nvda

# Configuration


def _find_brave() -> str:
    import os
    import sys

    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        raise FileNotFoundError("Brave not found. Checked:\n" + "\n".join(f"  {p}" for p in candidates))
    if sys.platform == "darwin":
        return "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
    return "/usr/bin/brave-browser"  # Linux


BRAVE_PATH = _find_brave()

GOLDEN_PROFILE_DIR = Path(__file__).parent / ".brave_golden_profile"
WAVE_JS_PATH = Path(__file__).parent / "wave.min.js"
LH_CONFIG_PATH = Path(__file__).parent / "custom-config.mjs"
FILTER_LIST_WAIT = 20

LAUNCH_ARGS = [
    "--no-first-run",
    "--disable-sync",
    "--enable-features=BraveAdblockDefault2Lists,BraveAdblockCosmeticFiltering",
    "--remote-debugging-port=9222",  # needed for Lighthouse to connect
    "--window-size=1920,1040",  # total window fits on a 1920x1080 screen (1080 - 40px taskbar)
]

IGNORE_DEFAULT_ARGS = [
    "--disable-component-update",
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    "--disable-background-networking",
]

# Default WAVE stats returned when injection fails
_WAVE_EMPTY = {"error": None, "contrast": None, "alert": None, "feature": None, "structure": None, "aria": None}
_WAVE_ZERO = {"error": 0, "contrast": 0, "alert": 0, "feature": 0, "structure": 0, "aria": 0}

# Helpers


def safe_dirname(url: str) -> str:
    name = re.sub(r"https?://", "", url)
    name = name.split("/")[0]
    name = re.sub(r"[^\w\-.]", "_", name)
    return name[:80]


def artifact_dir(artifacts_root: Path, scan_id: int, url: str) -> Path:
    d = artifacts_root / f"{scan_id:05d}_{safe_dirname(url)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _page_is_oom(page, crashed: bool) -> bool:
    """Non-blocking OOM check — crash-event flag or chrome-error:// URL.

    page.url is a locally-cached property (no CDP round-trip) so it never hangs.
    """
    if crashed:
        return True
    try:
        return page.url.startswith("chrome-error:")
    except Exception:
        return False


# SQLite


def init_db(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS brave_scans (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            url             TEXT    NOT NULL,
            scanned_at      TEXT    NOT NULL,
            http_status     INTEGER,
            page_error      TEXT,
            is_error_page   INTEGER NOT NULL DEFAULT 0,
            total_requests  INTEGER NOT NULL DEFAULT 0,
            blocked_count   INTEGER NOT NULL DEFAULT 0,
            block_rate_pct  REAL    NOT NULL DEFAULT 0.0,
            screenshot_path TEXT,
            html_path       TEXT,
            cookie_path     TEXT,
            -- WAVE accessibility statistics
            wave_error      INTEGER,
            wave_contrast   INTEGER,
            wave_alert      INTEGER,
            wave_feature    INTEGER,
            wave_structure  INTEGER,
            wave_aria       INTEGER,
            -- Lighthouse accessibility score (0-100, NULL if not run)
            lh_accessibility_score  REAL,
            lighthouse_path         TEXT,
            -- NVDA screen reader transcript
            nvda_path               TEXT
        );

        CREATE TABLE IF NOT EXISTS brave_blocked_requests (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id        INTEGER NOT NULL REFERENCES brave_scans(id),
            blocked_url    TEXT    NOT NULL,
            resource_type  TEXT,
            initiator      TEXT,
            blocked_reason TEXT,
            error_text     TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_brave_scans_url     ON brave_scans(url);
        CREATE INDEX IF NOT EXISTS idx_brave_scans_error   ON brave_scans(is_error_page);
        CREATE INDEX IF NOT EXISTS idx_brave_blocked_scan  ON brave_blocked_requests(scan_id);
        CREATE INDEX IF NOT EXISTS idx_brave_blocked_url   ON brave_blocked_requests(blocked_url);
    """)
    existing = {row[1] for row in con.execute("PRAGMA table_info(brave_scans)")}
    if "nvda_path" not in existing:
        con.execute("ALTER TABLE brave_scans ADD COLUMN nvda_path TEXT")
    con.commit()
    return con


# CSV loading


def load_urls(csv_path: Path) -> list[str]:
    """
    Read URLs from a CSV file. Handles:
        google.com              (bare domain)
        1,google.com            (Tranco/Alexa rank,domain)
        url\nhttps://...        (with header)
    """
    HEADER_WORDS = {"url", "domain", "site", "rank", "website"}
    urls = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",")
            if len(parts) >= 2 and parts[0].strip().isdigit():
                candidate = parts[1].strip()
            else:
                candidate = parts[0].strip()
            if candidate.lower() in HEADER_WORDS:
                continue
            if candidate and "://" not in candidate:
                candidate = "https://" + candidate
            if candidate:
                urls.append(candidate)
    return urls


# Golden profile


async def _bootstrap_golden_profile() -> None:
    print("[*] Golden profile not found — creating it now.")
    print(f"    Waiting ~{FILTER_LIST_WAIT}s for filter lists to download...")
    GOLDEN_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(GOLDEN_PROFILE_DIR),
            executable_path=BRAVE_PATH,
            headless=False,
            args=LAUNCH_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS,
        )
        page = await context.new_page()
        await page.goto("https://brave.com", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(FILTER_LIST_WAIT * 1000)
        await context.close()
    print("[+] Golden profile created.\n")


def _make_run_profile() -> Path:
    run_dir = Path(tempfile.mkdtemp(prefix="brave_run_"))
    shutil.copytree(
        str(GOLDEN_PROFILE_DIR),
        str(run_dir),
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "SingletonLock",
            "SingletonCookie",
            "SingletonSocket",
            "Cache",
            "Code Cache",
            "GPUCache",
            "ShaderCache",
            "*.log",
            "*.lck",
        ),
    )
    return run_dir


async def ensure_golden_profile() -> None:
    if not GOLDEN_PROFILE_DIR.exists():
        await _bootstrap_golden_profile()
    else:
        print(f"[*] Using golden profile at {GOLDEN_PROFILE_DIR}")


# Browser launch


async def launch_brave(playwright) -> tuple[BrowserContext, Page]:
    await ensure_golden_profile()
    run_profile = _make_run_profile()
    context: BrowserContext = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(run_profile),
        executable_path=BRAVE_PATH,
        headless=False,
        args=LAUNCH_ARGS,
        ignore_default_args=IGNORE_DEFAULT_ARGS,
        bypass_csp=True,
        viewport={"width": 1920, "height": 969},
    )
    page = await context.new_page()
    return context, page


# WAVE injection


async def run_wave(page: Page, output_path: Path | None = None) -> dict:
    """
    Inject wave.min.js into the current page and return the statistics dict.
    Mirrors the JS pattern from the original codebase.

    Returns a dict with keys: error, contrast, alert, feature, structure, aria.
    Values are -1 if injection failed or the response was unparseable.
    """
    if not WAVE_JS_PATH.exists():
        print(f"       [!] WAVE skipped — {WAVE_JS_PATH} not found")
        return _WAVE_EMPTY.copy()

    wave_script = WAVE_JS_PATH.read_text(encoding="utf-8")
    await page.add_script_tag(content=wave_script)

    wave_results_raw = await page.evaluate("() => JSON.parse(JSON.stringify(window.wave.results))")
    if output_path is not None:
        output_path.write_text(json.dumps(wave_results_raw, indent=2), encoding="utf-8")

    wave_stats = await page.evaluate("""() => {
        const cats = window.wave && window.wave.results && window.wave.results.categories;
        if (!cats) return null;
        const get = key => {
            const c = cats[key];
            if (!c) return 0;
            // count may live at .count, .items.length, or directly as a number
            if (typeof c.count === 'number') return c.count;
            if (Array.isArray(c.items))       return c.items.length;
            if (typeof c === 'number')        return c;
            return 0;
        };
        return {
            error:     get('error'),
            contrast:  get('contrast'),
            alert:     get('alert'),
            feature:   get('feature'),
            structure: get('structure'),
            aria:      get('aria'),
        };
    }""")

    if wave_stats is None:
        print("       [!] WAVE: window.wave.results.categories not found")
        return _WAVE_EMPTY.copy()

    # Remove all WAVE-injected DOM elements and restore original page styles
    await page.evaluate("document.dispatchEvent(new CustomEvent('resetWave'))")

    return wave_stats


# Lighthouse


async def run_lighthouse(
    url: str,
    output_path: Path,
    screenshot_file: Path | None = None,
) -> float | None:
    """
    Run a Lighthouse accessibility audit against the already-open Brave instance
    (which is listening on --remote-debugging-port=9222).

    Lighthouse is invoked as a Node subprocess so it attaches to the live page
    rather than opening a new browser, preserving any auth state or cookies.

    Returns the accessibility score (0-100) or None if Lighthouse is unavailable.
    """
    lh_json = output_path / "lighthouse.json"

    try:
        result = await asyncio.create_subprocess_exec(
            _LH_CMD,
            url,
            "--output=json",
            f"--output-path={lh_json}",
            f"--config-path={LH_CONFIG_PATH}",
            "--port=9222",  # attach to our running Brave instance
            "--chrome-flags=",  # don't launch a new browser
            "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(result.communicate(), timeout=120)

        if result.returncode != 0:
            err = stderr.decode(errors="replace").strip().splitlines()
            print(f"       [!] Lighthouse exited {result.returncode}: {err[-1] if err else ''}")
            return None

        if lh_json.exists():
            report = json.loads(lh_json.read_text())
            score = report.get("categories", {}).get("accessibility", {}).get("score")
            if score is not None:
                score = round(score * 100, 1)  # Lighthouse returns 0.0–1.0

            # Extract and save the full-page screenshot embedded in the report.
            try:
                # Lighthouse report shape differs by version:
                # - Newer: report["fullPageScreenshot"]["screenshot"]["data"]
                # - Older: report["audits"]["full-page-screenshot"]["details"]["screenshot"]["data"]
                ss_data = report.get("fullPageScreenshot", {}).get("screenshot", {}).get("data", "")
                if not ss_data:
                    ss_data = (
                        report.get("audits", {})
                        .get("full-page-screenshot", {})
                        .get("details", {})
                        .get("screenshot", {})
                        .get("data", "")
                    )

                if ss_data.startswith("data:"):
                    header, b64 = ss_data.split(",", 1)
                    ext = header.split(";")[0].split("/")[1]  # often 'webp' or 'png'
                    ss_path = screenshot_file or (output_path / f"lighthouse_screenshot.{ext}")

                    # If caller requests .png but Lighthouse provides another
                    # format, try to convert with Pillow when available.
                    if ss_path.suffix.lower() == ".png" and ext.lower() != "png":
                        try:
                            import io

                            pil_image = __import__("PIL.Image", fromlist=["Image"])

                            raw = base64.b64decode(b64)
                            pil_image.open(io.BytesIO(raw)).save(ss_path, format="PNG")
                        except Exception:
                            ss_path = ss_path.with_suffix(f".{ext}")
                            ss_path.write_bytes(base64.b64decode(b64))
                    else:
                        if not ss_path.suffix:
                            ss_path = ss_path.with_suffix(f".{ext}")
                        ss_path.write_bytes(base64.b64decode(b64))
            except Exception:
                pass  # screenshot extraction is best-effort

            print(f"       [+] Lighthouse accessibility score: {score}")
            return score

    except FileNotFoundError:
        print(f"       [!] Lighthouse not found ({_LH_CMD}) — install with: npm install -g lighthouse")
    except asyncio.TimeoutError:
        print("       [!] Lighthouse timed out")
    except Exception as e:
        print(f"       [!] Lighthouse error: {e}")

    return None


# Capture helpers


async def capture_screenshot(page: Page, dest: Path) -> str | None:
    try:
        await page.screenshot(path=str(dest), full_page=False)
        return str(dest)
    except Exception as e:
        print(f"       [!] Screenshot failed: {e}")
        return None


async def capture_html(page: Page, dest: Path) -> str | None:
    try:
        dest.write_text(await page.content(), encoding="utf-8")
        return str(dest)
    except Exception as e:
        print(f"       [!] HTML capture failed: {e}")
        return None


async def capture_cookies(page: Page, dest: Path) -> str | None:
    try:
        cookies = await page.context.cookies()
        dest.write_text(json.dumps(cookies), encoding="utf-8")
        return str(dest)
    except Exception as e:
        print(f"       [!] Cookie capture failed: {e}")
        return None


# Core scan


async def scan_url(
    context: BrowserContext,
    url: str,
    artifacts_root: Path,
    scan_id: int,
    timeout: int = 30,
    dwell: int = 60,
    run_wave_flag: bool = True,
    run_lighthouse_flag: bool = True,
    run_nvda_flag: bool = True,
) -> dict:
    page = await context.new_page()
    page.set_default_timeout(15_000)
    cdp = await context.new_cdp_session(page)

    requests: dict[str, dict] = {}
    http_status: int | None = None
    nav_error: str | None = None
    _crashed = False

    def _on_crash():
        nonlocal _crashed
        _crashed = True

    page.on("crash", _on_crash)

    await cdp.send("Network.enable")

    def on_request(params):
        req = params.get("request", {})
        requests[params["requestId"]] = {
            "blocked_url": req.get("url", ""),
            "resource_type": params.get("type", "Unknown"),
            "initiator": params.get("initiator", {}).get("type", "unknown"),
        }

    def on_loading_failed(params):
        error = params.get("errorText", "")
        reason = params.get("blockedReason", "")
        if reason or "ERR_BLOCKED" in error or "net::ERR_ABORTED" in error:
            rid = params.get("requestId")
            if rid in requests:
                requests[rid]["blocked"] = True
                requests[rid]["blocked_reason"] = reason or "n/a"
                requests[rid]["error_text"] = error

    cdp.on("Network.requestWillBeSent", on_request)
    cdp.on("Network.loadingFailed", on_loading_failed)

    # Navigate
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        http_status = response.status if response else None
    except Exception as e:
        nav_error = str(e).splitlines()[0]

    is_error_page = bool(nav_error or (http_status is not None and http_status >= 400))

    # Dwell (success only)
    wave_stats = _WAVE_EMPTY.copy()
    lh_score = None
    lighthouse_path = None
    nvda_path = None
    cookies_path = None

    if is_error_page:
        print(f"       [!] Error page ({nav_error or f'HTTP {http_status}'}) — skipping dwell")
    else:
        print(f"       [HTTP {http_status}] Waiting for network idle (max {dwell}s)...")
        _dwell_start = time.monotonic()
        try:
            await page.wait_for_load_state("networkidle", timeout=dwell * 1000)
            print("       [*] Network idle")
        except Exception:
            print(f"       [*] Network still active after {dwell}s — continuing")
        # Always wait out the remainder of the dwell window before capturing
        _elapsed = time.monotonic() - _dwell_start
        _remaining = dwell - _elapsed
        if _remaining > 0:
            print(f"       [*] Padding {_remaining:.1f}s to complete {dwell}s dwell...")
            await page.wait_for_timeout(_remaining * 1000)

        if _page_is_oom(page, _crashed):
            print("       [!] OOM / page crash after dwell — skipping captures, recording as error")
            is_error_page = True
            nav_error = nav_error or "ERR_OUT_OF_MEMORY"
        else:
            art_dir = artifact_dir(artifacts_root, scan_id, url)

            # HTML and Screenshot
            html_path = await capture_html(page, art_dir / "page.html")
            screenshot_path = await capture_screenshot(page, art_dir / "screenshot.png")
            cookies_path = await capture_cookies(page, art_dir / "cookies.json")

            # NVDA transcript
            if run_nvda_flag:
                print("       [*] NVDA transcript...")
                try:
                    await restart_nvda()
                    nvda_result = await capture_nvda_transcript(art_dir / "nvda.json")
                    if nvda_result is not None:
                        nvda_path = str(art_dir / "nvda.json")
                        print(f"       [+] NVDA: {len(nvda_result)} chars")
                    else:
                        nvda_path = None
                except Exception as e:
                    print(f"       [!] NVDA skipped: {e}")

            # Lighthouse
            if run_lighthouse_flag:
                print("       [*] Running Lighthouse accessibility audit...")
                lh_score = await run_lighthouse(
                    url,
                    art_dir,
                    screenshot_file=art_dir / "brave_lighthouse.png",
                )
                if lh_score is not None:
                    lighthouse_path = str(art_dir / "lighthouse.json")

            # WAVE
            if run_wave_flag:
                print("       [*] Running WAVE...")
                try:
                    wave_stats = await run_wave(page, art_dir / "wave.json")
                    print(f"       [+] WAVE: {wave_stats}")
                except Exception as e:
                    print(f"       [!] WAVE skipped: {str(e).splitlines()[0]}")

    # Screenshot and HTML for error pages
    if is_error_page:
        art_dir = artifact_dir(artifacts_root, scan_id, url)
        screenshot_path = await capture_screenshot(page, art_dir / "screenshot.png")
        html_path = await capture_html(page, art_dir / "page.html")

    if screenshot_path:
        print(f"       [+] Screenshot -> {screenshot_path}")
    if html_path:
        print(f"       [+] HTML       -> {html_path}")

    await cdp.detach()
    await page.close()

    blocked_details = [r for r in requests.values() if r.get("blocked")]

    return {
        "url": url,
        "http_status": http_status,
        "error": nav_error,
        "is_error_page": is_error_page,
        "total_requests": len(requests),
        "blocked_requests": len(blocked_details),
        "block_rate_pct": round(len(blocked_details) / max(len(requests), 1) * 100, 1),
        "blocked_details": blocked_details,
        "screenshot_path": screenshot_path,
        "html_path": html_path,
        "wave_stats": wave_stats,
        "lh_score": lh_score,
        "lighthouse_path": lighthouse_path,
        "nvda_path": nvda_path,
        "cookies_path": cookies_path,
    }


# Per-URL helper (importable by scan.py)


async def brave_process_url(
    con: sqlite3.Connection,
    context: BrowserContext,
    url: str,
    artifacts_root: Path,
    timeout: int = 30,
    dwell: int = 60,
    run_wave_flag: bool = True,
    run_lighthouse_flag: bool = True,
    run_nvda_flag: bool = True,
) -> int:
    """Scan one URL with Brave, write results to DB, return scan_id."""
    cur = con.execute(
        """INSERT INTO brave_scans (url, scanned_at, is_error_page,
               total_requests, blocked_count, block_rate_pct)
           VALUES (?, ?, 0, 0, 0, 0.0)""",
        (url, datetime.now(timezone.utc).isoformat()),
    )
    scan_id = cur.lastrowid
    con.commit()

    try:
        stats = await scan_url(
            context,
            url,
            artifacts_root,
            scan_id,
            timeout=timeout,
            dwell=dwell,
            run_wave_flag=run_wave_flag,
            run_lighthouse_flag=run_lighthouse_flag,
            run_nvda_flag=run_nvda_flag,
        )
    except Exception as _fatal:
        _emsg = str(_fatal).splitlines()[0]
        print(f"  [brave] fatal error for {url}: {_emsg}")
        con.execute(
            "UPDATE brave_scans SET page_error = ?, is_error_page = 1 WHERE id = ?",
            (_emsg, scan_id),
        )
        con.commit()
        return scan_id

    ws = stats["wave_stats"]

    con.execute(
        """UPDATE brave_scans SET
               http_status            = ?,
               page_error             = ?,
               is_error_page          = ?,
               total_requests         = ?,
               blocked_count          = ?,
               block_rate_pct         = ?,
               screenshot_path        = ?,
               html_path              = ?,
               cookie_path              = ?,
               wave_error             = ?,
               wave_contrast          = ?,
               wave_alert             = ?,
               wave_feature           = ?,
               wave_structure         = ?,
               wave_aria              = ?,
               lh_accessibility_score = ?,
               lighthouse_path        = ?,
               nvda_path              = ?
           WHERE id = ?""",
        (
            stats["http_status"],
            stats["error"],
            1 if stats["is_error_page"] else 0,
            stats["total_requests"],
            stats["blocked_requests"],
            stats["block_rate_pct"],
            stats["screenshot_path"],
            stats["html_path"],
            stats["cookies_path"],
            ws.get("error"),
            ws.get("contrast"),
            ws.get("alert"),
            ws.get("feature"),
            ws.get("structure"),
            ws.get("aria"),
            stats["lh_score"],
            stats["lighthouse_path"],
            stats["nvda_path"],
            scan_id,
        ),
    )
    if stats["blocked_details"]:
        con.executemany(
            """INSERT INTO brave_blocked_requests
                   (scan_id, blocked_url, resource_type, initiator,
                    blocked_reason, error_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    scan_id,
                    r["blocked_url"],
                    r.get("resource_type"),
                    r.get("initiator"),
                    r.get("blocked_reason"),
                    r.get("error_text"),
                )
                for r in stats["blocked_details"]
            ],
        )
    con.commit()

    if stats["is_error_page"]:
        print(f"       [Brave] error [HTTP {stats['http_status']}] [scan_id={scan_id}]")
    else:
        print(
            f"       [Brave] {stats['blocked_requests']} blocked / "
            f"{stats['total_requests']} total ({stats['block_rate_pct']}%) | "
            f"WAVE errors: {ws.get('error')} | LH: {stats['lh_score']} "
            f"[scan_id={scan_id}]"
        )
    return scan_id


# Main


async def brave_main(
    csv_path: Path,
    db_path: Path,
    artifacts_root: Path,
    timeout: int,
    dwell: int,
    run_wave_flag: bool,
    run_lighthouse_flag: bool,
    run_nvda_flag: bool = True,
) -> None:
    urls = load_urls(csv_path)
    if not urls:
        print("[!] No URLs found in CSV — exiting.")
        sys.exit(1)

    artifacts_root.mkdir(parents=True, exist_ok=True)
    con = init_db(db_path)

    print(f"\n{'=' * 60}")
    print("  Brave Shields + Accessibility Scanner")
    print(f"{'=' * 60}")
    print(f"  Input:       {csv_path}  ({len(urls)} URLs)")
    print(f"  Database:    {db_path}")
    print(f"  Artifacts:   {artifacts_root}")
    print(f"  Timeout:     {timeout}s  |  Dwell: {dwell}s")
    print(f"  WAVE:        {'yes' if run_wave_flag else 'no'}")
    print(f"  Lighthouse:  {'yes' if run_lighthouse_flag else 'no'}")
    print(f"  NVDA:        {'yes' if run_nvda_flag else 'no'}\n")

    async with async_playwright() as p:
        context, _ = await launch_brave(p)
        try:
            for i, url in enumerate(urls, 1):
                print(f"[{i}/{len(urls)}] {url}")

                # Insert placeholder row to get scan_id before scan starts
                cur = con.execute(
                    """INSERT INTO brave_scans (url, scanned_at, is_error_page,
                           total_requests, blocked_count, block_rate_pct)
                       VALUES (?, ?, 0, 0, 0, 0.0)""",
                    (url, datetime.now(timezone.utc).isoformat()),
                )
                scan_id = cur.lastrowid
                con.commit()

                stats = await scan_url(
                    context,
                    url,
                    artifacts_root,
                    scan_id,
                    timeout=timeout,
                    dwell=dwell,
                    run_wave_flag=run_wave_flag,
                    run_lighthouse_flag=run_lighthouse_flag,
                    run_nvda_flag=run_nvda_flag,
                )
                ws = stats["wave_stats"]

                con.execute(
                    """UPDATE brave_scans SET
                           http_status            = ?,
                           page_error             = ?,
                           is_error_page          = ?,
                           total_requests         = ?,
                           blocked_count          = ?,
                           block_rate_pct         = ?,
                           screenshot_path        = ?,
                           html_path              = ?,
                           wave_error             = ?,
                           wave_contrast          = ?,
                           wave_alert             = ?,
                           wave_feature           = ?,
                           wave_structure         = ?,
                           wave_aria              = ?,
                           lh_accessibility_score = ?,
                           lighthouse_path        = ?
                       WHERE id = ?""",
                    (
                        stats["http_status"],
                        stats["error"],
                        1 if stats["is_error_page"] else 0,
                        stats["total_requests"],
                        stats["blocked_requests"],
                        stats["block_rate_pct"],
                        stats["screenshot_path"],
                        stats["html_path"],
                        ws.get("error"),
                        ws.get("contrast"),
                        ws.get("alert"),
                        ws.get("feature"),
                        ws.get("structure"),
                        ws.get("aria"),
                        stats["lh_score"],
                        stats["lighthouse_path"],
                        stats["nvda_path"],
                        scan_id,
                    ),
                )
                if stats["blocked_details"]:
                    con.executemany(
                        """INSERT INTO brave_blocked_requests
                               (scan_id, blocked_url, resource_type, initiator,
                                blocked_reason, error_text)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        [
                            (
                                scan_id,
                                r["blocked_url"],
                                r.get("resource_type"),
                                r.get("initiator"),
                                r.get("blocked_reason"),
                                r.get("error_text"),
                            )
                            for r in stats["blocked_details"]
                        ],
                    )
                con.commit()

                if stats["is_error_page"]:
                    print(f"       -> error [HTTP {stats['http_status']}] [scan_id={scan_id}]")
                else:
                    print(
                        f"       -> {stats['blocked_requests']} blocked / "
                        f"{stats['total_requests']} total "
                        f"({stats['block_rate_pct']}%) | "
                        f"WAVE errors: {ws.get('error')} | "
                        f"LH: {stats['lh_score']} "
                        f"[scan_id={scan_id}]"
                    )
        finally:
            await context.close()
            con.close()

    print(f"\n[+] Done. Results saved to {db_path}")
    print(f"[+] Artifacts saved to {artifacts_root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan URLs for Brave Shields blocking and accessibility issues.")
    parser.add_argument("csv", type=Path, help="CSV file of URLs (first column used)")
    parser.add_argument("db", type=Path, help="SQLite output file")
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=None,
        help="Directory for screenshots, HTML, Lighthouse reports (default: <db_dir>/artifacts/)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Navigation timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--dwell",
        type=int,
        default=60,
        help="Seconds to dwell after page load (default: 60)",
    )
    parser.add_argument(
        "--no-wave",
        action="store_true",
        help="Skip WAVE accessibility injection",
    )
    parser.add_argument(
        "--no-lighthouse",
        action="store_true",
        help="Skip Lighthouse accessibility audit",
    )
    parser.add_argument(
        "--no-nvda",
        action="store_true",
        help="Skip NVDA screen reader transcript",
    )
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"[!] CSV file not found: {args.csv}")
        sys.exit(1)

    artifacts_root = args.artifacts or args.db.parent / "artifacts"

    asyncio.run(
        brave_main(
            args.csv,
            args.db,
            artifacts_root,
            args.timeout,
            args.dwell,
            run_wave_flag=not args.no_wave,
            run_lighthouse_flag=not args.no_lighthouse,
            run_nvda_flag=not args.no_nvda,
        )
    )
