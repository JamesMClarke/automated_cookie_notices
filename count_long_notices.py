"""
Count cookie notices long enough for WCAG 2.4.10 (Headings Useful for Navigation) to apply.
Uses a word-count threshold on text extracted from pre_page.html.
"""

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from analysis.utils import NOT_FP, open_merged

WORD_THRESHOLD = 200
ARTIFACTS_BASE = Path("/Volumes/Backups/cookie_notices_automation")
COOKIE_WORDS = {"cookie", "consent", "privacy", "gdpr", "tracking", "data protection"}


def resolve_path(windows_path):
    """Resolve a Windows NAS path to a local Path, handling artifacts/, crawl_two/, crawl_three/."""
    if not windows_path:
        return None
    p = windows_path.replace("\\", "/")
    marker = "cookie_notices_automation/"
    idx = p.lower().find(marker)
    if idx == -1:
        return None
    return ARTIFACTS_BASE / p[idx + len(marker) :]


def find_notice_element(soup):
    """Simplified cookie notice detection using ARIA and keyword heuristics."""
    SKIP_TAGS = {"html", "body", "head", "script", "style", "noscript"}

    def word_count(el):
        return len(el.get_text(" ", strip=True).split())

    def has_cookie_words(el):
        text = el.get_text(" ", strip=True).lower()
        return any(w in text for w in COOKIE_WORDS)

    # Pass 1: ARIA dialog with cookie keywords
    for sel in ('[role="dialog"]', '[role="alertdialog"]', '[aria-modal="true"]', "dialog"):
        for el in soup.select(sel):
            if has_cookie_words(el):
                return el

    # Pass 2: id/class attributes with cookie-related substrings — pick smallest
    cookie_re = re.compile(r"cookie|consent|gdpr|privacy.notice|cmp.banner", re.I)
    candidates = []
    for el in soup.find_all(True):
        if el.name in SKIP_TAGS:
            continue
        el_id = el.get("id", "")
        el_cls = " ".join(el.get("class", []))
        if cookie_re.search(el_id) or cookie_re.search(el_cls):
            wc = word_count(el)
            if wc > 10:
                candidates.append((wc, el))
    if candidates:
        # Smallest element that still has cookie keywords
        candidates = [(wc, el) for wc, el in candidates if has_cookie_words(el)]
        if candidates:
            return min(candidates, key=lambda x: x[0])[1]

    # Pass 3: smallest element with cookie keywords, capped at 2000 words
    candidates = []
    for el in soup.find_all(True):
        if el.name in SKIP_TAGS:
            continue
        wc = word_count(el)
        if 20 <= wc <= 2000 and has_cookie_words(el):
            candidates.append((wc, el))
    if candidates:
        return min(candidates, key=lambda x: x[0])[1]

    return None


def count_notice_words(html_path):
    try:
        html = Path(html_path).read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        el = find_notice_element(soup)
        if el is None:
            return None
        return len(el.get_text(" ", strip=True).split())
    except Exception:
        return None


def main():
    conn, db_paths = open_merged()
    print(f"Loaded: {[p.name for p in db_paths]}")

    rows = conn.execute(f"""
        SELECT id, url, pre_html_path
        FROM chrome_scans
        WHERE cookie_notice_detected=1
          AND {NOT_FP}
          AND is_error_page=0
          AND pre_html_path IS NOT NULL
    """).fetchall()

    print(f"Sites with detected cookie notice: {len(rows)}")

    results = []
    for scan_id, url, raw_path in rows:
        local_path = resolve_path(raw_path)
        if local_path is None or not local_path.exists():
            continue
        words = count_notice_words(local_path)
        if words is not None:
            results.append((url, words))

    results.sort(key=lambda x: -x[1])

    processed = len(results)
    word_counts = [w for _, w in results]
    avg = sum(word_counts) / processed
    median = sorted(word_counts)[processed // 2]
    long_notices = [(url, w) for url, w in results if w >= 500]

    print(f"\nProcessed: {processed} notices")
    print(f"Average word count: {avg:.1f}")
    print(f"Median word count:  {median}")
    print(f"Notices >= 500 words: {len(long_notices)} ({100 * len(long_notices) / processed:.1f}%)")

    print("\nWord count distribution:")
    for lo, hi in [(0, 50), (50, 100), (100, 200), (200, 500), (500, 1000), (1000, 99999)]:
        count = sum(1 for w in word_counts if lo <= w < hi)
        print(f"  {lo:5d}–{hi:5d} words: {count}")


if __name__ == "__main__":
    main()
