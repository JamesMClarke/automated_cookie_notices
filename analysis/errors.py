from collections import Counter
from pathlib import Path
import sqlite3

from .utils import q, latex_escape, DB_PATH


_DESCRIPTIONS = {
    "DNS not resolved": (
        "The domain name could not be resolved to an IP address, meaning the site has no public DNS record. "
        "This is the largest category and predominantly reflects infrastructure domains---"
        "CDN endpoints, ad-tech hostnames, and API subdomains---that are not intended to serve "
        "user-facing web pages."
    ),
    "Timed out": (
        "The browser failed to receive a complete response within the 30-second navigation timeout. "
        r"This covers genuinely slow or unreachable servers, as well as sites that caused the browser "
        r"renderer to exhaust memory (the ``Aw, Snap!'' out-of-memory state), which renders the tab "
        "unresponsive."
    ),
    "HTTP 403 Forbidden": (
        "The server responded but refused to serve the page. "
        "This typically indicates bot-detection or IP-blocking measures that prevent automated "
        "browsers from accessing the site."
    ),
    "TLS error": (
        "The HTTPS handshake could not be completed. "
        "Common causes include expired certificates, certificates issued for a different domain "
        r"(SNI mismatch), untrusted certificate authorities, and servers that only support "
        "deprecated cipher suites."
    ),
    "Connection failed": (
        "A TCP connection could not be established or was terminated before the response was received. "
        "This includes servers actively refusing connections, mid-flight connection resets, "
        "empty responses, and redirect loops."
    ),
    "HTTP 404 Not Found": (
        "The server was reachable but returned a 404, indicating that no page is served at that URL."
    ),
    "Other 4xx client error": (
        r"The server rejected the request with another 4xx status code (e.g.\ 400~Bad Request, "
        r"406~Not Acceptable), indicating the request was malformed or otherwise unacceptable "
        "to the server."
    ),
    "Other 5xx server error": (
        r"The server returned a 5xx status, indicating an internal failure on the server side "
        r"at the time of scanning (e.g.\ 502~Bad Gateway, 503~Service Unavailable)."
    ),
    "Other error": (
        "A site produced an unclassified error that did not match any known category."
    ),
}


def _error_category(page_error, http_status):
    # Timed out — covers nav timeout, connection timeout, and OOM hangs.
    # (OOM causes asyncio.TimeoutError whose str() is empty, so the scanner
    # stores the secondary IndexError "list index out of range" instead.)
    if page_error and (
        ("Timeout" in page_error and "exceeded" in page_error)
        or "ERR_CONNECTION_TIMED_OUT" in page_error
        or "ERR_OUT_OF_MEMORY" in page_error
        or page_error == "list index out of range"
    ):
        return "Timed out"
    # DNS not resolved
    if page_error and "ERR_NAME_NOT_RESOLVED" in page_error:
        return "DNS not resolved"
    # TLS / certificate — all ERR_CERT_* and ERR_SSL_* variants
    if page_error and ("ERR_CERT" in page_error or "ERR_SSL" in page_error):
        return "TLS error"
    # Connection failed — refused, reset, aborted, empty response, redirects, HTTP/2
    if page_error and any(k in page_error for k in (
        "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_RESET",
        "ERR_ABORTED", "ERR_EMPTY_RESPONSE",
        "ERR_TOO_MANY_REDIRECTS", "ERR_HTTP2_PROTOCOL_ERROR",
        "ERR_HTTP_RESPONSE_CODE_FAILURE",
    )):
        return "Connection failed"
    # HTTP status codes (404 and 403 kept separate)
    if http_status == 404:
        return "HTTP 404 Not Found"
    if http_status == 403:
        return "HTTP 403 Forbidden"
    if http_status is not None and http_status >= 500:
        return "Other 5xx server error"
    if http_status is not None and http_status >= 400:
        return "Other 4xx client error"
    # Catch-all
    return "Other error"


def _db_label(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").title()


def _cat_counts_for_db(db_path: Path) -> Counter:
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT http_status, page_error FROM chrome_scans WHERE is_error_page=1"
            ).fetchall()
            return Counter(_error_category(pe, hs) for hs, pe in rows)
        finally:
            conn.close()
    except Exception:
        return Counter()


def run(conn, db_paths=None):
    total_chrome = q(conn, "SELECT COUNT(*) FROM chrome_scans")[0][0]
    error_count  = q(conn, "SELECT COUNT(*) FROM chrome_scans WHERE is_error_page=1")[0][0]

    error_rows = q(
        conn,
        "SELECT url, http_status, page_error FROM chrome_scans WHERE is_error_page=1 ORDER BY url",
    )
    cat_counts = Counter(_error_category(pe, hs) for _, hs, pe in error_rows)
    top_error_cat = cat_counts.most_common(1)[0]

    print(r"\subsection{Unavailable Sites}")
    print(
        rf"Of the {total_chrome} sites scanned, \textbf{{{error_count}}} failed to load. "
        rf"The most common failure was \textbf{{{latex_escape(top_error_cat[0])}}} "
        rf"({top_error_cat[1]} sites), which typically indicates infrastructure or CDN domains "
        r"(e.g.\ \texttt{akamai.net}, \texttt{akadns.net}) that do not serve end-user web pages. "
        r"All unavailable sites were excluded from cookie-notice and accessibility analysis."
    )
    print()

    # Per-DB breakdown
    per_db = []
    if db_paths:
        for p in db_paths:
            if p.exists():
                per_db.append((_db_label(p), _cat_counts_for_db(p)))

    # Table
    if per_db:
        col_spec = "l" + "r" * len(per_db) + "r"
        db_headers = " & ".join(rf"\textbf{{{label}}}" for label, _ in per_db)
        header_row = rf"\textbf{{Error Category}} & {db_headers} & \textbf{{Total}} \\"
    else:
        col_spec = "lr"
        header_row = r"\textbf{Error Category} & \textbf{Count} \\"

    print(r"\begin{table}[ht]")
    print(r"\centering")
    print(rf"\begin{{tabular}}{{{col_spec}}}")
    print(r"\toprule")
    print(header_row + r" \midrule")
    for cat, total in cat_counts.most_common():
        if per_db:
            cells = " & ".join(str(counts.get(cat, 0)) for _, counts in per_db)
            print(rf"{latex_escape(cat)} & {cells} & {total} \\")
        else:
            print(rf"{latex_escape(cat)} & {total} \\")
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(r"\caption{Categories of errors encountered when loading sites in Chrome.}")
    print(r"\label{tab:errors}")
    print(r"\end{table}")
    print()

    # Per-category descriptions
    print(r"\begin{description}")
    for cat, _ in cat_counts.most_common():
        desc = _DESCRIPTIONS.get(cat)
        if desc:
            print(rf"  \item[\textbf{{{latex_escape(cat)}}}] {desc}")
        else:
            print(rf"  \item[\textbf{{{latex_escape(cat)}}}]")
    print(r"\end{description}")
    print()


if __name__ == "__main__":
    import sys
    from .utils import open_merged
    db_names = sys.argv[1:] if len(sys.argv) > 1 else None
    conn, db_paths = open_merged(db_names)
    try:
        run(conn, db_paths=db_paths)
    finally:
        conn.close()
