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
import base64
import json
import re
import shutil
import sqlite3
import sys

# On Windows, npm CLIs are installed as .cmd files which CreateProcess won't
# resolve without the shell — use the .cmd suffix directly instead.
_LH_CMD = "lighthouse.cmd" if sys.platform == "win32" else "lighthouse"

# Set to True via --debug to print verbose cookie-acceptance diagnostics.
DEBUG: bool = False
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright, Page, Frame, BrowserContext
from nvda_capture import capture_nvda_transcript, restart_nvda

# ── bannerclick keyword data (inlined from bannerclick/utility/dictWords.py) ───
# Inlined so chrome_scan.py is self-contained and requires no bannerclick install.
_BC_ACCEPT_WORDS = ['accept', 'agree', 'confirm', 'consent', 'allow',
                    'accept1', 'accept2', 'accept3']
_BC_NON_ACCEPTABLE = ['disagree', 'do not', "don't", "just", "only", "without"]

_BC_WORDS: dict[str, dict[str, str]] = {
    'en': {
        'cookies': 'cookies', 'cookies1': 'cookies2', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': 'accept', 'agree': 'agree',
        'confirm': 'confirm', 'consent': 'consent', 'allow': 'allow',
        'accept1': 'continue', 'accept2': 'accept2', 'accept3': 'accept3',
        'reject': 'reject', 'disagree': 'disagree', 'decline': 'decline',
        'deny': 'deny', 'refuse': 'refuse', 'reject1': 'disable',
        'reject2': 'essential', 'setting': 'setting', 'manage': 'manage',
        'option': 'option', 'choice': 'choice', 'purpose': 'purpose',
        'preference': 'preference', 'customize': 'customize', 'configur': 'configur',
        'partner': 'partner', 'personalised': 'personalised', 'policy': 'policy',
        'privacy': 'privacy', 'privacy policy': 'privacy policy',
        'legitimate interest': 'legitimate interest', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'de': {
        'cookies': 'cookies', 'cookies1': 'cookies2', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': 'akzeptieren', 'agree': 'stimme zu',
        'confirm': 'bestätigen', 'consent': 'consent', 'allow': 'allow',
        'accept1': 'zustimmen', 'accept2': 'annehmen', 'accept3': 'akzeptiere',
        'reject': 'ablehnen', 'disagree': 'oneens', 'decline': 'afnemen',
        'deny': 'weigeren', 'refuse': 'weigeren', 'reject1': 'reject1',
        'reject2': 'reject2', 'setting': 'einstellungen', 'manage': 'verwalten',
        'option': 'option', 'choice': 'auswahl', 'purpose': 'zwecke',
        'preference': 'präferenz', 'customize': 'anpassen', 'configur': 'konfigurieren',
        'partner': 'partner', 'personalised': 'personalisiert', 'policy': 'politik',
        'privacy': 'datenschutz', 'privacy policy': 'datenschutzerklärung',
        'legitimate interest': 'berechtigtes Interesse', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'es': {
        'cookies': 'cookies', 'cookies1': 'cookies1', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': 'aceptar', 'agree': 'acordar',
        'confirm': 'confirmar', 'consent': 'consentir', 'allow': 'permitir',
        'accept1': 'acept', 'accept2': 'acceptar', 'accept3': 'acordar',
        'reject': 'rechazar', 'disagree': 'desacuerdo', 'decline': 'declive',
        'deny': 'negar', 'refuse': 'rechazar', 'reject1': 'deshabilitar',
        'reject2': 'rechazarlas', 'setting': 'ajuste', 'manage': 'administrar',
        'option': 'opcione', 'choice': 'choice', 'purpose': 'purpose',
        'preference': 'preferencia', 'customize': 'personalizar', 'configur': 'configur',
        'partner': 'socio', 'personalised': 'personalizado', 'policy': 'política',
        'privacy': 'privacidad', 'privacy policy': 'política de privacidad',
        'legitimate interest': 'interés legítimo', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'it': {
        'cookies': 'cookies', 'cookies1': 'cookies1', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': 'accetta', 'agree': 'concordare',
        'confirm': 'conferma', 'consent': 'consenso', 'allow': 'permettere',
        'accept1': 'accett', 'accept2': 'acconsento', 'accept3': 'accept3',
        'reject': 'rifiuta', 'disagree': 'disagree', 'decline': 'declino',
        'deny': 'negare', 'refuse': 'rifiutare', 'reject1': 'disabilita',
        'reject2': 'rifiuto', 'setting': 'impostazione', 'manage': 'gestisci',
        'option': 'opzion', 'choice': 'scelt', 'purpose': 'purpose',
        'preference': 'preference', 'customize': 'personalizza', 'configur': 'configur',
        'partner': 'partner', 'personalised': 'personalizz', 'policy': 'politica',
        'privacy': 'privacy', 'privacy policy': 'informativa sulla privacy',
        'legitimate interest': 'interesse legittimo', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'pt': {
        'cookies': 'cookies', 'cookies1': 'cookies2', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': 'aceitar', 'agree': 'concordo',
        'confirm': 'confirmar', 'consent': 'consentimento', 'allow': 'permitir',
        'accept1': 'aceito', 'accept2': 'accept2', 'accept3': 'accept3',
        'reject': 'rejeitar', 'disagree': 'discordo', 'decline': 'declinar',
        'deny': 'negar', 'refuse': 'recusar', 'reject1': 'reject1',
        'reject2': 'reject2', 'setting': 'setting', 'manage': 'gerenciar',
        'option': 'opções', 'choice': 'escolha', 'purpose': 'purpose',
        'preference': 'preferência', 'customize': 'personalizar', 'configur': 'configur',
        'partner': 'parceiro', 'personalised': 'personalizado', 'policy': 'política',
        'privacy': 'privacidade', 'privacy policy': 'política de privacidade',
        'legitimate interest': 'interesse legítimo', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'fr': {
        'cookies': 'cookies', 'cookies1': 'cookies2', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': 'accepter', 'agree': 'accord',
        'confirm': 'Confirmer', 'consent': 'consent', 'allow': 'autoriser',
        'accept1': 'accepte', 'accept2': 'accept2', 'accept3': 'accept3',
        'reject': 'rejeter', 'disagree': "pas d'accord", 'decline': 'déclin',
        'deny': 'refuser', 'refuse': 'refuser', 'reject1': 'reject1',
        'reject2': 'reject2', 'setting': 'réglage', 'manage': 'gérer',
        'option': 'option', 'choice': 'choix', 'purpose': 'purpose',
        'preference': 'préférence', 'customize': 'personnaliser', 'configur': 'configur',
        'partner': 'partenaire', 'personalised': 'personnalisé', 'policy': 'politique',
        'privacy': 'confidentialité', 'privacy policy': 'politique de confidentialité',
        'legitimate interest': 'intérêt légitime', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'nl': {
        'cookies': 'cookies', 'cookies1': 'cookies2', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': 'accepteren', 'agree': 'akkoord',
        'confirm': 'bevestigen', 'consent': 'toestemming', 'allow': 'toestaan',
        'accept1': 'accepteer', 'accept2': 'accept2', 'accept3': 'accept3',
        'reject': 'weigeren', 'disagree': 'oneens', 'decline': 'afwijzen',
        'deny': 'weigeren', 'refuse': 'weigeren', 'reject1': 'reject1',
        'reject2': 'reject2', 'setting': 'instellingen', 'manage': 'beheren',
        'option': 'optie', 'choice': 'keuze', 'purpose': 'doel',
        'preference': 'voorkeur', 'customize': 'aanpassen', 'configur': 'configur',
        'partner': 'partner', 'personalised': 'gepersonaliseerd', 'policy': 'beleid',
        'privacy': 'privacy', 'privacy policy': 'privacybeleid',
        'legitimate interest': 'legitiem belang', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'sv': {
        'cookies': 'cookies', 'cookies1': 'cookies2', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': 'acceptera', 'agree': 'godkänn',
        'confirm': 'confirm', 'consent': 'consent', 'allow': 'tillåt',
        'accept1': 'accept1', 'accept2': 'accept2', 'accept3': 'accept3',
        'reject': 'avvisa', 'disagree': 'disagree', 'decline': 'decline',
        'deny': 'deny', 'refuse': 'refuse', 'reject1': 'disable',
        'reject2': 'reject2', 'setting': 'inställningar', 'manage': 'hantera',
        'option': 'option', 'choice': 'choice', 'purpose': 'purpose',
        'preference': 'preference', 'customize': 'customize', 'configur': 'configur',
        'partner': 'partner', 'personalised': 'personalised', 'policy': 'policy',
        'privacy': 'privacy', 'privacy policy': 'privacy policy',
        'legitimate interest': 'legitimate interest', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'tr': {
        'cookies': 'çerezler', 'cookies1': 'cookies', 'cookie': 'çerez',
        'Cookie': 'Çerezi', 'accept': 'kabul', 'agree': 'kabul',
        'confirm': 'onaylamak', 'consent': 'izni', 'allow': 'izin ver',
        'accept1': 'İzin', 'accept2': 'izin', 'accept3': 'accept3',
        'reject': 'reddet', 'disagree': 'katılmıyorum', 'decline': 'düşüş',
        'deny': 'inkar', 'refuse': 'reddet', 'reject1': 'reject1',
        'reject2': 'reject2', 'setting': 'setting', 'manage': 'manage',
        'option': 'option', 'choice': 'choice', 'purpose': 'purpose',
        'preference': 'preference', 'customize': 'customize', 'configur': 'configur',
        'partner': 'ortakları', 'personalised': 'Kişiselleştirilmiş',
        'policy': 'gizlilik', 'privacy': 'politikası',
        'privacy policy': 'gizlilik politikası',
        'legitimate interest': 'meşru menfaatt', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'ru': {
        'cookies': 'cookies', 'cookies1': 'cookies2', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': 'принимаю', 'agree': 'cогласен',
        'confirm': 'подтвердить', 'consent': 'согласие', 'allow': 'разрешить',
        'accept1': 'принимать', 'accept2': 'принять', 'accept3': 'accept3',
        'reject': 'отклонить', 'disagree': 'не соглас', 'decline': 'снижение',
        'deny': 'Запретить', 'refuse': 'oтказаться', 'reject1': 'не прин',
        'reject2': 'reject2', 'setting': 'настройки', 'manage': 'управлять',
        'option': 'параметры', 'choice': 'выбор', 'purpose': 'purpose',
        'preference': 'preference', 'customize': 'customize', 'configur': 'configur',
        'partner': 'партнер', 'personalised': 'персонализированный',
        'policy': 'policy', 'privacy': 'конфиденциальность',
        'privacy policy': 'политика конфиденциальности',
        'legitimate interest': 'законный интерес', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'ja': {
        'cookies': 'クッキー', 'cookies1': 'cookies', 'cookie': 'クッキー',
        'Cookie': 'クッキー', 'accept': '受け入れる', 'agree': '承認',
        'confirm': '確認', 'consent': '同意', 'allow': '許可する',
        'accept1': 'accept1', 'accept2': 'accept2', 'accept3': 'accept3',
        'reject': '拒絶', 'disagree': '同意しない', 'decline': 'decline',
        'deny': 'deny', 'refuse': 'refuse', 'reject1': 'reject1',
        'reject2': 'reject2', 'setting': 'setting', 'manage': 'manage',
        'option': 'option', 'choice': 'choice', 'purpose': 'purpose',
        'preference': 'preference', 'customize': 'customize', 'configur': 'configur',
        'partner': 'パートナー', 'personalised': 'パーソナライズ',
        'policy': 'ポリシー', 'privacy': 'プライバシー',
        'privacy policy': 'プライバシーポリシー',
        'legitimate interest': '正当な利益', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'zh': {
        'cookies': '承诺', 'cookies1': 'cookies', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': '接受', 'agree': '同意',
        'confirm': '确认', 'consent': '承诺', 'allow': '允许',
        'accept1': 'accept1', 'accept2': 'accept2', 'accept3': 'accept3',
        'reject': '拒绝', 'disagree': '不同意', 'decline': '拒绝',
        'deny': '拒绝', 'refuse': '拒绝', 'reject1': 'reject1',
        'reject2': 'reject2', 'setting': 'setting', 'manage': 'manage',
        'option': 'option', 'choice': 'choice', 'purpose': 'purpose',
        'preference': 'preference', 'customize': 'customize', 'configur': 'configur',
        'partner': 'partner', 'personalised': '个性化', 'policy': '政策',
        'privacy': '隐私', 'privacy policy': '隐私政策',
        'legitimate interest': '合法权益', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'ko': {
        'cookies': '쿠키', 'cookies1': 'cookies', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': 'accept', 'agree': 'agree',
        'confirm': 'confirm', 'consent': 'consent', 'allow': 'allow',
        'accept1': 'accept1', 'accept2': 'accept2', 'accept3': 'accept3',
        'reject': 'reject', 'disagree': 'disagree', 'decline': 'decline',
        'deny': 'deny', 'refuse': 'refuse', 'reject1': 'reject1',
        'reject2': 'reject2', 'setting': 'setting', 'manage': 'manage',
        'option': 'option', 'choice': 'choice', 'purpose': 'purpose',
        'preference': 'preference', 'customize': 'customize', 'configur': 'configur',
        'partner': 'partner', 'personalised': 'personalised', 'policy': 'policy',
        'privacy': 'privacy', 'privacy policy': 'privacy policy',
        'legitimate interest': 'legitimate interest', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
    'fa': {
        'cookies': 'کوکی', 'cookies1': 'cookies', 'cookie': 'cookie',
        'Cookie': 'Cookie', 'accept': 'accept', 'agree': 'موافقم',
        'confirm': 'تایید', 'consent': 'رضایت', 'allow': 'می پذیرم',
        'accept1': 'accept1', 'accept2': 'accept2', 'accept3': 'accept3',
        'reject': 'مخالف', 'disagree': 'disagree', 'decline': 'decline',
        'deny': 'deny', 'refuse': 'refuse', 'reject1': 'reject1',
        'reject2': 'reject2', 'setting': 'setting', 'manage': 'manage',
        'option': 'option', 'choice': 'choice', 'purpose': 'purpose',
        'preference': 'preference', 'customize': 'customize', 'configur': 'configur',
        'partner': 'partner', 'personalised': 'personalised', 'policy': 'policy',
        'privacy': 'privacy', 'privacy policy': 'privacy policy',
        'legitimate interest': 'legitimate interest', 'all': 'all',
        'login': 'login', 'einloggen': 'einloggen', 'selected': 'selected',
        'save': 'save', 'submit': 'submit', 'only': 'only', 'do not': 'do not',
        "don't": "don't", 'just': 'just', 'without': 'without',
    },
}

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
    "--disable-blink-features=AutomationControlled",  # hide automation from sites like YouTube
    "--window-size=1920,1040",  # total window fits on a 1920x1080 screen (1080 - 40px taskbar)
]

# Default WAVE stats returned when injection fails
_WAVE_EMPTY = {"error": None, "contrast": None, "alert": None,
               "feature": None, "structure": None, "aria": None}
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
                     "i accept", "ok, i understand", "allow all cookies"]

_BASE_REJECT_KW   = ["reject all", "reject", "decline", "refuse",
                     "no thanks", "deny", "do not accept", "disagree",
                     "only necessary", "only essential",
                     "necessary only", "essential only",
                     "necessary cookies only", "essential cookies only",
                     "without accepting", "without consenting"]

_BASE_SETTINGS_KW = ["settings", "manage preferences", "manage cookies",
                     "cookie settings", "cookie preferences", "privacy settings",
                     "preferences", "options", "manage",
                     "customize cookies", "customise cookies",
                     "customize", "customise"]

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


def _bc_kw_json(lang: str) -> dict[str, str]:
    """
    Build JSON-serialised keyword arrays for bannerclick-style detection/acceptance.
    Pulls vocabulary directly from bannerclick's dictWords module.
    """
    d  = _BC_WORDS.get(lang, _BC_WORDS['en'])
    en = _BC_WORDS['en']

    def collect(*keys: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for k in keys:
            for src in (d, en):
                v = src.get(k, '')
                if v and v not in seen:
                    seen.add(v)
                    result.append(v)
        return result

    # Cookie-detection words: bannerclick's find_els_with_cookie first tries
    # the 'cookies'/'cookies1' terms, then falls back to 'cookie' + 'privacy'/'consent'.
    cookie_words = collect('cookies', 'cookie', 'privacy', 'consent')

    # Accept-button words: bannerclick's accept_words list, translated.
    # Filters out placeholder keys whose English value is the bare key name.
    accept_kw = collect(*_BC_ACCEPT_WORDS)

    # Non-acceptable exclusion words: bannerclick's non_acceptable list.
    non_acc = collect(*_BC_NON_ACCEPTABLE)

    return {
        'BC_COOKIE_WORDS':   json.dumps(cookie_words),
        'BC_ACCEPT_WORDS':   json.dumps(accept_kw),
        'BC_NON_ACCEPTABLE': json.dumps(non_acc),
    }


def _bc_apply_kw(js_template: str, lang: str) -> str:
    """Substitute /*MARKER*/ sentinels in a bannerclick-style JS template."""
    for marker, value in _bc_kw_json(lang).items():
        js_template = js_template.replace(f'/*{marker}*/', value)
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
            cookie_accept_attempted INTEGER NOT NULL DEFAULT 0,
            manually_verified       INTEGER,
            false_positive          INTEGER,

            -- Cookie notice classification (paper taxonomy)
            cookie_position           TEXT,
            cookie_control_type       TEXT,
            cookie_emphasized_option  TEXT,
            cookie_has_reject         INTEGER NOT NULL DEFAULT 0,
            cookie_has_settings       INTEGER NOT NULL DEFAULT 0,
            cookie_pre_selected       INTEGER NOT NULL DEFAULT 0,

            -- Manual classification overrides (NULL = use auto value)
            manual_cookie_position           TEXT,
            manual_cookie_control_type       TEXT,
            manual_cookie_emphasized_option  TEXT,
            manual_cookie_has_reject         INTEGER,
            manual_cookie_has_settings       INTEGER,
            manual_cookie_pre_selected       INTEGER,
            cookie_bbox_x             REAL,
            cookie_bbox_y             REAL,
            cookie_bbox_width         REAL,
            cookie_bbox_height        REAL,

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

            -- Post-accept captures (NULL if no cookie notice found/accepted).
            -- Populated for accepted scans and for attempted-but-unconfirmed
            -- scans (post_screenshot_path at minimum) to allow manual verification.
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

        CREATE TABLE IF NOT EXISTS chrome_network_requests (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id       INTEGER NOT NULL REFERENCES chrome_scans(id),
            site_url      TEXT    NOT NULL,
            phase         TEXT    NOT NULL CHECK(phase IN ('pre','post')),
            request_url   TEXT    NOT NULL,
            method        TEXT,
            resource_type TEXT,
            status        INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_chrome_net_scan ON chrome_network_requests(scan_id);
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
        ("cookie_bbox_x",            "REAL"),
        ("false_positive",                   "INTEGER"),
        # Manual classification overrides
        ("manual_cookie_position",           "TEXT"),
        ("manual_cookie_control_type",       "TEXT"),
        ("manual_cookie_emphasized_option",  "TEXT"),
        ("manual_cookie_has_reject",         "INTEGER"),
        ("manual_cookie_has_settings",       "INTEGER"),
        ("manual_cookie_pre_selected",       "INTEGER"),
        ("cookie_bbox_y",            "REAL"),
        ("cookie_bbox_width",        "REAL"),
        ("cookie_bbox_height",       "REAL"),
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
        viewport={"width": 1920, "height": 969},
    )
    # Mask the webdriver flag so sites like YouTube don't detect automation
    # and serve a blank or degraded page.
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return context, profile_dir


# ── Cookie notice ──────────────────────────────────────────────────────────────

_DETECT_JS = """() => {
        // bannerclick detection approach:
        //   find_els_with_cookie  → XPath text-node search for cookie words
        //   find_fixed_ancestors  → walk up to fixed-position ancestor
        //   find_by_zindex        → fall back to z-index > 5 ancestor
        //   find_deepest_el       → last-resort deepest matching element
        // Filters: is_inside_viewport, has_enough_word (> 3 words), not is_signin_banner

        const COOKIE_WORDS = /*BC_COOKIE_WORDS*/;

        const vh = window.innerHeight || document.documentElement.clientHeight;
        const vw = window.innerWidth  || document.documentElement.clientWidth;

        // XPath query: find elements whose direct text nodes contain a cookie word
        // (mirrors bannerclick's find_els_with_cookie XPath strategy)
        const xpParts = COOKIE_WORDS.map(
            w => "contains(., '" + w.replace(/'/g, "&apos;") + "')"
        );
        const xp = '//*[text()[' + xpParts.join(' or ') + ']]';
        const snap = document.evaluate(
            xp, document.body, null,
            XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
        );
        const cookieEls = [];
        for (let i = 0; i < snap.snapshotLength; i++) {
            cookieEls.push(snap.snapshotItem(i));
        }
        if (!cookieEls.length) return false;

        const isVisible = el => {
            if (!el) return false;
            const s = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.display !== 'none' && s.visibility !== 'hidden' &&
                   parseFloat(s.opacity || '1') > 0 &&
                   r.width > 0 && r.height > 0;
        };

        const isInViewport = el => {
            const r = el.getBoundingClientRect();
            return r.top < vh && r.bottom > 0 && r.left < vw && r.right > 0;
        };

        // has_enough_word: more than 3 whitespace-separated tokens
        const hasEnoughWords = el => {
            const t = el.innerText || el.textContent || '';
            return (t.match(/\\w+/g) || []).length > 3;
        };

        // is_signin_banner: has an email-type input (bannerclick checks 'mail' in attrs)
        const isSignIn = el => {
            for (const inp of el.querySelectorAll('input')) {
                for (const attr of ['placeholder', 'name', 'type']) {
                    if ((inp.getAttribute(attr) || '').toLowerCase().includes('mail'))
                        return true;
                }
            }
            return false;
        };

        // find_fixed_or_sticky_ancestor: walk up looking for position fixed or sticky
        const findFixedOrStickyAncestor = el => {
            let cur = el;
            while (cur && cur !== document.documentElement) {
                const pos = window.getComputedStyle(cur).position;
                if (pos === 'fixed' || pos === 'sticky') return cur;
                cur = cur.parentElement;
            }
            return null;
        };

        // fine_ancestor_with_int_zindex: walk up looking for z-index > 5
        const findZIndexAncestor = el => {
            let cur = el;
            while (cur && cur !== document.documentElement) {
                const z = parseInt(window.getComputedStyle(cur).zIndex);
                if (!isNaN(z) && z > 5) return cur;
                cur = cur.parentElement;
            }
            return null;
        };

        // Pass 1 — fixed/sticky ancestors (bannerclick's find_fixed_ancestors)
        const fixedSeen = new Set();
        for (const el of cookieEls) {
            const fa = findFixedOrStickyAncestor(el);
            if (fa && !fixedSeen.has(fa)) {
                fixedSeen.add(fa);
                if (isVisible(fa) && isInViewport(fa) && hasEnoughWords(fa) && !isSignIn(fa))
                    return true;
            }
        }

        // Pass 2 — z-index ancestors (bannerclick's find_by_zindex)
        const zSeen = new Set();
        for (const el of cookieEls) {
            const za = findZIndexAncestor(el);
            if (za && !zSeen.has(za)) {
                zSeen.add(za);
                if (isVisible(za) && isInViewport(za) && hasEnoughWords(za) && !isSignIn(za))
                    return true;
            }
        }

        // Pass 3 — walk up from the deepest cookie-text element until finding a
        // container with enough content (bannerclick's find_deepest_el approach,
        // extended to keep climbing rather than stopping at the immediate parent).
        // A size cap (<50% viewport area) prevents accidentally matching <body>.
        const deepest = cookieEls.reduce((a, b) => {
            let da = 0, cur = a; while (cur) { da++; cur = cur.parentElement; }
            let db = 0; cur = b; while (cur) { db++; cur = cur.parentElement; }
            return da >= db ? a : b;
        });
        let cur3 = deepest.parentElement || deepest;
        while (cur3 && cur3 !== document.documentElement) {
            if (isVisible(cur3) && isInViewport(cur3) && hasEnoughWords(cur3) && !isSignIn(cur3)) {
                const r3 = cur3.getBoundingClientRect();
                if ((r3.width * r3.height) < (vw * vh * 0.5)) return true;
            }
            cur3 = cur3.parentElement;
        }

        // Pass 4 — ARIA dialog / modal elements
        // Catches sites like Facebook/Instagram that render cookie consent as a
        // React portal dialog with role="dialog"/aria-modal="true" and
        // position: absolute (no fixed ancestor, z-index on backdrop not dialog).
        const DIALOG_SEL = '[role="dialog"], [role="alertdialog"], [aria-modal="true"], dialog';
        for (const el of document.querySelectorAll(DIALOG_SEL)) {
            if (!isVisible(el) || !isInViewport(el)) continue;
            const t = (el.innerText || el.textContent || '').toLowerCase();
            if (!COOKIE_WORDS.some(w => t.includes(w.toLowerCase()))) continue;
            if (hasEnoughWords(el) && !isSignIn(el)) return true;
        }

        // Pass 5 — class/id keyword scan
        // Catches static-positioned banners (e.g. live.com bottom bar) and sites
        // like X.com that use custom component attributes rather than ARIA roles.
        const KW_SEL = [
            '[class*="cookie"]', '[id*="cookie"]',
            '[class*="consent"]', '[id*="consent"]',
            '[class*="gdpr"]',   '[id*="gdpr"]',
        ].join(', ');
        for (const el of document.querySelectorAll(KW_SEL)) {
            if (!isVisible(el) || !isInViewport(el)) continue;
            const t = (el.innerText || el.textContent || '').toLowerCase();
            if (!COOKIE_WORDS.some(w => t.includes(w.toLowerCase()))) continue;
            if (hasEnoughWords(el) && !isSignIn(el)) return true;
        }

        // Pass 6 — shadow DOM traversal
        // CMPs like OneTrust (newer versions used by Cloudflare) render their
        // banner inside a shadow root, making XPath and regular querySelector
        // searches blind to it.
        const BTN_SEL_D = 'button, a, [role="button"], input[type="submit"], input[type="button"]';
        function shadowHasCookieBanner(root) {
            const t = (root.textContent || '').toLowerCase();
            if (!COOKIE_WORDS.some(w => t.includes(w))) return false;
            for (const btn of root.querySelectorAll(BTN_SEL_D)) {
                const bt = (btn.innerText || btn.textContent
                            || btn.getAttribute('aria-label') || '').trim().toLowerCase();
                if (!bt) continue;
                const r = btn.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) return true;
            }
            return false;
        }
        for (const host of document.querySelectorAll('*')) {
            if (!host.shadowRoot) continue;
            if (shadowHasCookieBanner(host.shadowRoot)) return true;
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
    such as TrustArc). Uses bannerclick's detection vocabulary and algorithm.
    """
    js = _bc_apply_kw(_DETECT_JS, lang)
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
        // bannerclick acceptance approach:
        //   Re-locate banner using same XPath + fixed-ancestor logic as detection.
        //   extract_btns(element, choice=1): find_btns_by_list with accept_words,
        //     then remove_els_with_words with non_acceptable.
        //   click_func: prioritise <button> tags (find_tag_buttons), then others.

        const COOKIE_WORDS   = /*BC_COOKIE_WORDS*/;
        const ACCEPT_WORDS   = /*BC_ACCEPT_WORDS*/;
        const NON_ACCEPTABLE = /*BC_NON_ACCEPTABLE*/;

        const vh = window.innerHeight || document.documentElement.clientHeight;
        const vw = window.innerWidth  || document.documentElement.clientWidth;

        const xpParts = COOKIE_WORDS.map(
            w => "contains(., '" + w.replace(/'/g, "&apos;") + "')"
        );
        const xp = '//*[text()[' + xpParts.join(' or ') + ']]';
        const snap = document.evaluate(
            xp, document.body, null,
            XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
        );
        const cookieEls = [];
        for (let i = 0; i < snap.snapshotLength; i++) {
            cookieEls.push(snap.snapshotItem(i));
        }
        if (!cookieEls.length) return false;

        const isVisible = el => {
            if (!el) return false;
            const s = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.display !== 'none' && s.visibility !== 'hidden' &&
                   parseFloat(s.opacity || '1') > 0 &&
                   r.width > 0 && r.height > 0;
        };
        const isInViewport = el => {
            const r = el.getBoundingClientRect();
            return r.top < vh && r.bottom > 0 && r.left < vw && r.right > 0;
        };
        const findFixedOrStickyAncestor = el => {
            let cur = el;
            while (cur && cur !== document.documentElement) {
                const pos = window.getComputedStyle(cur).position;
                if (pos === 'fixed' || pos === 'sticky') return cur;
                cur = cur.parentElement;
            }
            return null;
        };
        const findZIndexAncestor = el => {
            let cur = el;
            while (cur && cur !== document.documentElement) {
                const z = parseInt(window.getComputedStyle(cur).zIndex);
                if (!isNaN(z) && z > 5) return cur;
                cur = cur.parentElement;
            }
            return null;
        };

        // Locate the banner (same strategy as detection)
        let banner = null;
        for (const el of cookieEls) {
            const fa = findFixedOrStickyAncestor(el);
            if (fa && isVisible(fa) && isInViewport(fa)) { banner = fa; break; }
        }
        if (!banner) {
            for (const el of cookieEls) {
                const za = findZIndexAncestor(el);
                if (za && isVisible(za) && isInViewport(za)) { banner = za; break; }
            }
        }
        if (!banner && cookieEls.length) {
            const deepest = cookieEls.reduce((a, b) => {
                let da = 0, cur = a; while (cur) { da++; cur = cur.parentElement; }
                let db = 0; cur = b; while (cur) { db++; cur = cur.parentElement; }
                return da >= db ? a : b;
            });
            // Walk up from deepest element, stop at first candidate that's small enough
            let cur3 = deepest.parentElement || deepest;
            while (cur3 && cur3 !== document.documentElement) {
                if (isVisible(cur3) && isInViewport(cur3)) {
                    const r3 = cur3.getBoundingClientRect();
                    if ((r3.width * r3.height) < (vw * vh * 0.5)) {
                        banner = cur3;
                        break;
                    }
                }
                cur3 = cur3.parentElement;
            }
        }
        // Pass 4 — ARIA dialog fallback (Meta-style portals)
        if (!banner) {
            const DIALOG_SEL = '[role="dialog"], [role="alertdialog"], [aria-modal="true"], dialog';
            for (const el of document.querySelectorAll(DIALOG_SEL)) {
                if (!isVisible(el) || !isInViewport(el)) continue;
                const t = (el.innerText || el.textContent || '').toLowerCase();
                if (COOKIE_WORDS.some(w => t.includes(w.toLowerCase()))) {
                    banner = el;
                    break;
                }
            }
        }
        // Pass 5 — class/id keyword fallback (live.com bottom bar, X.com custom components)
        if (!banner) {
            const KW_SEL = [
                '[class*="cookie"]', '[id*="cookie"]',
                '[class*="consent"]', '[id*="consent"]',
                '[class*="gdpr"]',   '[id*="gdpr"]',
            ].join(', ');
            for (const el of document.querySelectorAll(KW_SEL)) {
                if (!isVisible(el) || !isInViewport(el)) continue;
                const t = (el.innerText || el.textContent || '').toLowerCase();
                if (COOKIE_WORDS.some(w => t.includes(w.toLowerCase()))) {
                    banner = el;
                    break;
                }
            }
        }
        // Pass 6 — shadow DOM traversal (OneTrust and similar CMPs on e.g. Cloudflare)
        // Returns the shadow root itself as the banner context so collectBtns can
        // search inside it.
        let shadowBanner = null;
        if (!banner) {
            const BTN_SEL_S = 'button, a, [role="button"], input[type="submit"], input[type="button"]';
            for (const host of document.querySelectorAll('*')) {
                if (!host.shadowRoot) continue;
                const sr = host.shadowRoot;
                const t = (sr.textContent || '').toLowerCase();
                if (!COOKIE_WORDS.some(w => t.includes(w))) continue;
                // Check there is a visible accept candidate inside the shadow root
                for (const btn of sr.querySelectorAll(BTN_SEL_S)) {
                    const bt = (btn.innerText || btn.textContent
                                || btn.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (!bt) continue;
                    const r = btn.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) { shadowBanner = sr; break; }
                }
                if (shadowBanner) break;
            }
        }
        if (!banner && !shadowBanner) return false;

        // extract_btns(banner, choice=1): find_btns_by_list(accept_words) then
        // remove_els_with_words(non_acceptable).
        // click_func: try <button> tags first (find_tag_buttons), then everything else.
        // collectBtns recurses into shadow roots so CMPs like Transcend are covered.
        const BTN_SEL = 'button, a, [role="button"], input[type="submit"], input[type="button"]';
        const collectBtns = root => {
            const found = Array.from(root.querySelectorAll(BTN_SEL));
            if (root.shadowRoot) found.push(...collectBtns(root.shadowRoot));
            for (const el of root.querySelectorAll('*')) {
                if (el.shadowRoot) found.push(...collectBtns(el.shadowRoot));
            }
            return found;
        };
        const searchRoot = banner || shadowBanner;
        const allBtns = collectBtns(searchRoot);
        const tagBtns   = allBtns.filter(b => b.tagName === 'BUTTON');
        const otherBtns = allBtns.filter(b => b.tagName !== 'BUTTON');

        for (const btn of [...tagBtns, ...otherBtns]) {
            if (!isVisible(btn)) continue;
            const text = (btn.innerText || btn.textContent || btn.getAttribute('aria-label') || '')
                         .trim().toLowerCase();
            if (!text) continue;
            const hasAccept = ACCEPT_WORDS.some(w => text.includes(w.toLowerCase()));
            const hasNonAcc = NON_ACCEPTABLE.some(w => text.includes(w.toLowerCase()));
            if (hasAccept && !hasNonAcc) {
                btn.click();
                return true;
            }
        }
        return false;
    }"""


async def accept_cookie_notice(ctx: Page | Frame, lang: str = "en") -> bool:
    """
    Attempts to find and click a cookie consent accept button.
    Returns True if a button was found and clicked.
    Uses bannerclick's extract_btns / click_func acceptance strategy.
    Accepts a Page or Frame context (from detect_cookie_notice).
    """
    return bool(await ctx.evaluate(_bc_apply_kw(_ACCEPT_JS, lang)))


# ── Native agree-button click (used just before accepting) ────────────────────
# JS marks the agree button with a data attribute; Python then retrieves the
# ElementHandle and calls .click() on it.  ElementHandle.click() handles
# cross-origin iframe offsets, scrolling, and produces isTrusted=true events
# without any manual coordinate arithmetic.

_MARK_AGREE_BTN_JS = """(bbox) => {
    // bbox: {x, y, w, h} in the context's coordinate space, or null.
    // When provided, only buttons whose centre lies within that box are
    // considered — prevents accidentally clicking agree-like buttons
    // elsewhere on the page (sign-in forms, newsletter opt-ins, etc.).
    const AGREE_KW = /*AGREE_KW*/;

    const vh = window.innerHeight || document.documentElement.clientHeight;

    const isVisible = el => {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden'
            && parseFloat(s.opacity || '1') > 0
            && !(r.width === 0 && r.height === 0);
    };

    const txt = el =>
        (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
        .trim().replace(/\\s+/g, ' ').toLowerCase();

    const inBbox = r => {
        if (!bbox) return true;
        const cx = r.left + r.width  / 2;
        const cy = r.top  + r.height / 2;
        return cx >= bbox.x && cx <= bbox.x + bbox.w
            && cy >= bbox.y && cy <= bbox.y + bbox.h;
    };

    const BTN_SEL = 'button, a, [role="button"], input[type="submit"], input[type="button"]';
    const collectBtns = root => {
        const found = Array.from(root.querySelectorAll(BTN_SEL));
        if (root.shadowRoot) found.push(...collectBtns(root.shadowRoot));
        for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) found.push(...collectBtns(el.shadowRoot));
        }
        return found;
    };

    const vw = window.innerWidth || document.documentElement.clientWidth;

    // Fully in viewport: both dimensions non-zero and on-screen
    const isInViewport = r =>
        r.width > 0 && r.height > 0
        && r.top < vh && r.bottom > 0
        && r.left < vw && r.right > 0;

    // Partially rendered: at least one dimension non-zero, centre in bbox.
    // Handles buttons whose CSS transition hasn't completed yet (height: 0).
    const isPartiallyRendered = r =>
        (r.width > 0 || r.height > 0)
        && r.top < vh && r.bottom >= 0
        && r.left < vw && r.right >= 0;

    const scrollableAncestorInBbox = el => {
        let cur = el.parentElement;
        while (cur && cur !== document.body) {
            const s = window.getComputedStyle(cur);
            if (/auto|scroll/.test(s.overflow + s.overflowY)) {
                const ar = cur.getBoundingClientRect();
                const cx = ar.left + ar.width  / 2;
                const cy = ar.top  + ar.height / 2;
                return cx >= bbox.x && cx <= bbox.x + bbox.w
                    && cy >= bbox.y && cy <= bbox.y + bbox.h;
            }
            cur = cur.parentElement;
        }
        return false;
    };

    const allBtns = collectBtns(document.body);
    // Sort: <button> tags first — they are the most reliable accept targets
    // and least likely to be incidental agree-keyword matches like policy links.
    const candidates = [
        ...allBtns.filter(b => b.tagName === 'BUTTON'),
        ...allBtns.filter(b => b.tagName !== 'BUTTON'),
    ].filter(btn => {
        if (!isVisible(btn)) return false;
        if (!AGREE_KW.some(k => txt(btn).includes(k))) return false;
        // Skip real navigation links
        if (btn.tagName === 'A') {
            const href = (btn.getAttribute('href') || '').trim();
            if (href && href !== '#' && !href.startsWith('javascript:')) return false;
        }
        return true;
    });

    // Pass 1: fully visible and centre in bbox — no scrolling needed
    for (const btn of candidates) {
        const r = btn.getBoundingClientRect();
        if (isInViewport(r) && inBbox(r)) {
            btn.setAttribute('data-pw-agree', '1');
            return 'pass1';
        }
    }

    // Pass 2: off-screen but inside a scroll container that overlaps the bbox
    for (const btn of candidates) {
        const r0 = btn.getBoundingClientRect();
        if (isInViewport(r0)) continue;
        if (!bbox) continue;
        if (!scrollableAncestorInBbox(btn)) continue;
        btn.scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'instant'});
        const r = btn.getBoundingClientRect();
        if (isInViewport(r) && inBbox(r)) {
            btn.setAttribute('data-pw-agree', '1');
            return 'pass2';
        }
    }

    // Pass 3: button within the bbox but outside the visible viewport.
    // Covers two cases:
    //   a) window is taller than the physical screen — button is "in the window"
    //      but getBoundingClientRect().top >= window.innerHeight
    //   b) button has one zero dimension due to a CSS transition not yet complete
    // scrollIntoView brings it into the visible area before clicking.
    for (const btn of candidates) {
        const r = btn.getBoundingClientRect();
        if (isInViewport(r)) continue; // already tried in pass 1
        if (!inBbox(r)) continue;
        btn.scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'instant'});
        const r2 = btn.getBoundingClientRect();
        if (inBbox(r2)) {
            btn.setAttribute('data-pw-agree', '1');
            return 'pass3';
        }
    }

    // Pass 4: semantic cookie-container scan.
    // Same container-finding logic as the classify step: any visible div/section/etc.
    // that contains cookie-related text AND a standard action button (settings, manage,
    // etc.). Then searches inside that container for any child with agree keywords +
    // cursor:pointer — catches CMPs that render Accept as a bare <div>/<span>.
    const COOKIE_WORDS = /*COOKIE_WORDS*/;
    const ACTION_WORDS = /*ACTION_WORDS*/;
    const hasCookieText = el => {
        const t = (el.innerText || el.textContent || '').toLowerCase();
        return COOKIE_WORDS.some(w => t.includes(w));
    };
    const hasActionBtn = ctr => {
        for (const b of ctr.querySelectorAll(BTN_SEL)) {
            if (!isVisible(b)) continue;
            const t = txt(b);
            if (ACTION_WORDS.some(w => t.includes(w))) return true;
        }
        return false;
    };
    const CONTAINER_SEL = 'div,section,aside,form,dialog,nav,header,footer,main,article,'
        + '[role="dialog"],[role="alertdialog"],[aria-modal="true"],[role="banner"]';
    for (const ctr of document.querySelectorAll(CONTAINER_SEL)) {
        const rc = ctr.getBoundingClientRect();
        if (rc.width < 50 || rc.height < 20) continue;
        if (!isVisible(ctr)) continue;
        if (!hasCookieText(ctr)) continue;
        if (!hasActionBtn(ctr)) continue;
        // Container looks like a cookie notice — find agree element inside it.
        for (const el of ctr.querySelectorAll('*')) {
            const t = txt(el);
            if (!AGREE_KW.some(k => t.includes(k))) continue;
            const s = window.getComputedStyle(el);
            if (s.cursor !== 'pointer') continue;
            const r = el.getBoundingClientRect();
            if (r.width < 20 || r.height < 10) continue;
            if (!inBbox(r)) continue;
            if (isInViewport(r)) {
                el.setAttribute('data-pw-agree', '1');
                return 'pass4';
            }
            el.scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'instant'});
            const r2 = el.getBoundingClientRect();
            if (isInViewport(r2)) {
                el.setAttribute('data-pw-agree', '1');
                return 'pass4-scroll';
            }
        }
    }

    return false;
}"""

_UNMARK_AGREE_BTN_JS = (
    "() => { const el = document.querySelector('[data-pw-agree]'); "
    "if (el) el.removeAttribute('data-pw-agree'); }"
)

# Returned when DEBUG=True and no button was found — shows every keyword-matching
# candidate and the reason(s) it was skipped.
_DEBUG_WHY_JS = """(bbox) => {
    const AGREE_KW = /*AGREE_KW*/;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const vw = window.innerWidth  || document.documentElement.clientWidth;

    const isVisible = el => {
        if (!el) return [];
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        const reasons = [];
        if (s.display === 'none')       reasons.push('display:none');
        if (s.visibility === 'hidden')  reasons.push('visibility:hidden');
        if (parseFloat(s.opacity||'1') <= 0) reasons.push('opacity:0');
        if (r.width === 0 && r.height === 0) reasons.push('zero-size');
        return reasons;
    };

    const txt = el =>
        (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
        .trim().replace(/\\s+/g, ' ').toLowerCase();

    const BTN_SEL = 'button, a, [role="button"], input[type="submit"], input[type="button"]';
    const collectBtns = root => {
        const found = Array.from(root.querySelectorAll(BTN_SEL));
        if (root.shadowRoot) found.push(...collectBtns(root.shadowRoot));
        for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) found.push(...collectBtns(el.shadowRoot));
        }
        return found;
    };

    const allBtns = collectBtns(document.body);
    const results = [];

    for (const btn of allBtns) {
        const t = txt(btn);
        if (!AGREE_KW.some(k => t.includes(k))) continue;

        const r    = btn.getBoundingClientRect();
        const reasons = [...isVisible(btn)];

        const isNavLink = btn.tagName === 'A' && (() => {
            const href = (btn.getAttribute('href') || '').trim();
            return href && href !== '#' && !href.startsWith('javascript:');
        })();
        if (isNavLink) reasons.push('nav-link');

        const inVp = r.width > 0 && r.height > 0
                  && r.top < vh && r.bottom > 0
                  && r.left < vw && r.right > 0;
        if (!inVp) reasons.push(
            `off-viewport(top=${Math.round(r.top)} bot=${Math.round(r.bottom)} vh=${Math.round(vh)})`
        );

        if (bbox) {
            const cx = r.left + r.width  / 2;
            const cy = r.top  + r.height / 2;
            const inBox = cx >= bbox.x && cx <= bbox.x + bbox.w
                       && cy >= bbox.y && cy <= bbox.y + bbox.h;
            if (!inBox) reasons.push(
                `off-bbox(cx=${Math.round(cx)} cy=${Math.round(cy)} ` +
                `bbox=${Math.round(bbox.x)},${Math.round(bbox.y)}` +
                `+${Math.round(bbox.w)}x${Math.round(bbox.h)})`
            );
        }

        results.push({
            tag:      btn.tagName,
            text:     t.slice(0, 60),
            x: Math.round(r.left), y: Math.round(r.top),
            w: Math.round(r.width), h: Math.round(r.height),
            rejected: reasons.length > 0,
            reasons,
        });
        if (results.length >= 25) break;
    }

    return {vh: Math.round(vh), vw: Math.round(vw), candidates: results};
}"""


async def native_click_agree_button(
    ctx: Page | Frame,
    lang: str = "en",
    bbox: dict | None = None,
) -> bool:
    """
    Marks the agree button via JS (scoped to bbox when provided) then clicks
    it through Playwright's ElementHandle.click().

    If the button is not found in ctx, falls back to searching visible same-
    origin iframes on the page — covers CMP overlays like OneTrust/digicert
    that render their buttons inside an embedded iframe.
    """
    async def _try_ctx(target: Page | Frame, target_bbox: dict | None) -> bool:
        try:
            found = await target.evaluate(_apply_kw(_MARK_AGREE_BTN_JS, lang), target_bbox)
            if not found:
                if DEBUG:
                    try:
                        dbg = await target.evaluate(
                            _apply_kw(_DEBUG_WHY_JS, lang), target_bbox
                        )
                        vh, vw = dbg.get("vh"), dbg.get("vw")
                        cands  = dbg.get("candidates", [])
                        ctx_label = "main page" if isinstance(target, Page) else "iframe"
                        print(f"       [DEBUG] No button found in {ctx_label} "
                              f"(viewport {vw}x{vh}, bbox={target_bbox})")
                        if not cands:
                            print("       [DEBUG]   No keyword-matching candidates found at all")
                            # Show ALL BTN_SEL elements so we can see what text they have
                            try:
                                all_btns = await target.evaluate("""() => {
                                    const SEL = 'button,a,[role="button"],input[type="submit"],input[type="button"]';
                                    const collectAll = root => {
                                        const f = Array.from(root.querySelectorAll(SEL));
                                        for (const el of root.querySelectorAll('*'))
                                            if (el.shadowRoot) f.push(...collectAll(el.shadowRoot));
                                        return f;
                                    };
                                    return collectAll(document.body).slice(0,20).map(b => {
                                        const r = b.getBoundingClientRect();
                                        return {tag: b.tagName,
                                                text: (b.innerText||b.textContent||b.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ').slice(0,60),
                                                x: Math.round(r.left), y: Math.round(r.top),
                                                w: Math.round(r.width), h: Math.round(r.height)};
                                    });
                                }""")
                                if all_btns:
                                    print("       [DEBUG]   All BTN_SEL elements (first 20, no keyword filter):")
                                    for b in all_btns:
                                        print(f"       [DEBUG]     <{b['tag']}> at ({b['x']},{b['y']}) {b['w']}x{b['h']} '{b['text']}'")
                                else:
                                    print("       [DEBUG]   No BTN_SEL elements in DOM at all")
                            except Exception:
                                pass
                            # Pass 4 diagnostics: show CMP containers and why
                            # agree-keyword elements inside them were rejected.
                            try:
                                p4_diag = await target.evaluate(
                                    _apply_kw("""() => {
                                    const AGREE_KW = /*AGREE_KW*/;
                                    const txt = el =>
                                        (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                                        .trim().replace(/\\s+/g,' ').toLowerCase();
                                    const isVisible = el => {
                                        if (!el) return false;
                                        const s = window.getComputedStyle(el);
                                        const r = el.getBoundingClientRect();
                                        return s.display !== 'none' && s.visibility !== 'hidden'
                                            && parseFloat(s.opacity||'1') > 0
                                            && !(r.width === 0 && r.height === 0);
                                    };
                                    const COOKIE_WORDS = /*COOKIE_WORDS*/;
                                    const ACTION_WORDS = /*ACTION_WORDS*/;
                                    const BTN_SEL = 'button,a,[role="button"],input[type="submit"],input[type="button"]';
                                    const hasCookieText = el => {
                                        const t = (el.innerText || el.textContent || '').toLowerCase();
                                        return COOKIE_WORDS.some(w => t.includes(w));
                                    };
                                    const hasActionBtn = ctr => {
                                        for (const b of ctr.querySelectorAll(BTN_SEL)) {
                                            if (!isVisible(b)) continue;
                                            const t = (b.innerText||b.textContent||b.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ').toLowerCase();
                                            if (ACTION_WORDS.some(w => t.includes(w))) return true;
                                        }
                                        return false;
                                    };
                                    const CONTAINER_SEL = 'div,section,aside,form,dialog,nav,header,footer,main,article,'
                                        + '[role="dialog"],[role="alertdialog"],[aria-modal="true"],[role="banner"]';
                                    const ctrs = [];
                                    for (const ctr of document.querySelectorAll(CONTAINER_SEL)) {
                                        const rc = ctr.getBoundingClientRect();
                                        if (rc.width < 50 || rc.height < 20) continue;
                                        if (!isVisible(ctr)) continue;
                                        if (!hasCookieText(ctr)) continue;
                                        if (!hasActionBtn(ctr)) continue;
                                        ctrs.push(ctr);
                                    }
                                    const report = [];
                                    for (const ctr of ctrs.slice(0, 3)) {
                                        const rc = ctr.getBoundingClientRect();
                                        const ctrInfo = {
                                            tag: ctr.tagName,
                                            id: ctr.id || '',
                                            cls: (ctr.className || '').toString().slice(0, 80),
                                            x: Math.round(rc.left), y: Math.round(rc.top),
                                            w: Math.round(rc.width), h: Math.round(rc.height),
                                            children: [],
                                        };
                                        for (const el of Array.from(ctr.querySelectorAll('*')).slice(0,200)) {
                                            const t = txt(el);
                                            if (!AGREE_KW.some(k => t.includes(k))) continue;
                                            const s = window.getComputedStyle(el);
                                            const r = el.getBoundingClientRect();
                                            ctrInfo.children.push({
                                                tag: el.tagName,
                                                text: t.slice(0, 60),
                                                cursor: s.cursor,
                                                x: Math.round(r.left), y: Math.round(r.top),
                                                w: Math.round(r.width), h: Math.round(r.height),
                                            });
                                            if (ctrInfo.children.length >= 5) break;
                                        }
                                        report.push(ctrInfo);
                                    }
                                    return {total_ctrs: ctrs.length, visible: report};
                                }""", lang)
                                )
                                total = p4_diag.get("total_ctrs", 0)
                                visible = p4_diag.get("visible", [])
                                print(f"       [DEBUG]   Pass4: {total} semantic cookie container(s), "
                                      f"{len(visible)} shown")
                                for ci in visible:
                                    print(f"       [DEBUG]     cookie ctr <{ci['tag']}> "
                                          f"#{ci['id']} .{ci['cls'][:40]} "
                                          f"at ({ci['x']},{ci['y']}) {ci['w']}x{ci['h']}")
                                    for ch in ci.get("children", []):
                                        print(f"       [DEBUG]       child <{ch['tag']}> "
                                              f"'{ch['text']}' cursor={ch['cursor']} "
                                              f"at ({ch['x']},{ch['y']}) {ch['w']}x{ch['h']}")
                            except Exception:
                                pass
                        for c in cands:
                            status = "REJECTED" if c["rejected"] else "passed-filters"
                            reasons = ", ".join(c["reasons"]) if c["reasons"] else "—"
                            print(f"       [DEBUG]   {status} <{c['tag']}> "
                                  f"at ({c['x']},{c['y']}) {c['w']}x{c['h']} "
                                  f"'{c['text']}' | {reasons}")
                    except Exception as dbg_err:
                        print(f"       [DEBUG] Debug JS failed: {dbg_err}")
                return False
            if DEBUG:
                print(f"       [DEBUG] Button marked via {found}")
            # Log what element was selected to help diagnose wrong-click issues
            try:
                info = await target.evaluate(
                    "() => { const el = document.querySelector('[data-pw-agree=\"1\"]');"
                    " if (!el) return null;"
                    " const r = el.getBoundingClientRect();"
                    " return {tag: el.tagName, text: (el.innerText||el.textContent||'').trim().slice(0,60),"
                    "  x: Math.round(r.left), y: Math.round(r.top),"
                    "  w: Math.round(r.width), h: Math.round(r.height)}; }"
                )
                if info:
                    print(f"       [*] Click target: <{info['tag']}> "
                          f"at ({info['x']},{info['y']}) {info['w']}x{info['h']} "
                          f"'{info['text']}'")
            except Exception:
                pass
            handle = await target.query_selector('[data-pw-agree="1"]')
            if handle is None:
                return False
            await handle.scroll_into_view_if_needed()
            await handle.click(timeout=5000)
            return True
        except Exception as e:
            print(f"       [!] Element-handle click failed: {e}")
            return False
        finally:
            try:
                await target.evaluate(_UNMARK_AGREE_BTN_JS)
            except Exception:
                pass

    # Primary: try the main context up to 3 times with short delays.
    # Facebook and similar CMPs render the accept button slightly after
    # the classification JS runs — a brief wait resolves the race.
    for attempt in range(3):
        if await _try_ctx(ctx, bbox):
            return True
        if attempt < 2:
            if DEBUG:
                print(f"       [DEBUG] Retry {attempt + 1}/2 in 800 ms...")
            await asyncio.sleep(0.8)

    if not isinstance(ctx, Page):
        return False

    # Closed-shadow-DOM pass: Playwright's CSS locator only pierces OPEN shadow
    # roots; the Transcend CMP on digicert.com uses
    # <template shadowrootmode="closed"> which is invisible to both JS and CSS
    # selectors.  get_by_role() queries the computed accessibility tree, which
    # Chrome exposes regardless of shadow-root mode.
    agree_kw = _merge_kw(_BASE_AGREE_KW, lang, _LANG_AGREE_KW)
    kw_pattern = re.compile(
        "|".join(re.escape(k) for k in agree_kw), re.IGNORECASE
    )

    async def _try_aria(target: Page) -> bool:
        """Try clicking an agree button via ARIA role (pierces closed shadow DOM)."""
        try:
            loc = target.get_by_role("button", name=kw_pattern)
            n = await loc.count()
            if DEBUG:
                print(f"       [DEBUG] ARIA locator: {n} agree-keyword button(s)")
            if n == 0:
                return False
            # Prefer button whose centre is inside the cookie notice bbox.
            if bbox:
                for i in range(n):
                    btn = loc.nth(i)
                    try:
                        btn_box = await btn.bounding_box()
                        if btn_box:
                            cx = btn_box["x"] + btn_box["width"]  / 2
                            cy = btn_box["y"] + btn_box["height"] / 2
                            if (bbox["x"] <= cx <= bbox["x"] + bbox["w"] and
                                    bbox["y"] <= cy <= bbox["y"] + bbox["h"]):
                                if DEBUG:
                                    print(f"       [DEBUG] ARIA click: button {i} "
                                          f"at ({btn_box['x']:.0f},{btn_box['y']:.0f})")
                                await btn.click(timeout=5000)
                                return True
                    except Exception:
                        continue
            # Fallback: click the first found.
            if DEBUG:
                print("       [DEBUG] ARIA click: first button (no bbox match)")
            await loc.first.click(timeout=5000)
            return True
        except Exception as e:
            if DEBUG:
                print(f"       [DEBUG] ARIA locator failed: {e}")
            return False

    if await _try_aria(ctx):
        return True

    all_frames = [f for f in ctx.frames if f != ctx.main_frame]
    if DEBUG:
        print(f"       [DEBUG] {len(all_frames)} sub-frame(s) to check")

    # Same-origin iframes (JS evaluate works).
    same_origin = []
    cross_origin = []
    for frame in all_frames:
        try:
            await frame.evaluate("1")
            same_origin.append(frame)
        except Exception:
            cross_origin.append(frame)

    if DEBUG:
        print(f"       [DEBUG] Same-origin: {len(same_origin)}, cross-origin: {len(cross_origin)}")

    for frame in same_origin:
        if DEBUG:
            print(f"       [DEBUG] Checking same-origin iframe: {frame.url[:100]}")
        if await _try_ctx(frame, None):
            print("       [*] Accept button found in same-origin iframe")
            return True

    # Cross-origin iframes — JS evaluate is blocked by the browser, but
    # Playwright's CDP-backed locator API can still interact with them.
    _btn_sel = "button, [role='button'], input[type='submit'], input[type='button']"
    for frame in cross_origin:
        if DEBUG:
            print(f"       [DEBUG] Checking cross-origin iframe: {frame.url[:100]}")
        try:
            loc = frame.locator(_btn_sel).filter(has_text=kw_pattern)
            n = await loc.count()
            if DEBUG:
                print(f"       [DEBUG]   Locator found {n} agree-keyword button(s)")
            if n == 0:
                continue
            await loc.first.click(timeout=5000)
            print("       [*] Accept button found in cross-origin iframe")
            return True
        except Exception as e:
            if DEBUG:
                print(f"       [DEBUG]   Cross-origin iframe click failed: {e}")

    return False


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
                      + '[aria-modal="true"], [role="banner"], [role="main"]';
            const addCandidate = el => {
                if (!isVisible(el)) return;
                if (!hasCookieText(el)) return;
                if (!hasActionButton(el)) return;
                if (!isFixedOrSticky(el) && !isInViewport(el)) return;
                candidates.push(el);
            };
            const candidates = [];
            for (const el of document.querySelectorAll(SEL)) addCandidate(el);
            // Fallback: class/id keyword selectors (catches banners on X.com,
            // Cloudflare, etc. that use custom element types or non-ARIA attributes)
            if (candidates.length === 0) {
                const KW_SEL = [
                    '[class*="cookie"]', '[id*="cookie"]',
                    '[class*="consent"]', '[id*="consent"]',
                    '[class*="gdpr"]',   '[id*="gdpr"]',
                ].join(', ');
                for (const el of document.querySelectorAll(KW_SEL)) addCandidate(el);
            }
            if (candidates.length === 0) {
                return {
                    position: 'none', control_type: 'none',
                    emphasized_option: 'none',
                    has_reject: false, has_settings: false,
                };
            }
            // Drop candidates that cover more than 60 % of the viewport — avoids
            // accidentally matching <main>, <body> wrappers, or full-screen overlays
            // that merely contain a notice somewhere inside them.
            const filtered = candidates.filter(el => {
                const r = el.getBoundingClientRect();
                return (r.width * r.height) < (vw * vh * 0.60);
            });
            const pool = filtered.length > 0 ? filtered : candidates;

            // Sort: fixed/sticky first, then by z-index descending, then by area
            // descending (largest qualifying container = the full notice panel, not
            // just its button row).
            pool.sort((a, b) => {
                const aFixed = isFixedOrSticky(a) ? 1 : 0;
                const bFixed = isFixedOrSticky(b) ? 1 : 0;
                if (aFixed !== bFixed) return bFixed - aFixed;
                const aZ = getMaxZIndex(a), bZ = getMaxZIndex(b);
                if (aZ !== bZ) return bZ - aZ;
                const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                return (rb.width * rb.height) - (ra.width * ra.height);
            });
            const container = pool[0];

            // ── 2. Position ───────────────────────────────────────────────────
            const rect = container.getBoundingClientRect();
            // Clip to the visible viewport before computing position — containers
            // that extend beyond the fold (e.g. tall Meta modals) should be
            // classified based on their visible portion, not their full rendered size.
            const visLeft   = Math.max(0, rect.left);
            const visTop    = Math.max(0, rect.top);
            const visRight  = Math.min(rect.right,  vw);
            const visBottom = Math.min(rect.bottom, vh);
            const coverage = ((visRight - visLeft) * (visBottom - visTop)) / (vw * vh);
            const midX     = (visLeft + visRight)  / 2;
            const midY     = (visTop  + visBottom) / 2;
            const relX     = midX / vw;
            const relY     = midY / vh;

            const isSmall   = coverage < 0.18;
            const inCornerH = relX < 0.3 || relX > 0.7;
            const inCornerV = relY < 0.25 || relY > 0.75;
            const isTall    = (visBottom - visTop) > (visRight - visLeft) * 1.5;

            let position;
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

            // ── 3. Button/link inventory ──────────────────────────────────────
            // Use isButtonVisible (no min-width constraint) so narrow buttons like
            // a compact "Reject" pill are not filtered out.
            const interactive = collectInteractive(container).filter(isButtonVisible);

            // Normalise internal whitespace so button labels split across
            // DOM lines (e.g. "Reject" + newline + "all") still match keywords.
            const txt = el =>
                (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                .trim().replace(/\\s+/g, ' ').toLowerCase();

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
            // A button is "filled" when it has a visually distinct non-white background.
            // Checks background-image first (catches gradient CTAs whose backgroundColor
            // is transparent), then falls back to backgroundColor.
            const parseBgColor = str => {
                const m = str.match(
                    /rgba?\\(\\s*([\\d.]+)\\s*,\\s*([\\d.]+)\\s*,\\s*([\\d.]+)(?:\\s*,\\s*([\\d.]+))?\\s*\\)/
                );
                if (!m) return null;
                return {
                    r: parseFloat(m[1]), g: parseFloat(m[2]), b: parseFloat(m[3]),
                    a: m[4] !== undefined ? parseFloat(m[4]) : 1.0,
                };
            };
            const isColoredFill = c => c && c.a >= 0.1 && !(c.r > 225 && c.g > 225 && c.b > 225);
            const hasFill = el => {
                const s = window.getComputedStyle(el);
                // Gradient or image background → visually filled
                if (s.backgroundImage && s.backgroundImage !== 'none') return true;
                const c = parseBgColor(s.backgroundColor);
                if (isColoredFill(c)) return true;
                // Transparent own background — walk up within the container to find
                // a coloured ancestor that provides the button's visual fill
                // (e.g. a wrapper <span> or <div> with the brand colour).
                if (!c || c.a < 0.1) {
                    let cur = el.parentElement;
                    while (cur && cur !== container) {
                        const ps = window.getComputedStyle(cur);
                        if (ps.backgroundImage && ps.backgroundImage !== 'none') return true;
                        if (isColoredFill(parseBgColor(ps.backgroundColor))) return true;
                        cur = cur.parentElement;
                    }
                }
                return false;
            };

            const agreeButtons    = agreeEls.filter(isBtn);
            const rejectButtons   = rejectEls.filter(isBtn);
            const settingsButtons = settingsEls.filter(isBtn);

            let emphasizedOption = 'none';
            if (agreeButtons.length > 0 && (hasReject || hasSettings)) {
                const agreeFill  = hasFill(agreeButtons[0]);
                const otherFill  = (rejectButtons.length   > 0 && hasFill(rejectButtons[0]))
                                || (settingsButtons.length > 0 && hasFill(settingsButtons[0]));
                if (agreeFill && !otherFill)       emphasizedOption = 'accept';
                else if (!agreeFill && otherFill)  emphasizedOption = 'other';
                else                               emphasizedOption = 'equal';
            }

            // ── 7. Agree button bounding rect (for Playwright native click) ─────
            // Return the screen coordinates of the first agree button so the
            // caller can use page.mouse.click() instead of a JS .click() call.
            // This produces trusted events that work on React portals, OneTrust,
            // and other CMPs that reject synthetic (isTrusted=false) events.
            let agreeBtnRect = null;
            const agreeTargets = agreeButtons.length > 0 ? agreeButtons : agreeEls;
            for (const btn of agreeTargets) {
                const r = btn.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    agreeBtnRect = {x: r.left, y: r.top, width: r.width, height: r.height};
                    break;
                }
            }

            return {
                position:          position,
                control_type:      controlType,
                emphasized_option: emphasizedOption,
                has_reject:        hasReject,
                has_settings:      hasSettings,
                bbox_x:            visLeft,
                bbox_y:            visTop,
                bbox_width:        visRight  - visLeft,
                bbox_height:       visBottom - visTop,
                agree_btn_rect:    agreeBtnRect,
            };
        }"""


async def classify_cookie_notice(ctx: Page | Frame, lang: str = "en") -> dict:
    """
    Classifies a visible cookie consent notice using the taxonomy from:
      "A Cross-Platform Evaluation of Privacy Notices and Tracking Practices"

    Must be called AFTER detect_cookie_notice() returns True and BEFORE
    accept_cookie_notice() so the notice is in its natural state.

    Returns:
        position          : 'top_overlay' | 'bottom_overlay' |
                            'middle_overlay' | 'left_overlay' | 'right_overlay' |
                            'corner_overlay' | 'none'
        control_type      : 'accept_only' | 'accept_or_reject' | 'accept_or_settings' |
                            'accept_reject_or_settings' | 'accept_or_pay' |
                            'reject_or_pay' | 'close_only' | 'informational_only' | 'none'
        emphasized_option : 'accept' | 'other' | 'equal' | 'none'
                            'accept'  — accept button is filled/coloured, reject/settings not
                            'other'   — reject/settings button is filled/coloured, accept not
                            'equal'   — both buttons same fill (both coloured or both plain)
                            'none'    — no reject/settings to compare, or no agree button
        has_reject        : bool
        has_settings      : bool
    """
    _fallback = {
        "position": "none", "control_type": "none",
        "emphasized_option": "none",
        "has_reject": False, "has_settings": False,
    }
    try:
        result = await ctx.evaluate(_apply_kw(_CLASSIFY_JS, lang))
        if not result:
            return _fallback

        # When the notice is in an iframe, _CLASSIFY_JS computes position relative
        # to the frame's local viewport.  Re-derive position from the <iframe>
        # element's bounding box on the main page so it reflects the actual
        # on-screen location (e.g. a bottom-strip iframe that fills its frame
        # would give relY≈0.5 → middle_overlay instead of bottom_overlay).
        if isinstance(ctx, Frame) and result.get("position") != "none":
            try:
                frame_el = await ctx.frame_element()
                bb = await frame_el.bounding_box()
                if bb:
                    vp = ctx.page.viewport_size or {"width": 1280, "height": 720}
                    vw, vh = float(vp["width"]), float(vp["height"])
                    mid_x = bb["x"] + bb["width"]  / 2
                    mid_y = bb["y"] + bb["height"] / 2
                    rel_x, rel_y = mid_x / vw, mid_y / vh
                    coverage = (bb["width"] * bb["height"]) / (vw * vh)
                    is_small   = coverage < 0.18
                    in_corner_h = rel_x < 0.3 or rel_x > 0.7
                    in_corner_v = rel_y < 0.25 or rel_y > 0.75
                    is_tall    = bb["height"] > bb["width"] * 1.5
                    if is_small and in_corner_h and in_corner_v:
                        result["position"] = "corner_overlay"
                    elif is_tall and rel_x < 0.3:
                        result["position"] = "left_overlay"
                    elif is_tall and rel_x > 0.7:
                        result["position"] = "right_overlay"
                    elif rel_y < 0.35:
                        result["position"] = "top_overlay"
                    elif rel_y > 0.65:
                        result["position"] = "bottom_overlay"
                    else:
                        result["position"] = "middle_overlay"
            except Exception:
                pass  # keep frame-local position if override fails

        return result
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

async def run_lighthouse(
    url: str,
    output_file: Path,
    screenshot_file: Path | None = None,
) -> float | None:
    """
    Run Lighthouse against the already-open Chrome instance and save the
    report to output_file. If screenshot_file is provided, save Lighthouse's
    full-page screenshot there when available. Returns accessibility score
    (0-100) or None.
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
            report = json.loads(output_file.read_text(encoding="utf-8"))
            score  = report.get("categories", {}).get("accessibility", {}).get("score")
            if score is not None:
                score = round(score * 100, 1)

            # Extract and save the full-page screenshot embedded in the report.
            try:
                # Lighthouse report shape differs by version:
                # - Newer: report["fullPageScreenshot"]["screenshot"]["data"]
                # - Older: report["audits"]["full-page-screenshot"]["details"]["screenshot"]["data"]
                ss_data = (
                    report.get("fullPageScreenshot", {})
                          .get("screenshot", {})
                          .get("data", "")
                )
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
                    ss_path = screenshot_file or (output_file.parent / f"lighthouse_{output_file.stem}_screenshot.{ext}")

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


# ── Core scan ──────────────────────────────────────────────────────────────────

NETWORKIDLE_TIMEOUT = 30  # seconds to wait for network idle after navigation


def _same_notice(orig: dict | None, re_cls: dict) -> bool:
    """
    Return True if the re-classified cookie notice is likely the same notice
    that was present before acceptance (i.e. the notice was not dismissed).

    Requires BOTH checks to agree when data for both is available:

    1. Container bbox overlap (>50 % of smaller area) — rules out elements that
       are in a completely different part of the page.
    2. Agree-button centre within 30 px — rules out a different element that
       happens to sit in the same screen region but has its buttons elsewhere
       (e.g. a Fastly footer strip behind where the banner was).

    Requiring both together is much stricter than either alone:
    - Facebook (notice stays): same container + same button position → True ✓
    - Fastly (footer behind banner): containers overlap but buttons far apart → False ✓
    - bit.ly / digicert (different popup): different container entirely → False ✓

    Falls back to whichever single check is available when one side lacks data.
    When no dimensions are available at all, returns True (conservative).
    """
    if not orig:
        return True

    orig_btn = orig.get("agree_btn_rect")
    re_btn   = re_cls.get("agree_btn_rect")

    # ── Container bbox overlap ────────────────────────────────────────────────
    ow = orig.get("bbox_width") or 0
    oh = orig.get("bbox_height") or 0
    rw = re_cls.get("bbox_width") or 0
    rh = re_cls.get("bbox_height") or 0
    has_bbox = ow > 0 and oh > 0 and rw > 0 and rh > 0

    if has_bbox:
        ox  = orig.get("bbox_x") or 0
        oy  = orig.get("bbox_y") or 0
        rx  = re_cls.get("bbox_x") or 0
        ry  = re_cls.get("bbox_y") or 0
        ix  = max(ox, rx);  iy  = max(oy, ry)
        ix2 = min(ox + ow, rx + rw);  iy2 = min(oy + oh, ry + rh)
        if ix2 <= ix or iy2 <= iy:
            bbox_overlap = False
        else:
            intersection = (ix2 - ix) * (iy2 - iy)
            smaller_area = min(ow * oh, rw * rh)
            bbox_overlap = smaller_area > 0 and (intersection / smaller_area) > 0.5
    else:
        bbox_overlap = True  # no dimensions → be conservative

    # ── Agree-button centre distance ──────────────────────────────────────────
    if orig_btn and re_btn:
        ocx = orig_btn.get("x", 0) + orig_btn.get("width",  0) / 2
        ocy = orig_btn.get("y", 0) + orig_btn.get("height", 0) / 2
        rcx = re_btn.get("x",  0)  + re_btn.get("width",   0) / 2
        rcy = re_btn.get("y",  0)  + re_btn.get("height",  0) / 2
        btn_same = abs(ocx - rcx) <= 30 and abs(ocy - rcy) <= 30
        result = bbox_overlap and btn_same
        if DEBUG:
            print(f"       [DEBUG] _same_notice: bbox_overlap={bbox_overlap} "
                  f"btn_same={btn_same} (orig_btn_centre=({ocx:.0f},{ocy:.0f}) "
                  f"re_btn_centre=({rcx:.0f},{rcy:.0f})) -> {result}")
        return result

    # If the original had an agree button but the re-classified notice doesn't,
    # the button disappearing is strong evidence the notice was accepted/dismissed.
    # Returning False here prevents a shrunken post-accept remnant (e.g. a
    # settings-only strip) from being mistaken for the original un-accepted notice.
    if orig_btn and not re_btn:
        if DEBUG:
            print(f"       [DEBUG] _same_notice: orig had agree btn, re-classify has none "
                  f"(bbox_overlap={bbox_overlap}) -> False")
        return False

    if DEBUG:
        print(f"       [DEBUG] _same_notice: bbox_overlap={bbox_overlap} "
              f"(no btn comparison — orig_btn={orig_btn is not None} "
              f"re_btn={re_btn is not None}) -> {bbox_overlap}")
    # Only one check available — use it
    return bbox_overlap


def _attach_net_logger(page: "Page", log: list, phase: str) -> None:
    """Attach a response listener that appends entries to *log* tagged with *phase*."""
    def _on_response(response) -> None:
        try:
            log.append({
                "phase":         phase,
                "request_url":   response.url,
                "method":        response.request.method,
                "resource_type": response.request.resource_type,
                "status":        response.status,
            })
        except Exception:
            pass
    page.on("response", _on_response)


async def scan_url(
    playwright,
    url: str,
    artifacts_root: Path,
    scan_id: int,
    timeout: int = 30,
    dwell: int = 60,
    run_wave_flag: bool = True,
    run_lighthouse_flag: bool = True,
    run_nvda_flag: bool = True,
) -> dict:
    art_dir = artifact_dir(artifacts_root, scan_id, url)

    # ── PRE-ACCEPT SESSION ─────────────────────────────────────────────────────
    # Fresh Chrome profile: completely clean cookies, cache, history.
    # Corresponds to the "Clear Cookies and Cache" → "Open Each Website and Wait"
    # steps in the Pre-Accept box of the diagram.
    pre_net_log: list[dict] = []
    context_pre, profile_pre = await launch_chrome_fresh(playwright)
    http_status:     int | None = None
    nav_error:       str | None = None
    is_error_page:   bool       = False
    cookie_detected: bool       = False
    cookie_info:     dict | None = None
    page_lang:       str        = "en"
    pre:             dict       = _empty_phase()

    try:
        page_pre = await context_pre.new_page()
        _attach_net_logger(page_pre, pre_net_log, phase="pre")

        # Navigate
        try:
            response    = await page_pre.goto(url, wait_until="domcontentloaded",
                                              timeout=timeout * 1000)
            http_status = response.status if response else None
        except Exception as e:
            nav_error = str(e).splitlines()[0]

        is_error_page = bool(nav_error or (http_status is not None and http_status >= 400))

        if is_error_page:
            print(f"       [!] Error page ({nav_error or f'HTTP {http_status}'}) — skipping")
            await capture_screenshot(page_pre, art_dir / "pre_screenshot.png")
        else:
            # Wait for network idle
            print(f"       [HTTP {http_status}] Waiting for network idle (max {NETWORKIDLE_TIMEOUT}s)...")
            try:
                await page_pre.wait_for_load_state("networkidle",
                                                   timeout=NETWORKIDLE_TIMEOUT * 1000)
                print("       [*] Network idle")
            except Exception:
                print(f"       [*] Network still active after {NETWORKIDLE_TIMEOUT}s — continuing")

            # Wait briefly for CMPs to initialise — they often fire JS after networkidle
            await page_pre.wait_for_timeout(3000)

            # ── Cookie notice detection & classification ───────────────────────
            page_lang = await detect_page_language(page_pre)
            cookie_detected, cookie_ctx = await detect_cookie_notice(page_pre, lang=page_lang)

            if not cookie_detected:
                # Some CMPs are slow-loading — wait a further 7 s and retry once
                print("       [*] No cookie notice yet — retrying in 7 s...")
                await page_pre.wait_for_timeout(7000)
                cookie_detected, cookie_ctx = await detect_cookie_notice(page_pre, lang=page_lang)

            if cookie_detected:
                print("       [*] Cookie notice detected"
                      + (" (in iframe)" if cookie_ctx is not page_pre else "")
                      + " — classifying...")
                cookie_info = await classify_cookie_notice(cookie_ctx, lang=page_lang)
                print(f"       [+] Classification: {cookie_info}")
            else:
                print("       [*] No cookie notice detected")

            # Short pause so NVDA's virtual buffer has time to build
            await page_pre.wait_for_timeout(5000)

            # ── Pre-accept captures ────────────────────────────────────────────
            print("       [*] Pre-accept captures...")
            pre = await _capture_phase(
                page_pre, art_dir, "pre", url,
                run_wave_flag, run_lighthouse_flag, run_nvda_flag,
            )
    finally:
        await context_pre.close()
        shutil.rmtree(profile_pre, ignore_errors=True)

    # ── Shared result fields ───────────────────────────────────────────────────
    cls = cookie_info or {}
    base_result = {
        "url":                      url,
        "http_status":              http_status,
        "error":                    nav_error,
        "cookie_position":          cls.get("position"),
        "cookie_control_type":      cls.get("control_type"),
        "cookie_emphasized_option": cls.get("emphasized_option"),
        "cookie_has_reject":        cls.get("has_reject", False),
        "cookie_has_settings":      cls.get("has_settings", False),
        "cookie_bbox_x":            cls.get("bbox_x"),
        "cookie_bbox_y":            cls.get("bbox_y"),
        "cookie_bbox_width":        cls.get("bbox_width"),
        "cookie_bbox_height":       cls.get("bbox_height"),
        "pre":                      pre,
    }

    # Error page or no cookie notice: return with only pre data
    if is_error_page:
        return {
            **base_result,
            "is_error_page":          True,
            "cookie_notice_detected": False,
            "cookie_notice_accepted": False,
            "post":                   None,
            "network_log":            pre_net_log,
        }

    if not cookie_detected:
        return {
            **base_result,
            "is_error_page":          False,
            "cookie_notice_detected": False,
            "cookie_notice_accepted": False,
            "post":                   None,
            "network_log":            pre_net_log,
        }

    # ── POST-ACCEPT SESSION ────────────────────────────────────────────────────
    # A second, completely fresh browser profile — clean cookies, cache, history.
    # Corresponds to the "Clear Cookies and Cache" → "Accept Cookie Notice and
    # Wait" steps in the Post-Accept box of the diagram.
    post_net_log:      list[dict] = []
    cookie_accepted:   bool       = False
    click_attempted:   bool       = False  # True when a button was found+clicked
    post:              dict | None = None

    context_post, profile_post = await launch_chrome_fresh(playwright)
    try:
        page_post = await context_post.new_page()
        _attach_net_logger(page_post, post_net_log, phase="post")

        # Navigate fresh
        http_status_post: int | None = None
        nav_error_post:   str | None = None
        try:
            response_post    = await page_post.goto(url, wait_until="domcontentloaded",
                                                    timeout=timeout * 1000)
            http_status_post = response_post.status if response_post else None
        except Exception as e:
            nav_error_post = str(e).splitlines()[0]

        is_error_post = bool(nav_error_post or
                             (http_status_post is not None and http_status_post >= 400))

        if is_error_post:
            print(f"       [!] Post-session navigation failed "
                  f"({nav_error_post or f'HTTP {http_status_post}'}) — skipping post-accept")
        else:
            # Wait for network idle + CMP init
            print(f"       [HTTP {http_status_post}] Waiting for network idle "
                  f"(max {NETWORKIDLE_TIMEOUT}s)...")
            try:
                await page_post.wait_for_load_state("networkidle",
                                                    timeout=NETWORKIDLE_TIMEOUT * 1000)
                print("       [*] Network idle")
            except Exception:
                print(f"       [*] Network still active after {NETWORKIDLE_TIMEOUT}s — continuing")
            await page_post.wait_for_timeout(3000)

            # Re-detect notice in the fresh session (cookie_ctx from pre session
            # is closed — we need a live handle from this page)
            page_lang_post = await detect_page_language(page_post)
            post_detected, cookie_ctx_post = await detect_cookie_notice(
                page_post, lang=page_lang_post
            )
            if not post_detected:
                await page_post.wait_for_timeout(7000)
                post_detected, cookie_ctx_post = await detect_cookie_notice(
                    page_post, lang=page_lang_post
                )

            if not post_detected:
                print("       [!] Cookie notice not detected in post session — skipping acceptance")
            else:
                print("       [*] Attempting to accept cookie notice...")

                # Build bbox from pre-session classification (plain dict — safe to
                # reference after pre-session browser has been closed)
                cls_bbox = None
                if cookie_info and cookie_info.get("bbox_width"):
                    cls_bbox = {
                        "x": cookie_info["bbox_x"],
                        "y": cookie_info["bbox_y"],
                        "w": cookie_info["bbox_width"],
                        "h": cookie_info["bbox_height"],
                    }

                # Primary: ElementHandle.click() via JS marker
                elem_click_ok = await native_click_agree_button(
                    cookie_ctx_post, lang=page_lang_post, bbox=cls_bbox
                )
                if elem_click_ok:
                    click_attempted = True
                    cookie_accepted = True
                    print("       [+] ElementHandle click succeeded")

                # Fallback: JS-based acceptance (shadow-DOM buttons, etc.)
                if not cookie_accepted:
                    js_ok = await accept_cookie_notice(
                        cookie_ctx_post, lang=page_lang_post
                    )
                    if js_ok:
                        click_attempted = True
                    cookie_accepted = js_ok

                if cookie_accepted:
                    # Wait for notice to animate/dismiss
                    await page_post.wait_for_timeout(5000)

                    # Re-classify to verify dismissal
                    re_cls = await classify_cookie_notice(page_post, lang=page_lang_post)
                    re_has_notice = (
                        re_cls.get("agree_btn_rect") is not None
                        or re_cls.get("control_type", "none") not in ("none", None)
                    )
                    if DEBUG:
                        print(f"       [DEBUG] Re-classify: "
                              f"control_type={re_cls.get('control_type')!r} "
                              f"agree_btn_rect={re_cls.get('agree_btn_rect')} "
                              f"bbox=({re_cls.get('bbox_x')},{re_cls.get('bbox_y')}) "
                              f"{re_cls.get('bbox_width')}x{re_cls.get('bbox_height')} "
                              f"-> re_has_notice={re_has_notice}")
                    if re_has_notice and _same_notice(cookie_info, re_cls):
                        print("       [!] Cookie notice still present after ElementHandle click"
                              " — trying JS fallback")
                        cookie_accepted = await accept_cookie_notice(
                            cookie_ctx_post, lang=page_lang_post
                        )
                        if cookie_accepted:
                            await page_post.wait_for_timeout(3000)
                            re_cls2 = await classify_cookie_notice(page_post, lang=page_lang_post)
                            re_has_notice2 = (
                                re_cls2.get("agree_btn_rect") is not None
                                or re_cls2.get("control_type", "none") not in ("none", None)
                            )
                            if re_has_notice2 and _same_notice(cookie_info, re_cls2):
                                print("       [!] Cookie notice still present after JS fallback"
                                      " — marking as not accepted")
                                cookie_accepted = False
                            else:
                                print("       [+] Cookie notice accepted and dismissed (JS fallback)")
                        else:
                            print("       [!] JS fallback also found no button — marking as not accepted")
                    else:
                        print("       [+] Cookie notice accepted and dismissed")

                if cookie_accepted:
                    # Dwell: wait for post-acceptance network traffic to settle.
                    # Corresponds to the "Wait 60s" node in the Post-Accept box.
                    print(f"       [*] Dwelling {dwell}s for post-acceptance network traffic...")
                    await page_post.wait_for_timeout(dwell * 1000)
                    # Short pause so NVDA's virtual buffer has time to build
                    await page_post.wait_for_timeout(5000)
                    print("       [*] Post-accept captures...")
                    post = await _capture_phase(
                        page_post, art_dir, "post", url,
                        run_wave_flag, run_lighthouse_flag, run_nvda_flag,
                    )
                elif click_attempted:
                    # A click was made but dismissal could not be confirmed.
                    # Capture screenshot + HTML + cookies (no LH/WAVE/NVDA to
                    # keep it fast) so the user can manually verify in review.html
                    print("       [!] Cookie notice found but could not be confirmed dismissed")
                    print("       [*] Capturing post-attempt state for manual review...")
                    post = await _capture_phase(
                        page_post, art_dir, "post", url,
                        run_wave_flag=False,
                        run_lighthouse_flag=False,
                        run_nvda_flag=False,
                    )
                else:
                    print("       [!] Cookie notice found but could not be accepted")
    finally:
        await context_post.close()
        shutil.rmtree(profile_post, ignore_errors=True)

    return {
        **base_result,
        "is_error_page":           False,
        "cookie_notice_detected":  cookie_detected,
        "cookie_notice_accepted":  cookie_accepted,
        "cookie_accept_attempted": click_attempted,
        "post":                    post,
        "network_log":             pre_net_log + post_net_log,
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

    # HTML, screenshot and cookie capture are independent — run in parallel
    phase["html_path"], phase["screenshot_path"], phase["cookies_path"] = (
        await asyncio.gather(
            capture_html(page,       art_dir / f"{prefix}_page.html"),
            capture_screenshot(page, art_dir / f"{prefix}_screenshot.png"),
            capture_cookies(page,    art_dir / f"{prefix}_cookies.json"),
        )
    )

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
        phase["lh_score"] = await run_lighthouse(
            url,
            lh_path,
            screenshot_file=art_dir / f"lighthouse_{prefix}.png",
        )
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
    dwell: int = 60,
    run_wave_flag: bool = True,
    run_lighthouse_flag: bool = True,
    run_nvda_flag: bool = True,
) -> int:
    """Scan one URL with Chrome, write results to DB, return scan_id."""
    cur = con.execute(
        """INSERT INTO chrome_scans (url, scanned_at, is_error_page,
               cookie_notice_detected, cookie_notice_accepted, cookie_accept_attempted)
           VALUES (?, ?, 0, 0, 0, 0)""",
        (url, datetime.now(timezone.utc).isoformat()),
    )
    scan_id = cur.lastrowid
    con.commit()

    stats = await scan_url(
        p, url, artifacts_root, scan_id,
        timeout=timeout,
        dwell=dwell,
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
               cookie_accept_attempted = ?,
               cookie_position         = ?,
               cookie_control_type     = ?,
               cookie_emphasized_option = ?,
               cookie_has_reject       = ?,
               cookie_has_settings     = ?,
               cookie_bbox_x           = ?,
               cookie_bbox_y           = ?,
               cookie_bbox_width       = ?,
               cookie_bbox_height      = ?,
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
            1 if stats.get("cookie_accept_attempted") else 0,
            stats["cookie_position"],
            stats["cookie_control_type"],
            stats["cookie_emphasized_option"],
            1 if stats["cookie_has_reject"] else 0,
            1 if stats["cookie_has_settings"] else 0,
            stats["cookie_bbox_x"],
            stats["cookie_bbox_y"],
            stats["cookie_bbox_width"],
            stats["cookie_bbox_height"],
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

    # ── Network request log ────────────────────────────────────────────────────
    network_log = stats.get("network_log") or []
    if network_log:
        con.executemany(
            """INSERT INTO chrome_network_requests
                   (scan_id, site_url, phase, request_url, method, resource_type, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (scan_id, url, r["phase"], r["request_url"],
                 r["method"], r["resource_type"], r["status"])
                for r in network_log
            ],
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
    dwell: int = 60,
    run_wave_flag: bool = True,
    run_lighthouse_flag: bool = True,
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
    print(f"  Timeout:     {timeout}s  |  Network idle timeout: {NETWORKIDLE_TIMEOUT}s  |  Dwell: {dwell}s")
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
                    dwell=dwell,
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
    parser.add_argument("--dwell", type=int, default=60,
                        help="Seconds to dwell after cookie acceptance for post-accept captures (default: 60)")
    parser.add_argument("--no-wave",       action="store_true",
                        help="Skip WAVE accessibility injection")
    parser.add_argument("--no-lighthouse", action="store_true",
                        help="Skip Lighthouse accessibility audit")
    parser.add_argument("--no-nvda",       action="store_true",
                        help="Skip NVDA screen reader transcript")
    parser.add_argument("--debug",         action="store_true",
                        help="Print verbose cookie-acceptance diagnostics "
                             "(candidate buttons, rejection reasons, re-verify details)")
    args = parser.parse_args()

    if args.debug:
        DEBUG = True

    if not args.csv.exists():
        print(f"[!] CSV file not found: {args.csv}")
        sys.exit(1)

    artifacts_root = args.artifacts or args.db.parent / "artifacts"

    asyncio.run(chrome_main(
        args.csv, args.db, artifacts_root,
        args.timeout,
        dwell=args.dwell,
        run_wave_flag=not args.no_wave,
        run_lighthouse_flag=not args.no_lighthouse,
        run_nvda_flag=not args.no_nvda,
    ))
