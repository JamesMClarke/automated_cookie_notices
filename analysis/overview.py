from collections import defaultdict

from .utils import ACCEPTED, NOT_FP, col_exists, fmt, latex_escape, q, q_safe


def run(conn):
    has_reject_cols = col_exists(conn, "chrome_scans", "cookie_notice_rejected")
    has_tracker_col = col_exists(conn, "chrome_network_requests", "is_tracker")

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

    if has_tracker_col:
        tracker_totals = q_safe(
            conn,
            f"""SELECT
                 SUM(CASE WHEN phase='pre'         AND is_tracker=1 THEN 1 ELSE 0 END),
                 SUM(CASE WHEN phase='post_accept' AND is_tracker=1 AND {ACCEPTED} THEN 1 ELSE 0 END),
                 SUM(CASE WHEN phase='pre'         THEN 1 ELSE 0 END),
                 SUM(CASE WHEN phase='post_accept' AND {ACCEPTED} THEN 1 ELSE 0 END)
               FROM chrome_network_requests r
               JOIN chrome_scans c ON c.id = r.scan_id
               WHERE c.is_error_page=0 AND {NOT_FP}""",
        )
        tracker_totals = tracker_totals[0] if tracker_totals else (0, 0, 0, 0)
    else:
        tracker_totals = (None, None, None, None)

    pre_trackers_total = tracker_totals[0] or 0
    post_trackers_total = tracker_totals[1] or 0
    pre_requests_total = tracker_totals[2] or 0
    post_requests_total = tracker_totals[3] or 0

    if not has_tracker_col:
        req_totals = q_safe(
            conn,
            f"""SELECT
                 SUM(CASE WHEN phase='pre'         THEN 1 ELSE 0 END),
                 SUM(CASE WHEN phase='post_accept' AND {ACCEPTED} THEN 1 ELSE 0 END)
               FROM chrome_network_requests r
               JOIN chrome_scans c ON c.id = r.scan_id
               WHERE c.is_error_page=0 AND {NOT_FP}""",
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
            f"""SELECT
                 SUM(CASE WHEN is_tracker=1 THEN 1 ELSE 0 END),
                 COUNT(*)
               FROM chrome_network_requests r
               JOIN chrome_scans c ON c.id = r.scan_id
               WHERE c.is_error_page=0 AND {NOT_FP} AND c.cookie_notice_rejected=1 AND r.phase='post_reject'""",
        )
        if reject_tracker_rows and reject_tracker_rows[0][1]:
            reject_trackers_total = reject_tracker_rows[0][0] or 0
            reject_requests_total = reject_tracker_rows[0][1] or 0
            reject_tracker_rate = reject_trackers_total / reject_requests_total * 100
    else:
        rej_req_rows = q_safe(
            conn,
            f"""SELECT COUNT(*) FROM chrome_network_requests r
               JOIN chrome_scans c ON c.id = r.scan_id
               WHERE c.is_error_page=0 AND {NOT_FP} AND c.cookie_notice_rejected=1 AND r.phase='post_reject'""",
        )
        if rej_req_rows:
            reject_requests_total = rej_req_rows[0][0] or 0

    _acc_scan_id_sql = f"SELECT id FROM chrome_scans WHERE {ACCEPTED} AND is_error_page=0 AND {NOT_FP}"
    _rej_scan_id_sql = f"SELECT id FROM chrome_scans WHERE cookie_notice_rejected=1 AND is_error_page=0 AND {NOT_FP}"
    _pre_scan_id_sql = f"SELECT id FROM chrome_scans WHERE is_error_page=0 AND {NOT_FP}"
    _cc_phase_rows = q_safe(
        conn,
        f"""SELECT 'pre' AS phase, COUNT(*) AS total
            FROM cookie_classifications WHERE phase='pre' AND scan_id IN ({_pre_scan_id_sql})
            UNION ALL
            SELECT 'post_accept', COUNT(*)
            FROM cookie_classifications WHERE phase='post_accept' AND scan_id IN ({_acc_scan_id_sql})
            UNION ALL
            SELECT 'post_reject', COUNT(*)
            FROM cookie_classifications WHERE phase='post_reject' AND scan_id IN ({_rej_scan_id_sql})""",
    )
    _cc_phase_totals = {row[0]: row[1] for row in _cc_phase_rows}
    pre_total = _cc_phase_totals.get("pre", 0)
    acc_total = _cc_phase_totals.get("post_accept", 0)
    rej_total = _cc_phase_totals.get("post_reject", 0)
    _pre_sites_n = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE is_error_page=0 AND {NOT_FP}")[0][0]
    avg_pre_db = pre_total / _pre_sites_n if _pre_sites_n else None
    avg_acc_db = acc_total / cookie_accepted if cookie_accepted else None
    avg_rej_db = rej_total / cookie_rejected if cookie_rejected else None
    has_rej_cookies = rej_total > 0

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
    _top_rising_cat = max(
        (c for c in {cat for _, cat, _ in cat_rows} if c != "Unclassified"),
        key=lambda c: cat_by_phase.get("post_accept", {}).get(c, 0) - cat_by_phase.get("pre", {}).get(c, 0),
        default=None,
    )

    print(r"\subsection{Overview}")
    print(
        rf"An automated audit was performed on the top-{total_chrome} websites by Tranco rank. "
        rf"Of these, \textbf{{{error_count}}} ({error_count / total_chrome * 100:.0f}\,\%) "
        r"could not be loaded successfully and were excluded from further analysis, "
        rf"leaving \textbf{{{reachable}}} reachable sites. "
        r"Each reachable site was visited with Chrome to detect and classify cookie notices, "
        r"capture accessibility metrics, and record network requests."
    )
    print()
    auto_accepted = cookie_accepted - manually_verified_count
    print(
        rf"A cookie notice was detected on \textbf{{{cookie_detected}}} of the {reachable} reachable sites "
        rf"({cookie_detected / reachable * 100:.0f}\,\%), and was successfully accepted on "
        rf"\textbf{{{cookie_accepted}}} of those ({cookie_accepted / cookie_detected * 100:.0f}\,\%) "
        rf"({auto_accepted} confirmed automatically"
        + (rf", {manually_verified_count} manually verified" if manually_verified_count else "")
        + r")."
    )
    if has_reject_cols and reject_attempted:
        print(
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
        print(
            rf"Cookie analysis found an average of \textbf{{{fmt(avg_pre_db)}}} cookies per site "
            rf"pre-accept, rising to \textbf{{{fmt(avg_acc_db)}}} post-accept"
            + (rf" and \textbf{{{fmt(avg_rej_db)}}} post-reject" if has_rej_cookies else "")
            + rf". The \textbf{{{latex_escape(_top_rising_cat)}}} category showed the largest increase, "
            rf"accounting for {_pre_pct:.0f}\,\% of pre-accept cookies and "
            rf"{_acc_pct:.0f}\,\% post-accept" + (rf" ({_rej_n} post-reject)" if _rej_n is not None else "") + r"."
        )
    print()

    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{High-level scan counts}")
    print(r"\begin{tabular}{lr}")
    print(r"\toprule Metric & Count \\ \midrule")
    print(rf"Chrome scans & {total_chrome} \\")
    print(rf"Unavailable (error) & {error_count} ({error_count / total_chrome * 100:.0f}\,\%) \\")
    print(rf"Reachable sites & {reachable} \\")
    print(rf"Cookie notice detected & {cookie_detected} ({cookie_detected / reachable * 100:.0f}\,\%) \\")
    print(rf"Cookie notice accepted & {cookie_accepted} ({cookie_accepted / cookie_detected * 100:.0f}\,\%) \\")
    if has_reject_cols and reject_attempted:
        print(
            rf"Cookie notice rejected & {cookie_rejected} ({cookie_rejected / reject_attempted * 100:.0f}\,\% of attempted) \\"
        )
    print(rf"Pre-accept tracker requests & {pre_trackers_total} ({pre_tracker_rate:.0f}\,\% of requests) \\")
    print(rf"Post-accept tracker requests & {post_trackers_total} ({post_tracker_rate:.0f}\,\% of requests) \\")
    if reject_requests_total:
        print(rf"Post-reject tracker requests & {reject_trackers_total} ({reject_tracker_rate:.0f}\,\% of requests) \\")
    if avg_pre_db is not None:
        print(rf"Avg.\ cookies pre-accept & {fmt(avg_pre_db)} \\")
    if avg_acc_db is not None:
        print(rf"Avg.\ cookies post-accept & {fmt(avg_acc_db)} \\")
    if avg_rej_db is not None:
        print(rf"Avg.\ cookies post-reject & {fmt(avg_rej_db)} \\")
    if _top_rising_cat:
        _pre_pct = cat_by_phase["pre"].get(_top_rising_cat, 0) / pre_total * 100 if pre_total else 0
        _acc_pct = cat_by_phase["post_accept"].get(_top_rising_cat, 0) / acc_total * 100 if acc_total else 0
        print(
            rf"Top rising category & {latex_escape(_top_rising_cat)} ({_pre_pct:.0f}\,\% $\to$ {_acc_pct:.0f}\,\%) \\"
        )
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")


if __name__ == "__main__":
    import sys

    from .utils import open_merged

    db_names = sys.argv[1:] if len(sys.argv) > 1 else None
    conn, db_paths = open_merged(db_names)
    try:
        run(conn)
    finally:
        conn.close()
