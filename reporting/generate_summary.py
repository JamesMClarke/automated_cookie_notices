#!/usr/bin/env python3
"""Generate a LaTeX summary report from one or more SQLite crawl databases.

Reads DBs from and writes .tex output to the repo root (where the DBs live),
regardless of the current working directory.

Usage:
    python reporting/generate_summary.py                 # process all default DBs
    python reporting/generate_summary.py top-1000.sqlite  # specific DB(s)
    python reporting/generate_summary.py crawl_two.sqlite crawl_three.sqlite
"""

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

DEFAULT_DBS = [
    "top-1000.sqlite",
    "crawl_two.sqlite",
    "crawl_three.sqlite",
]
ARTIFACTS_BASE = Path("/Volumes/Backups/cookie_notices_automation")


def resolve_artifact_path(windows_path):
    """Convert a Windows path stored in the DB to a local filesystem path."""
    if not windows_path:
        return None
    p = windows_path.replace("\\", "/")
    idx = p.lower().find("artifacts/")
    if idx == -1:
        return None
    return ARTIFACTS_BASE / p[idx:]


def read_storage_from_path(windows_path):
    """Return (localStorage_count, sessionStorage_count) from a storage JSON file, or (None, None)."""
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
    """Return the number of cookies in a JSON cookie file, or None if unavailable."""
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
    """Like q() but returns [] if the query references a missing column."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def col_exists(conn, table, col):
    """Return True if col exists in table."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    return col in cols


def classify_label(s):
    """Map raw DB classification values to display labels."""
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
    """Return the label of the nth most common row (rows sorted DESC by count)."""
    return latex_escape(rows[n - 1][0]) if len(rows) >= n else "---"


def build_report(conn):
    lines = []
    appendix_lines = []

    def emit(*args):
        lines.append(" ".join(str(a) for a in args))

    def emit_app(*args):
        appendix_lines.append(" ".join(str(a) for a in args))

    # Rows that count as "accepted" for analysis purposes: scanner confirmed,
    # OR a click was attempted and the user manually verified it was accepted.
    ACCEPTED = "(cookie_notice_accepted=1 OR manually_verified=1)"

    # Exclude false positives from all detection-based analysis.
    NOT_FP = "(false_positive IS NULL OR false_positive=0)"

    # Whether the reject phase columns exist (older DBs pre-date the feature)
    has_reject_cols = col_exists(conn, "chrome_scans", "cookie_notice_rejected")
    # Whether the network requests table has an is_tracker classification column
    has_tracker_col = col_exists(conn, "chrome_network_requests", "is_tracker")

    # ------------------------------------------------------------------ #
    # 1. Overview
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Overview}")

    total_chrome = q(conn, "SELECT COUNT(*) FROM chrome_scans")[0][0]
    error_count = q(conn, "SELECT COUNT(*) FROM chrome_scans WHERE is_error_page=1")[0][0]
    reachable = total_chrome - error_count
    cookie_detected = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}")[0][0]
    cookie_accepted = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE {ACCEPTED} AND {NOT_FP}")[0][0]
    manually_verified_count = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE manually_verified=1 AND {NOT_FP}")[0][0]

    cookie_rejected = 0
    reject_attempted = 0
    if has_reject_cols:
        cookie_rejected = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_rejected=1 AND {NOT_FP}")[0][
            0
        ]
        reject_attempted = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_reject_attempted=1 AND {NOT_FP}")[
            0
        ][0]

    # Tracker totals across pre / post-accept / post-reject phases.
    # Falls back to total request counts when is_tracker column is absent.
    if has_tracker_col:
        tracker_totals = q_safe(
            conn,
            """SELECT
                 SUM(CASE WHEN phase='pre'         AND is_tracker=1 THEN 1 ELSE 0 END),
                 SUM(CASE WHEN phase='post_accept' AND is_tracker=1 THEN 1 ELSE 0 END),
                 SUM(CASE WHEN phase='pre'         THEN 1 ELSE 0 END),
                 SUM(CASE WHEN phase='post_accept' THEN 1 ELSE 0 END)
               FROM chrome_network_requests r
               JOIN chrome_scans c ON c.id = r.scan_id
               WHERE c.is_error_page=0""",
        )
        tracker_totals = tracker_totals[0] if tracker_totals else (0, 0, 0, 0)
    else:
        tracker_totals = (None, None, None, None)

    pre_trackers_total = tracker_totals[0] or 0
    post_trackers_total = tracker_totals[1] or 0
    pre_requests_total = tracker_totals[2] or 0
    post_requests_total = tracker_totals[3] or 0

    # Fall back to total request counts when no tracker classification exists
    if not has_tracker_col:
        req_totals = q_safe(
            conn,
            """SELECT
                 SUM(CASE WHEN phase='pre'         THEN 1 ELSE 0 END),
                 SUM(CASE WHEN phase='post_accept' THEN 1 ELSE 0 END)
               FROM chrome_network_requests r
               JOIN chrome_scans c ON c.id = r.scan_id
               WHERE c.is_error_page=0""",
        )
        if req_totals:
            pre_requests_total = req_totals[0][0] or 0
            post_requests_total = req_totals[0][1] or 0

    pre_tracker_rate = pre_trackers_total / pre_requests_total * 100 if pre_requests_total and has_tracker_col else 0
    post_tracker_rate = (
        post_trackers_total / post_requests_total * 100 if post_requests_total and has_tracker_col else 0
    )

    reject_trackers_total = 0
    reject_requests_total = 0
    reject_tracker_rate = 0
    if has_tracker_col:
        reject_tracker_rows = q_safe(
            conn,
            """SELECT
                 SUM(CASE WHEN is_tracker=1 THEN 1 ELSE 0 END),
                 COUNT(*)
               FROM chrome_network_requests r
               JOIN chrome_scans c ON c.id = r.scan_id
               WHERE c.is_error_page=0 AND r.phase='post_reject'""",
        )
        if reject_tracker_rows and reject_tracker_rows[0][1]:
            reject_trackers_total = reject_tracker_rows[0][0] or 0
            reject_requests_total = reject_tracker_rows[0][1] or 0
            reject_tracker_rate = reject_trackers_total / reject_requests_total * 100
    else:
        rej_req_rows = q_safe(
            conn,
            """SELECT COUNT(*) FROM chrome_network_requests r
               JOIN chrome_scans c ON c.id = r.scan_id
               WHERE c.is_error_page=0 AND r.phase='post_reject'""",
        )
        if rej_req_rows:
            reject_requests_total = rej_req_rows[0][0] or 0

    # Cookie counts and category breakdown from cookie_classifications
    _cc_phase_rows = q_safe(
        conn,
        """SELECT phase, COUNT(*) AS total, COUNT(DISTINCT scan_id) AS sites
           FROM cookie_classifications GROUP BY phase""",
    )
    _cc_phase_totals = {row[0]: (row[1], row[2]) for row in _cc_phase_rows}
    pre_total, pre_sites = _cc_phase_totals.get("pre", (0, 0))
    acc_total, acc_sites = _cc_phase_totals.get("post_accept", (0, 0))
    rej_total, rej_sites = _cc_phase_totals.get("post_reject", (0, 0))
    avg_pre_db = pre_total / pre_sites if pre_sites else None
    avg_acc_db = acc_total / acc_sites if acc_sites else None
    avg_rej_db = rej_total / rej_sites if rej_sites else None
    has_rej_cookies = rej_sites > 0

    from collections import defaultdict

    cat_rows = q_safe(
        conn,
        """SELECT phase, COALESCE(category,'Unclassified') AS cat, COUNT(*) AS n
           FROM cookie_classifications
           GROUP BY phase, cat
           ORDER BY phase, n DESC""",
    )
    cat_by_phase = defaultdict(dict)
    for _phase, _cat, _n in cat_rows:
        cat_by_phase[_phase][_cat] = _n
    all_cats = sorted(
        {cat for _, cat, _ in cat_rows},
        key=lambda c: -(cat_by_phase.get("pre", {}).get(c, 0) + cat_by_phase.get("post_accept", {}).get(c, 0)),
    )
    # Category with the biggest absolute increase pre -> post-accept
    _top_rising_cat = max(
        (c for c in all_cats if c != "Unclassified"),
        key=lambda c: cat_by_phase.get("post_accept", {}).get(c, 0) - cat_by_phase.get("pre", {}).get(c, 0),
        default=None,
    )

    # Storage classification counts (storage_classifications table, may not exist yet)
    _has_storage_class = bool(
        q_safe(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='storage_classifications'")
    )
    sc_phase_type_totals = {}  # (phase, storage_type) -> (total, sites)
    sc_cat_by_phase_type = defaultdict(dict)  # (phase, storage_type) -> {cat: count}
    sc_all_cats = []
    if _has_storage_class:
        for row in q_safe(
            conn,
            """
                SELECT phase, storage_type, COUNT(*), COUNT(DISTINCT scan_id)
                FROM storage_classifications GROUP BY phase, storage_type""",
        ):
            sc_phase_type_totals[(row[0], row[1])] = (row[2], row[3])
        for row in q_safe(
            conn,
            """
                SELECT phase, storage_type, COALESCE(category,'Unclassified'), COUNT(*)
                FROM storage_classifications
                GROUP BY phase, storage_type, 3 ORDER BY phase, storage_type, 4 DESC""",
        ):
            sc_cat_by_phase_type[(row[0], row[1])][row[2]] = row[3]
        sc_all_cats = sorted(
            {cat for (_, _), d in sc_cat_by_phase_type.items() for cat in d},
            key=lambda c: (
                -(
                    sc_cat_by_phase_type.get(("pre", "local"), {}).get(c, 0)
                    + sc_cat_by_phase_type.get(("post_accept", "local"), {}).get(c, 0)
                )
            ),
        )

    emit(
        rf"An automated audit was performed on the top-{total_chrome} websites by Tranco rank. "
        rf"Of these, \textbf{{{error_count}}} ({error_count / total_chrome * 100:.0f}\,\%) "
        r"could not be loaded successfully and were excluded from further analysis, "
        rf"leaving \textbf{{{reachable}}} reachable sites. "
        r"Each reachable site was visited with Chrome to detect and classify cookie notices, "
        r"capture accessibility metrics, and record network requests."
    )
    emit()
    auto_accepted = cookie_accepted - manually_verified_count
    emit(
        rf"A cookie notice was detected on \textbf{{{cookie_detected}}} of the {reachable} reachable sites "
        rf"({cookie_detected / reachable * 100:.0f}\,\%), and was successfully accepted on "
        rf"\textbf{{{cookie_accepted}}} of those ({cookie_accepted / cookie_detected * 100:.0f}\,\%) "
        rf"({auto_accepted} confirmed automatically"
        + (rf", {manually_verified_count} manually verified" if manually_verified_count else "")
        + r")."
    )
    if has_reject_cols and reject_attempted:
        emit(
            rf"A reject/decline button was found and clicked on \textbf{{{cookie_rejected}}} of the "
            rf"{reject_attempted} sites where rejection was attempted "
            rf"({cookie_rejected / reject_attempted * 100:.0f}\,\%)."
        )
    if _top_rising_cat and pre_total and acc_total:
        _pre_n = cat_by_phase["pre"].get(_top_rising_cat, 0)
        _acc_n = cat_by_phase["post_accept"].get(_top_rising_cat, 0)
        _rej_n = cat_by_phase["post_reject"].get(_top_rising_cat, 0) if has_rej_cookies else None
        _pre_pct = _pre_n / pre_total * 100
        _acc_pct = _acc_n / acc_total * 100
        emit(
            rf"Cookie analysis found an average of \textbf{{{fmt(avg_pre_db)}}} cookies per site "
            rf"pre-accept, rising to \textbf{{{fmt(avg_acc_db)}}} post-accept"
            + (rf" and \textbf{{{fmt(avg_rej_db)}}} post-reject" if has_rej_cookies else "")
            + rf". The \textbf{{{latex_escape(_top_rising_cat)}}} category showed the largest increase, "
            rf"accounting for {_pre_pct:.0f}\,\% of pre-accept cookies and "
            rf"{_acc_pct:.0f}\,\% post-accept" + (rf" ({_rej_n} post-reject)" if _rej_n is not None else "") + r"."
        )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{High-level scan counts}")
    emit(r"\begin{tabular}{lr}")
    emit(r"\toprule Metric & Count \\ \midrule")
    emit(rf"Chrome scans & {total_chrome} \\")
    emit(rf"Unavailable (error) & {error_count} ({error_count / total_chrome * 100:.0f}\,\%) \\")
    emit(rf"Reachable sites & {reachable} \\")
    emit(rf"Cookie notice detected & {cookie_detected} ({cookie_detected / reachable * 100:.0f}\,\%) \\")
    emit(rf"Cookie notice accepted & {cookie_accepted} ({cookie_accepted / cookie_detected * 100:.0f}\,\%) \\")
    if has_reject_cols and reject_attempted:
        emit(
            rf"Cookie notice rejected & {cookie_rejected} ({cookie_rejected / reject_attempted * 100:.0f}\,\% of attempted) \\"
        )
    emit(rf"Pre-accept tracker requests & {pre_trackers_total} ({pre_tracker_rate:.0f}\,\% of requests) \\")
    emit(rf"Post-accept tracker requests & {post_trackers_total} ({post_tracker_rate:.0f}\,\% of requests) \\")
    if reject_requests_total:
        emit(rf"Post-reject tracker requests & {reject_trackers_total} ({reject_tracker_rate:.0f}\,\% of requests) \\")
    if avg_pre_db is not None:
        emit(rf"Avg.\ cookies pre-accept & {fmt(avg_pre_db)} \\")
    if avg_acc_db is not None:
        emit(rf"Avg.\ cookies post-accept & {fmt(avg_acc_db)} \\")
    if avg_rej_db is not None:
        emit(rf"Avg.\ cookies post-reject & {fmt(avg_rej_db)} \\")
    if _top_rising_cat:
        _pre_pct = cat_by_phase["pre"].get(_top_rising_cat, 0) / pre_total * 100 if pre_total else 0
        _acc_pct = cat_by_phase["post_accept"].get(_top_rising_cat, 0) / acc_total * 100 if acc_total else 0
        emit(rf"Top rising category & {latex_escape(_top_rising_cat)} ({_pre_pct:.0f}\,\% $\to$ {_acc_pct:.0f}\,\%) \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    # ------------------------------------------------------------------ #
    # 2. Unavailable / Error Sites
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Unavailable Sites}")

    def error_category(page_error, http_status):
        if page_error and "ERR_NAME_NOT_RESOLVED" in page_error:
            return "DNS not resolved"
        if page_error and "ERR_CONNECTION_REFUSED" in page_error:
            return "Connection refused"
        if page_error and "ERR_CERT" in page_error:
            return "TLS/certificate error"
        if page_error and "ERR_ABORTED" in page_error:
            return "Request aborted"
        if http_status == 404:
            return "HTTP 404 Not Found"
        if http_status == 403:
            return "HTTP 403 Forbidden"
        if http_status is not None and http_status >= 400:
            return f"HTTP {http_status}"
        return "Other error"

    error_rows = q(
        conn,
        "SELECT url, http_status, page_error FROM chrome_scans WHERE is_error_page=1 ORDER BY url",
    )
    cat_counts = Counter(error_category(pe, hs) for _, hs, pe in error_rows)
    top_error_cat = cat_counts.most_common(1)[0]
    dns_count = cat_counts.get("DNS not resolved", 0)  # noqa: F841
    http4xx_count = sum(v for k, v in cat_counts.items() if k.startswith("HTTP"))

    emit(
        rf"Of the {total_chrome} sites scanned, \textbf{{{error_count}}} failed to load. "
        rf"The most common failure was \textbf{{{latex_escape(top_error_cat[0])}}} "
        rf"({top_error_cat[1]} sites), which typically indicates infrastructure or CDN domains "
        r"(e.g.\ \texttt{akamai.net}, \texttt{akadns.net}) that do not serve end-user web pages. "
        rf"A further {http4xx_count} site(s) returned HTTP 4xx error codes. "
        r"All unavailable sites were excluded from cookie-notice and accessibility analysis."
    )
    emit()

    # emit(r"\begin{table}[ht]\centering\footnotesize")
    # emit(r"\caption{Error categories for unavailable sites}")
    # emit(r"\begin{tabular}{lr}")
    # emit(r"\toprule Error type & Count \\ \midrule")
    # for cat, cnt in cat_counts.most_common():
    #     emit(rf"  {latex_escape(cat)} & {cnt} \\")
    # emit(r"\midrule")
    # emit(rf"  \textbf{{Total}} & \textbf{{{error_count}}} \\")
    # emit(r"\bottomrule\end{tabular}")
    # emit(r"\end{table}")

    # emit(
    #     r"Table~\ref{tab:errors} lists every unavailable site. "
    #     r"Sites returning HTTP 404 are predominantly Google-owned infrastructure "
    #     r"domains (\texttt{googleapis.com}, \texttt{gstatic.com}, etc.)\ that redirect "
    #     r"rather than serving a browsable page."
    # )
    # emit()

    # emit(r"\begin{table}[ht]\centering\footnotesize")
    # emit(r"\caption{Per-site error details}\label{tab:errors}")
    # emit(r"\begin{tabular}{>{\ttfamily}p{3cm} r p{3cm}}")
    # emit(r"\toprule \normalfont URL & HTTP & Category \\ \midrule")
    # for url, hs, pe in error_rows:
    #     cat = error_category(pe, hs)
    #     status_str = str(hs) if hs else "---"
    #     emit(rf"  {latex_escape(url)} & {status_str} & {latex_escape(cat)} \\")
    # emit(r"\bottomrule\end{tabular}")
    # emit(r"\end{table}")

    # ------------------------------------------------------------------ #
    # 3. Cookie Notice Classification
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Cookie Notice Classification}")

    emit(r"\subsubsection{Position}")

    pos_rows = q(
        conn,
        f"SELECT COALESCE(manual_cookie_position, cookie_position,'unknown'), COUNT(*) "
        f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"GROUP BY COALESCE(manual_cookie_position, cookie_position) ORDER BY COUNT(*) DESC",
    )
    top_pos, top_pos_cnt = pos_rows[0]
    second_pos, second_pos_cnt = pos_rows[1] if len(pos_rows) > 1 else ("", 0)

    emit(
        rf"Cookie notices appeared most commonly as a \textbf{{{latex_escape(classify_label(top_pos))}}} "
        rf"({top_pos_cnt} of {cookie_detected} sites, "
        rf"{top_pos_cnt / cookie_detected * 100:.0f}\,\%), "
        rf"followed by \texttt{{{latex_escape(classify_label(second_pos))}}} ({second_pos_cnt} sites). "
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Cookie notice position}\label{tab:position}")
    emit(r"\begin{tabular}{lrr}")
    emit(r"\toprule Position & $n$ & \% \\ \midrule")
    for pos, cnt in pos_rows:
        emit(rf"  \texttt{{{latex_escape(classify_label(pos))}}} & {cnt} & {cnt / cookie_detected * 100:.0f}\,\% \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    emit(r"\subsubsection{Control Type (Response Options)}")

    ctrl_rows = q(
        conn,
        f"SELECT COALESCE(manual_cookie_control_type, cookie_control_type,'unknown'), COUNT(*) "
        f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"GROUP BY COALESCE(manual_cookie_control_type, cookie_control_type) ORDER BY COUNT(*) DESC",
    )
    full_choice = next((cnt for ct, cnt in ctrl_rows if ct == "accept_reject_or_settings"), 0)  # noqa: F841

    emit(
        rf"The most common control type was \textbf{{{latex_escape(classify_label(ctrl_rows[0][0]))}}} ({ctrl_rows[0][1] if ctrl_rows else 0} sites, "
        rf"{ctrl_rows[0][1] / cookie_detected * 100:.0f}\,\%), meaning \textcolor{{red}}{{update}}. "
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Cookie notice control type}\label{tab:control}")
    emit(r"\begin{tabular}{lrr}")
    emit(r"\toprule Control type & $n$ & \% \\ \midrule")
    for ctrl, cnt in ctrl_rows:
        emit(rf"  \texttt{{{latex_escape(classify_label(ctrl))}}} & {cnt} & {cnt / cookie_detected * 100:.0f}\,\% \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    emit(r"\subsubsection{Emphasised Option}")

    emph_rows = q(
        conn,
        f"SELECT COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option,'unknown'), COUNT(*) "
        f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"GROUP BY COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option) ORDER BY COUNT(*) DESC",
    )
    equal_cnt = next((cnt for e, cnt in emph_rows if e == "equal"), 0)
    none_cnt = next((cnt for e, cnt in emph_rows if e == "none"), 0)  # noqa: F841
    eph_cnt = next((cnt for e, cnt in emph_rows if e == "emphasized"), 0)

    emit(
        # rf"Of the {cookie_detected} detected notices, {none_cnt} had \texttt{{none}} as the "
        # r"emphasized option, corresponding entirely to informational-only notices where no "
        # r"choice is offered. "
        rf"Among notices that offer a choice, {equal_cnt} presented accept and "
        r"reject options with \textbf{equal} visual weight --- a positive finding indicating "
        r"no deliberate dark pattern was detected in these cases."
        rf"In total, \textbf{{{eph_cnt}}} notices emphasised the accept option over reject, "
        rf"potentially nudging users towards consent. "
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Emphasised option on cookie notices}\label{tab:emph}")
    emit(r"\begin{tabular}{lrr}")
    emit(r"\toprule Emphasised option & $n$ & \% \\ \midrule")
    for emph, cnt in emph_rows:
        emit(rf"  \texttt{{{latex_escape(classify_label(emph))}}} & {cnt} & {cnt / cookie_detected * 100:.0f}\,\% \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    emit(r"\subsubsection{Additional Features}")

    has_reject = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND COALESCE(manual_cookie_has_reject,   cookie_has_reject)=1",
    )[0][0]
    has_settings = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND COALESCE(manual_cookie_has_settings, cookie_has_settings)=1",
    )[0][0]
    pre_selected = q(  # noqa: F841
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND COALESCE(manual_cookie_pre_selected, cookie_pre_selected)=1",
    )[0][0]

    emit(
        rf"A reject button or link was present on {has_reject} of {cookie_detected} notices "
        rf"({has_reject / cookie_detected * 100:.0f}\,\%), and a settings or preferences link on "
        rf"{has_settings} ({has_settings / cookie_detected * 100:.0f}\,\%). "
        # rf"No notices in this sample had options pre-selected, "
        # r"meaning none defaulted consent to `on' before the user interacted."
    )
    emit()

    # emit(r"\begin{table}[ht]\centering\footnotesize")
    # emit(r"\caption{Additional cookie notice features}")
    # emit(r"\begin{tabular}{lrr}")
    # emit(r"\toprule Feature & $n$ & \% \\ \midrule")
    # emit(rf"Has reject button/link & {has_reject} & {has_reject/cookie_detected*100:.0f}\,\% \\")
    # emit(rf"Has settings link & {has_settings} & {has_settings/cookie_detected*100:.0f}\,\% \\")
    # emit(rf"Options pre-selected & {pre_selected} & {pre_selected/cookie_detected*100:.0f}\,\% \\")
    # emit(r"\bottomrule\end{tabular}")
    # emit(r"\end{table}")

    # # ------------------------------------------------------------------ #
    # # 4. Per-site cookie notice summary
    # # ------------------------------------------------------------------ #
    # emit(r"\subsection{Per-Site Cookie Notice Summary}")

    # emit(
    #     r"Table~\ref{tab:persite} lists every site where a cookie notice was detected "
    #     r"alongside its full classification. "
    #     r"The `Rej', `Set', and `Pre' columns indicate whether a reject button, "
    #     r"settings link, or pre-selected options were present (\checkmark) or absent (---), "
    #     r"respectively."
    # )
    # emit()

    # site_rows = q(
    #     conn,
    #     f"""SELECT url,
    #               COALESCE(manual_cookie_position,          cookie_position),
    #               COALESCE(manual_cookie_control_type,      cookie_control_type),
    #               COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option),
    #               COALESCE(manual_cookie_has_reject,        cookie_has_reject),
    #               COALESCE(manual_cookie_has_settings,      cookie_has_settings),
    #               COALESCE(manual_cookie_pre_selected,      cookie_pre_selected)
    #        FROM chrome_scans
    #        WHERE cookie_notice_detected=1 AND {NOT_FP}
    #        ORDER BY url""",
    # )

    # emit(r"\begin{table*}[ht]\centering\footnotesize")
    # emit(r"\caption{Per-site cookie notice classification}\label{tab:persite}")
    # emit(r"\begin{tabular}{>{\ttfamily}p{2.3cm} p{1.5cm} p{2.5cm} p{1.1cm} c c c}")
    # emit(r"\toprule \normalfont URL & Pos. & Control type & Emph. & Rej & Set & Pre \\ \midrule")
    # for url, pos, ctrl, emph, rej, sett, pre in site_rows:
    #     def yn(v): return r"\checkmark" if v else "---"
    #     emit(
    #         rf"  {latex_escape(url)} & "
    #         rf"\scriptsize {latex_escape(pos)} & "
    #         rf"\scriptsize {latex_escape(ctrl)} & "
    #         rf"\scriptsize {latex_escape(emph)} & "
    #         rf"{yn(rej)} & {yn(sett)} & {yn(pre)} \\"
    #     )
    # emit(r"\bottomrule\end{tabular}")
    # emit(r"\end{table*}")

    # ------------------------------------------------------------------ #
    # 5. Trackers: pre-accept vs post-accept vs post-reject
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Tracker Requests: Pre-Accept, Post-Accept, and Post-Reject}")

    if has_tracker_col:
        tracker_rows = q_safe(
            conn,
            """SELECT c.url,
                 SUM(CASE WHEN r.phase='pre'         AND r.is_tracker=1 THEN 1 ELSE 0 END),
                 SUM(CASE WHEN r.phase='post_accept' AND r.is_tracker=1 THEN 1 ELSE 0 END),
                 SUM(CASE WHEN r.phase='post_reject' AND r.is_tracker=1 THEN 1 ELSE 0 END),
                 SUM(CASE WHEN r.phase='pre'         THEN 1 ELSE 0 END),
                 SUM(CASE WHEN r.phase='post_accept' THEN 1 ELSE 0 END),
                 SUM(CASE WHEN r.phase='post_reject' THEN 1 ELSE 0 END)
               FROM chrome_scans c
               LEFT JOIN chrome_network_requests r ON c.id = r.scan_id
               WHERE c.is_error_page=0
               GROUP BY c.url
               ORDER BY 2 DESC""",
        )
    else:
        tracker_rows = q_safe(
            conn,
            """SELECT c.url,
                 NULL, NULL, NULL,
                 SUM(CASE WHEN r.phase='pre'         THEN 1 ELSE 0 END),
                 SUM(CASE WHEN r.phase='post_accept' THEN 1 ELSE 0 END),
                 SUM(CASE WHEN r.phase='post_reject' THEN 1 ELSE 0 END)
               FROM chrome_scans c
               LEFT JOIN chrome_network_requests r ON c.id = r.scan_id
               WHERE c.is_error_page=0
               GROUP BY c.url
               ORDER BY 5 DESC""",
        )

    sites_with_post = [
        (url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot)
        for url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot in tracker_rows
        if post_tot and post_tot > 0
    ]
    sites_with_reject = [
        (url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot)
        for url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot in tracker_rows
        if rej_tot and rej_tot > 0
    ]

    avg_pre_t = sum(r[1] or 0 for r in tracker_rows) / len(tracker_rows) if tracker_rows else 0
    avg_post_t = sum(r[2] or 0 for r in sites_with_post) / len(sites_with_post) if sites_with_post else 0
    avg_reject_t = sum(r[3] or 0 for r in sites_with_reject) / len(sites_with_reject) if sites_with_reject else 0

    tracker_increased = sum(
        1 for _, pre_t, post_t, *_ in sites_with_post if post_t is not None and pre_t is not None and post_t > pre_t
    )
    tracker_decreased = sum(
        1 for _, pre_t, post_t, *_ in sites_with_post if post_t is not None and pre_t is not None and post_t < pre_t
    )

    emit(
        r"Network requests were classified as tracker or non-tracker for the pre-accept "
        r"(cookie notice visible), post-accept (notice accepted), and post-reject (notice rejected) "
        r"phases. "
        rf"Across all {reachable} reachable sites, an average of \textbf{{{avg_pre_t:.1f}}} "
        r"tracker requests were recorded in the pre-accept phase per site. "
        rf"For the {len(sites_with_post)} sites where a post-accept scan was performed, "
        rf"the average rose to \textbf{{{avg_post_t:.1f}}} tracker requests. "
    )
    if sites_with_reject:
        emit(
            rf"The post-reject phase was completed for {len(sites_with_reject)} sites, "
            rf"averaging \textbf{{{avg_reject_t:.1f}}} tracker requests --- "
            r"a comparison that reveals whether rejecting the cookie notice meaningfully "
            r"reduces third-party tracking activity."
        )
    emit(
        rf"Of the {len(sites_with_post)} accepted sites, {tracker_increased} showed \emph{{more}} "
        rf"tracker requests post-accept and {tracker_decreased} showed fewer."
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Average tracker request counts per site}")
    col_hdr = r"Pre & Post-accept & Post-reject \\" if sites_with_reject else r"Pre & Post-accept \\"
    col_fmt = "lrrr" if sites_with_reject else "lrr"
    emit(rf"\begin{{tabular}}{{{col_fmt}}}")
    emit(rf"\toprule Metric & {col_hdr} \midrule")
    if sites_with_reject:
        emit(rf"Avg.\ tracker requests/site & {avg_pre_t:.1f} & {avg_post_t:.1f} & {avg_reject_t:.1f} \\")
        emit(rf"Total tracker requests & {pre_trackers_total} & {post_trackers_total} & {reject_trackers_total} \\")
        emit(rf"Total network requests & {pre_requests_total} & {post_requests_total} & {reject_requests_total} \\")
        emit(
            rf"Tracker rate & {pre_tracker_rate:.1f}\,\% & {post_tracker_rate:.1f}\,\% & {reject_tracker_rate:.1f}\,\% \\"
        )
    else:
        emit(rf"Avg.\ tracker requests/site & {avg_pre_t:.1f} & {avg_post_t:.1f} \\")
        emit(rf"Total tracker requests & {pre_trackers_total} & {post_trackers_total} \\")
        emit(rf"Total network requests & {pre_requests_total} & {post_requests_total} \\")
        emit(rf"Tracker rate & {pre_tracker_rate:.1f}\,\% & {post_tracker_rate:.1f}\,\% \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    emit(r"Per-site figures are in Table~\ref{tab:trackers} in the appendix.")
    emit()

    # Per-site tracker table — emitted to appendix
    if sites_with_reject:
        emit_app(r"\begin{table*}[ht]\centering\footnotesize")
        emit_app(r"\caption{Per-site tracker request counts (pre vs post-accept vs post-reject)}\label{tab:trackers}")
        emit_app(r"\begin{tabular}{>{\ttfamily}p{2.2cm} r r r r r r r r r}")
        emit_app(
            r"\toprule \normalfont URL & \multicolumn{3}{c}{Trackers} & \multicolumn{3}{c}{Total Req.} & \multicolumn{2}{c}{$\Delta$ trackers} \\"
        )
        emit_app(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-9}")
        emit_app(r"\normalfont & Pre & Acc & Rej & Pre & Acc & Rej & vs.\ Acc & vs.\ Rej \\ \midrule")
        for url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot in tracker_rows:
            pre_s = fmt(pre_t, 0) if pre_t is not None else "---"
            post_s = fmt(post_t, 0) if post_t is not None else "---"
            rej_s = fmt(rej_t, 0) if rej_t is not None else "---"
            pre_tot_s = fmt(pre_tot, 0) if pre_tot is not None else "---"
            post_tot_s = fmt(post_tot, 0) if post_tot is not None else "---"
            rej_tot_s = fmt(rej_tot, 0) if rej_tot is not None else "---"
            d_acc = (
                (rf"\textbf{{{post_t - pre_t:+d}}}" if post_t - pre_t != 0 else "0")
                if pre_t is not None and post_t is not None
                else "---"
            )
            d_rej = (
                (rf"\textbf{{{rej_t - pre_t:+d}}}" if rej_t - pre_t != 0 else "0")
                if pre_t is not None and rej_t is not None
                else "---"
            )
            emit_app(
                rf"  {latex_escape(url)} & "
                rf"{pre_s} & {post_s} & {rej_s} & "
                rf"{pre_tot_s} & {post_tot_s} & {rej_tot_s} & "
                rf"{d_acc} & {d_rej} \\"
            )
        emit_app(r"\bottomrule\end{tabular}")
        emit_app(r"\end{table*}")
    else:
        emit_app(r"\begin{table*}[ht]\centering\footnotesize")
        emit_app(r"\caption{Per-site tracker request counts (pre vs post accept)}\label{tab:trackers}")
        emit_app(r"\begin{tabular}{>{\ttfamily}p{2.5cm} r r r r r}")
        emit_app(
            r"\toprule \normalfont URL & \multicolumn{2}{c}{Trackers} & \multicolumn{2}{c}{Total Req.} & $\Delta$ \\"
        )
        emit_app(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}")
        emit_app(r"\normalfont & Pre & Post & Pre & Post & \\ \midrule")
        for url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot in tracker_rows:
            pre_t_s = fmt(pre_t, 0) if pre_t is not None else "---"
            post_t_s = fmt(post_t, 0) if post_t is not None else "---"
            pre_tot_s = fmt(pre_tot, 0) if pre_tot is not None else "---"
            post_tot_s = fmt(post_tot, 0) if post_tot is not None else "---"
            if pre_t is not None and post_t is not None:
                d = post_t - pre_t
                delta_s = rf"\textbf{{{d:+d}}}" if d != 0 else "0"
            else:
                delta_s = "---"
            emit_app(
                rf"  {latex_escape(url)} & "
                rf"{pre_t_s} & {post_t_s} & "
                rf"{pre_tot_s} & {post_tot_s} & "
                rf"{delta_s} \\"
            )
        emit_app(r"\bottomrule\end{tabular}")
        emit_app(r"\end{table*}")

    # ------------------------------------------------------------------ #
    # 6. Cookies: pre-accept vs post-accept vs post-reject
    # ------------------------------------------------------------------ #
    # 6. Cookies: pre-accept vs post-accept vs post-reject
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Browser Cookies: Pre-Accept, Post-Accept, and Post-Reject}")

    emit(
        rf"Cookie classifications were drawn from the \texttt{{cookie\_classifications}} table, "
        rf"which records each cookie's name, domain, and category for every scan phase. "
        rf"In the pre-accept phase, \textbf{{{pre_total}}} cookies were observed across "
        rf"\textbf{{{pre_sites}}} sites (avg.\ {fmt(avg_pre_db)} per site). "
        rf"After accepting the cookie notice, this rose to \textbf{{{acc_total}}} cookies "
        rf"across \textbf{{{acc_sites}}} sites (avg.\ {fmt(avg_acc_db)} per site). "
    )
    if has_rej_cookies:
        emit(
            rf"After rejecting, \textbf{{{rej_total}}} cookies were observed across "
            rf"\textbf{{{rej_sites}}} sites (avg.\ {fmt(avg_rej_db)} per site), "
            rf"compared with {fmt(avg_pre_db)} pre-accept --- suggesting rejection "
            r"does not fully prevent cookie setting."
        )
    emit()

    # Summary counts table
    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Cookie counts from \texttt{cookie\_classifications} by phase}")
    if has_rej_cookies:
        emit(r"\begin{tabular}{lrrr}")
        emit(r"\toprule Metric & Pre & Post-accept & Post-reject \\ \midrule")
        emit(rf"Total cookies & {pre_total} & {acc_total} & {rej_total} \\")
        emit(rf"Sites measured & {pre_sites} & {acc_sites} & {rej_sites} \\")
        emit(rf"Avg.\ cookies/site & {fmt(avg_pre_db)} & {fmt(avg_acc_db)} & {fmt(avg_rej_db)} \\")
    else:
        emit(r"\begin{tabular}{lrr}")
        emit(r"\toprule Metric & Pre & Post-accept \\ \midrule")
        emit(rf"Total cookies & {pre_total} & {acc_total} \\")
        emit(rf"Sites measured & {pre_sites} & {acc_sites} \\")
        emit(rf"Avg.\ cookies/site & {fmt(avg_pre_db)} & {fmt(avg_acc_db)} \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    # Category breakdown table
    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Cookie categories by scan phase (count of cookies)}\label{tab:cookie_cats}")
    if has_rej_cookies:
        emit(r"\begin{tabular}{lrrrrrr}")
        emit(
            r"\toprule Category & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} & \multicolumn{2}{c}{Post-reject} \\"
        )
        emit(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
        emit(r"& $n$ & \% & $n$ & \% & $n$ & \% \\ \midrule")
        for cat in all_cats:
            pre_n = cat_by_phase["pre"].get(cat, 0)
            acc_n = cat_by_phase["post_accept"].get(cat, 0)
            rej_n = cat_by_phase["post_reject"].get(cat, 0)
            pre_pct = pre_n / pre_total * 100 if pre_total else 0
            acc_pct = acc_n / acc_total * 100 if acc_total else 0
            rej_pct = rej_n / rej_total * 100 if rej_total else 0
            emit(
                rf"  {latex_escape(cat)} & {pre_n} & {pre_pct:.0f}\,\% & "
                rf"{acc_n} & {acc_pct:.0f}\,\% & "
                rf"{rej_n} & {rej_pct:.0f}\,\% \\"
            )
        emit(r"\midrule")
        emit(rf"  \textbf{{Total}} & \textbf{{{pre_total}}} & & \textbf{{{acc_total}}} & & \textbf{{{rej_total}}} & \\")
    else:
        emit(r"\begin{tabular}{lrrrr}")
        emit(r"\toprule Category & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} \\")
        emit(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}")
        emit(r"& $n$ & \% & $n$ & \% \\ \midrule")
        for cat in all_cats:
            pre_n = cat_by_phase["pre"].get(cat, 0)
            acc_n = cat_by_phase["post_accept"].get(cat, 0)
            pre_pct = pre_n / pre_total * 100 if pre_total else 0
            acc_pct = acc_n / acc_total * 100 if acc_total else 0
            emit(
                rf"  {latex_escape(cat)} & {pre_n} & {pre_pct:.0f}\,\% & "
                rf"{acc_n} & {acc_pct:.0f}\,\% \\"
            )
        emit(r"\midrule")
        emit(rf"  \textbf{{Total}} & \textbf{{{pre_total}}} & & \textbf{{{acc_total}}} & \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    # Per-site cookie counts by phase
    site_cookie_rows = q_safe(
        conn,
        """SELECT scan_id,
                  SUM(CASE WHEN phase='pre'         THEN 1 ELSE 0 END),
                  SUM(CASE WHEN phase='post_accept' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN phase='post_reject' THEN 1 ELSE 0 END)
           FROM cookie_classifications
           GROUP BY scan_id""",
    )
    scan_to_url = dict(q_safe(conn, "SELECT id, url FROM chrome_scans"))
    site_cookie_data = [
        (scan_to_url.get(sid, f"scan_{sid}"), pre_n, acc_n, rej_n) for sid, pre_n, acc_n, rej_n in site_cookie_rows
    ]
    site_cookie_data.sort(key=lambda r: r[0])

    def _ck_delta(a, b):
        if a is None or b is None:
            return "---"
        d = b - a
        return rf"\textbf{{{d:+d}}}" if d != 0 else "0"

    emit_app(r"\begin{table}[ht]\centering\footnotesize")
    if has_rej_cookies:
        emit_app(r"\caption{Per-site cookie counts by phase}\label{tab:cookie_persite}")
        emit_app(r"\begin{tabular}{>{\ttfamily}p{2.6cm} r r r r r}")
        emit_app(r"\toprule \normalfont URL & Pre & Post-acc & Post-rej & $\Delta$\,Acc & $\Delta$\,Rej \\ \midrule")
        for url, pre_n, acc_n, rej_n in site_cookie_data:
            emit_app(
                rf"  {latex_escape(url)} & {pre_n} & {acc_n} & {rej_n} & "
                rf"{_ck_delta(pre_n, acc_n)} & {_ck_delta(pre_n, rej_n)} \\"
            )
    else:
        emit_app(r"\caption{Per-site cookie counts by phase}\label{tab:cookie_persite}")
        emit_app(r"\begin{tabular}{>{\ttfamily}p{3cm} r r r}")
        emit_app(r"\toprule \normalfont URL & Pre & Post-accept & $\Delta$ \\ \midrule")
        for url, pre_n, acc_n, rej_n in site_cookie_data:
            emit_app(rf"  {latex_escape(url)} & {pre_n} & {acc_n} & {_ck_delta(pre_n, acc_n)} \\")
    emit_app(r"\bottomrule\end{tabular}")
    emit_app(r"\end{table}")

    # Storage classifications (if available)
    if _has_storage_class and sc_phase_type_totals:

        def _sc_total(phase, stype):
            return sc_phase_type_totals.get((phase, stype), (0, 0))[0]

        def _sc_sites(phase, stype):
            return sc_phase_type_totals.get((phase, stype), (0, 0))[1]

        pre_ls_total = _sc_total("pre", "local")
        pre_ss_total = _sc_total("pre", "session")
        acc_ls_total = _sc_total("post_accept", "local")
        acc_ss_total = _sc_total("post_accept", "session")
        rej_ls_total = _sc_total("post_reject", "local")
        rej_ss_total = _sc_total("post_reject", "session")
        has_rej_sc = rej_ls_total + rej_ss_total > 0

        emit()
        emit(r"\subsubsection{Web Storage Key Classifications}")
        emit(
            rf"The \texttt{{storage\_classifications}} table records the category of each "
            rf"localStorage and sessionStorage key observed during each scan phase, using the "
            rf"same Open Cookie Database and Cookiedatabase.org sources as for browser cookies. "
            rf"In the pre-accept phase, \textbf{{{pre_ls_total}}} localStorage key records and "
            rf"\textbf{{{pre_ss_total}}} sessionStorage key records were classified across "
            rf"\textbf{{{_sc_sites('pre', 'local')}}} sites. "
            rf"After accepting, these rose to \textbf{{{acc_ls_total}}} and "
            rf"\textbf{{{acc_ss_total}}} respectively."
        )
        if has_rej_sc:
            emit(
                rf"After rejecting, \textbf{{{rej_ls_total}}} localStorage and "
                rf"\textbf{{{rej_ss_total}}} sessionStorage key records were observed."
            )
        emit()

        # Summary counts table
        emit(r"\begin{table}[ht]\centering\footnotesize")
        emit(r"\caption{Storage key classification counts by phase and type}")
        if has_rej_sc:
            emit(r"\begin{tabular}{llrrr}")
            emit(r"\toprule Type & Metric & Pre & Post-accept & Post-reject \\ \midrule")
            emit(rf"localStorage  & Total keys & {pre_ls_total} & {acc_ls_total} & {rej_ls_total} \\")
            emit(
                rf"              & Sites      & {_sc_sites('pre', 'local')} & {_sc_sites('post_accept', 'local')} & {_sc_sites('post_reject', 'local')} \\"
            )
            emit(r"\midrule")
            emit(rf"sessionStorage & Total keys & {pre_ss_total} & {acc_ss_total} & {rej_ss_total} \\")
            emit(
                rf"               & Sites      & {_sc_sites('pre', 'session')} & {_sc_sites('post_accept', 'session')} & {_sc_sites('post_reject', 'session')} \\"
            )
        else:
            emit(r"\begin{tabular}{llrr}")
            emit(r"\toprule Type & Metric & Pre & Post-accept \\ \midrule")
            emit(rf"localStorage  & Total keys & {pre_ls_total} & {acc_ls_total} \\")
            emit(rf"              & Sites      & {_sc_sites('pre', 'local')} & {_sc_sites('post_accept', 'local')} \\")
            emit(r"\midrule")
            emit(rf"sessionStorage & Total keys & {pre_ss_total} & {acc_ss_total} \\")
            emit(
                rf"               & Sites      & {_sc_sites('pre', 'session')} & {_sc_sites('post_accept', 'session')} \\"
            )
        emit(r"\bottomrule\end{tabular}")
        emit(r"\end{table}")

        # Category breakdown (localStorage)
        if sc_all_cats:
            for stype, label in [("local", "localStorage"), ("session", "sessionStorage")]:
                pre_t = _sc_total("pre", stype)
                acc_t = _sc_total("post_accept", stype)
                rej_t = _sc_total("post_reject", stype)
                if pre_t + acc_t == 0:
                    continue
                pre_d = sc_cat_by_phase_type.get(("pre", stype), {})
                acc_d = sc_cat_by_phase_type.get(("post_accept", stype), {})
                rej_d = sc_cat_by_phase_type.get(("post_reject", stype), {})
                emit(r"\begin{table}[ht]\centering\footnotesize")
                if has_rej_sc and rej_t:
                    emit(rf"\caption{{{latex_escape(label)} key categories by scan phase}}")
                    emit(r"\begin{tabular}{lrrrrrr}")
                    emit(
                        r"\toprule Category & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} & \multicolumn{2}{c}{Post-reject} \\"
                    )
                    emit(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
                    emit(r"& $n$ & \% & $n$ & \% & $n$ & \% \\ \midrule")
                    for cat in sc_all_cats:
                        pn = pre_d.get(cat, 0)
                        an = acc_d.get(cat, 0)
                        rn = rej_d.get(cat, 0)
                        pp = pn / pre_t * 100 if pre_t else 0
                        ap = an / acc_t * 100 if acc_t else 0
                        rp = rn / rej_t * 100 if rej_t else 0
                        emit(
                            rf"  {latex_escape(cat)} & {pn} & {pp:.0f}\,\% & {an} & {ap:.0f}\,\% & {rn} & {rp:.0f}\,\% \\"
                        )
                    emit(r"\midrule")
                    emit(rf"  \textbf{{Total}} & \textbf{{{pre_t}}} & & \textbf{{{acc_t}}} & & \textbf{{{rej_t}}} & \\")
                else:
                    emit(rf"\caption{{{latex_escape(label)} key categories by scan phase}}")
                    emit(r"\begin{tabular}{lrrrr}")
                    emit(r"\toprule Category & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} \\")
                    emit(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}")
                    emit(r"& $n$ & \% & $n$ & \% \\ \midrule")
                    for cat in sc_all_cats:
                        pn = pre_d.get(cat, 0)
                        an = acc_d.get(cat, 0)
                        pp = pn / pre_t * 100 if pre_t else 0
                        ap = an / acc_t * 100 if acc_t else 0
                        emit(rf"  {latex_escape(cat)} & {pn} & {pp:.0f}\,\% & {an} & {ap:.0f}\,\% \\")
                    emit(r"\midrule")
                    emit(rf"  \textbf{{Total}} & \textbf{{{pre_t}}} & & \textbf{{{acc_t}}} & \\")
                emit(r"\bottomrule\end{tabular}")
                emit(r"\end{table}")

    # ------------------------------------------------------------------ #
    # 7. Accessibility – Chrome (pre vs post-accept vs post-reject)
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Accessibility Metrics (Chrome)}")

    # Base pre/post-accept averages (all detected notices)
    avg = q(
        conn,
        f"""SELECT
             ROUND(AVG(pre_lh_score),1),         ROUND(AVG(post_accept_lh_score),1),
             ROUND(AVG(pre_wave_error),1),        ROUND(AVG(post_accept_wave_error),1),
             ROUND(AVG(pre_wave_contrast),1),     ROUND(AVG(post_accept_wave_contrast),1),
             ROUND(AVG(pre_wave_alert),1),        ROUND(AVG(post_accept_wave_alert),1)
           FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}""",
    )[0]

    # Post-reject averages
    avg_rej = q_safe(
        conn,
        f"""SELECT
             ROUND(AVG(post_reject_lh_score),1),
             ROUND(AVG(post_reject_wave_error),1),
             ROUND(AVG(post_reject_wave_contrast),1),
             ROUND(AVG(post_reject_wave_alert),1)
           FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}
           AND post_reject_lh_score IS NOT NULL""",
    )
    avg_rej = avg_rej[0] if avg_rej else (None, None, None, None)
    has_reject_a11y = avg_rej[0] is not None

    lh_improved = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND post_accept_lh_score > pre_lh_score",
    )[0][0]
    lh_declined = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND post_accept_lh_score < pre_lh_score",
    )[0][0]
    lh_measured = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND pre_lh_score IS NOT NULL AND post_accept_lh_score IS NOT NULL",
    )[0][0]

    emit(
        r"Lighthouse and WAVE accessibility tools were run before accepting, after accepting, "
        + (r"and after rejecting " if has_reject_a11y else r"")
        + r"the cookie notice, allowing a direct comparison of the notice's impact on accessibility. "
        rf"Average Lighthouse scores were \textbf{{{fmt(avg[0])}}} pre-accept and "
        rf"\textbf{{{fmt(avg[1])}}} post-accept across the {cookie_detected} sites with notices. "
        + (rf"The post-reject average was \textbf{{{fmt(avg_rej[0])}}}. " if has_reject_a11y else "")
        + rf"Of the {lh_measured} sites where both pre- and post-accept scores were available, "
        rf"{lh_improved} improved after acceptance and {lh_declined} declined."
    )
    emit()
    emit(
        rf"WAVE reported an average of \textbf{{{fmt(avg[2])}}} errors per page before "
        rf"acceptance and \textbf{{{fmt(avg[3])}}} after"
        + (rf", and \textbf{{{fmt(avg_rej[1])}}} post-reject" if has_reject_a11y else "")
        + r"."
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    if has_reject_a11y:
        emit(r"\caption{Average accessibility metrics --- sites with cookie notices}")
        emit(r"\begin{tabular}{lrrr}")
        emit(r"\toprule Metric & Pre & Post-accept & Post-reject \\ \midrule")
        emit(rf"Lighthouse score & {fmt(avg[0])} & {fmt(avg[1])} & {fmt(avg_rej[0])} \\")
        emit(rf"WAVE errors & {fmt(avg[2])} & {fmt(avg[3])} & {fmt(avg_rej[1])} \\")
        emit(rf"WAVE contrast errors & {fmt(avg[4])} & {fmt(avg[5])} & {fmt(avg_rej[2])} \\")
        emit(rf"WAVE alerts & {fmt(avg[6])} & {fmt(avg[7])} & {fmt(avg_rej[3])} \\")
    else:
        emit(r"\caption{Average accessibility metrics --- sites with cookie notices}")
        emit(r"\begin{tabular}{lrr}")
        emit(r"\toprule Metric & Pre & Post \\ \midrule")
        emit(rf"Lighthouse score & {fmt(avg[0])} & {fmt(avg[1])} \\")
        emit(rf"WAVE errors & {fmt(avg[2])} & {fmt(avg[3])} \\")
        emit(rf"WAVE contrast errors & {fmt(avg[4])} & {fmt(avg[5])} \\")
        emit(rf"WAVE alerts & {fmt(avg[6])} & {fmt(avg[7])} \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    lh_rows = q_safe(
        conn,
        f"""SELECT url, pre_lh_score, post_accept_lh_score, post_reject_lh_score,
                  pre_wave_error, post_accept_wave_error, post_reject_wave_error,
                  pre_wave_contrast, post_accept_wave_contrast, post_reject_wave_contrast
           FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}
           ORDER BY url""",
    )
    if not lh_rows:
        # Fallback for old DBs without reject columns
        lh_rows_base = q(
            conn,
            f"""SELECT url, pre_lh_score, post_accept_lh_score,
                      pre_wave_error, post_accept_wave_error,
                      pre_wave_contrast, post_accept_wave_contrast
               FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}
               ORDER BY url""",
        )
        lh_rows = [
            (url, pre_lh, post_lh, None, pre_we, post_we, None, pre_wc, post_wc, None)
            for url, pre_lh, post_lh, pre_we, post_we, pre_wc, post_wc in lh_rows_base
        ]

    worst_wave = max(lh_rows, key=lambda r: r[4] or 0)
    worst_contrast = max(lh_rows, key=lambda r: r[7] or 0)

    emit(
        rf"The site with the most WAVE errors pre-acceptance was "
        rf"\texttt{{{latex_escape(worst_wave[0])}}} ({fmt(worst_wave[4], 0)} errors). "
        rf"The worst contrast errors were on \texttt{{{latex_escape(worst_contrast[0])}}} "
        rf"({fmt(worst_contrast[7], 0)} contrast errors). "
        r"Per-site figures are in Table~\ref{tab:lh}."
    )
    emit()

    emit_app(r"\begin{table*}[ht]\centering\footnotesize")
    if has_reject_a11y:
        emit_app(r"\caption{Per-site Chrome accessibility (cookie-notice sites)}\label{tab:lh}")
        emit_app(r"\begin{tabular}{>{\ttfamily}p{2cm} r r r r r r r r r}")
        emit_app(r"\toprule")
        emit_app(r"\normalfont URL & \multicolumn{3}{c}{LH} & \multicolumn{3}{c}{Err} & \multicolumn{3}{c}{Con} \\")
        emit_app(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}")
        emit_app(r"\normalfont & Pre & Acc & Rej & Pre & Acc & Rej & Pre & Acc & Rej \\ \midrule")
        for url, pre_lh, post_lh, rej_lh, pre_we, post_we, rej_we, pre_wc, post_wc, rej_wc in lh_rows:
            emit_app(
                rf"  {latex_escape(url)} & "
                rf"{fmt(pre_lh)} & {fmt(post_lh)} & {fmt(rej_lh)} & "
                rf"{fmt(pre_we, 0)} & {fmt(post_we, 0)} & {fmt(rej_we, 0)} & "
                rf"{fmt(pre_wc, 0)} & {fmt(post_wc, 0)} & {fmt(rej_wc, 0)} \\"
            )
    else:
        emit_app(r"\caption{Per-site Chrome accessibility (cookie-notice sites)}\label{tab:lh}")
        emit_app(r"\begin{tabular}{>{\ttfamily}p{2.2cm} r r r r r r}")
        emit_app(r"\toprule")
        emit_app(r"\normalfont URL & \multicolumn{2}{c}{LH} & \multicolumn{2}{c}{Err} & \multicolumn{2}{c}{Con} \\")
        emit_app(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
        emit_app(r"\normalfont & Pre & Post & Pre & Post & Pre & Post \\ \midrule")
        for url, pre_lh, post_lh, rej_lh, pre_we, post_we, rej_we, pre_wc, post_wc, rej_wc in lh_rows:
            emit(
                rf"  {latex_escape(url)} & "
                rf"{fmt(pre_lh)} & {fmt(post_lh)} & "
                rf"{fmt(pre_we, 0)} & {fmt(post_we, 0)} & "
                rf"{fmt(pre_wc, 0)} & {fmt(post_wc, 0)} \\"
            )
    emit_app(r"\bottomrule\end{tabular}")
    emit_app(r"\end{table*}")

    # ------------------------------------------------------------------ #
    # 8. Control options summary (user-facing taxonomy)
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Cookie Notice Control Options and GDPR}")

    ct_rows = q(
        conn,
        f"""SELECT COALESCE(manual_cookie_control_type, cookie_control_type),
                  COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option),
                  COUNT(*)
           FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}
           GROUP BY COALESCE(manual_cookie_control_type, cookie_control_type),
                    COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option)""",
    )
    ct_map = {(ct, em): cnt for ct, em, cnt in ct_rows}  # noqa: F841

    no_notice_chrome = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE (cookie_notice_detected=0 OR NOT {NOT_FP}) AND is_error_page=0",
    )[0][0]

    emit(
        r"Table~\ref{tab:options} classifies all reachable sites by the control options "
        r"offered to visitors, using the taxonomy from the paper. "
        r"The \textbf{GDPR violation} column indicates whether the notice design "
        r"satisfies the GDPR requirement for freely given, unambiguous consent: "
        r"notices that offer no explicit reject path are considered non-compliant."
    )
    emit()

    emit(r"\begin{table*}[t]\centering\footnotesize")
    emit(r"\caption{Cookie notice control options and GDPR compliance.}\label{tab:options}")
    emit(r"\begin{tabular}{llrr} \toprule")
    emit(
        r"  \textbf{Control options} & \textbf{Emphasised option} & \textbf{Sites} & \textbf{GDPR violation} \\ \midrule"
    )
    sorted_ct_rows = sorted(
        ct_rows,
        key=lambda row: (-row[2], str(row[0] or ""), str(row[1] or "")),
    )
    for ctrl, emph, cnt in sorted_ct_rows:
        ctrl_label = latex_escape(ctrl if ctrl is not None else "unknown")
        emph_label = latex_escape(emph if emph is not None else "unknown")
        gdpr_violation = "No" if ctrl in ("accept_or_reject", "accept_reject_or_settings") else "Yes"
        emit(rf"  {ctrl_label} & {emph_label} & {cnt} & {gdpr_violation} \\")
    emit(rf"  \multicolumn{{2}}{{l}}{{(v) No Notice}} & {no_notice_chrome} & Yes \\")
    emit(r"  \bottomrule\end{tabular}")
    emit(r"\end{table*}")

    # ------------------------------------------------------------------ #
    # 9. Accessibility: pre vs post-accept vs post-reject comparison
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Accessibility: Pre-Accept, Post-Accept, and Post-Reject Comparison}")

    pre_post = q(
        conn,
        f"""SELECT
             ROUND(AVG(pre_lh_score),1),         ROUND(AVG(post_accept_lh_score),1),
             ROUND(AVG(pre_wave_error),1),        ROUND(AVG(post_accept_wave_error),1),
             ROUND(AVG(pre_wave_contrast),1),     ROUND(AVG(post_accept_wave_contrast),1),
             ROUND(AVG(pre_wave_alert),1),        ROUND(AVG(post_accept_wave_alert),1),
             COUNT(*)
           FROM chrome_scans
           WHERE cookie_notice_detected=1 AND is_error_page=0 AND {ACCEPTED} AND {NOT_FP}
           AND pre_lh_score IS NOT NULL AND post_accept_lh_score IS NOT NULL""",
    )[0]

    pre_lh, post_lh = pre_post[0], pre_post[1]
    pre_we, post_we = pre_post[2], pre_post[3]
    pre_wc, post_wc = pre_post[4], pre_post[5]
    pre_wa, post_wa = pre_post[6], pre_post[7]
    n_compared = pre_post[8]

    # Post-reject accessibility (sites where reject succeeded)
    post_reject_a11y = q_safe(
        conn,
        f"""SELECT
             ROUND(AVG(post_reject_lh_score),1),
             ROUND(AVG(post_reject_wave_error),1),
             ROUND(AVG(post_reject_wave_contrast),1),
             ROUND(AVG(post_reject_wave_alert),1),
             COUNT(*)
           FROM chrome_scans
           WHERE cookie_notice_detected=1 AND is_error_page=0 AND {NOT_FP}
           AND pre_lh_score IS NOT NULL AND post_reject_lh_score IS NOT NULL""",
    )
    post_reject_a11y = post_reject_a11y[0] if post_reject_a11y else (None,) * 5
    rej_lh, rej_we, rej_wc, rej_wa, n_rej_compared = post_reject_a11y
    has_rej_a11y_cmp = rej_lh is not None

    def delta(a, b):
        if a is None or b is None:
            return "---"
        d = round(b - a, 1)
        return rf"\textbf{{{d:+.1f}}}" if d != 0 else "0.0"

    emit(
        rf"Table~\ref{{tab:a11y}} compares accessibility metrics before and after "
        rf"accepting the cookie notice for the {n_compared} sites where both pre- and "
        r"post-accept Lighthouse scores were available. "
        + (
            rf"Post-reject metrics are shown for the {n_rej_compared} sites where rejection "
            r"succeeded and Lighthouse ran. "
            if has_rej_a11y_cmp
            else ""
        )
        + r"$\Delta$ columns show the change relative to pre-accept."
    )
    emit()

    emit(r"\begin{table*}[t]\centering\footnotesize")
    if has_rej_a11y_cmp:
        emit(
            r"\caption{Mean accessibility metrics. "
            r"LH\,=\,Lighthouse score (0--100); higher is better. "
            r"WAVE metrics: lower is better.}\label{tab:a11y}"
        )
        emit(r"\begin{tabular}{lrrrrr} \toprule")
        emit(
            r"  \textbf{Metric} & \textbf{Pre} & \textbf{Post-acc} & $\Delta$\,\textbf{Acc} & \textbf{Post-rej} & $\Delta$\,\textbf{Rej} \\ \midrule"
        )
        emit(
            rf"  Lighthouse score    & {fmt(pre_lh)} & {fmt(post_lh)} & {delta(pre_lh, post_lh)} & {fmt(rej_lh)} & {delta(pre_lh, rej_lh)} \\"
        )
        emit(
            rf"  WAVE errors         & {fmt(pre_we)} & {fmt(post_we)} & {delta(pre_we, post_we)} & {fmt(rej_we)} & {delta(pre_we, rej_we)} \\"
        )
        emit(
            rf"  WAVE contrast errs  & {fmt(pre_wc)} & {fmt(post_wc)} & {delta(pre_wc, post_wc)} & {fmt(rej_wc)} & {delta(pre_wc, rej_wc)} \\"
        )
        emit(
            rf"  WAVE alerts         & {fmt(pre_wa)} & {fmt(post_wa)} & {delta(pre_wa, post_wa)} & {fmt(rej_wa)} & {delta(pre_wa, rej_wa)} \\"
        )
    else:
        emit(
            rf"\caption{{Mean accessibility metrics across {n_compared} sites"
            r" (cookie-notice sites only)."
            r" LH\,=\,Lighthouse score (0--100); higher is better."
            r" WAVE metrics: lower is better.}\label{tab:a11y}"
        )
        emit(r"\begin{tabular}{lrrr} \toprule")
        emit(r"  \textbf{Metric} & \textbf{Pre-accept} & \textbf{Post-accept} & $\Delta$\,\textbf{Post} \\ \midrule")
        emit(rf"  Lighthouse score    & {fmt(pre_lh)} & {fmt(post_lh)} & {delta(pre_lh, post_lh)} \\")
        emit(rf"  WAVE errors         & {fmt(pre_we)} & {fmt(post_we)} & {delta(pre_we, post_we)} \\")
        emit(rf"  WAVE contrast errs  & {fmt(pre_wc)} & {fmt(post_wc)} & {delta(pre_wc, post_wc)} \\")
        emit(rf"  WAVE alerts         & {fmt(pre_wa)} & {fmt(post_wa)} & {delta(pre_wa, post_wa)} \\")
    emit(r"  \bottomrule\end{tabular}")
    emit(r"\end{table*}")

    # ------------------------------------------------------------------ #
    # 9b. Accessibility: Specific Issues Introduced / Removed
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Accessibility: Specific Issues Changed by Cookie Interaction}")

    _has_wave_issues = bool(q_safe(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='wave_issues'"))
    _has_lh_issues = bool(
        q_safe(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='lighthouse_issues'")
    )

    if not _has_wave_issues and not _has_lh_issues:
        emit(
            r"Detailed issue data not yet available (run postProcessor to populate "
            r"\texttt{wave\_issues} and \texttt{lighthouse\_issues} tables)."
        )
        emit()
    else:
        if _has_wave_issues:
            emit(r"\subsubsection{WAVE Issue Changes}")
            for post_phase, phase_label in [("post_accept", "accept"), ("post_reject", "reject")]:
                wave_added = q_safe(
                    conn,
                    f"""
                    SELECT wi_post.issue_id, wi_post.category,
                           MAX(wi_post.description) AS description, COUNT(*) AS sites
                    FROM wave_issues wi_post
                    JOIN chrome_scans cs ON cs.id = wi_post.scan_id
                    LEFT JOIN wave_issues wi_pre
                           ON wi_pre.scan_id  = wi_post.scan_id
                          AND wi_pre.issue_id = wi_post.issue_id
                          AND wi_pre.phase    = 'pre'
                    WHERE wi_post.phase = '{post_phase}'
                      AND cs.cookie_notice_detected = 1 AND {NOT_FP}
                      AND wi_pre.id IS NULL
                    GROUP BY wi_post.issue_id, wi_post.category
                    ORDER BY sites DESC""",
                )
                wave_removed = q_safe(
                    conn,
                    f"""
                    SELECT wi_pre.issue_id, wi_pre.category,
                           MAX(wi_pre.description) AS description, COUNT(*) AS sites
                    FROM wave_issues wi_pre
                    JOIN chrome_scans cs ON cs.id = wi_pre.scan_id
                    LEFT JOIN wave_issues wi_post
                           ON wi_post.scan_id  = wi_pre.scan_id
                          AND wi_post.issue_id = wi_pre.issue_id
                          AND wi_post.phase    = '{post_phase}'
                    WHERE wi_pre.phase = 'pre'
                      AND cs.cookie_notice_detected = 1 AND {NOT_FP}
                      AND wi_post.id IS NULL
                    GROUP BY wi_pre.issue_id, wi_pre.category
                    ORDER BY sites DESC""",
                )
                if not wave_added and not wave_removed:
                    continue
                emit(
                    rf"\paragraph{{Post-{phase_label} WAVE changes}} "
                    rf"{len(wave_added)} issue type(s) appeared and "
                    rf"{len(wave_removed)} disappeared after {phase_label}ing the cookie notice."
                )
                emit()
                if wave_added:
                    emit(r"\begin{table*}[ht]\centering\footnotesize")
                    emit(rf"\caption{{WAVE issues introduced after {phase_label}ing cookie notice}}")
                    emit(r"\begin{tabular}{llp{5.5cm}r} \toprule")
                    emit(r"Issue ID & Category & Description & Sites \\ \midrule")
                    for issue_id, category, description, sites in wave_added:
                        emit(
                            rf"  \texttt{{{latex_escape(issue_id)}}} & "
                            rf"{latex_escape(category)} & "
                            rf"{latex_escape(description or '')} & {sites} \\"
                        )
                    emit(r"\bottomrule\end{tabular}")
                    emit(r"\end{table*}")
                if wave_removed:
                    emit(r"\begin{table*}[ht]\centering\footnotesize")
                    emit(rf"\caption{{WAVE issues removed after {phase_label}ing cookie notice}}")
                    emit(r"\begin{tabular}{llp{5.5cm}r} \toprule")
                    emit(r"Issue ID & Category & Description & Sites \\ \midrule")
                    for issue_id, category, description, sites in wave_removed:
                        emit(
                            rf"  \texttt{{{latex_escape(issue_id)}}} & "
                            rf"{latex_escape(category)} & "
                            rf"{latex_escape(description or '')} & {sites} \\"
                        )
                    emit(r"\bottomrule\end{tabular}")
                    emit(r"\end{table*}")

        if _has_lh_issues:
            emit(r"\subsubsection{Lighthouse Audit Changes}")
            for post_phase, phase_label in [("post_accept", "accept"), ("post_reject", "reject")]:
                lh_added = q_safe(
                    conn,
                    f"""
                    SELECT lhi_post.audit_id, MAX(lhi_post.title) AS title, COUNT(*) AS sites
                    FROM lighthouse_issues lhi_post
                    JOIN chrome_scans cs ON cs.id = lhi_post.scan_id
                    LEFT JOIN lighthouse_issues lhi_pre
                           ON lhi_pre.scan_id  = lhi_post.scan_id
                          AND lhi_pre.audit_id = lhi_post.audit_id
                          AND lhi_pre.phase    = 'pre'
                    WHERE lhi_post.phase = '{post_phase}'
                      AND cs.cookie_notice_detected = 1 AND {NOT_FP}
                      AND lhi_pre.id IS NULL
                    GROUP BY lhi_post.audit_id
                    ORDER BY sites DESC""",
                )
                lh_removed = q_safe(
                    conn,
                    f"""
                    SELECT lhi_pre.audit_id, MAX(lhi_pre.title) AS title, COUNT(*) AS sites
                    FROM lighthouse_issues lhi_pre
                    JOIN chrome_scans cs ON cs.id = lhi_pre.scan_id
                    LEFT JOIN lighthouse_issues lhi_post
                           ON lhi_post.scan_id  = lhi_pre.scan_id
                          AND lhi_post.audit_id = lhi_pre.audit_id
                          AND lhi_post.phase    = '{post_phase}'
                    WHERE lhi_pre.phase = 'pre'
                      AND cs.cookie_notice_detected = 1 AND {NOT_FP}
                      AND lhi_post.id IS NULL
                    GROUP BY lhi_pre.audit_id
                    ORDER BY sites DESC""",
                )
                if not lh_added and not lh_removed:
                    continue
                emit(
                    rf"\paragraph{{Post-{phase_label} Lighthouse changes}} "
                    rf"{len(lh_added)} audit(s) newly failed and "
                    rf"{len(lh_removed)} stopped failing after {phase_label}ing the cookie notice."
                )
                emit()
                if lh_added:
                    emit(r"\begin{table*}[ht]\centering\footnotesize")
                    emit(rf"\caption{{Lighthouse audits newly failing after {phase_label}ing cookie notice}}")
                    emit(r"\begin{tabular}{lp{7.5cm}r} \toprule")
                    emit(r"Audit ID & Title & Sites \\ \midrule")
                    for audit_id, title, sites in lh_added:
                        emit(
                            rf"  \texttt{{{latex_escape(audit_id)}}} & "
                            rf"{latex_escape(title or '')} & {sites} \\"
                        )
                    emit(r"\bottomrule\end{tabular}")
                    emit(r"\end{table*}")
                if lh_removed:
                    emit(r"\begin{table*}[ht]\centering\footnotesize")
                    emit(rf"\caption{{Lighthouse audits no longer failing after {phase_label}ing cookie notice}}")
                    emit(r"\begin{tabular}{lp{7.5cm}r} \toprule")
                    emit(r"Audit ID & Title & Sites \\ \midrule")
                    for audit_id, title, sites in lh_removed:
                        emit(
                            rf"  \texttt{{{latex_escape(audit_id)}}} & "
                            rf"{latex_escape(title or '')} & {sites} \\"
                        )
                    emit(r"\bottomrule\end{tabular}")
                    emit(r"\end{table*}")

    # ------------------------------------------------------------------ #
    # 10. Post-Reject Analysis
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Post-Reject Analysis}")

    reject_attempted_total = q_safe(
        conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_reject_attempted=1 AND {NOT_FP}"
    )
    reject_attempted_total = reject_attempted_total[0][0] if reject_attempted_total else 0

    reject_succeeded_total = q_safe(
        conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_rejected=1 AND {NOT_FP}"
    )
    reject_succeeded_total = reject_succeeded_total[0][0] if reject_succeeded_total else 0

    reject_rate = reject_succeeded_total / reject_attempted_total * 100 if reject_attempted_total else 0

    emit(
        rf"Of the {cookie_detected} sites where a cookie notice was detected, "
        rf"rejection was attempted on \textbf{{{reject_attempted_total}}} sites. "
        rf"The reject button was successfully clicked and confirmed dismissed on "
        rf"\textbf{{{reject_succeeded_total}}} of these "
        rf"({reject_rate:.0f}\,\%). "
        r"The remaining sites either had no visible reject button, required a multi-step "
        r"flow that could not be resolved, or the banner remained visible after clicking."
    )
    emit()

    # Per-site reject outcome table
    reject_site_rows = q_safe(  # noqa: F841
        conn,
        f"""SELECT url, cookie_reject_attempted, cookie_notice_rejected,
                   post_reject_lh_score, post_reject_wave_error,
                   post_reject_cookies_path
            FROM chrome_scans
            WHERE cookie_reject_attempted=1 AND {NOT_FP}
            ORDER BY url""",
    )

    # emit(r"\begin{table}[ht]\centering\footnotesize")
    # emit(r"\caption{Per-site reject outcomes}\label{tab:reject_sites}")
    # emit(r"\begin{tabular}{>{\ttfamily}p{2.8cm} c c r r r}")
    # emit(r"\toprule \normalfont URL & Attempted & Rejected & LH (post-rej) & WAVE err & Cookies \\ \midrule")
    # for url, attempted, rejected, lh, wave_err, ck_path in reject_site_rows:
    #     ck_count = count_cookies_from_path(ck_path)
    #     emit(
    #         rf"  {latex_escape(url)} & "
    #         rf"{'Yes' if attempted else 'No'} & "
    #         rf"{'Yes' if rejected else 'No'} & "
    #         rf"{fmt(lh)} & "
    #         rf"{fmt(wave_err, 0)} & "
    #         rf"{ck_count if ck_count is not None else '---'} \\"
    #     )
    # emit(r"\bottomrule\end{tabular}")
    # emit(r"\end{table}")

    # Cookie counts: pre / post-accept / post-reject comparison
    emit(r"\subsubsection{Cookie Counts: Pre, Post-Accept, and Post-Reject}")

    rej_cookie_rows = q_safe(
        conn,
        f"""SELECT url, pre_cookies_path, post_accept_cookies_path, post_reject_cookies_path
            FROM chrome_scans
            WHERE cookie_notice_rejected=1 AND {NOT_FP}
            ORDER BY url""",
    )
    rej_cookie_data = [
        (url, count_cookies_from_path(pre_p), count_cookies_from_path(acc_p), count_cookies_from_path(rej_p))
        for url, pre_p, acc_p, rej_p in rej_cookie_rows
    ]
    rej_cookie_valid = [
        (url, pre, acc, rej) for url, pre, acc, rej in rej_cookie_data if pre is not None and rej is not None
    ]

    if rej_cookie_valid:
        avg_pre_ck = sum(r[1] for r in rej_cookie_valid) / len(rej_cookie_valid)
        avg_acc_ck = sum(r[2] for r in rej_cookie_valid if r[2] is not None) / max(
            1, sum(1 for r in rej_cookie_valid if r[2] is not None)
        )
        avg_rej_ck = sum(r[3] for r in rej_cookie_valid) / len(rej_cookie_valid)
        ck_reduced = sum(1 for _, pre, _, rej in rej_cookie_valid if rej < pre)
        ck_same = sum(1 for _, pre, _, rej in rej_cookie_valid if rej == pre)
        ck_increased = sum(1 for _, pre, _, rej in rej_cookie_valid if rej > pre)

        emit(
            rf"For the {len(rej_cookie_valid)} sites where rejection succeeded and cookie "
            r"files were captured, the average cookie count was "
            rf"\textbf{{{avg_pre_ck:.1f}}} before interaction, "
            rf"\textbf{{{avg_acc_ck:.1f}}} post-accept (where measured), and "
            rf"\textbf{{{avg_rej_ck:.1f}}} post-reject. "
            rf"Rejecting reduced the cookie count on {ck_reduced} sites, "
            rf"left it unchanged on {ck_same}, and increased it on {ck_increased}."
        )
        emit()

        emit_app(r"\begin{table*}[ht]\centering\footnotesize")
        emit_app(r"\caption{Cookie counts on successfully rejected sites}\label{tab:reject_cookies}")
        emit_app(r"\begin{tabular}{>{\ttfamily}p{2.8cm} r r r r r}")
        emit_app(r"\toprule \normalfont URL & Pre & Post-acc & Post-rej & $\Delta$\,acc & $\Delta$\,rej \\ \midrule")
        for url, pre, acc, rej in sorted(rej_cookie_data, key=lambda r: r[0]):
            pre_s = str(pre) if pre is not None else "---"
            acc_s = str(acc) if acc is not None else "---"
            rej_s = str(rej) if rej is not None else "---"
            d_acc = (
                (rf"\textbf{{{acc - pre:+d}}}" if acc - pre != 0 else "0")
                if pre is not None and acc is not None
                else "---"
            )
            d_rej = (
                (rf"\textbf{{{rej - pre:+d}}}" if rej - pre != 0 else "0")
                if pre is not None and rej is not None
                else "---"
            )
            emit_app(rf"  {latex_escape(url)} & {pre_s} & {acc_s} & {rej_s} & {d_acc} & {d_rej} \\")
        emit_app(r"\bottomrule\end{tabular}")
        emit_app(r"\end{table*}")
    else:
        emit(r"Cookie count files were not available for the rejected sites in this environment.")
        emit()

    # Network requests: pre vs post-reject
    emit(r"\subsubsection{Network Requests: Pre vs Post-Reject}")

    req_phase_rows = q_safe(
        conn,
        f"""SELECT c.url,
               SUM(CASE WHEN r.phase='pre'         THEN 1 ELSE 0 END) AS pre_total,
               SUM(CASE WHEN r.phase='post_reject' THEN 1 ELSE 0 END) AS rej_total
               {", SUM(CASE WHEN r.phase='pre'         AND r.is_tracker=1 THEN 1 ELSE 0 END)," if has_tracker_col else ""}
               {" SUM(CASE WHEN r.phase='post_reject' AND r.is_tracker=1 THEN 1 ELSE 0 END)" if has_tracker_col else ""}
            FROM chrome_scans c
            JOIN chrome_network_requests r ON c.id = r.scan_id
            WHERE c.cookie_notice_rejected=1 AND c.{NOT_FP.strip("()")}
            GROUP BY c.url
            ORDER BY c.url""",
    )

    # Simpler query without f-string injection for the conditional tracker columns
    if has_tracker_col:
        req_phase_rows = q_safe(
            conn,
            """SELECT c.url,
                   SUM(CASE WHEN r.phase='pre'         THEN 1 ELSE 0 END),
                   SUM(CASE WHEN r.phase='post_reject' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN r.phase='pre'         AND r.is_tracker=1 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN r.phase='post_reject' AND r.is_tracker=1 THEN 1 ELSE 0 END)
                FROM chrome_scans c
                JOIN chrome_network_requests r ON c.id = r.scan_id
                WHERE c.cookie_notice_rejected=1 AND (false_positive IS NULL OR false_positive=0)
                GROUP BY c.url ORDER BY c.url""",
        )
    else:
        req_phase_rows = q_safe(
            conn,
            """SELECT c.url,
                   SUM(CASE WHEN r.phase='pre'         THEN 1 ELSE 0 END),
                   SUM(CASE WHEN r.phase='post_reject' THEN 1 ELSE 0 END),
                   NULL, NULL
                FROM chrome_scans c
                JOIN chrome_network_requests r ON c.id = r.scan_id
                WHERE c.cookie_notice_rejected=1 AND (false_positive IS NULL OR false_positive=0)
                GROUP BY c.url ORDER BY c.url""",
        )

    if req_phase_rows:
        avg_pre_req = sum((r[1] or 0) for r in req_phase_rows) / len(req_phase_rows)
        avg_rej_req = sum((r[2] or 0) for r in req_phase_rows) / len(req_phase_rows)
        req_reduced = sum(1 for r in req_phase_rows if (r[2] or 0) < (r[1] or 0))

        tracker_note = ""
        if has_tracker_col:
            avg_pre_tr = sum((r[3] or 0) for r in req_phase_rows) / len(req_phase_rows)
            avg_rej_tr = sum((r[4] or 0) for r in req_phase_rows) / len(req_phase_rows)
            tracker_note = (
                rf" Of these, an average of \textbf{{{avg_pre_tr:.1f}}} were tracker requests "
                rf"pre-reject, falling to \textbf{{{avg_rej_tr:.1f}}} post-reject."
            )

        emit(
            rf"Across the {len(req_phase_rows)} successfully rejected sites, "
            rf"an average of \textbf{{{avg_pre_req:.1f}}} network requests were made in the "
            rf"pre-reject phase and \textbf{{{avg_rej_req:.1f}}} in the post-reject phase."
            + tracker_note
            + rf" Request counts decreased on {req_reduced} of {len(req_phase_rows)} sites after rejection."
        )
        emit()

        emit_app(r"\begin{table*}[ht]\centering\footnotesize")
        if has_tracker_col:
            emit_app(r"\caption{Network requests on rejected sites (pre vs post-reject)}\label{tab:reject_requests}")
            emit_app(r"\begin{tabular}{>{\ttfamily}p{2.5cm} r r r r r r}")
            emit_app(
                r"\toprule \normalfont URL & \multicolumn{2}{c}{Total req.} & \multicolumn{2}{c}{Trackers} & \multicolumn{2}{c}{$\Delta$} \\"
            )
            emit_app(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
            emit_app(r"\normalfont & Pre & Post-rej & Pre & Post-rej & Total & Tracker \\ \midrule")
            for url, pre_r, rej_r, pre_t, rej_t in req_phase_rows:
                d_total = (
                    (rf"\textbf{{{(rej_r or 0) - (pre_r or 0):+d}}}" if (rej_r or 0) != (pre_r or 0) else "0")
                    if pre_r is not None and rej_r is not None
                    else "---"
                )
                d_tracker = (
                    (rf"\textbf{{{(rej_t or 0) - (pre_t or 0):+d}}}" if (rej_t or 0) != (pre_t or 0) else "0")
                    if pre_t is not None and rej_t is not None
                    else "---"
                )
                emit_app(
                    rf"  {latex_escape(url)} & "
                    rf"{pre_r or '---'} & {rej_r or '---'} & "
                    rf"{pre_t or '---'} & {rej_t or '---'} & "
                    rf"{d_total} & {d_tracker} \\"
                )
        else:
            emit_app(r"\caption{Network requests on rejected sites (pre vs post-reject)}\label{tab:reject_requests}")
            emit_app(r"\begin{tabular}{>{\ttfamily}p{3cm} r r r}")
            emit_app(r"\toprule \normalfont URL & Pre & Post-rej & $\Delta$ \\ \midrule")
            for url, pre_r, rej_r, _, _ in req_phase_rows:
                d = (
                    (rf"\textbf{{{(rej_r or 0) - (pre_r or 0):+d}}}" if (rej_r or 0) != (pre_r or 0) else "0")
                    if pre_r is not None and rej_r is not None
                    else "---"
                )
                emit_app(rf"  {latex_escape(url)} & {pre_r or '---'} & {rej_r or '---'} & {d} \\")
        emit_app(r"\bottomrule\end{tabular}")
        emit_app(r"\end{table*}")
    else:
        emit_app(r"No network request data was found for the post-reject phase.")
        emit_app()

    # Accessibility: pre vs post-reject
    emit(r"\subsubsection{Accessibility: Pre vs Post-Reject}")

    rej_a11y_rows = q_safe(
        conn,
        f"""SELECT url,
               pre_lh_score, post_reject_lh_score,
               pre_wave_error, post_reject_wave_error,
               pre_wave_contrast, post_reject_wave_contrast,
               pre_wave_alert, post_reject_wave_alert
            FROM chrome_scans
            WHERE cookie_notice_rejected=1 AND {NOT_FP}
            AND post_reject_lh_score IS NOT NULL
            ORDER BY url""",
    )

    if rej_a11y_rows:
        avg_pre_lh_r = sum((r[1] or 0) for r in rej_a11y_rows) / len(rej_a11y_rows)
        avg_rej_lh_r = sum((r[2] or 0) for r in rej_a11y_rows) / len(rej_a11y_rows)
        avg_pre_we_r = sum((r[3] or 0) for r in rej_a11y_rows) / len(rej_a11y_rows)
        avg_rej_we_r = sum((r[4] or 0) for r in rej_a11y_rows) / len(rej_a11y_rows)
        lh_improved_r = sum(1 for r in rej_a11y_rows if (r[2] or 0) > (r[1] or 0))
        lh_declined_r = sum(1 for r in rej_a11y_rows if (r[2] or 0) < (r[1] or 0))

        emit(
            rf"Lighthouse and WAVE metrics were captured for {len(rej_a11y_rows)} sites "
            r"where rejection succeeded. "
            rf"The average Lighthouse score was \textbf{{{avg_pre_lh_r:.1f}}} before "
            rf"and \textbf{{{avg_rej_lh_r:.1f}}} after rejection. "
            rf"Scores improved on {lh_improved_r} sites and declined on {lh_declined_r}. "
            rf"Average WAVE errors moved from \textbf{{{avg_pre_we_r:.1f}}} to "
            rf"\textbf{{{avg_rej_we_r:.1f}}}."
        )
        emit()

        emit_app(r"\begin{table*}[ht]\centering\footnotesize")
        emit_app(r"\caption{Per-site accessibility metrics before and after rejection}\label{tab:reject_a11y}")
        emit_app(r"\begin{tabular}{>{\ttfamily}p{2.5cm} r r r r r r r r}")
        emit_app(
            r"\toprule \normalfont URL & \multicolumn{3}{c}{Lighthouse} & \multicolumn{3}{c}{WAVE err} & \multicolumn{2}{c}{Contrast} \\"
        )
        emit_app(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-9}")
        emit_app(r"\normalfont & Pre & Post-rej & $\Delta$ & Pre & Post-rej & $\Delta$ & Pre & Post-rej \\ \midrule")
        for url, pre_lh, rej_lh, pre_we, rej_we, pre_wc, rej_wc, pre_wa, rej_wa in rej_a11y_rows:
            d_lh = delta(pre_lh, rej_lh)
            d_we = delta(pre_we, rej_we)
            emit_app(
                rf"  {latex_escape(url)} & "
                rf"{fmt(pre_lh)} & {fmt(rej_lh)} & {d_lh} & "
                rf"{fmt(pre_we, 0)} & {fmt(rej_we, 0)} & {d_we} & "
                rf"{fmt(pre_wc, 0)} & {fmt(rej_wc, 0)} \\"
            )
        emit_app(r"\bottomrule\end{tabular}")
        emit_app(r"\end{table*}")
    else:
        emit_app(r"No post-reject Lighthouse data was available for this dataset.")
        emit_app()

    # ------------------------------------------------------------------ #
    # 11. Web Storage: localStorage and sessionStorage
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Web Storage: localStorage and sessionStorage}")

    storage_rows = q_safe(
        conn,
        f"""SELECT url, pre_storage_path, post_accept_storage_path, post_reject_storage_path
            FROM chrome_scans WHERE is_error_page=0 AND {NOT_FP}
            ORDER BY url""",
    )

    storage_data = []
    for url, pre_p, acc_p, rej_p in storage_rows:
        pre_ls, pre_ss = read_storage_from_path(pre_p)
        acc_ls, acc_ss = read_storage_from_path(acc_p)
        rej_ls, rej_ss = read_storage_from_path(rej_p)
        storage_data.append((url, pre_ls, pre_ss, acc_ls, acc_ss, rej_ls, rej_ss))

    measured_s = [
        (u, pls, pss, als, ass_, rls, rss) for u, pls, pss, als, ass_, rls, rss in storage_data if pls is not None
    ]
    acc_s = [(u, pls, pss, als, ass_, rls, rss) for u, pls, pss, als, ass_, rls, rss in measured_s if als is not None]
    rej_s = [(u, pls, pss, als, ass_, rls, rss) for u, pls, pss, als, ass_, rls, rss in measured_s if rls is not None]

    if measured_s:
        avg_pre_ls = sum(r[1] for r in measured_s) / len(measured_s)
        avg_pre_ss = sum(r[2] for r in measured_s) / len(measured_s)
        avg_acc_ls = sum(r[3] for r in acc_s) / len(acc_s) if acc_s else None
        avg_acc_ss = sum(r[4] for r in acc_s) / len(acc_s) if acc_s else None
        avg_rej_ls = sum(r[5] for r in rej_s) / len(rej_s) if rej_s else None
        avg_rej_ss = sum(r[6] for r in rej_s) / len(rej_s) if rej_s else None

        ls_increased_acc = sum(1 for r in acc_s if r[3] > r[1])
        ls_reduced_acc = sum(1 for r in acc_s if r[3] < r[1])
        ls_increased_rej = sum(1 for r in rej_s if r[5] > r[1])
        ls_reduced_rej = sum(1 for r in rej_s if r[5] < r[1])

        emit(
            rf"Web storage was captured for {len(measured_s)} sites. "
            rf"Before any cookie interaction, sites had an average of "
            rf"\textbf{{{avg_pre_ls:.1f}}} localStorage entries and "
            rf"\textbf{{{avg_pre_ss:.1f}}} sessionStorage entries. "
        )
        if avg_acc_ls is not None:
            emit(
                rf"After accepting the cookie notice ({len(acc_s)} sites), "
                rf"localStorage averaged \textbf{{{avg_acc_ls:.1f}}} entries "
                rf"({ls_increased_acc} sites increased, {ls_reduced_acc} decreased) "
                rf"and sessionStorage averaged \textbf{{{avg_acc_ss:.1f}}} entries. "
            )
        if avg_rej_ls is not None:
            emit(
                rf"After rejecting ({len(rej_s)} sites), "
                rf"localStorage averaged \textbf{{{avg_rej_ls:.1f}}} entries "
                rf"({ls_increased_rej} sites increased, {ls_reduced_rej} decreased) "
                rf"and sessionStorage averaged \textbf{{{avg_rej_ss:.1f}}} entries."
            )
        emit()

        # Summary averages table
        emit(r"\begin{table}[ht]\centering\footnotesize")
        emit(r"\caption{Average web storage entry counts per site}\label{tab:storage_avg}")
        if rej_s:
            emit(r"\begin{tabular}{lrrr}")
            emit(r"\toprule Metric & Pre & Post-accept & Post-reject \\ \midrule")
            emit(rf"Avg.\ localStorage entries  & {avg_pre_ls:.1f} & {fmt(avg_acc_ls)} & {fmt(avg_rej_ls)} \\")
            emit(rf"Avg.\ sessionStorage entries & {avg_pre_ss:.1f} & {fmt(avg_acc_ss)} & {fmt(avg_rej_ss)} \\")
            emit(rf"Sites measured              & {len(measured_s)} & {len(acc_s)} & {len(rej_s)} \\")
        else:
            emit(r"\begin{tabular}{lrr}")
            emit(r"\toprule Metric & Pre & Post-accept \\ \midrule")
            emit(rf"Avg.\ localStorage entries  & {avg_pre_ls:.1f} & {fmt(avg_acc_ls)} \\")
            emit(rf"Avg.\ sessionStorage entries & {avg_pre_ss:.1f} & {fmt(avg_acc_ss)} \\")
            emit(rf"Sites measured              & {len(measured_s)} & {len(acc_s)} \\")
        emit(r"\bottomrule\end{tabular}")
        emit(r"\end{table}")

        # Per-site table
        emit_app(r"\begin{table*}[ht]\centering\footnotesize")
        if rej_s:
            emit_app(r"\caption{Per-site web storage entry counts}\label{tab:storage_persite}")
            emit_app(r"\begin{tabular}{>{\ttfamily}p{2.4cm} r r r r r r r r}")
            emit_app(
                r"\toprule \normalfont URL & \multicolumn{3}{c}{localStorage} & \multicolumn{3}{c}{sessionStorage} & \multicolumn{2}{c}{$\Delta$\,LS} \\"
            )
            emit_app(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-9}")
            emit_app(r"\normalfont & Pre & Acc & Rej & Pre & Acc & Rej & vs\,Acc & vs\,Rej \\ \midrule")
            for url, pls, pss, als, ass_, rls, rss in sorted(storage_data, key=lambda r: r[0]):

                def _s(v):
                    return str(v) if v is not None else "---"

                def _d(a, b):
                    if a is None or b is None:
                        return "---"
                    d = b - a
                    return rf"\textbf{{{d:+d}}}" if d != 0 else "0"

                emit_app(
                    rf"  {latex_escape(url)} & "
                    rf"{_s(pls)} & {_s(als)} & {_s(rls)} & "
                    rf"{_s(pss)} & {_s(ass_)} & {_s(rss)} & "
                    rf"{_d(pls, als)} & {_d(pls, rls)} \\"
                )
        else:
            emit_app(r"\caption{Per-site web storage entry counts}\label{tab:storage_persite}")
            emit_app(r"\begin{tabular}{>{\ttfamily}p{2.8cm} r r r r r r}")
            emit_app(
                r"\toprule \normalfont URL & \multicolumn{2}{c}{localStorage} & \multicolumn{2}{c}{sessionStorage} & \multicolumn{2}{c}{$\Delta$\,LS} \\"
            )
            emit_app(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
            emit_app(r"\normalfont & Pre & Acc & Pre & Acc & $\Delta$\,LS & $\Delta$\,SS \\ \midrule")
            for url, pls, pss, als, ass_, rls, rss in sorted(storage_data, key=lambda r: r[0]):

                def _s(v):
                    return str(v) if v is not None else "---"

                def _d(a, b):
                    if a is None or b is None:
                        return "---"
                    d = b - a
                    return rf"\textbf{{{d:+d}}}" if d != 0 else "0"

                emit_app(
                    rf"  {latex_escape(url)} & "
                    rf"{_s(pls)} & {_s(als)} & "
                    rf"{_s(pss)} & {_s(ass_)} & "
                    rf"{_d(pls, als)} & {_d(pss, ass_)} \\"
                )
        emit_app(r"\bottomrule\end{tabular}")
        emit_app(r"\end{table*}")
    else:
        emit_app(
            r"Web storage files were not available for this dataset. "
            r"Ensure the \texttt{artifacts/} directory is accessible."
        )
        emit()

    # ------------------------------------------------------------------ #
    # 12. Screen Reader Accessibility Metrics
    # ------------------------------------------------------------------ #
    _has_srm = bool(q_safe(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='screen_reader_metrics'"))

    if _has_srm:
        emit(r"\subsection{Screen Reader Accessibility Metrics}")

        # Labels for each metric column, matching the paper criteria numbering.
        srm_metrics = [
            ("metric_readable", r"(i) Readable"),
            ("metric_immediately_read", r"(ii) Immediately Read"),
            ("metric_keyboard_nav", r"(iii) Keyboard Navigable"),
            ("metric_link_purpose", r"(iv) Link or Button Purpose"),
            ("metric_abbreviations", r"(v) Abbreviations Explained"),
            ("metric_page_titled", r"(vi) Page Titled"),
            ("metric_notice_titled", r"(vii) Cookie Notice Titled"),
            ("metric_headings_useful", r"(viii) Headings Useful"),
        ]

        srm_total = q_safe(conn, "SELECT COUNT(*) FROM screen_reader_metrics")[0][0]
        srm_with_notice = q_safe(conn, "SELECT COUNT(*) FROM screen_reader_metrics WHERE metric_readable != -1")
        srm_with_notice = srm_with_notice[0][0] if srm_with_notice else 0

        emit(
            rf"Each of the {srm_total} scanned sites was evaluated against nine "
            r"screen reader accessibility criteria. "
            rf"Metrics scoped to the cookie notice (i)--(v), (vii)--(viii) are marked "
            r"\textit{N/A} for the "
            rf"{srm_total - srm_with_notice} sites where no cookie notice was detected. "
            # r"Metric (vi) Page Titled and (ix) No Timing apply to all sites. "
            r"Pass rates below are computed over sites where the metric was applicable."
        )
        emit()

        # Per-metric pass/fail/N/A counts
        col_parts = ", ".join(
            f"SUM(CASE WHEN {col}=1 THEN 1 ELSE 0 END), "
            f"SUM(CASE WHEN {col}=0 THEN 1 ELSE 0 END), "
            f"SUM(CASE WHEN {col}=-1 THEN 1 ELSE 0 END)"
            for col, _ in srm_metrics
        )
        srm_row = q_safe(conn, f"SELECT {col_parts} FROM screen_reader_metrics")
        srm_row = srm_row[0] if srm_row else ([0] * (len(srm_metrics) * 3))

        # Build list of (label, pass, fail, na, rate)
        srm_data = []
        for i, (_, label) in enumerate(srm_metrics):
            p = srm_row[i * 3] or 0
            f_ = srm_row[i * 3 + 1] or 0
            na = srm_row[i * 3 + 2] or 0
            applicable = p + f_
            rate = f"{p / applicable * 100:.0f}\\,\\%" if applicable else "---"
            srm_data.append((label, p, f_, na, rate))

        emit(r"\begin{table}[ht]\centering\footnotesize")
        emit(r"\caption{Screen reader accessibility metric results}\label{tab:srm}")
        emit(r"\begin{tabular}{lrrrr}")
        emit(r"\toprule Metric & Pass & Fail & N/A & Pass rate \\ \midrule")
        for label, p, f_, na, rate in srm_data:
            emit(rf"  {label} & {p} & {f_} & {na} & {rate} \\")
        emit(r"\bottomrule\end{tabular}")
        emit(r"\end{table}")

        # Brief narrative: highlight best and worst metric
        applicable_metrics = [(label, p, f_) for label, p, f_, na, rate in srm_data if p + f_ > 0]
        if applicable_metrics:
            best = max(applicable_metrics, key=lambda r: r[1] / (r[1] + r[2]))
            worst = min(applicable_metrics, key=lambda r: r[1] / (r[1] + r[2]))
            best_rate = best[1] / (best[1] + best[2]) * 100
            worst_rate = worst[1] / (worst[1] + worst[2]) * 100
            emit(
                rf"The highest pass rate was for \textbf{{{best[0]}}} "
                rf"({best[1]} of {best[1] + best[2]} applicable sites, {best_rate:.0f}\,\%). "
                rf"The lowest was \textbf{{{worst[0]}}} "
                rf"({worst[1]} of {worst[1] + worst[2]} applicable sites, {worst_rate:.0f}\,\%)."
            )
            emit()

        # ---- Immediately-read distance analysis ----
        if col_exists(conn, "screen_reader_metrics", "immediately_read_distance"):
            dist_rows = q_safe(
                conn,
                "SELECT immediately_read_distance FROM screen_reader_metrics "
                "WHERE immediately_read_distance IS NOT NULL ORDER BY immediately_read_distance",
            )
            distances = [r[0] for r in dist_rows]
            if distances:
                n_dist = len(distances)
                mean_d = sum(distances) / n_dist
                mid = n_dist // 2
                median_d = distances[mid] if n_dist % 2 else (distances[mid - 1] + distances[mid]) / 2

                buckets = [
                    (r"$= 0$", sum(1 for d in distances if d == 0)),
                    (r"$1$--$10$", sum(1 for d in distances if 1 <= d <= 10)),
                    (r"$11$--$30$", sum(1 for d in distances if 11 <= d <= 30)),
                    (r"$31$--$100$", sum(1 for d in distances if 31 <= d <= 100)),
                    (r"$> 100$", sum(1 for d in distances if d > 100)),
                ]

                emit(r"\subsubsection{Immediately Read: Word Distance Analysis}")
                emit(
                    rf"Among the {n_dist} sites where a cookie-related term was found in the "
                    r"NVDA transcript, the median number of words appearing before that term "
                    rf"was \textbf{{{median_d:.0f}}} (mean: {mean_d:.1f}). "
                    r"Table~\ref{tab:ird} shows the distribution across distance bands; "
                    r"sites in the first three bands (0--30 words) pass criterion~(ii)."
                )
                emit()
                emit(r"\begin{table}[ht]\centering\footnotesize")
                emit(r"\caption{Distribution of word distance before first cookie keyword}\label{tab:ird}")
                emit(r"\begin{tabular}{lrr}")
                emit(r"\toprule Words before cookie keyword & Sites & \% \\ \midrule")
                for label, count in buckets:
                    pct = f"{count / n_dist * 100:.0f}\\,\\%" if n_dist else "---"
                    emit(rf"  {label} & {count} & {pct} \\")
                emit(r"\midrule")
                passing = sum(1 for d in distances if d <= 30)
                emit(
                    rf"  \textbf{{Pass ($\leq 30$)}} & \textbf{{{passing}}} "
                    rf"& \textbf{{{passing / n_dist * 100:.0f}\,\%}} \\"
                )
                emit(r"\bottomrule\end{tabular}")
                emit(r"\end{table}")

    return "\n".join(lines), "\n".join(appendix_lines)


def main():
    base = Path(__file__).resolve().parent.parent
    db_names = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_DBS

    for db_name in db_names:
        db_path = base / db_name
        if not db_path.exists():
            print(f"Skipping {db_name}: file not found")
            continue
        stem = db_path.stem.replace("-", "_")
        out_path = base / f"{stem}.tex"
        appendix_path = base / f"{stem}_appendix.tex"

        conn = sqlite3.connect(db_path)
        try:
            tex, appendix_tex = build_report(conn)
        finally:
            conn.close()

        out_path.write_text(tex, encoding="utf-8")
        print(f"Written: {out_path}")
        appendix_path.write_text(appendix_tex, encoding="utf-8")
        print(f"Written: {appendix_path}")


if __name__ == "__main__":
    main()
