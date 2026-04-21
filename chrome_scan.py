"""
chrome_scan.py
==============
Runs pre- and post-cookie-accept accessibility scans on Chrome for each URL
in a CSV file. Results are saved to SQLite. Screenshots, HTML snapshots,
WAVE JSON, and Lighthouse reports are saved per site.

For each URL the script:
  1. Opens a fresh browser context (clean cookies/cache).
  2. Navigates and waits up to 30 s for network idle.
  3. Pre-accept: screenshot, HTML, WAVE, Lighthouse.
  4. Detects a cookie notice and attempts to accept it.
  5. Post-accept (if accepted): screenshot, HTML, WAVE, Lighthouse.

Output layout:
    results.db
    artifacts/
        <scan_id>_<domain>/
            pre_screenshot.png
            pre_page.html
            pre_wave.json
            pre_lighthouse.json
            post_screenshot.png     (if cookie notice accepted)
            post_page.html
            post_wave.json
            post_lighthouse.json

Prerequisites:
    pip install playwright
    playwright install chrome
    npm install -g lighthouse
    Place wave.min.js next to this script

Usage:
    python chrome_scan.py sites.csv results.db
    python chrome_scan.py sites.csv results.db --timeout 30
    python chrome_scan.py sites.csv results.db --no-lighthouse
    python chrome_scan.py sites.csv results.db --no-wave

Notes:
    - Chrome must NOT be running on the same debugging port before the script starts.
    - Lighthouse runs in a new tab and navigates fresh; post-accept Lighthouse
      may not reflect cookie-accepted state if the site tracks consent server-side.
"""

import argparse
import asyncio
import json
import re
import shutil
import sqlite3
import sys

# On Windows, npm CLIs are installed as .cmd files which CreateProcess won't
# resolve without the shell — use the .cmd suffix directly instead.
_LH_CMD = "lighthouse.cmd" if sys.platform == "win32" else "lighthouse"
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright, Page, Frame, BrowserContext
from nvda_capture import capture_nvda_transcript, restart_nvda

# ── Configuration ─────────────────────────────────────────────────────────────

CHROME_PATH = {
    "linux":   "/usr/bin/google-chrome",
    "macos":   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "windows": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
}["windows"]  # change key to match your OS

WAVE_JS_PATH   = Path(__file__).parent / "wave.min.js"
LH_CONFIG_PATH = Path(__file__).parent / "custom-config.mjs"

DEBUGGING_PORT = 9223  # use 9223 to avoid clashing with a running Brave instance

LAUNCH_ARGS = [
    "--no-first-run",
    "--disable-sync",
    f"--remote-debugging-port={DEBUGGING_PORT}",
    "--force-renderer-accessibility",  # ensure accessibility tree is always active for NVDA
]

# Default WAVE stats returned when injection fails
_WAVE_EMPTY = {"error": -1, "contrast": -1, "alert": -1,
               "feature": -1, "structure": -1, "aria": -1}
_WAVE_ZERO  = {"error": 0,  "contrast": 0,  "alert": 0,
               "feature": 0,  "structure": 0,  "aria": 0}

# ── Iframe filter constants ────────────────────────────────────────────────────
# Frames smaller than this are almost certainly ad slots or tracking pixels.
_FRAME_MIN_WIDTH  = 200   # px
_FRAME_MIN_HEIGHT = 100   # px

_FRAME_SKIP_PATTERNS = re.compile(
    r"(doubleclick\.net|google-analytics|googletagmanager|facebook\.net"
    r"|twitter\.com/i/|youtube\.com/embed|vimeo\.com/video"
    r"|/ads/|/tracking/|/analytics/|/pixel/)",
    re.IGNORECASE,
)

# ── Multilingual keyword lists ────────────────────────────────────────────────
# Base lists (English) — must stay identical to the hard-coded JS defaults.
_BASE_COOKIE_WORDS = ["cookie", "consent", "privacy", "gdpr",
                      "tracking", "personal data", "data protection"]

_BASE_ACTION_WORDS = ["accept", "agree", "allow", "reject", "decline",
                      "refuse", "settings", "preferences", "manage",
                      "got it", "dismiss", "only necessary", "only essential",
                      "i understand", "i accept"]

_BASE_AGREE_KW    = ["accept all", "accept cookies", "accept", "allow all",
                     "allow cookies", "i agree", "agree", "got it",
                     "i accept", "ok, i understand"]

_BASE_REJECT_KW   = ["reject all", "reject", "decline", "refuse",
                     "no thanks", "deny", "do not accept", "disagree",
                     "only necessary", "only essential"]

_BASE_SETTINGS_KW = ["settings", "manage preferences", "manage cookies",
                     "cookie settings", "preferences", "options",
                     "manage", "customize", "customise"]

# Language-specific additions (supplements; English base always included).
_LANG_COOKIE_WORDS: dict[str, list[str]] = {
    "de": ["datenschutz", "einwilligung", "cookie-richtlinie"],
    "fr": ["confidentialité", "consentement", "données personnelles", "traceurs"],
    "es": ["privacidad", "consentimiento", "datos personales"],
    "it": ["informativa sui cookie", "consenso", "dati personali"],
    "nl": ["cookiebeleid", "toestemming", "persoonsgegevens"],
    "pt": ["privacidade", "consentimento", "dados pessoais"],
}

_LANG_ACTION_WORDS: dict[str, list[str]] = {
    "de": ["akzeptieren", "zustimmen", "erlauben", "ablehnen", "einstellungen",
           "nur notwendige", "schließen", "ich verstehe"],
    "fr": ["accepter", "refuser", "paramètres", "gérer", "je comprends",
           "continuer sans accepter", "uniquement nécessaires", "fermer"],
    "es": ["aceptar", "rechazar", "ajustes", "gestionar", "entendido",
           "solo necesarias", "cerrar", "de acuerdo"],
    "it": ["accetta", "rifiuta", "impostazioni", "gestisci", "capisco",
           "solo necessari", "chiudi"],
    "nl": ["accepteren", "weigeren", "instellingen", "beheren", "begrepen",
           "alleen noodzakelijke", "sluiten", "akkoord"],
    "pt": ["aceitar", "recusar", "configurações", "gerir", "entendi",
           "apenas necessários", "fechar", "concordo"],
}

_LANG_AGREE_KW: dict[str, list[str]] = {
    "de": ["alle akzeptieren", "akzeptieren", "alle erlauben", "zustimmen",
           "ich stimme zu", "einverstanden", "cookies akzeptieren"],
    "fr": ["tout accepter", "accepter", "tout autoriser", "j'accepte",
           "je suis d'accord", "d'accord"],
    "es": ["aceptar todo", "aceptar", "permitir todo", "de acuerdo",
           "acepto", "aceptar cookies"],
    "it": ["accetta tutto", "accetta", "consenti tutto", "sono d'accordo",
           "accetto"],
    "nl": ["alles accepteren", "accepteren", "alles toestaan", "akkoord",
           "ik ga akkoord", "cookies accepteren"],
    "pt": ["aceitar tudo", "aceitar", "permitir tudo", "concordo", "aceito"],
}

_LANG_REJECT_KW: dict[str, list[str]] = {
    "de": ["alle ablehnen", "ablehnen", "verweigern", "nein danke",
           "nur notwendige", "nicht zustimmen"],
    "fr": ["tout refuser", "refuser", "non merci", "je refuse",
           "uniquement nécessaires", "continuer sans accepter"],
    "es": ["rechazar todo", "rechazar", "no gracias", "no acepto",
           "solo necesarias", "continuar sin aceptar"],
    "it": ["rifiuta tutto", "rifiuta", "no grazie", "non accetto",
           "solo necessari"],
    "nl": ["alles weigeren", "weigeren", "nee bedankt", "ik weiger",
           "alleen noodzakelijke"],
    "pt": ["recusar tudo", "recusar", "não obrigado", "não aceito",
           "apenas necessários"],
}

_LANG_SETTINGS_KW: dict[str, list[str]] = {
    "de": ["einstellungen", "cookie-einstellungen", "präferenzen", "verwalten",
           "datenschutzeinstellungen", "anpassen"],
    "fr": ["paramètres", "paramètres des cookies", "préférences", "gérer",
           "personnaliser"],
    "es": ["ajustes", "configuración de cookies", "preferencias", "gestionar",
           "personalizar"],
    "it": ["impostazioni", "impostazioni cookie", "preferenze", "gestisci",
           "personalizza"],
    "nl": ["instellingen", "cookie-instellingen", "voorkeuren", "beheren",
           "aanpassen"],
    "pt": ["configurações", "configurações de cookies", "preferências", "gerir",
           "personalizar"],
}


def _merge_kw(base: list[str], lang: str, extras: dict[str, list[str]]) -> list[str]:
    """Merge base keywords with language additions, preserving order, no duplicates."""
    seen = set(base)
    result = list(base)
    for w in extras.get(lang, []):
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result


def _kw_json(lang: str) -> dict[str, str]:
    """Return JSON-serialised keyword arrays for JS /*MARKER*/ substitution."""
    return {
        "COOKIE_WORDS": json.dumps(_merge_kw(_BASE_COOKIE_WORDS, lang, _LANG_COOKIE_WORDS)),
        "ACTION_WORDS": json.dumps(_merge_kw(_BASE_ACTION_WORDS, lang, _LANG_ACTION_WORDS)),
        "AGREE_KW":     json.dumps(_merge_kw(_BASE_AGREE_KW,     lang, _LANG_AGREE_KW)),
        "REJECT_KW":    json.dumps(_merge_kw(_BASE_REJECT_KW,    lang, _LANG_REJECT_KW)),
        "SETTINGS_KW":  json.dumps(_merge_kw(_BASE_SETTINGS_KW,  lang, _LANG_SETTINGS_KW)),
    }


def _apply_kw(js_template: str, lang: str) -> str:
    """Substitute /*MARKER*/ sentinels in a JS template with language-aware keyword arrays."""
    for marker, value in _kw_json(lang).items():
        js_template = js_template.replace(f"/*{marker}*/", value)
    return js_template


# ── Helpers ────────────────────────────────────────────────────────────────────

def safe_dirname(url: str) -> str:
    name = re.sub(r"https?://", "", url)
    name = name.split("/")[0]
    name = re.sub(r"[^\w\-.]", "_", name)
    return name[:80]


def artifact_dir(artifacts_root: Path, scan_id: int, url: str) -> Path:
    d = artifacts_root / f"{scan_id:05d}_{safe_dirname(url)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── SQLite ─────────────────────────────────────────────────────────────────────

def init_db(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS chrome_scans (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            url                     TEXT    NOT NULL,
            scanned_at              TEXT    NOT NULL,
            http_status             INTEGER,
            page_error              TEXT,
            is_error_page           INTEGER NOT NULL DEFAULT 0,

            -- Cookie notice
            cookie_notice_detected  INTEGER NOT NULL DEFAULT 0,
            cookie_notice_accepted  INTEGER NOT NULL DEFAULT 0,

            -- Cookie notice classification (paper taxonomy)
            cookie_position           TEXT,
            cookie_control_type       TEXT,
            cookie_emphasized_option  TEXT,
            cookie_has_reject         INTEGER NOT NULL DEFAULT 0,
            cookie_has_settings       INTEGER NOT NULL DEFAULT 0,
            cookie_pre_selected       INTEGER NOT NULL DEFAULT 0,

            -- Pre-accept captures
            pre_screenshot_path     TEXT,
            pre_html_path           TEXT,
            pre_cookies_path        TEXT,
            pre_wave_path           TEXT,
            pre_wave_error          INTEGER,
            pre_wave_contrast       INTEGER,
            pre_wave_alert          INTEGER,
            pre_wave_feature        INTEGER,
            pre_wave_structure      INTEGER,
            pre_wave_aria           INTEGER,
            pre_lh_score            REAL,
            pre_lh_path             TEXT,
            pre_nvda_path           TEXT,

            -- Post-accept captures (NULL if no cookie notice found/accepted)
            post_screenshot_path    TEXT,
            post_html_path          TEXT,
            post_cookies_path       TEXT,
            post_wave_path          TEXT,
            post_wave_error         INTEGER,
            post_wave_contrast      INTEGER,
            post_wave_alert         INTEGER,
            post_wave_feature       INTEGER,
            post_wave_structure     INTEGER,
            post_wave_aria          INTEGER,
            post_lh_score           REAL,
            post_lh_path            TEXT,
            post_nvda_path          TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_chrome_scans_url    ON chrome_scans(url);
        CREATE INDEX IF NOT EXISTS idx_chrome_scans_error  ON chrome_scans(is_error_page);
        CREATE INDEX IF NOT EXISTS idx_chrome_scans_cookie ON chrome_scans(cookie_notice_detected);
    """)
    # Safe migration: add classification columns to any existing database
    existing = {row[1] for row in con.execute("PRAGMA table_info(chrome_scans)")}
    for col_name, col_def in [
        ("cookie_position",          "TEXT"),
        ("cookie_control_type",      "TEXT"),
        ("cookie_emphasized_option", "TEXT"),
        ("cookie_has_reject",        "INTEGER NOT NULL DEFAULT 0"),
        ("cookie_has_settings",      "INTEGER NOT NULL DEFAULT 0"),
        ("cookie_pre_selected",      "INTEGER NOT NULL DEFAULT 0"),
        ("pre_nvda_path",            "TEXT"),
        ("post_nvda_path",           "TEXT"),
        ("pre_cookies_path",         "TEXT"),
        ("post_cookies_path",        "TEXT"),
    ]:
        if col_name not in existing:
            con.execute(f"ALTER TABLE chrome_scans ADD COLUMN {col_name} {col_def}")
    con.commit()
    return con


# ── CSV loading ────────────────────────────────────────────────────────────────

def load_urls(csv_path: Path) -> list[str]:
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


# ── Browser launch ─────────────────────────────────────────────────────────────

async def launch_chrome_fresh(playwright) -> tuple[BrowserContext, Path]:
    """
    Launch Chrome with a brand-new temp profile directory.
    Returns (context, profile_dir) — caller must close context and delete profile_dir.
    """
    profile_dir = Path(tempfile.mkdtemp(prefix="chrome_scan_"))
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        executable_path=CHROME_PATH,
        headless=False,
        args=LAUNCH_ARGS,
        bypass_csp=True,
        viewport={"width": 1920, "height": 1080},
    )
    return context, profile_dir


# ── Cookie notice ──────────────────────────────────────────────────────────────

_DETECT_JS = """() => {
        const vh = window.innerHeight || document.documentElement.clientHeight;
        const vw = window.innerWidth  || document.documentElement.clientWidth;

        // Container visibility: requires minimum size to rule out tiny/hidden elements
        const isContainerVisible = el => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            if (r.width < 50 || r.height < 20) return false;
            const s = window.getComputedStyle(el);
            return s.display !== 'none' && s.visibility !== 'hidden'
                && parseFloat(s.opacity || '1') > 0;
        };

        // Button visibility: no size constraint — buttons like <a>Accept</a> can be narrow
        const isButtonVisible = el => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) return false;
            const s = window.getComputedStyle(el);
            return s.display !== 'none' && s.visibility !== 'hidden'
                && parseFloat(s.opacity || '1') > 0;
        };

        // Element must be at least partially within the current viewport
        const isInViewport = el => {
            const r = el.getBoundingClientRect();
            return r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
        };

        // True if the element or any ancestor is fixed or sticky positioned
        const isFixedOrSticky = el => {
            let cur = el;
            while (cur && cur !== document.documentElement) {
                const pos = window.getComputedStyle(cur).position;
                if (pos === 'fixed' || pos === 'sticky') return true;
                cur = cur.parentElement;
            }
            return false;
        };

        const COOKIE_WORDS = /*COOKIE_WORDS*/;
        const ACTION_WORDS = /*ACTION_WORDS*/;

        // Include the element's own shadow root text (for CMPs like Transcend that
        // render entirely within a shadow DOM attached to a fixed host element)
        const hasCookieText = el => {
            let t = (el.innerText || el.textContent || '').toLowerCase();
            if (el.shadowRoot) t += (el.shadowRoot.textContent || '').toLowerCase();
            return COOKIE_WORDS.some(w => t.includes(w));
        };

        // Collect interactive elements, traversing shadow DOM.
        // Also checks the root element's own shadow root (not just descendants').
        const BTN_SEL = 'button, [role="button"], input[type="submit"], input[type="button"], a';
        const collectButtons = root => {
            const found = Array.from(root.querySelectorAll(BTN_SEL));
            if (root.shadowRoot) found.push(...collectButtons(root.shadowRoot));
            for (const el of root.querySelectorAll('*')) {
                if (el.shadowRoot) found.push(...collectButtons(el.shadowRoot));
            }
            return found;
        };

        const hasActionButton = container =>
            collectButtons(container).some(el => {
                if (!isButtonVisible(el)) return false;
                const t = (el.innerText || el.textContent
                           || el.getAttribute('aria-label') || '').toLowerCase();
                return ACTION_WORDS.some(w => t.includes(w));
            });

        // Check every visible block element for cookie text + action button.
        // Using content rather than class/id names so any CMP is matched.
        // Require either fixed/sticky positioning OR in-viewport presence to
        // avoid false positives from footer "Cookie Settings" links.
        const SEL = 'div, section, aside, form, dialog, nav, header, footer, '
                  + 'main, article, [role="dialog"], [role="alertdialog"], '
                  + '[role="banner"], [role="main"]';
        for (const el of document.querySelectorAll(SEL)) {
            if (!isContainerVisible(el)) continue;
            if (!hasCookieText(el)) continue;
            if (!hasActionButton(el)) continue;
            if (isFixedOrSticky(el) || isInViewport(el)) return true;
        }
        return false;
    }"""


async def detect_page_language(page: Page) -> str:
    """
    Read <html lang="..."> and return the primary language subtag (e.g. 'de').
    Falls back to 'en' if the attribute is absent or the call fails.
    """
    try:
        raw = await page.evaluate("() => document.documentElement.lang || ''")
        lang = (raw or "").strip().lower().split("-")[0]
        return lang if lang else "en"
    except Exception:
        return "en"


async def _candidate_frames(page: Page) -> list[Frame]:
    """
    Return child frames that are plausible cookie-notice hosts.
    Filters out the main frame, known tracker URLs, and geometrically tiny frames.
    """
    out = []
    for frame in page.frames:
        if frame is page.main_frame:
            continue
        url = frame.url or ""
        if not url or url.startswith(("about:", "data:")) or _FRAME_SKIP_PATTERNS.search(url):
            continue
        try:
            d = await frame.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
            if d["w"] >= _FRAME_MIN_WIDTH and d["h"] >= _FRAME_MIN_HEIGHT:
                out.append(frame)
        except Exception:
            continue  # frame detached or inaccessible
    return out


async def detect_cookie_notice(page: Page, lang: str = "en") -> tuple[bool, Page | Frame]:
    """
    Returns (True, context) if a visible cookie consent banner is found,
    where context is the Page (main document) or the Frame containing the notice.
    Returns (False, page) when nothing is found.

    Searches the main document first, then child frames (for iframe-based CMPs
    such as TrustArc). Language-aware: expands keyword lists for non-English pages.
    """
    js = _apply_kw(_DETECT_JS, lang)
    if bool(await page.evaluate(js)):
        return True, page
    for frame in await _candidate_frames(page):
        try:
            if bool(await frame.evaluate(js)):
                return True, frame
        except Exception:
            continue
    return False, page


_ACCEPT_JS = """() => {
        // Known framework accept buttons (most specific first)
        const directSelectors = [
            '#onetrust-accept-btn-handler',
            '.onetrust-accept-btn-handler',
            '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
            '#CybotCookiebotDialogBodyButtonAccept',
            '.trustarc-agree-btn',
            '#truste-consent-button',
            '.qc-cmp2-summary-buttons button:first-child',
            '.ch2-allow-all-btn',
            '#cookiescript_accept',
            '.cky-btn-accept',
            '#sp-cc-accept',
            '[id*="accept-all"]',
            '[id*="acceptAll"]',
            '[class*="accept-all"]:not([class*="reject"])',
            '[class*="acceptAll"]:not([class*="reject"])',
        ];

        const isVisible = el => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) return false;
            const s = window.getComputedStyle(el);
            return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        };

        for (const sel of directSelectors) {
            const el = document.querySelector(sel);
            if (isVisible(el)) {
                el.click();
                return true;
            }
        }

        // Fallback: find accept-labelled button/link inside a cookie container
        const containerSelectors = [
            '[id*="cookie"]', '[id*="consent"]', '[id*="gdpr"]',
            '[class*="cookie"]', '[class*="consent"]', '[class*="gdpr"]',
            '[role="dialog"]', '[role="alertdialog"]',
        ];
        const acceptTexts = /*AGREE_KW*/;

        for (const containerSel of containerSelectors) {
            for (const container of document.querySelectorAll(containerSel)) {
                if (!isVisible(container)) continue;
                for (const btn of container.querySelectorAll('button, a, [role="button"]')) {
                    const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                    if (acceptTexts.some(t => text === t || text.startsWith(t + ' '))) {
                        btn.click();
                        return true;
                    }
                }
            }
        }

        return false;
    }"""


async def accept_cookie_notice(ctx: Page | Frame, lang: str = "en") -> bool:
    """
    Attempts to find and click a cookie consent accept button.
    Returns True if a button was found and clicked.
    Accepts a Page or Frame context (from detect_cookie_notice).
    """
    return bool(await ctx.evaluate(_apply_kw(_ACCEPT_JS, lang)))


# ── Cookie notice classification ───────────────────────────────────────────────

_CLASSIFY_JS = """() => {
            // ── Shared helpers ────────────────────────────────────────────────
            const vh = window.innerHeight || document.documentElement.clientHeight;
            const vw = window.innerWidth  || document.documentElement.clientWidth;

            // Container visibility: requires minimum size
            const isVisible = el => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                if (r.width < 50 || r.height < 20) return false;
                const s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden'
                    && parseFloat(s.opacity || '1') > 0;
            };

            // Button visibility: no size constraint — short labels like "Accept" can be narrow
            const isButtonVisible = el => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                if (r.width === 0 && r.height === 0) return false;
                const s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden'
                    && parseFloat(s.opacity || '1') > 0;
            };

            const COOKIE_WORDS = /*COOKIE_WORDS*/;
            const ACTION_WORDS = /*ACTION_WORDS*/;

            // Include the element's own shadow root text (for CMPs like Transcend)
            const hasCookieText = el => {
                let t = (el.innerText || el.textContent || '').toLowerCase();
                if (el.shadowRoot) t += (el.shadowRoot.textContent || '').toLowerCase();
                return COOKIE_WORDS.some(w => t.includes(w));
            };

            // Element must be at least partially within the current viewport
            const isInViewport = el => {
                const r = el.getBoundingClientRect();
                return r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
            };

            // True if the element or any ancestor is fixed or sticky positioned
            const isFixedOrSticky = el => {
                let cur = el;
                while (cur && cur !== document.documentElement) {
                    const pos = window.getComputedStyle(cur).position;
                    if (pos === 'fixed' || pos === 'sticky') return true;
                    cur = cur.parentElement;
                }
                return false;
            };

            const getMaxZIndex = el => {
                let maxZ = 0;
                let cur = el;
                while (cur && cur !== document.documentElement) {
                    const z = parseInt(window.getComputedStyle(cur).zIndex) || 0;
                    if (z > maxZ) maxZ = z;
                    cur = cur.parentElement;
                }
                return maxZ;
            };

            // Collect interactive elements including inside shadow DOM.
            // Checks the root element's own shadow root as well as descendants'.
            const BTN_SEL = 'button, a, [role="button"], input[type="submit"], input[type="button"]';
            const collectInteractive = root => {
                const found = Array.from(root.querySelectorAll(BTN_SEL));
                if (root.shadowRoot) found.push(...collectInteractive(root.shadowRoot));
                for (const el of root.querySelectorAll('*')) {
                    if (el.shadowRoot) found.push(...collectInteractive(el.shadowRoot));
                }
                return found;
            };

            const hasActionButton = container =>
                collectInteractive(container).some(el => {
                    if (!isButtonVisible(el)) return false;
                    const t = (el.innerText || el.textContent
                               || el.getAttribute('aria-label') || '').toLowerCase();
                    return ACTION_WORDS.some(w => t.includes(w));
                });

            // ── 1. Find the visible cookie container ─────────────────────────
            // Require fixed/sticky or in-viewport to avoid false positives from
            // footer "Cookie Settings" links on long pages.
            const SEL = 'div, section, aside, form, dialog, nav, header, footer, '
                      + 'main, article, [role="dialog"], [role="alertdialog"], '
                      + '[role="banner"], [role="main"]';
            const candidates = [];
            for (const el of document.querySelectorAll(SEL)) {
                if (!isVisible(el)) continue;
                if (!hasCookieText(el)) continue;
                if (!hasActionButton(el)) continue;
                if (!isFixedOrSticky(el) && !isInViewport(el)) continue;
                candidates.push(el);
            }
            if (candidates.length === 0) {
                return {
                    position: 'none', control_type: 'none',
                    emphasized_option: 'none',
                    has_reject: false, has_settings: false, pre_selected: false,
                };
            }
            // Sort: fixed/sticky first, then by z-index descending, then by area ascending
            // (smallest area = most specific container within the same positioning tier)
            candidates.sort((a, b) => {
                const aFixed = isFixedOrSticky(a) ? 1 : 0;
                const bFixed = isFixedOrSticky(b) ? 1 : 0;
                if (aFixed !== bFixed) return bFixed - aFixed;
                const aZ = getMaxZIndex(a), bZ = getMaxZIndex(b);
                if (aZ !== bZ) return bZ - aZ;
                const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                return (ra.width * ra.height) - (rb.width * rb.height);
            });
            const container = candidates[0];

            // ── 2. Position ───────────────────────────────────────────────────
            const rect     = container.getBoundingClientRect();
            const coverage = (rect.width * rect.height) / (vw * vh);
            const midX     = (rect.left + rect.right)  / 2;
            const midY     = (rect.top  + rect.bottom) / 2;
            const relX     = midX / vw;
            const relY     = midY / vh;

            // Overall: blocks page interaction.
            // aria-modal="true" alone is sufficient — the attribute explicitly declares
            // the element traps focus and interaction regardless of scroll-lock state.
            const isAriaModal = container.getAttribute('aria-modal') === 'true';

            let position;
            if (coverage >= 0.4 || isAriaModal) {
                position = 'overall';
            } else {
                const isSmall   = coverage < 0.18;
                const inCornerH = relX < 0.3 || relX > 0.7;
                const inCornerV = relY < 0.25 || relY > 0.75;
                const isTall    = rect.height > rect.width * 1.5;

                if (isSmall && inCornerH && inCornerV) {
                    position = 'corner_overlay';
                } else if (isTall && relX < 0.3) {
                    position = 'left_overlay';
                } else if (isTall && relX > 0.7) {
                    position = 'right_overlay';
                } else if (relY < 0.35) {
                    position = 'top_overlay';
                } else if (relY > 0.65) {
                    position = 'bottom_overlay';
                } else {
                    position = 'middle_overlay';
                }
            }

            // ── 3. Button/link inventory ──────────────────────────────────────
            const interactive = collectInteractive(container).filter(isVisible);

            const txt = el =>
                (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                .trim().toLowerCase();

            const AGREE_KW    = /*AGREE_KW*/;
            const REJECT_KW   = /*REJECT_KW*/;
            const SETTINGS_KW = /*SETTINGS_KW*/;
            const PAY_KW      = ['pay', 'subscribe', 'subscription', 'premium',
                                  'per month', 'per year'];
            const CLOSE_KW    = ['close', 'dismiss'];

            const matches = kws => el => kws.some(k => txt(el).includes(k));
            // Include <a> tags — many CMPs use styled anchor elements as buttons
            const isBtn   = el =>
                el.tagName === 'BUTTON' || el.tagName === 'INPUT' ||
                el.tagName === 'A'      ||
                el.getAttribute('role') === 'button';

            const agreeEls    = interactive.filter(matches(AGREE_KW));
            const rejectEls   = interactive.filter(matches(REJECT_KW));
            const settingsEls = interactive.filter(matches(SETTINGS_KW));
            const payEls      = interactive.filter(matches(PAY_KW));
            const closeEls    = interactive.filter(matches(CLOSE_KW));

            const hasAgree    = agreeEls.length    > 0;
            const hasReject   = rejectEls.length   > 0;
            const hasSettings = settingsEls.length > 0;
            const hasPay      = payEls.length      > 0;

            // ── 4. Control type ───────────────────────────────────────────────
            let controlType;
            if (hasPay && hasReject && !hasAgree) {
                controlType = 'reject_or_pay';
            } else if (hasPay) {
                controlType = 'accept_or_pay';
            } else if (hasAgree && hasReject && hasSettings) {
                controlType = 'accept_reject_or_settings';
            } else if (hasAgree && hasReject) {
                controlType = 'accept_or_reject';
            } else if (hasAgree && hasSettings) {
                controlType = 'accept_or_settings';
            } else if (hasAgree) {
                controlType = 'accept_only';
            } else if (hasReject && hasSettings) {
                // reject + settings visible but no explicit accept button
                controlType = 'accept_reject_or_settings';
            } else if (hasReject) {
                // reject visible but no explicit accept button
                controlType = 'accept_or_reject';
            } else if (hasSettings) {
                // settings visible but no explicit accept button
                controlType = 'accept_or_settings';
            } else if (closeEls.length > 0) {
                controlType = 'close_only';
            } else {
                controlType = 'informational_only';
            }

            // ── 5. Emphasis ───────────────────────────────────────────────────
            // A button is considered "filled" only when it has a coloured, non-transparent
            // background that is visually distinct from white (e.g. a blue CTA).
            // Near-white backgrounds (r,g,b all > 225) are treated as "unfilled" so that
            // outlined / ghost buttons are not confused with filled ones.
            const hasFill = el => {
                const bg = window.getComputedStyle(el).backgroundColor;
                const m = bg.match(
                    /rgba?\\(\\s*([\\d.]+)\\s*,\\s*([\\d.]+)\\s*,\\s*([\\d.]+)(?:\\s*,\\s*([\\d.]+))?\\s*\\)/
                );
                if (!m) return false;
                const alpha = m[4] !== undefined ? parseFloat(m[4]) : 1.0;
                if (alpha < 0.1) return false;
                const r = parseFloat(m[1]), g = parseFloat(m[2]), b = parseFloat(m[3]);
                return !(r > 225 && g > 225 && b > 225);  // exclude near-white
            };

            const agreeButtons    = agreeEls.filter(isBtn);
            const rejectButtons   = rejectEls.filter(isBtn);
            const settingsButtons = settingsEls.filter(isBtn);

            let emphasizedOption = 'none';
            if (agreeButtons.length > 0) {
                const agreeFill    = hasFill(agreeButtons[0]);
                const rejectFill   = rejectButtons.length   > 0 && hasFill(rejectButtons[0]);
                const settingsFill = settingsButtons.length > 0 && hasFill(settingsButtons[0]);

                if (hasReject || hasSettings) {
                    const othersFilled = [rejectFill, settingsFill].filter(Boolean).length;
                    if (agreeFill && othersFilled === 0)       emphasizedOption = 'accept';
                    else if (!agreeFill && rejectFill)         emphasizedOption = 'reject';
                    else if (!agreeFill && settingsFill)       emphasizedOption = 'settings';
                    else                                       emphasizedOption = 'equal';
                } else {
                    emphasizedOption = agreeFill ? 'accept' : 'none';
                }
            }

            // ── 6. Pre-selected checkboxes ────────────────────────────────────
            const preSelected = Array.from(
                container.querySelectorAll('input[type="checkbox"]')
            ).some(cb => cb.checked && !cb.disabled);

            return {
                position:          position,
                control_type:      controlType,
                emphasized_option: emphasizedOption,
                has_reject:        hasReject,
                has_settings:      hasSettings,
                pre_selected:      preSelected,
            };
        }"""


async def classify_cookie_notice(ctx: Page | Frame, lang: str = "en") -> dict:
    """
    Classifies a visible cookie consent notice using the taxonomy from:
      "A Cross-Platform Evaluation of Privacy Notices and Tracking Practices"

    Must be called AFTER detect_cookie_notice() returns True and BEFORE
    accept_cookie_notice() so the notice is in its natural state.

    Returns:
        position          : 'overall' | 'top_overlay' | 'bottom_overlay' |
                            'middle_overlay' | 'left_overlay' | 'right_overlay' |
                            'corner_overlay' | 'none'
        control_type      : 'accept_only' | 'accept_or_reject' | 'accept_or_settings' |
                            'accept_reject_or_settings' | 'accept_or_pay' |
                            'reject_or_pay' | 'close_only' | 'informational_only' | 'none'
        emphasized_option : 'accept' | 'reject' | 'settings' | 'equal' | 'none'
        has_reject        : bool
        has_settings      : bool
        pre_selected      : bool  — non-disabled consent checkboxes are pre-checked
    """
    _fallback = {
        "position": "none", "control_type": "none",
        "emphasized_option": "none",
        "has_reject": False, "has_settings": False, "pre_selected": False,
    }
    try:
        result = await ctx.evaluate(_apply_kw(_CLASSIFY_JS, lang))
        return result if result else _fallback
    except Exception as e:
        print(f"       [!] Cookie notice classification failed: {e}")
        return _fallback


# ── WAVE injection ─────────────────────────────────────────────────────────────

async def run_wave(page: Page, output_path: Path | None = None) -> dict:
    """
    Inject wave.min.js, extract accessibility statistics, save raw JSON to
    output_path (if given), then remove WAVE's UI from the page.
    """
    if not WAVE_JS_PATH.exists():
        print(f"       [!] WAVE skipped — {WAVE_JS_PATH} not found")
        return _WAVE_EMPTY.copy()

    wave_script = WAVE_JS_PATH.read_text(encoding="utf-8")
    await page.add_script_tag(content=wave_script)

    wave_results_raw = await page.evaluate(
        "() => JSON.parse(JSON.stringify(window.wave.results))"
    )
    if output_path is not None:
        output_path.write_text(json.dumps(wave_results_raw, indent=2), encoding="utf-8")

    wave_stats = await page.evaluate("""() => {
        const cats = window.wave && window.wave.results && window.wave.results.categories;
        if (!cats) return null;
        const get = key => {
            const c = cats[key];
            if (!c) return 0;
            if (typeof c.count === 'number') return c.count;
            if (Array.isArray(c.items))      return c.items.length;
            if (typeof c === 'number')       return c;
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

    await page.evaluate("document.dispatchEvent(new CustomEvent('resetWave'))")
    return wave_stats


# ── Lighthouse ─────────────────────────────────────────────────────────────────

async def run_lighthouse(url: str, output_file: Path) -> float | None:
    """
    Run Lighthouse against the already-open Chrome instance and save the
    report to output_file. Returns the accessibility score (0–100) or None.
    """
    try:
        result = await asyncio.create_subprocess_exec(
            _LH_CMD, url,
            "--output=json",
            f"--output-path={output_file}",
            f"--config-path={LH_CONFIG_PATH}",
            f"--port={DEBUGGING_PORT}",
            "--chrome-flags=",
            "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(result.communicate(), timeout=120)

        if result.returncode != 0:
            err = stderr.decode(errors="replace").strip().splitlines()
            print(f"       [!] Lighthouse exited {result.returncode}: {err[-1] if err else ''}")
            return None

        if output_file.exists():
            report = json.loads(output_file.read_text())
            score  = report.get("categories", {}).get("accessibility", {}).get("score")
            if score is not None:
                score = round(score * 100, 1)
            return score

    except FileNotFoundError:
        print(f"       [!] Lighthouse not found ({_LH_CMD}) — install with: npm install -g lighthouse")
    except asyncio.TimeoutError:
        print("       [!] Lighthouse timed out")
    except Exception as e:
        print(f"       [!] Lighthouse error: {e}")

    return None


# ── Capture helpers ────────────────────────────────────────────────────────────

async def capture_screenshot(page: Page, dest: Path) -> str | None:
    try:
        await page.screenshot(path=str(dest), full_page=True)
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


# ── Core scan ──────────────────────────────────────────────────────────────────

NETWORKIDLE_TIMEOUT = 30  # seconds to wait for network idle after navigation

async def scan_url(
    playwright,
    url: str,
    artifacts_root: Path,
    scan_id: int,
    timeout: int = 30,
    run_wave_flag: bool = True,
    run_lighthouse_flag: bool = True,
    run_nvda_flag: bool = True,
) -> dict:
    # Fresh Chrome profile per URL — completely clean cookies, cache, history
    context, profile_dir = await launch_chrome_fresh(playwright)
    page = await context.new_page()

    http_status: int | None = None
    nav_error:   str | None = None

    # ── Navigate ───────────────────────────────────────────────────────────────
    try:
        response    = await page.goto(url, wait_until="domcontentloaded",
                                       timeout=timeout * 1000)
        http_status = response.status if response else None
    except Exception as e:
        nav_error = str(e).splitlines()[0]

    is_error_page = bool(
        nav_error or (http_status is not None and http_status >= 400)
    )

    art_dir = artifact_dir(artifacts_root, scan_id, url)

    if is_error_page:
        print(f"       [!] Error page ({nav_error or f'HTTP {http_status}'}) — skipping")
        await capture_screenshot(page, art_dir / "pre_screenshot.png")
        await context.close()
        shutil.rmtree(profile_dir, ignore_errors=True)
        return {
            "url": url, "http_status": http_status, "error": nav_error,
            "is_error_page": True,
            "cookie_notice_detected": False, "cookie_notice_accepted": False,
            "cookie_position": None, "cookie_control_type": None,
            "cookie_emphasized_option": None, "cookie_has_reject": False,
            "cookie_has_settings": False, "cookie_pre_selected": False,
            "pre": _empty_phase(), "post": None,
        }

    # ── Wait for network idle ─────────────────────────────────────────────────
    print(f"       [HTTP {http_status}] Waiting for network idle (max {NETWORKIDLE_TIMEOUT}s)...")
    try:
        await page.wait_for_load_state("networkidle", timeout=NETWORKIDLE_TIMEOUT * 1000)
        print("       [*] Network idle")
    except Exception:
        print(f"       [*] Network still active after {NETWORKIDLE_TIMEOUT}s — continuing")

    # Wait briefly for CMPs to initialise — they often fire JS after networkidle
    await page.wait_for_timeout(3000)

    # ── Cookie notice detection & classification (before any captures) ───────────
    page_lang = await detect_page_language(page)
    cookie_detected, cookie_ctx = await detect_cookie_notice(page, lang=page_lang)
    cookie_info     = None
    cookie_accepted = False
    post            = None

    if not cookie_detected:
        # Some CMPs are slow-loading — wait a further 7 s and retry once
        print("       [*] No cookie notice yet — retrying in 7 s...")
        await page.wait_for_timeout(7000)
        cookie_detected, cookie_ctx = await detect_cookie_notice(page, lang=page_lang)

    if cookie_detected:
        print("       [*] Cookie notice detected"
              + (" (in iframe)" if cookie_ctx is not page else "")
              + " — classifying...")
        cookie_info = await classify_cookie_notice(cookie_ctx, lang=page_lang)
        print(f"       [+] Classification: {cookie_info}")
    else:
        print("       [*] No cookie notice detected")

    # Short pause so NVDA's virtual buffer has time to build
    await page.wait_for_timeout(5000)

    # ── Pre-accept captures ────────────────────────────────────────────────────
    print("       [*] Pre-accept captures...")
    pre = await _capture_phase(
        page, art_dir, "pre", url,
        run_wave_flag, run_lighthouse_flag, run_nvda_flag,
    )

    # ── Accept cookie notice & post-accept captures ────────────────────────────
    if cookie_detected:
        print("       [*] Attempting to accept cookie notice...")
        cookie_accepted = await accept_cookie_notice(cookie_ctx, lang=page_lang)
        if cookie_accepted:
            print("       [+] Cookie notice accepted")
            # Reload the page so NVDA gets a fresh tree interceptor for the
            # post-accept state. Consent is already stored in cookies/localStorage
            # so the reload will not show the cookie notice again.
            # try:
            #     await page.reload(wait_until="networkidle", timeout=30000)
            # except Exception:
            #     pass
            print("       [*] Post-accept captures...")
            post = await _capture_phase(
                page, art_dir, "post", url,
                run_wave_flag, run_lighthouse_flag, run_nvda_flag,
            )
        else:
            print("       [!] Cookie notice found but could not be accepted")

    await context.close()
    shutil.rmtree(profile_dir, ignore_errors=True)

    cls = cookie_info or {}
    return {
        "url":                    url,
        "http_status":            http_status,
        "error":                  nav_error,
        "is_error_page":          False,
        "cookie_notice_detected": cookie_detected,
        "cookie_notice_accepted": cookie_accepted,
        "cookie_position":         cls.get("position"),
        "cookie_control_type":     cls.get("control_type"),
        "cookie_emphasized_option": cls.get("emphasized_option"),
        "cookie_has_reject":       cls.get("has_reject", False),
        "cookie_has_settings":     cls.get("has_settings", False),
        "cookie_pre_selected":     cls.get("pre_selected", False),
        "pre":                    pre,
        "post":                   post,
    }


def _empty_phase() -> dict:
    return {
        "screenshot_path": None, "html_path": None,
        "cookies_path": None,
        "wave_path": None, "wave_stats": _WAVE_EMPTY.copy(),
        "lh_score": None, "lh_path": None,
        "nvda_path": None,
    }


async def _capture_phase(
    page: Page,
    art_dir: Path,
    prefix: str,
    url: str,
    run_wave_flag: bool,
    run_lighthouse_flag: bool,
    run_nvda_flag: bool = True,
) -> dict:
    phase = _empty_phase()

    phase["html_path"] = await capture_html(page, art_dir / f"{prefix}_page.html")
    phase["screenshot_path"] = await capture_screenshot(
        page, art_dir / f"{prefix}_screenshot.png"
    )
    phase["cookies_path"] = await capture_cookies(page, art_dir / f"{prefix}_cookies.json")

    if run_nvda_flag:
        nvda_path = art_dir / f"{prefix}_nvda.json"
        print(f"       [*] NVDA transcript {prefix}...")
        try:
            # Restart NVDA for a clean virtual buffer with no stale content from
            # previous captures or browser sessions.
            await restart_nvda()
            await page.bring_to_front()
            try:
                await page.focus("body")
            except Exception:
                pass
            result = await capture_nvda_transcript(nvda_path, url=url)
            phase["nvda_path"] = str(nvda_path) if result is not None else None
            if result is not None:
                print(f"       [+] NVDA {prefix}: {len(result)} chars")
        except Exception as e:
            print(f"       [!] NVDA {prefix} skipped: {e}")

    if run_lighthouse_flag:
        lh_path = art_dir / f"{prefix}_lighthouse.json"
        phase["lh_score"] = await run_lighthouse(url, lh_path)
        phase["lh_path"]  = str(lh_path) if phase["lh_score"] is not None else None
        print(f"       [+] Lighthouse {prefix}: {phase['lh_score']}")

    if run_wave_flag:
        wave_path = art_dir / f"{prefix}_wave.json"
        try:
            phase["wave_stats"] = await run_wave(page, wave_path)
            phase["wave_path"]  = str(wave_path)
            print(f"       [+] WAVE {prefix}: {phase['wave_stats']}")
        except Exception as e:
            print(f"       [!] WAVE {prefix} skipped: {e}")

    return phase


# ── Per-URL helper (used by scan.py for interleaved scanning) ──────────────────

async def chrome_process_url(
    con: sqlite3.Connection,
    p,  # Playwright instance
    url: str,
    artifacts_root: Path,
    timeout: int = 30,
    run_wave_flag: bool = True,
    run_lighthouse_flag: bool = True,
    run_nvda_flag: bool = True,
) -> int:
    """Scan one URL with Chrome, write results to DB, return scan_id."""
    cur = con.execute(
        """INSERT INTO chrome_scans (url, scanned_at, is_error_page,
               cookie_notice_detected, cookie_notice_accepted)
           VALUES (?, ?, 0, 0, 0)""",
        (url, datetime.now(timezone.utc).isoformat()),
    )
    scan_id = cur.lastrowid
    con.commit()

    stats = await scan_url(
        p, url, artifacts_root, scan_id,
        timeout=timeout,
        run_wave_flag=run_wave_flag,
        run_lighthouse_flag=run_lighthouse_flag,
        run_nvda_flag=run_nvda_flag,
    )

    pre  = stats["pre"]
    post = stats["post"] or _empty_phase()
    pws  = pre["wave_stats"]
    pows = post["wave_stats"]

    con.execute(
        """UPDATE chrome_scans SET
               http_status             = ?,
               page_error              = ?,
               is_error_page           = ?,
               cookie_notice_detected  = ?,
               cookie_notice_accepted  = ?,
               cookie_position         = ?,
               cookie_control_type     = ?,
               cookie_emphasized_option = ?,
               cookie_has_reject       = ?,
               cookie_has_settings     = ?,
               cookie_pre_selected     = ?,
               pre_screenshot_path     = ?,
               pre_html_path           = ?,
               pre_cookies_path        = ?,
               pre_wave_path           = ?,
               pre_wave_error          = ?,
               pre_wave_contrast       = ?,
               pre_wave_alert          = ?,
               pre_wave_feature        = ?,
               pre_wave_structure      = ?,
               pre_wave_aria           = ?,
               pre_lh_score            = ?,
               pre_lh_path             = ?,
               post_screenshot_path    = ?,
               post_html_path          = ?,
               post_cookies_path       = ?,
               post_wave_path          = ?,
               post_wave_error         = ?,
               post_wave_contrast      = ?,
               post_wave_alert         = ?,
               post_wave_feature       = ?,
               post_wave_structure     = ?,
               post_wave_aria          = ?,
               post_lh_score           = ?,
               post_lh_path            = ?,
               pre_nvda_path           = ?,
               post_nvda_path          = ?
           WHERE id = ?""",
        (
            stats["http_status"],
            stats["error"],
            1 if stats["is_error_page"] else 0,
            1 if stats["cookie_notice_detected"] else 0,
            1 if stats["cookie_notice_accepted"] else 0,
            stats["cookie_position"],
            stats["cookie_control_type"],
            stats["cookie_emphasized_option"],
            1 if stats["cookie_has_reject"] else 0,
            1 if stats["cookie_has_settings"] else 0,
            1 if stats["cookie_pre_selected"] else 0,
            pre["screenshot_path"],
            pre["html_path"],
            pre["cookies_path"],
            pre["wave_path"],
            pws.get("error"),   pws.get("contrast"), pws.get("alert"),
            pws.get("feature"), pws.get("structure"), pws.get("aria"),
            pre["lh_score"],
            pre["lh_path"],
            stats["post"] and post["screenshot_path"],
            stats["post"] and post["html_path"],
            stats["post"] and post["cookies_path"],
            stats["post"] and post["wave_path"],
            stats["post"] and pows.get("error"),
            stats["post"] and pows.get("contrast"),
            stats["post"] and pows.get("alert"),
            stats["post"] and pows.get("feature"),
            stats["post"] and pows.get("structure"),
            stats["post"] and pows.get("aria"),
            stats["post"] and post["lh_score"],
            stats["post"] and post["lh_path"],
            pre["nvda_path"],
            stats["post"] and post["nvda_path"],
            scan_id,
        ),
    )
    con.commit()

    if stats["is_error_page"]:
        print(f"       [Chrome] error [HTTP {stats['http_status']}] [scan_id={scan_id}]")
    else:
        cookie_status = (
            "accepted" if stats["cookie_notice_accepted"] else
            "detected, not accepted" if stats["cookie_notice_detected"] else
            "none"
        )
        print(
            f"       [Chrome] cookie: {cookie_status} | "
            f"pre WAVE errors: {pws.get('error')} | "
            f"pre LH: {pre['lh_score']} | "
            f"post WAVE errors: {pows.get('error') if stats['post'] else 'n/a'} | "
            f"post LH: {post['lh_score'] if stats['post'] else 'n/a'} "
            f"[scan_id={scan_id}]"
        )
    return scan_id


# ── Main ───────────────────────────────────────────────────────────────────────

async def chrome_main(
    csv_path: Path,
    db_path: Path,
    artifacts_root: Path,
    timeout: int,
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

    print(f"\n{'='*60}")
    print(f"  Chrome Cookie Notice & Accessibility Scanner")
    print(f"{'='*60}")
    print(f"  Input:       {csv_path}  ({len(urls)} URLs)")
    print(f"  Database:    {db_path}")
    print(f"  Artifacts:   {artifacts_root}")
    print(f"  Timeout:     {timeout}s  |  Network idle timeout: {NETWORKIDLE_TIMEOUT}s")
    print(f"  WAVE:        {'yes' if run_wave_flag else 'no'}")
    print(f"  Lighthouse:  {'yes' if run_lighthouse_flag else 'no'}")
    print(f"  NVDA:        {'yes' if run_nvda_flag else 'no'}\n")

    async with async_playwright() as p:
        try:
            for i, url in enumerate(urls, 1):
                print(f"[{i}/{len(urls)}] {url}")

                cur = con.execute(
                    """INSERT INTO chrome_scans (url, scanned_at, is_error_page,
                           cookie_notice_detected, cookie_notice_accepted)
                       VALUES (?, ?, 0, 0, 0)""",
                    (url, datetime.now(timezone.utc).isoformat()),
                )
                scan_id = cur.lastrowid
                con.commit()

                stats = await scan_url(
                    p, url, artifacts_root, scan_id,
                    timeout=timeout,
                    run_wave_flag=run_wave_flag,
                    run_lighthouse_flag=run_lighthouse_flag,
                    run_nvda_flag=run_nvda_flag,
                )

                pre  = stats["pre"]
                post = stats["post"] or _empty_phase()
                pws  = pre["wave_stats"]
                pows = post["wave_stats"]

                con.execute(
                    """UPDATE chrome_scans SET
                           http_status             = ?,
                           page_error              = ?,
                           is_error_page           = ?,
                           cookie_notice_detected  = ?,
                           cookie_notice_accepted  = ?,
                           cookie_position         = ?,
                           cookie_control_type     = ?,
                           cookie_emphasized_option = ?,
                           cookie_has_reject       = ?,
                           cookie_has_settings     = ?,
                           cookie_pre_selected     = ?,
                           pre_screenshot_path     = ?,
                           pre_html_path           = ?,
                           pre_cookies_path        = ?,
                           pre_wave_path           = ?,
                           pre_wave_error          = ?,
                           pre_wave_contrast       = ?,
                           pre_wave_alert          = ?,
                           pre_wave_feature        = ?,
                           pre_wave_structure      = ?,
                           pre_wave_aria           = ?,
                           pre_lh_score            = ?,
                           pre_lh_path             = ?,
                           post_screenshot_path    = ?,
                           post_html_path          = ?,
                           post_cookies_path       = ?,
                           post_wave_path          = ?,
                           post_wave_error         = ?,
                           post_wave_contrast      = ?,
                           post_wave_alert         = ?,
                           post_wave_feature       = ?,
                           post_wave_structure     = ?,
                           post_wave_aria          = ?,
                           post_lh_score           = ?,
                           post_lh_path            = ?,
                           pre_nvda_path           = ?,
                           post_nvda_path          = ?
                       WHERE id = ?""",
                    (
                        stats["http_status"],
                        stats["error"],
                        1 if stats["is_error_page"] else 0,
                        1 if stats["cookie_notice_detected"] else 0,
                        1 if stats["cookie_notice_accepted"] else 0,
                        stats["cookie_position"],
                        stats["cookie_control_type"],
                        stats["cookie_emphasized_option"],
                        1 if stats["cookie_has_reject"] else 0,
                        1 if stats["cookie_has_settings"] else 0,
                        1 if stats["cookie_pre_selected"] else 0,
                        pre["screenshot_path"],
                        pre["html_path"],
                        pre["cookies_path"],
                        pre["wave_path"],
                        pws.get("error"),   pws.get("contrast"), pws.get("alert"),
                        pws.get("feature"), pws.get("structure"), pws.get("aria"),
                        pre["lh_score"],
                        pre["lh_path"],
                        stats["post"] and post["screenshot_path"],
                        stats["post"] and post["html_path"],
                        stats["post"] and post["cookies_path"],
                        stats["post"] and post["wave_path"],
                        stats["post"] and pows.get("error"),
                        stats["post"] and pows.get("contrast"),
                        stats["post"] and pows.get("alert"),
                        stats["post"] and pows.get("feature"),
                        stats["post"] and pows.get("structure"),
                        stats["post"] and pows.get("aria"),
                        stats["post"] and post["lh_score"],
                        stats["post"] and post["lh_path"],
                        pre["nvda_path"],
                        stats["post"] and post["nvda_path"],
                        scan_id,
                    ),
                )
                con.commit()

                if stats["is_error_page"]:
                    print(f"       -> error [HTTP {stats['http_status']}] [scan_id={scan_id}]")
                else:
                    cookie_status = (
                        "accepted" if stats["cookie_notice_accepted"] else
                        "detected, not accepted" if stats["cookie_notice_detected"] else
                        "none"
                    )
                    print(
                        f"       -> cookie: {cookie_status} | "
                        f"pre WAVE errors: {pws.get('error')} | "
                        f"pre LH: {pre['lh_score']} | "
                        f"post WAVE errors: {pows.get('error') if stats['post'] else 'n/a'} | "
                        f"post LH: {post['lh_score'] if stats['post'] else 'n/a'} "
                        f"[scan_id={scan_id}]"
                    )
        finally:
            con.close()

    print(f"\n[+] Done. Results saved to {db_path}")
    print(f"[+] Artifacts saved to {artifacts_root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scan URLs with Chrome for cookie notices and accessibility."
    )
    parser.add_argument("csv",  type=Path, help="CSV file of URLs")
    parser.add_argument("db",   type=Path, help="SQLite output file")
    parser.add_argument(
        "--artifacts", type=Path, default=None,
        help="Directory for artifacts (default: <db_dir>/artifacts/)",
    )
    parser.add_argument("--timeout", type=int, default=30,
                        help="Navigation timeout in seconds (default: 30)")
    parser.add_argument("--no-wave",       action="store_true",
                        help="Skip WAVE accessibility injection")
    parser.add_argument("--no-lighthouse", action="store_true",
                        help="Skip Lighthouse accessibility audit")
    parser.add_argument("--no-nvda",       action="store_true",
                        help="Skip NVDA screen reader transcript")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"[!] CSV file not found: {args.csv}")
        sys.exit(1)

    artifacts_root = args.artifacts or args.db.parent / "artifacts"

    asyncio.run(chrome_main(
        args.csv, args.db, artifacts_root,
        args.timeout,
        run_wave_flag=not args.no_wave,
        run_lighthouse_flag=not args.no_lighthouse,
        run_nvda_flag=not args.no_nvda,
    ))
