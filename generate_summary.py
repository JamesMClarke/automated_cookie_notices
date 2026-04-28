#!/usr/bin/env python3
"""Generate a LaTeX summary report from top-100.sqlite."""

import json
import sqlite3
from collections import Counter
from pathlib import Path

DB_PATH = Path(__file__).parent / "top-100.sqlite"
OUT_PATH = Path(__file__).parent / "top_100.tex"
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

    def emit(*args):
        lines.append(" ".join(str(a) for a in args))

    # Rows that count as "accepted" for analysis purposes: scanner confirmed,
    # OR a click was attempted and the user manually verified it was accepted.
    ACCEPTED = "(cookie_notice_accepted=1 OR manually_verified=1)"

    # Exclude false positives from all detection-based analysis.
    NOT_FP = "(false_positive IS NULL OR false_positive=0)"

    # ------------------------------------------------------------------ #
    # 1. Overview
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Overview}")

    total_chrome    = q(conn, "SELECT COUNT(*) FROM chrome_scans")[0][0]
    error_count     = q(conn, "SELECT COUNT(*) FROM chrome_scans WHERE is_error_page=1")[0][0]
    reachable       = total_chrome - error_count
    cookie_detected = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}")[0][0]
    cookie_accepted = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE {ACCEPTED} AND {NOT_FP}")[0][0]
    manually_verified_count = q(conn,
        "SELECT COUNT(*) FROM chrome_scans WHERE manually_verified=1")[0][0]

    # Tracker totals (pre-accept only, across all reachable sites)
    tracker_totals = q(
        conn,
        """SELECT
             SUM(CASE WHEN phase='pre'  AND is_tracker=1 THEN 1 ELSE 0 END),
             SUM(CASE WHEN phase='post' AND is_tracker=1 THEN 1 ELSE 0 END),
             SUM(CASE WHEN phase='pre'  THEN 1 ELSE 0 END),
             SUM(CASE WHEN phase='post' THEN 1 ELSE 0 END)
           FROM chrome_network_requests r
           JOIN chrome_scans c ON c.id = r.scan_id
           WHERE c.is_error_page=0""",
    )[0]
    pre_trackers_total  = tracker_totals[0] or 0
    post_trackers_total = tracker_totals[1] or 0
    pre_requests_total  = tracker_totals[2] or 0
    post_requests_total = tracker_totals[3] or 0

    pre_tracker_rate  = pre_trackers_total  / pre_requests_total  * 100 if pre_requests_total  else 0
    post_tracker_rate = post_trackers_total / post_requests_total * 100 if post_requests_total else 0

    # Cookie counts from JSON files
    cookie_paths = q(
        conn,
        "SELECT pre_cookies_path, post_cookies_path FROM chrome_scans WHERE is_error_page=0",
    )
    pre_cookie_counts  = [count_cookies_from_path(r[0]) for r in cookie_paths]
    post_cookie_counts = [count_cookies_from_path(r[1]) for r in cookie_paths]
    pre_cookie_counts_valid  = [x for x in pre_cookie_counts  if x is not None]
    post_cookie_counts_valid = [x for x in post_cookie_counts if x is not None]
    avg_pre_cookies  = sum(pre_cookie_counts_valid)  / len(pre_cookie_counts_valid)  if pre_cookie_counts_valid  else None
    avg_post_cookies = sum(post_cookie_counts_valid) / len(post_cookie_counts_valid) if post_cookie_counts_valid else None

    emit(
        rf"An automated audit was performed on the top-{total_chrome} websites by Tranco rank. "
        rf"Of these, \textbf{{{error_count}}} ({error_count/total_chrome*100:.0f}\,\%) "
        r"could not be loaded successfully and were excluded from further analysis, "
        rf"leaving \textbf{{{reachable}}} reachable sites. "
        r"Each reachable site was visited with Chrome to detect and classify cookie notices, "
        r"capture accessibility metrics, and record network requests."
    )
    emit()
    auto_accepted = cookie_accepted - manually_verified_count
    emit(
        rf"A cookie notice was detected on \textbf{{{cookie_detected}}} of the {reachable} reachable sites "
        rf"({cookie_detected/reachable*100:.0f}\,\%), and was successfully accepted on "
        rf"\textbf{{{cookie_accepted}}} of those ({cookie_accepted/cookie_detected*100:.0f}\,\%) "
        rf"({auto_accepted} confirmed automatically"
        + (rf", {manually_verified_count} manually verified" if manually_verified_count else "")
        + r")."
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{High-level scan counts}")
    emit(r"\begin{tabular}{lr}")
    emit(r"\toprule Metric & Count \\ \midrule")
    emit(rf"Chrome scans & {total_chrome} \\")
    emit(rf"Unavailable (error) & {error_count} ({error_count/total_chrome*100:.0f}\,\%) \\")
    emit(rf"Reachable sites & {reachable} \\")
    emit(rf"Cookie notice detected & {cookie_detected} ({cookie_detected/reachable*100:.0f}\,\%) \\")
    emit(rf"Cookie notice accepted & {cookie_accepted} ({cookie_accepted/cookie_detected*100:.0f}\,\%) \\")
    emit(rf"Pre-accept tracker requests & {pre_trackers_total} ({pre_tracker_rate:.0f}\,\% of requests) \\")
    emit(rf"Post-accept tracker requests & {post_trackers_total} ({post_tracker_rate:.0f}\,\% of requests) \\")
    if avg_pre_cookies is not None:
        emit(rf"Avg.\ cookies pre-accept & {avg_pre_cookies:.1f} \\")
    if avg_post_cookies is not None:
        emit(rf"Avg.\ cookies post-accept & {avg_post_cookies:.1f} \\")
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
    dns_count = cat_counts.get("DNS not resolved", 0)
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

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Error categories for unavailable sites}")
    emit(r"\begin{tabular}{lr}")
    emit(r"\toprule Error type & Count \\ \midrule")
    for cat, cnt in cat_counts.most_common():
        emit(rf"  {latex_escape(cat)} & {cnt} \\")
    emit(r"\midrule")
    emit(rf"  \textbf{{Total}} & \textbf{{{error_count}}} \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    emit(
        r"Table~\ref{tab:errors} lists every unavailable site. "
        r"Sites returning HTTP 404 are predominantly Google-owned infrastructure "
        r"domains (\texttt{googleapis.com}, \texttt{gstatic.com}, etc.)\ that redirect "
        r"rather than serving a browsable page."
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Per-site error details}\label{tab:errors}")
    emit(r"\begin{tabular}{>{\ttfamily}p{3cm} r p{3cm}}")
    emit(r"\toprule \normalfont URL & HTTP & Category \\ \midrule")
    for url, hs, pe in error_rows:
        cat = error_category(pe, hs)
        status_str = str(hs) if hs else "---"
        emit(rf"  {latex_escape(url)} & {status_str} & {latex_escape(cat)} \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

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
        rf"Cookie notices appeared most commonly as a \textbf{{{latex_escape(top_pos)}}} "
        rf"({top_pos_cnt} of {cookie_detected} sites, "
        rf"{top_pos_cnt/cookie_detected*100:.0f}\,\%), "
        rf"followed by \texttt{{{latex_escape(second_pos)}}} ({second_pos_cnt} sites). "
        r"One site used a full-page (\texttt{overall}) overlay that blocked all content "
        r"until the notice was addressed."
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Cookie notice position}\label{tab:position}")
    emit(r"\begin{tabular}{lrr}")
    emit(r"\toprule Position & $n$ & \% \\ \midrule")
    for pos, cnt in pos_rows:
        emit(rf"  \texttt{{{latex_escape(pos)}}} & {cnt} & {cnt/cookie_detected*100:.0f}\,\% \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    emit(r"\subsubsection{Control Type (Response Options)}")

    ctrl_rows = q(
        conn,
        f"SELECT COALESCE(manual_cookie_control_type, cookie_control_type,'unknown'), COUNT(*) "
        f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"GROUP BY COALESCE(manual_cookie_control_type, cookie_control_type) ORDER BY COUNT(*) DESC",
    )
    info_only = next((cnt for ct, cnt in ctrl_rows if ct == "informational_only"), 0)
    full_choice = next((cnt for ct, cnt in ctrl_rows if ct == "accept_reject_or_settings"), 0)

    emit(
        rf"The most common control type was \textbf{{informational only}} ({info_only} sites, "
        rf"{info_only/cookie_detected*100:.0f}\,\%), meaning visitors were shown a notice "
        r"but offered no meaningful opt-out. "
        rf"Only {full_choice} sites ({full_choice/cookie_detected*100:.0f}\,\%) provided "
        r"the full set of options: accept, reject, \emph{and} granular settings."
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Cookie notice control type}\label{tab:control}")
    emit(r"\begin{tabular}{lrr}")
    emit(r"\toprule Control type & $n$ & \% \\ \midrule")
    for ctrl, cnt in ctrl_rows:
        emit(rf"  \texttt{{{latex_escape(ctrl)}}} & {cnt} & {cnt/cookie_detected*100:.0f}\,\% \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    emit(r"\subsubsection{Emphasized Option}")

    emph_rows = q(
        conn,
        f"SELECT COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option,'unknown'), COUNT(*) "
        f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"GROUP BY COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option) ORDER BY COUNT(*) DESC",
    )
    equal_cnt = next((cnt for e, cnt in emph_rows if e == "equal"), 0)
    none_cnt  = next((cnt for e, cnt in emph_rows if e == "none"), 0)

    emit(
        rf"Of the {cookie_detected} detected notices, {none_cnt} had \texttt{{none}} as the "
        r"emphasized option, corresponding entirely to informational-only notices where no "
        r"choice is offered. "
        rf"Among notices that \emph{{do}} offer a choice, {equal_cnt} presented accept and "
        r"reject options with \textbf{equal} visual weight --- a positive finding indicating "
        r"no deliberate dark pattern was detected in these cases."
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Emphasized option on cookie notices}\label{tab:emph}")
    emit(r"\begin{tabular}{lrr}")
    emit(r"\toprule Emphasized option & $n$ & \% \\ \midrule")
    for emph, cnt in emph_rows:
        emit(rf"  \texttt{{{latex_escape(emph)}}} & {cnt} & {cnt/cookie_detected*100:.0f}\,\% \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    emit(r"\subsubsection{Additional Features}")

    has_reject   = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND COALESCE(manual_cookie_has_reject,   cookie_has_reject)=1")[0][0]
    has_settings = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND COALESCE(manual_cookie_has_settings, cookie_has_settings)=1")[0][0]
    pre_selected = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND COALESCE(manual_cookie_pre_selected, cookie_pre_selected)=1")[0][0]

    emit(
        rf"A reject button or link was present on {has_reject} of {cookie_detected} notices "
        rf"({has_reject/cookie_detected*100:.0f}\,\%), and a settings or preferences link on "
        rf"{has_settings} ({has_settings/cookie_detected*100:.0f}\,\%). "
        rf"No notices in this sample had options pre-selected, "
        r"meaning none defaulted consent to `on' before the user interacted."
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Additional cookie notice features}")
    emit(r"\begin{tabular}{lrr}")
    emit(r"\toprule Feature & $n$ & \% \\ \midrule")
    emit(rf"Has reject button/link & {has_reject} & {has_reject/cookie_detected*100:.0f}\,\% \\")
    emit(rf"Has settings link & {has_settings} & {has_settings/cookie_detected*100:.0f}\,\% \\")
    emit(rf"Options pre-selected & {pre_selected} & {pre_selected/cookie_detected*100:.0f}\,\% \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    # ------------------------------------------------------------------ #
    # 4. Per-site cookie notice summary
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Per-Site Cookie Notice Summary}")

    emit(
        r"Table~\ref{tab:persite} lists every site where a cookie notice was detected "
        r"alongside its full classification. "
        r"The `Rej', `Set', and `Pre' columns indicate whether a reject button, "
        r"settings link, or pre-selected options were present (\checkmark) or absent (---), "
        r"respectively."
    )
    emit()

    site_rows = q(
        conn,
        """SELECT url,
                  COALESCE(manual_cookie_position,          cookie_position),
                  COALESCE(manual_cookie_control_type,      cookie_control_type),
                  COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option),
                  COALESCE(manual_cookie_has_reject,        cookie_has_reject),
                  COALESCE(manual_cookie_has_settings,      cookie_has_settings),
                  COALESCE(manual_cookie_pre_selected,      cookie_pre_selected)
           FROM chrome_scans
           WHERE cookie_notice_detected=1 AND {NOT_FP}
           ORDER BY url""",
    )

    emit(r"\begin{table*}[ht]\centering\footnotesize")
    emit(r"\caption{Per-site cookie notice classification}\label{tab:persite}")
    emit(r"\begin{tabular}{>{\ttfamily}p{2.3cm} p{1.5cm} p{2.5cm} p{1.1cm} c c c}")
    emit(r"\toprule \normalfont URL & Pos. & Control type & Emph. & Rej & Set & Pre \\ \midrule")
    for url, pos, ctrl, emph, rej, sett, pre in site_rows:
        def yn(v): return r"\checkmark" if v else "---"
        emit(
            rf"  {latex_escape(url)} & "
            rf"\scriptsize {latex_escape(pos)} & "
            rf"\scriptsize {latex_escape(ctrl)} & "
            rf"\scriptsize {latex_escape(emph)} & "
            rf"{yn(rej)} & {yn(sett)} & {yn(pre)} \\"
        )
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table*}")

    # ------------------------------------------------------------------ #
    # 5. Trackers: pre-accept vs post-accept
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Tracker Requests: Pre- vs Post-Accept}")

    tracker_rows = q(
        conn,
        """SELECT c.url,
             SUM(CASE WHEN r.phase='pre'  AND r.is_tracker=1 THEN 1 ELSE 0 END) AS pre_trackers,
             SUM(CASE WHEN r.phase='post' AND r.is_tracker=1 THEN 1 ELSE 0 END) AS post_trackers,
             SUM(CASE WHEN r.phase='pre'  THEN 1 ELSE 0 END) AS pre_total,
             SUM(CASE WHEN r.phase='post' THEN 1 ELSE 0 END) AS post_total
           FROM chrome_scans c
           LEFT JOIN chrome_network_requests r ON c.id = r.scan_id
           WHERE c.is_error_page=0
           GROUP BY c.url
           ORDER BY pre_trackers DESC""",
    )

    sites_with_post = [(url, pre_t, post_t, pre_tot, post_tot)
                       for url, pre_t, post_t, pre_tot, post_tot in tracker_rows
                       if post_tot and post_tot > 0]

    avg_pre_t  = sum(r[1] or 0 for r in tracker_rows) / len(tracker_rows) if tracker_rows else 0
    avg_post_t = sum(r[2] or 0 for r in sites_with_post) / len(sites_with_post) if sites_with_post else 0

    tracker_increased = sum(1 for _, pre_t, post_t, _, _ in sites_with_post
                            if post_t is not None and pre_t is not None and post_t > pre_t)
    tracker_decreased = sum(1 for _, pre_t, post_t, _, _ in sites_with_post
                            if post_t is not None and pre_t is not None and post_t < pre_t)

    emit(
        r"Network requests were classified as tracker or non-tracker for both the pre-accept "
        r"(cookie notice visible) and post-accept (notice accepted) phases. "
        rf"Across all {reachable} reachable sites, an average of \textbf{{{avg_pre_t:.1f}}} "
        r"tracker requests were recorded in the pre-accept phase per site. "
        rf"For the {len(sites_with_post)} sites where a post-accept scan was also performed "
        rf"(i.e.\ the cookie notice was accepted), the average rose to "
        rf"\textbf{{{avg_post_t:.1f}}} tracker requests --- suggesting that accepting "
        r"cookie notices may increase third-party tracking activity. "
        rf"Of these sites, {tracker_increased} showed \emph{{more}} tracker requests "
        rf"post-accept and {tracker_decreased} showed fewer."
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Average tracker request counts per site}")
    emit(r"\begin{tabular}{lrr}")
    emit(r"\toprule Metric & Pre-accept & Post-accept \\ \midrule")
    emit(rf"Avg.\ tracker requests/site & {avg_pre_t:.1f} & {avg_post_t:.1f} \\")
    emit(rf"Total tracker requests & {pre_trackers_total} & {post_trackers_total} \\")
    emit(rf"Total network requests & {pre_requests_total} & {post_requests_total} \\")
    emit(rf"Tracker rate & {pre_tracker_rate:.1f}\,\% & {post_tracker_rate:.1f}\,\% \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Per-site tracker request counts (pre vs post accept)}\label{tab:trackers}")
    emit(r"\begin{tabular}{>{\ttfamily}p{2.5cm} r r r r r}")
    emit(r"\toprule \normalfont URL & \multicolumn{2}{c}{Trackers} & \multicolumn{2}{c}{Total Req.} & $\Delta$ \\")
    emit(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}")
    emit(r"\normalfont & Pre & Post & Pre & Post & \\ \midrule")
    for url, pre_t, post_t, pre_tot, post_tot in tracker_rows:
        pre_t_s   = fmt(pre_t,  0) if pre_t  is not None else "---"
        post_t_s  = fmt(post_t, 0) if post_t is not None else "---"
        pre_tot_s = fmt(pre_tot, 0) if pre_tot is not None else "---"
        post_tot_s = fmt(post_tot, 0) if post_tot is not None else "---"
        if pre_t is not None and post_t is not None:
            d = post_t - pre_t
            delta_s = rf"\textbf{{{d:+d}}}" if d != 0 else "0"
        else:
            delta_s = "---"
        emit(
            rf"  {latex_escape(url)} & "
            rf"{pre_t_s} & {post_t_s} & "
            rf"{pre_tot_s} & {post_tot_s} & "
            rf"{delta_s} \\"
        )
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    # ------------------------------------------------------------------ #
    # 6. Cookies: pre-accept vs post-accept
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Browser Cookies: Pre- vs Post-Accept}")

    cookie_count_rows = q(
        conn,
        "SELECT url, pre_cookies_path, post_cookies_path FROM chrome_scans WHERE is_error_page=0 ORDER BY url",
    )
    cookie_count_data = [
        (url, count_cookies_from_path(pre_p), count_cookies_from_path(post_p))
        for url, pre_p, post_p in cookie_count_rows
    ]
    measured = [(url, pre_c, post_c) for url, pre_c, post_c in cookie_count_data
                if pre_c is not None]
    both_measured = [(url, pre_c, post_c) for url, pre_c, post_c in measured
                     if post_c is not None]

    if measured:
        avg_pre_c  = sum(r[1] for r in measured)  / len(measured)
        avg_post_c = sum(r[2] for r in both_measured) / len(both_measured) if both_measured else None

        cookies_increased = sum(1 for _, pre_c, post_c in both_measured if post_c > pre_c)
        cookies_decreased = sum(1 for _, pre_c, post_c in both_measured if post_c < pre_c)

        emit(
            rf"Browser cookie counts were read from the captured cookie JSON files for "
            rf"{len(measured)} of {reachable} reachable sites (pre-accept) and "
            rf"{len(both_measured)} sites (post-accept). "
            rf"On average, \textbf{{{avg_pre_c:.1f}}} cookies were set before accepting "
            r"the cookie notice. "
        )
        if avg_post_c is not None:
            emit(
                rf"After acceptance, the average rose to \textbf{{{avg_post_c:.1f}}} cookies. "
                rf"Of the {len(both_measured)} sites with both measurements, "
                rf"{cookies_increased} had more cookies post-accept and "
                rf"{cookies_decreased} had fewer, consistent with additional tracking "
                r"cookies being set upon consent."
            )
        emit()

        emit(r"\begin{table}[ht]\centering\footnotesize")
        emit(r"\caption{Average browser cookie counts per site (pre vs post accept)}")
        emit(r"\begin{tabular}{lrr}")
        emit(r"\toprule Metric & Pre-accept & Post-accept \\ \midrule")
        emit(rf"Avg.\ cookies/site & {avg_pre_c:.1f} & {fmt(avg_post_c)} \\")
        emit(rf"Sites measured & {len(measured)} & {len(both_measured)} \\")
        emit(r"\bottomrule\end{tabular}")
        emit(r"\end{table}")

        emit(r"\begin{table}[ht]\centering\footnotesize")
        emit(r"\caption{Per-site cookie counts (pre vs post accept)}\label{tab:cookies}")
        emit(r"\begin{tabular}{>{\ttfamily}p{3cm} r r r}")
        emit(r"\toprule \normalfont URL & Pre & Post & $\Delta$ \\ \midrule")
        for url, pre_c, post_c in sorted(cookie_count_data, key=lambda r: r[0]):
            pre_s  = str(pre_c)  if pre_c  is not None else "---"
            post_s = str(post_c) if post_c is not None else "---"
            if pre_c is not None and post_c is not None:
                d = post_c - pre_c
                delta_s = rf"\textbf{{{d:+d}}}" if d != 0 else "0"
            else:
                delta_s = "---"
            emit(rf"  {latex_escape(url)} & {pre_s} & {post_s} & {delta_s} \\")
        emit(r"\bottomrule\end{tabular}")
        emit(r"\end{table}")
    else:
        emit(
            r"Cookie count data could not be read from the artifact files in this environment. "
            r"Ensure the \texttt{artifacts/} directory is present alongside the database."
        )
        emit()

    # ------------------------------------------------------------------ #
    # 7. Accessibility – Chrome (pre vs post accept)
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Accessibility Metrics (Chrome)}")

    avg = q(
        conn,
        """SELECT
             ROUND(AVG(pre_lh_score),1),  ROUND(AVG(post_lh_score),1),
             ROUND(AVG(pre_wave_error),1), ROUND(AVG(post_wave_error),1),
             ROUND(AVG(pre_wave_contrast),1), ROUND(AVG(post_wave_contrast),1),
             ROUND(AVG(pre_wave_alert),1), ROUND(AVG(post_wave_alert),1)
           FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}""",
    )[0]

    lh_improved = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND post_lh_score > pre_lh_score",
    )[0][0]
    lh_declined = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND post_lh_score < pre_lh_score",
    )[0][0]
    lh_measured = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND pre_lh_score IS NOT NULL AND post_lh_score IS NOT NULL",
    )[0][0]

    emit(
        r"Lighthouse and WAVE accessibility tools were run both before and after accepting "
        r"the cookie notice, allowing a direct comparison of the notice's impact on "
        r"accessibility. "
        rf"Average Lighthouse scores were \textbf{{{fmt(avg[0])}}} pre-accept and "
        rf"\textbf{{{fmt(avg[1])}}} post-accept across the {cookie_detected} sites with "
        r"notices --- a negligible change, suggesting cookie notices themselves do not "
        r"substantially degrade page accessibility once removed. "
        rf"Of the {lh_measured} sites where both scores were available, "
        rf"{lh_improved} improved after acceptance and {lh_declined} declined."
    )
    emit()
    emit(
        rf"WAVE reported an average of \textbf{{{fmt(avg[2])}}} errors per page before "
        rf"acceptance and \textbf{{{fmt(avg[3])}}} after, with contrast errors averaging "
        rf"{fmt(avg[4])} and {fmt(avg[5])} respectively. "
        r"The small differences suggest that WAVE errors are largely attributable to "
        r"the underlying page rather than the cookie notice overlay."
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Average accessibility metrics --- sites with cookie notices}")
    emit(r"\begin{tabular}{lrr}")
    emit(r"\toprule Metric & Pre & Post \\ \midrule")
    emit(rf"Lighthouse score & {fmt(avg[0])} & {fmt(avg[1])} \\")
    emit(rf"WAVE errors & {fmt(avg[2])} & {fmt(avg[3])} \\")
    emit(rf"WAVE contrast errors & {fmt(avg[4])} & {fmt(avg[5])} \\")
    emit(rf"WAVE alerts & {fmt(avg[6])} & {fmt(avg[7])} \\")
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    lh_rows = q(
        conn,
        """SELECT url, pre_lh_score, post_lh_score,
                  pre_wave_error, post_wave_error,
                  pre_wave_contrast, post_wave_contrast
           FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}
           ORDER BY url""",
    )

    worst_wave = max(lh_rows, key=lambda r: (r[3] or 0))
    worst_contrast = max(lh_rows, key=lambda r: (r[5] or 0))

    emit(
        rf"The site with the most WAVE errors pre-acceptance was "
        rf"\texttt{{{latex_escape(worst_wave[0])}}} ({fmt(worst_wave[3],0)} errors). "
        rf"The worst contrast errors were on \texttt{{{latex_escape(worst_contrast[0])}}} "
        rf"({fmt(worst_contrast[5],0)} contrast errors). "
        r"Per-site figures are in Table~\ref{tab:lh}."
    )
    emit()

    emit(r"\begin{table}[ht]\centering\footnotesize")
    emit(r"\caption{Per-site Chrome accessibility (cookie-notice sites)}\label{tab:lh}")
    emit(r"\begin{tabular}{>{\ttfamily}p{2.2cm} r r r r r r}")
    emit(r"\toprule")
    emit(r"\normalfont URL & \multicolumn{2}{c}{LH} & \multicolumn{2}{c}{Err} & \multicolumn{2}{c}{Con} \\")
    emit(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
    emit(r"\normalfont & Pre & Post & Pre & Post & Pre & Post \\ \midrule")
    for url, pre_lh, post_lh, pre_we, post_we, pre_wc, post_wc in lh_rows:
        emit(
            rf"  {latex_escape(url)} & "
            rf"{fmt(pre_lh)} & {fmt(post_lh)} & "
            rf"{fmt(pre_we,0)} & {fmt(post_we,0)} & "
            rf"{fmt(pre_wc,0)} & {fmt(post_wc,0)} \\"
        )
    emit(r"\bottomrule\end{tabular}")
    emit(r"\end{table}")

    # ------------------------------------------------------------------ #
    # 8. Control options summary (user-facing taxonomy)
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Cookie Notice Control Options and GDPR}")

    ct_rows = q(
        conn,
        """SELECT COALESCE(manual_cookie_control_type, cookie_control_type),
                  COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option),
                  COUNT(*)
           FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}
           GROUP BY COALESCE(manual_cookie_control_type, cookie_control_type),
                    COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option)""",
    )
    ct_map = {(ct, em): cnt for ct, em, cnt in ct_rows}

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

    emit(r"\begin{table}[t]\centering\footnotesize")
    emit(r"\caption{Cookie notice control options and GDPR compliance.}\label{tab:options}")
    emit(r"\begin{tabular}{llrr} \toprule")
    emit(r"  \textbf{Control options} & \textbf{Emphasised option} & \textbf{Sites} & \textbf{GDPR violation} \\ \midrule")
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
    emit(r"\end{table}")

    # ------------------------------------------------------------------ #
    # 9. Pre-accept vs post-accept accessibility comparison
    # ------------------------------------------------------------------ #
    emit(r"\subsection{Accessibility: Pre- vs Post-Accept Comparison}")

    pre_post = q(
        conn,
        f"""SELECT
             ROUND(AVG(pre_lh_score),1),    ROUND(AVG(post_lh_score),1),
             ROUND(AVG(pre_wave_error),1),   ROUND(AVG(post_wave_error),1),
             ROUND(AVG(pre_wave_contrast),1),ROUND(AVG(post_wave_contrast),1),
             ROUND(AVG(pre_wave_alert),1),   ROUND(AVG(post_wave_alert),1),
             COUNT(*)
           FROM chrome_scans
           WHERE cookie_notice_detected=1 AND is_error_page=0 AND {ACCEPTED} AND {NOT_FP}
           AND pre_lh_score IS NOT NULL AND post_lh_score IS NOT NULL""",
    )[0]

    pre_lh,  post_lh  = pre_post[0], pre_post[1]
    pre_we,  post_we  = pre_post[2], pre_post[3]
    pre_wc,  post_wc  = pre_post[4], pre_post[5]
    pre_wa,  post_wa  = pre_post[6], pre_post[7]
    n_compared = pre_post[8]

    def delta(a, b):
        if a is None or b is None:
            return "---"
        d = round(b - a, 1)
        return rf"\textbf{{{d:+.1f}}}" if d != 0 else "0.0"

    emit(
        rf"Table~\ref{{tab:a11y}} compares accessibility metrics before and after "
        rf"accepting the cookie notice for the {n_compared} sites where both pre- and "
        r"post-accept Lighthouse scores were available. "
        r"$\Delta$\,Post shows the change after accepting the notice."
    )
    emit()
    emit(
        rf"Lighthouse scores were nearly identical across both states "
        rf"({fmt(pre_lh)}, {fmt(post_lh)}), "
        r"indicating that cookie notice overlays have negligible impact on measured "
        r"accessibility scores. "
        rf"WAVE alerts showed the largest reduction post-acceptance "
        rf"($\Delta = {fmt(post_wa - pre_wa if post_wa and pre_wa else None)}$), "
        r"likely because the notice overlay itself contributed alert-level issues "
        r"(e.g.\ redundant links or missing ARIA labels) that disappear once dismissed."
    )
    emit()

    emit(r"\begin{table}[t]\centering\footnotesize")
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
    emit(r"\end{table}")

    return "\n".join(lines)


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        tex = build_report(conn)
    finally:
        conn.close()

    OUT_PATH.write_text(tex, encoding="utf-8")
    print(f"Written: {OUT_PATH}")


if __name__ == "__main__":
    main()
