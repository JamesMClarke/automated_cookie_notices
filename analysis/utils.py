import json
import sqlite3
import sys
from functools import lru_cache
from pathlib import Path

import tldextract


@lru_cache(maxsize=None)
def _reg_domain(raw):
    ext = tldextract.extract(raw)
    return f"{ext.domain}.{ext.suffix}" if ext.domain and ext.suffix else None


def cookie_party(site_url, domain_or_url):
    """Return 'first', 'third', or None (unclassifiable) for a cookie domain or request URL."""
    if not domain_or_url:
        return None
    site_r = _reg_domain(site_url)
    other_r = _reg_domain(domain_or_url.lstrip('.'))
    if not site_r or not other_r:
        return None
    return "first" if site_r == other_r else "third"

DB_PATH = Path(__file__).parent.parent / "top-1000.sqlite"

DEFAULT_DBS = [
    "top-1000.sqlite",
    "crawl_two.sqlite",
    "crawl_three.sqlite",
]

CHILD_TABLES = [
    "chrome_network_requests",
    "cookie_classifications",
    "storage_classifications",
    "wave_issues",
    "lighthouse_issues",
    "screen_reader_metrics",
]


def merge_databases(db_paths):
    """Return an in-memory SQLite connection containing merged rows from all db_paths."""
    mem = sqlite3.connect(":memory:")

    first = sqlite3.connect(str(db_paths[0]))
    for (sql,) in first.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL AND name != 'sqlite_sequence'"
    ):
        mem.execute(sql)
    first.close()

    dest_cols = {
        table: [r[1] for r in mem.execute(f"PRAGMA table_info({table})")]
        for (table,) in mem.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    scan_offset = 0
    for db_path in db_paths:
        src = sqlite3.connect(str(db_path))
        src_tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        if "chrome_scans" in src_tables:
            src_cols = {r[1] for r in src.execute("PRAGMA table_info(chrome_scans)")}
            cols = [c for c in dest_cols["chrome_scans"] if c in src_cols]
            id_idx = cols.index("id")
            col_list = ",".join(cols)
            placeholders = ",".join("?" * len(cols))
            for row in src.execute(f"SELECT {col_list} FROM chrome_scans"):
                adjusted = list(row)
                adjusted[id_idx] += scan_offset
                mem.execute(f"INSERT INTO chrome_scans ({col_list}) VALUES ({placeholders})", adjusted)

        for table in CHILD_TABLES:
            if table not in src_tables:
                continue
            src_cols = {r[1] for r in src.execute(f"PRAGMA table_info({table})")}
            cols = [c for c in dest_cols[table] if c in src_cols and c != "id"]
            scan_id_idx = cols.index("scan_id")
            col_list = ",".join(cols)
            placeholders = ",".join("?" * len(cols))
            for row in src.execute(f"SELECT {col_list} FROM {table}"):
                adjusted = list(row)
                adjusted[scan_id_idx] += scan_offset
                mem.execute(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})", adjusted)

        scan_offset += src.execute("SELECT COALESCE(MAX(id), 0) FROM chrome_scans").fetchone()[0]
        src.close()

    mem.commit()
    return mem


def open_merged(db_names=None):
    """Resolve db_names (filenames relative to the repo root) to paths, merge, and return (conn, db_paths)."""
    base = Path(__file__).parent.parent
    names = db_names if db_names is not None else DEFAULT_DBS
    db_paths = []
    for name in names:
        p = base / name
        if p.exists():
            db_paths.append(p)
        else:
            print(f"Warning: {name} not found, skipping", file=sys.stderr)
    if not db_paths:
        print("No databases found.", file=sys.stderr)
        sys.exit(1)
    return merge_databases(db_paths), db_paths
ARTIFACTS_BASE = Path("/Volumes/Backups/cookie_notices_automation")

NOT_FP = "(false_positive IS NULL OR false_positive=0)"
ACCEPTED = "(cookie_notice_accepted=1 OR manually_verified=1)"
SHOW_PER = False

# Normalises raw category values from OCD and Cookiedatabase.org into a
# consistent vocabulary. Use as a drop-in replacement for COALESCE(category,…)
# in GROUP BY queries so the DB aggregates by the normalised label.
NORM_CAT_SQL = """CASE
    WHEN category IS NULL                    THEN 'Unclassified'
    WHEN LOWER(category) = 'resource'        THEN 'Functional'
    WHEN LOWER(category) = 'local storage'   THEN 'Functional'
    WHEN LOWER(category) = 'statistics'      THEN 'Analytics'
    WHEN LOWER(category) = 'cookie'          THEN 'Unclassified'
    WHEN LOWER(category) = 'functional'      THEN 'Functional'
    WHEN LOWER(category) = 'marketing'       THEN 'Marketing'
    WHEN LOWER(category) = 'analytics'       THEN 'Analytics'
    WHEN LOWER(category) = 'security'        THEN 'Security'
    WHEN LOWER(category) = 'necessary'       THEN 'Necessary'
    WHEN LOWER(category) = 'personalization' THEN 'Personalization'
    WHEN LOWER(category) = 'Statistics (anonymous)' THEN 'Analytics'
    ELSE category
END"""


def resolve_artifact_path(windows_path):
    if not windows_path:
        return None
    p = windows_path.replace("\\", "/")
    idx = p.lower().find("artifacts/")
    if idx == -1:
        return None
    return ARTIFACTS_BASE / p[idx:]


def read_storage_from_path(windows_path):
    local_path = resolve_artifact_path(windows_path)
    if not local_path or not local_path.exists():
        return None, None
    try:
        data = json.loads(local_path.read_text(encoding="utf-8"))
        ls_count = len(data.get("localStorage", {}))
        ss_count = len(data.get("sessionStorage", {}))
        return ls_count, ss_count
    except Exception:
        return None, None


def count_cookies_from_path(windows_path):
    local_path = resolve_artifact_path(windows_path)
    if not local_path or not local_path.exists():
        return None
    try:
        data = json.loads(local_path.read_text(encoding="utf-8"))
        return len(data) if isinstance(data, list) else None
    except Exception:
        return None


def q(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        raise


def q_safe(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def col_exists(conn, table, col):
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    return col in cols


def classify_label(s):
    if s == "none":
        return "Needs manually classifying"
    return s


def latex_escape(s):
    if s is None:
        return r"\textit{N/A}"
    return (
        str(s)
        .replace("_", r"\_")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("#", r"\#")
        .replace("https://", "")
        .replace("http://", "")
    )


def fmt(val, decimals=1):
    if val is None:
        return "---"
    if isinstance(val, float):
        return f"{val:.{decimals}f}"
    return str(val)


def top(rows, n=1):
    return latex_escape(rows[n - 1][0]) if len(rows) >= n else "---"


def delta(a, b):
    if a is None or b is None:
        return "---"
    d = round(b - a, 1)
    return rf"\textbf{{{d:+.1f}}}" if d != 0 else "0.0"


def ck_delta(a, b):
    if a is None or b is None:
        return "---"
    d = b - a
    return (rf"\textbf{{{d:+d}}}" if d != 0 else "0")


def wilcoxon_p(pre, post, alternative="two-sided"):
    """Wilcoxon signed-rank test on paired non-None values. Returns (stat, p) or (None, None)."""
    try:
        from scipy.stats import wilcoxon as _wilcoxon
    except ImportError:
        return None, None
    pairs = [(a, b) for a, b in zip(pre, post) if a is not None and b is not None]
    if len(pairs) < 10:
        return None, None
    pre_a  = [p[0] for p in pairs]
    post_a = [p[1] for p in pairs]
    try:
        stat, p = _wilcoxon(pre_a, post_a, alternative=alternative)
        return stat, p
    except Exception:
        return None, None


def fmt_p(p):
    """Format a p-value for LaTeX (inline or table cell)."""
    if p is None:
        return "---"
    if p < 0.001:
        return r"$<\!0.001$"
    return rf"${p:.3f}$"
