from collections import Counter, defaultdict
from statistics import median as _median_stat

from .utils import (
    ACCEPTED,
    NOT_FP,
    SHOW_PER,
    _reg_domain,
    col_exists,
    cookie_party,
    fmt,
    latex_escape,
    q,
    q_safe,
)


def _median(vals):
    return _median_stat(vals) if vals else None


def _mode(vals):
    return Counter(vals).most_common(1)[0][0] if vals else None


def run(conn):
    has_tracker_col = col_exists(conn, "chrome_network_requests", "is_tracker")

    total_chrome = q(conn, "SELECT COUNT(*) FROM chrome_scans")[0][0]
    error_count = q(conn, "SELECT COUNT(*) FROM chrome_scans WHERE is_error_page=1")[0][0]
    reachable = total_chrome - error_count

    if has_tracker_col:
        tracker_totals = q_safe(
            conn,
            f"""SELECT
                 SUM(CASE WHEN phase='pre' AND is_tracker=1 AND (c.cookie_notice_detected IS NULL OR c.cookie_notice_detected!=0) THEN 1 ELSE 0 END),
                 SUM(CASE WHEN phase='post_accept' AND is_tracker=1 AND {ACCEPTED} THEN 1 ELSE 0 END),
                 SUM(CASE WHEN phase='pre' AND (c.cookie_notice_detected IS NULL OR c.cookie_notice_detected!=0) THEN 1 ELSE 0 END),
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
                 SUM(CASE WHEN phase='pre' AND (c.cookie_notice_detected IS NULL OR c.cookie_notice_detected!=0) THEN 1 ELSE 0 END),
                 SUM(CASE WHEN phase='post_accept' THEN 1 ELSE 0 END)
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

    accepted_urls = set()
    acc_rows = q_safe(conn, f"SELECT url FROM chrome_scans WHERE {ACCEPTED} AND is_error_page=0 AND {NOT_FP}")
    if acc_rows:
        accepted_urls = {r[0] for r in acc_rows}

    rejected_urls = set()
    has_reject_col = col_exists(conn, "chrome_scans", "cookie_notice_rejected")
    if has_reject_col:
        rej_rows = q_safe(
            conn, f"SELECT url FROM chrome_scans WHERE cookie_notice_rejected=1 AND is_error_page=0 AND {NOT_FP}"
        )
        if rej_rows:
            rejected_urls = {r[0] for r in rej_rows}

    notice_urls = set()
    notice_rows = q_safe(
        conn, f"SELECT url FROM chrome_scans WHERE cookie_notice_detected=1 AND is_error_page=0 AND {NOT_FP}"
    )
    if notice_rows:
        notice_urls = {r[0] for r in notice_rows}

    sites_with_notice = [
        (url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot)
        for url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot in tracker_rows
        if url in notice_urls
    ]
    sites_without_notice = [
        (url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot)
        for url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot in tracker_rows
        if url not in notice_urls
    ]
    sites_with_post = [
        (url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot)
        for url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot in tracker_rows
        if url in accepted_urls
    ]
    sites_with_reject = [
        (url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot)
        for url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot in tracker_rows
        if url in rejected_urls
    ]

    avg_pre_t = sum(r[1] or 0 for r in sites_with_notice) / len(sites_with_notice) if sites_with_notice else 0
    mean_no_notice_t = (
        sum(r[1] or 0 for r in sites_without_notice) / len(sites_without_notice) if sites_without_notice else 0
    )
    avg_post_t = sum(r[2] or 0 for r in sites_with_post) / len(sites_with_post) if sites_with_post else 0
    avg_reject_t = sum(r[3] or 0 for r in sites_with_reject) / len(sites_with_reject) if sites_with_reject else 0

    _pre_t_vals = [r[1] or 0 for r in sites_with_notice] if has_tracker_col else []
    _nn_t_vals = [r[1] or 0 for r in sites_without_notice] if has_tracker_col else []
    _post_t_vals = [r[2] or 0 for r in sites_with_post] if has_tracker_col else []
    _reject_t_vals = [r[3] or 0 for r in sites_with_reject] if has_tracker_col else []
    med_pre_t = _median(_pre_t_vals)
    mode_pre_t = _mode(_pre_t_vals)
    med_nn_t = _median(_nn_t_vals)
    mode_nn_t = _mode(_nn_t_vals)
    med_post_t = _median(_post_t_vals)
    mode_post_t = _mode(_post_t_vals)
    med_reject_t = _median(_reject_t_vals)
    mode_reject_t = _mode(_reject_t_vals)
    no_notice_trackers_total = sum(r[1] or 0 for r in sites_without_notice)
    no_notice_requests_total = sum(r[4] or 0 for r in sites_without_notice)
    no_notice_tracker_rate = (
        no_notice_trackers_total / no_notice_requests_total * 100 if no_notice_requests_total and has_tracker_col else 0
    )

    tracker_increased = sum(
        1 for _, pre_t, post_t, *_ in sites_with_post if post_t is not None and pre_t is not None and post_t > pre_t
    )
    tracker_decreased = sum(
        1 for _, pre_t, post_t, *_ in sites_with_post if post_t is not None and pre_t is not None and post_t < pre_t
    )
    tracker_same_acc = sum(
        1 for _, pre_t, post_t, *_ in sites_with_post if post_t is not None and pre_t is not None and post_t == pre_t
    )

    # post-reject vs pre-accept (baseline)
    rej_vs_pre_more = sum(
        1 for _, pre_t, _, rej_t, *_ in sites_with_reject if rej_t is not None and pre_t is not None and rej_t > pre_t
    )
    rej_vs_pre_less = sum(
        1 for _, pre_t, _, rej_t, *_ in sites_with_reject if rej_t is not None and pre_t is not None and rej_t < pre_t
    )
    rej_vs_pre_same = sum(
        1 for _, pre_t, _, rej_t, *_ in sites_with_reject if rej_t is not None and pre_t is not None and rej_t == pre_t
    )

    # post-reject vs post-accept (for sites that have both)
    sites_with_both = [
        (url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot)
        for url, pre_t, post_t, rej_t, pre_tot, post_tot, rej_tot in tracker_rows
        if url in accepted_urls and url in rejected_urls
    ]
    rej_vs_acc_more = sum(
        1 for _, _, post_t, rej_t, *_ in sites_with_both if rej_t is not None and post_t is not None and rej_t > post_t
    )
    rej_vs_acc_less = sum(
        1 for _, _, post_t, rej_t, *_ in sites_with_both if rej_t is not None and post_t is not None and rej_t < post_t
    )
    rej_vs_acc_same = sum(
        1 for _, _, post_t, rej_t, *_ in sites_with_both if rej_t is not None and post_t is not None and rej_t == post_t
    )

    BUCKETS = [(0, 0), (1, 5), (6, 10), (11, 20), (21, 50), (51, None)]

    def bucket_label(lo, hi):
        return str(lo) if lo == hi else (rf"{lo}+" if hi is None else rf"{lo}--{hi}")

    def bucket_counts(values):
        counts = []
        for lo, hi in BUCKETS:
            if hi is None:
                counts.append(sum(1 for v in values if v >= lo))
            elif lo == hi:
                counts.append(sum(1 for v in values if v == lo))
            else:
                counts.append(sum(1 for v in values if lo <= v <= hi))
        return counts

    no_notice_dist = (
        bucket_counts([r[1] or 0 for r in sites_without_notice]) if has_tracker_col and sites_without_notice else None
    )
    pre_dist = bucket_counts([r[1] or 0 for r in sites_with_notice]) if has_tracker_col and sites_with_notice else None
    post_dist = bucket_counts([r[2] or 0 for r in sites_with_post]) if sites_with_post else None
    rej_dist = bucket_counts([r[3] or 0 for r in sites_with_reject]) if sites_with_reject else None

    print(r"\subsection{Tracker Requests: Pre-Accept, Post-Accept, and Post-Reject}")
    print(
        r"Network requests were classified as tracker or non-tracker for the pre-accept "
        r"(cookie notice visible), post-accept (notice accepted), and post-reject (notice rejected) "
        r"phases. "
        rf"Of the {reachable} reachable sites, {len(sites_with_notice)} displayed a cookie notice "
        rf"and {len(sites_without_notice)} did not. "
        rf"Sites \emph{{with}} a cookie notice averaged \textbf{{{avg_pre_t:.1f}}} tracker requests "
        r"in the pre-accept phase; "
        rf"sites \emph{{without}} a cookie notice averaged \textbf{{{mean_no_notice_t:.1f}}}. "
        rf"For the {len(sites_with_post)} sites where a post-accept scan was performed, "
        rf"the average rose to \textbf{{{avg_post_t:.1f}}} tracker requests. "
    )
    if sites_with_reject:
        print(
            rf"The post-reject phase was completed for {len(sites_with_reject)} sites, "
            rf"averaging \textbf{{{avg_reject_t:.1f}}} tracker requests --- "
            r"a comparison that reveals whether rejecting the cookie notice meaningfully "
            r"reduces third-party tracking activity."
        )
    print(
        rf"Of the {len(sites_with_post)} accepted sites, {tracker_increased} showed \emph{{more}} "
        rf"tracker requests post-accept, {tracker_decreased} showed fewer, "
        rf"and {tracker_same_acc} showed no change."
    )
    if sites_with_reject:
        print(
            rf"Compared to the pre-accept baseline, {rej_vs_pre_more} of the "
            rf"{len(sites_with_reject)} rejected sites showed \emph{{more}} tracker requests "
            rf"post-reject, {rej_vs_pre_less} showed fewer, and {rej_vs_pre_same} showed no change."
        )
    if sites_with_both:
        print(
            rf"Comparing post-reject directly to post-accept across the {len(sites_with_both)} sites "
            rf"with both phases, {rej_vs_acc_less} had \emph{{fewer}} trackers after rejection, "
            rf"{rej_vs_acc_more} had more, and {rej_vs_acc_same} had the same number."
        )
    print()

    col_hdr = (
        r"No notice & Pre & Post-accept & Post-reject \\" if sites_with_reject else r"No notice & Pre & Post-accept \\"
    )
    col_fmt = "lrrrr" if sites_with_reject else "lrrr"
    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{Average tracker request counts per site}")
    print(rf"\begin{{tabular}}{{{col_fmt}}}")
    print(rf"\toprule Metric & {col_hdr} \midrule")
    if sites_with_reject:
        print(
            rf"Avg.\ tracker requests/site & {mean_no_notice_t:.1f} & {avg_pre_t:.1f} & {avg_post_t:.1f} & {avg_reject_t:.1f} \\"
        )
        print(
            rf"Median tracker requests/site & {fmt(med_nn_t, 1)} & {fmt(med_pre_t, 1)} & {fmt(med_post_t, 1)} & {fmt(med_reject_t, 1)} \\"
        )
        print(
            rf"Mode tracker requests/site & {fmt(mode_nn_t, 0)} & {fmt(mode_pre_t, 0)} & {fmt(mode_post_t, 0)} & {fmt(mode_reject_t, 0)} \\"
        )
        print(
            rf"Total tracker requests & {no_notice_trackers_total} & {pre_trackers_total} & {post_trackers_total} & {reject_trackers_total} \\"
        )
        print(
            rf"Total network requests & {no_notice_requests_total} & {pre_requests_total} & {post_requests_total} & {reject_requests_total} \\"
        )
        print(
            rf"Tracker rate & {no_notice_tracker_rate:.1f}\,\% & {pre_tracker_rate:.1f}\,\% & {post_tracker_rate:.1f}\,\% & {reject_tracker_rate:.1f}\,\% \\"
        )
    else:
        print(rf"Avg.\ tracker requests/site & {mean_no_notice_t:.1f} & {avg_pre_t:.1f} & {avg_post_t:.1f} \\")
        print(rf"Median tracker requests/site & {fmt(med_nn_t, 1)} & {fmt(med_pre_t, 1)} & {fmt(med_post_t, 1)} \\")
        print(rf"Mode tracker requests/site & {fmt(mode_nn_t, 0)} & {fmt(mode_pre_t, 0)} & {fmt(mode_post_t, 0)} \\")
        print(rf"Total tracker requests & {no_notice_trackers_total} & {pre_trackers_total} & {post_trackers_total} \\")
        print(rf"Total network requests & {no_notice_requests_total} & {pre_requests_total} & {post_requests_total} \\")
        print(
            rf"Tracker rate & {no_notice_tracker_rate:.1f}\,\% & {pre_tracker_rate:.1f}\,\% & {post_tracker_rate:.1f}\,\% \\"
        )
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")

    if has_tracker_col and (sites_with_post or sites_with_reject):
        print(r"\begin{table}[ht]\centering\footnotesize")
        print(r"\caption{Sites with more / same / fewer tracker requests across phases}")
        print(r"\begin{tabular}{lrrr}")
        print(r"\toprule Comparison & More & Same & Fewer \\ \midrule")
        print(
            rf"Post-accept vs.\ pre-accept ({len(sites_with_post)} sites) & "
            rf"{tracker_increased} & {tracker_same_acc} & {tracker_decreased} \\"
        )
        if sites_with_reject:
            print(
                rf"Post-reject vs.\ pre-accept ({len(sites_with_reject)} sites) & "
                rf"{rej_vs_pre_more} & {rej_vs_pre_same} & {rej_vs_pre_less} \\"
            )
        if sites_with_both:
            print(
                rf"Post-reject vs.\ post-accept ({len(sites_with_both)} sites) & "
                rf"{rej_vs_acc_more} & {rej_vs_acc_same} & {rej_vs_acc_less} \\"
            )
        print(r"\bottomrule\end{tabular}")
        print(r"\end{table}")
        print()

    if pre_dist is not None or no_notice_dist is not None:
        has_no_notice_dist = no_notice_dist is not None
        has_post_dist = post_dist is not None
        has_rej_dist = rej_dist is not None
        n_no_notice = len(sites_without_notice)
        n_pre = len(sites_with_notice)
        n_post = len(sites_with_post)
        n_rej = len(sites_with_reject)

        def cell(count, total):
            pct = count / total * 100 if total else 0
            return rf"{count} ({pct:.0f}\%)"

        col_fmt = (
            "l"
            + ("r" if has_no_notice_dist else "")
            + ("r" if pre_dist is not None else "")
            + ("r" if has_post_dist else "")
            + ("r" if has_rej_dist else "")
        )
        hdr_phases = ""
        if has_no_notice_dist:
            hdr_phases += rf"No notice ({n_no_notice} sites)"
        if pre_dist is not None:
            hdr_phases += ("" if not hdr_phases else " & ") + rf"Pre-accept ({n_pre} sites)"
        if has_post_dist:
            hdr_phases += rf" & Post-accept ({n_post} sites)"
        if has_rej_dist:
            hdr_phases += rf" & Post-reject ({n_rej} sites)"
        print(r"\begin{table}[ht]\centering\footnotesize")
        print(r"\caption{Distribution of sites by tracker request count range}")
        print(rf"\begin{{tabular}}{{{col_fmt}}}")
        print(rf"\toprule Trackers/site & {hdr_phases} \\ \midrule")
        for i, (lo, hi) in enumerate(BUCKETS):
            label = bucket_label(lo, hi)
            row = rf"{label}"
            if has_no_notice_dist:
                row += rf" & {cell(no_notice_dist[i], n_no_notice)}"
            if pre_dist is not None:
                row += rf" & {cell(pre_dist[i], n_pre)}"
            if has_post_dist:
                row += rf" & {cell(post_dist[i], n_post)}"
            if has_rej_dist:
                row += rf" & {cell(rej_dist[i], n_rej)}"
            print(row + r" \\")
        print(r"\bottomrule\end{tabular}")
        print(r"\end{table}")
        print()

    # =========================================================
    # First-party vs Third-party tracker requests
    # =========================================================
    if has_tracker_col:
        _tp_rows = q_safe(
            conn,
            f"""
            SELECT r.phase, r.site_url, r.request_url, r.is_tracker, r.scan_id
            FROM chrome_network_requests r
            JOIN chrome_scans c ON c.id = r.scan_id
            WHERE c.is_error_page=0 AND {NOT_FP} AND r.is_tracker IS NOT NULL
        """,
        )

        _tp_counts = defaultdict(lambda: [0, set()])
        for _ph, _surl, _rurl, _is_t, _sid in _tp_rows:
            _p = cookie_party(_surl, _rurl)
            if _p is None:
                continue
            if _ph == "pre" and _surl not in notice_urls:
                continue
            if _ph == "post_accept" and _surl not in accepted_urls:
                continue
            if _ph == "post_reject" and _surl not in rejected_urls:
                continue
            _tp_counts[(_ph, _p, bool(_is_t))][0] += 1
            _tp_counts[(_ph, _p, bool(_is_t))][1].add(_sid)

        def _tp_n(ph, p, t):
            return _tp_counts[(ph, p, t)][0]

        def _tp_s(ph, p, t):
            return len(_tp_counts[(ph, p, t)][1])

        _tp_pre_1 = _tp_n("pre", "first", True)
        _tp_pre_3 = _tp_n("pre", "third", True)
        _tp_acc_1 = _tp_n("post_accept", "first", True)
        _tp_acc_3 = _tp_n("post_accept", "third", True)
        _tp_rej_1 = _tp_n("post_reject", "first", True)
        _tp_rej_3 = _tp_n("post_reject", "third", True)
        _tp_pre_tot = _tp_pre_1 + _tp_pre_3
        _tp_acc_tot = _tp_acc_1 + _tp_acc_3
        _tp_rej_tot = _tp_rej_1 + _tp_rej_3
        _has_rej_tp = bool(sites_with_reject) and _tp_rej_tot > 0

        _rq_pre_1 = _tp_n("pre", "first", True) + _tp_n("pre", "first", False)
        _rq_pre_3 = _tp_n("pre", "third", True) + _tp_n("pre", "third", False)
        _rq_acc_1 = _tp_n("post_accept", "first", True) + _tp_n("post_accept", "first", False)
        _rq_acc_3 = _tp_n("post_accept", "third", True) + _tp_n("post_accept", "third", False)
        _rq_rej_1 = _tp_n("post_reject", "first", True) + _tp_n("post_reject", "first", False)
        _rq_rej_3 = _tp_n("post_reject", "third", True) + _tp_n("post_reject", "third", False)

        if _tp_pre_tot > 0:
            _tp_par = (
                rf"Breaking tracker requests down by party origin: of the \textbf{{{_tp_pre_tot}}} "
                rf"classifiable tracker requests pre-accept, "
                rf"\textbf{{{_tp_pre_1}}} ({_tp_pre_1 / _tp_pre_tot * 100:.0f}\,\%) were first-party and "
                rf"\textbf{{{_tp_pre_3}}} ({_tp_pre_3 / _tp_pre_tot * 100:.0f}\,\%) were third-party."
            )
            if _tp_acc_tot:
                _tp_par += (
                    rf" After accepting, \textbf{{{_tp_acc_1}}} ({_tp_acc_1 / _tp_acc_tot * 100:.0f}\,\%) "
                    rf"were first-party and \textbf{{{_tp_acc_3}}} "
                    rf"({_tp_acc_3 / _tp_acc_tot * 100:.0f}\,\%) were third-party."
                )
            if _has_rej_tp:
                _tp_par += (
                    rf" After rejecting, \textbf{{{_tp_rej_1}}} ({_tp_rej_1 / _tp_rej_tot * 100:.0f}\,\%) "
                    rf"were first-party and \textbf{{{_tp_rej_3}}} "
                    rf"({_tp_rej_3 / _tp_rej_tot * 100:.0f}\,\%) were third-party."
                )
            print(_tp_par)
            print()

            # Top third-party tracker domains
            _third_domain_counts = defaultdict(int)
            for _ph2, _surl2, _rurl2, _is_t2, _sid2 in _tp_rows:
                if not _is_t2:
                    continue
                if cookie_party(_surl2, _rurl2) != "third":
                    continue
                _dom = _reg_domain(_rurl2) if _rurl2 else None
                if _dom:
                    _third_domain_counts[_dom] += 1
            if _third_domain_counts:
                _sorted_doms = sorted(_third_domain_counts.items(), key=lambda x: x[1], reverse=True)
                _top_n = min(5, len(_sorted_doms))
                _top_doms = _sorted_doms[:_top_n]
                _top_total = sum(_third_domain_counts.values())
                _top_sum = sum(c for _, c in _top_doms)
                _top_pct = _top_sum / _top_total * 100 if _top_total else 0
                _names = [rf"\texttt{{{d}}}" for d, _ in _top_doms]
                if len(_names) == 1:
                    _names_str = _names[0]
                elif len(_names) == 2:
                    _names_str = rf"{_names[0]} and {_names[1]}"
                else:
                    _names_str = ", ".join(_names[:-1]) + rf", and {_names[-1]}"
                print(
                    rf"The most prevalent third-party tracking domains (across all phases) were "
                    rf"{_names_str}, together accounting for "
                    rf"\textbf{{{_top_pct:.0f}\,\%}} of all third-party tracker requests."
                )
                print()

            # Summary table: first/third party tracker counts by phase
            print(r"\begin{table}[ht]\centering\footnotesize")
            print(r"\caption{First- and third-party tracker requests by phase}\label{tab:tracker_party}")
            if _has_rej_tp:
                print(r"\begin{tabular}{lrrrrrr}")
                print(
                    r"\toprule Party & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} & \multicolumn{2}{c}{Post-reject} \\"
                )
                print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
                print(r"& Trackers & \% & Trackers & \% & Trackers & \% \\ \midrule")
                for _pl, _pn, _an, _rn in [
                    ("First-party", _tp_pre_1, _tp_acc_1, _tp_rej_1),
                    ("Third-party", _tp_pre_3, _tp_acc_3, _tp_rej_3),
                ]:
                    _pp = _pn / _tp_pre_tot * 100 if _tp_pre_tot else 0
                    _ap = _an / _tp_acc_tot * 100 if _tp_acc_tot else 0
                    _rp = _rn / _tp_rej_tot * 100 if _tp_rej_tot else 0
                    print(rf"  {_pl} & {_pn} & {_pp:.0f}\,\% & {_an} & {_ap:.0f}\,\% & {_rn} & {_rp:.0f}\,\% \\")
                print(r"\midrule")
                print(
                    rf"  \textbf{{Total}} & \textbf{{{_tp_pre_tot}}} & & \textbf{{{_tp_acc_tot}}} & & \textbf{{{_tp_rej_tot}}} & \\"
                )
            else:
                print(r"\begin{tabular}{lrrrr}")
                print(r"\toprule Party & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} \\")
                print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}")
                print(r"& Trackers & \% & Trackers & \% \\ \midrule")
                for _pl, _pn, _an in [
                    ("First-party", _tp_pre_1, _tp_acc_1),
                    ("Third-party", _tp_pre_3, _tp_acc_3),
                ]:
                    _pp = _pn / _tp_pre_tot * 100 if _tp_pre_tot else 0
                    _ap = _an / _tp_acc_tot * 100 if _tp_acc_tot else 0
                    print(rf"  {_pl} & {_pn} & {_pp:.0f}\,\% & {_an} & {_ap:.0f}\,\% \\")
                print(r"\midrule")
                print(rf"  \textbf{{Total}} & \textbf{{{_tp_pre_tot}}} & & \textbf{{{_tp_acc_tot}}} & \\")
            print(r"\bottomrule\end{tabular}")
            print(r"\end{table}")

            # Tracker rate within each party type
            print(r"\begin{table}[ht]\centering\footnotesize")
            print(r"\caption{Tracker rate within first- and third-party requests}\label{tab:tracker_rate_party}")
            if _has_rej_tp:
                print(r"\begin{tabular}{lrrrrrr}")
                print(
                    r"\toprule Party & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} & \multicolumn{2}{c}{Post-reject} \\"
                )
                print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
                print(r"& Requests & Rate & Requests & Rate & Requests & Rate \\ \midrule")
                for _pl, _pk, _rq_pre, _rq_acc, _rq_rej in [
                    ("First-party", "first", _rq_pre_1, _rq_acc_1, _rq_rej_1),
                    ("Third-party", "third", _rq_pre_3, _rq_acc_3, _rq_rej_3),
                ]:
                    _pr = _tp_n("pre", _pk, True) / _rq_pre * 100 if _rq_pre else 0
                    _ar = _tp_n("post_accept", _pk, True) / _rq_acc * 100 if _rq_acc else 0
                    _rr = _tp_n("post_reject", _pk, True) / _rq_rej * 100 if _rq_rej else 0
                    print(
                        rf"  {_pl} & {_rq_pre} & {_pr:.1f}\,\% & {_rq_acc} & {_ar:.1f}\,\% & {_rq_rej} & {_rr:.1f}\,\% \\"
                    )
            else:
                print(r"\begin{tabular}{lrrrr}")
                print(r"\toprule Party & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} \\")
                print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}")
                print(r"& Requests & Rate & Requests & Rate \\ \midrule")
                for _pl, _pk, _rq_pre, _rq_acc in [
                    ("First-party", "first", _rq_pre_1, _rq_acc_1),
                    ("Third-party", "third", _rq_pre_3, _rq_acc_3),
                ]:
                    _pr = _tp_n("pre", _pk, True) / _rq_pre * 100 if _rq_pre else 0
                    _ar = _tp_n("post_accept", _pk, True) / _rq_acc * 100 if _rq_acc else 0
                    print(rf"  {_pl} & {_rq_pre} & {_pr:.1f}\,\% & {_rq_acc} & {_ar:.1f}\,\% \\")
            print(r"\bottomrule\end{tabular}")
            print(r"\end{table}")
            print()

    if SHOW_PER:
        print(r"Per-site figures are in Table~\ref{tab:trackers} in the appendix.")
    print()

    # Appendix: per-site tracker table
    if SHOW_PER:
        if sites_with_reject:
            print(r"\begin{table*}[ht]\centering\footnotesize")
            print(r"\caption{Per-site tracker request counts (pre vs post-accept vs post-reject)}\label{tab:trackers}")
            print(r"\begin{tabular}{>{\ttfamily}p{2.2cm} r r r r r r r r r}")
            print(
                r"\toprule \normalfont URL & \multicolumn{3}{c}{Trackers} & \multicolumn{3}{c}{Total Req.} & \multicolumn{2}{c}{$\Delta$ trackers} \\"
            )
            print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-9}")
            print(r"\normalfont & Pre & Acc & Rej & Pre & Acc & Rej & vs.\ Acc & vs.\ Rej \\ \midrule")
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
                print(
                    rf"  {latex_escape(url)} & "
                    rf"{pre_s} & {post_s} & {rej_s} & "
                    rf"{pre_tot_s} & {post_tot_s} & {rej_tot_s} & "
                    rf"{d_acc} & {d_rej} \\"
                )
        else:
            print(r"\begin{table*}[ht]\centering\footnotesize")
            print(r"\caption{Per-site tracker request counts (pre vs post accept)}\label{tab:trackers}")
            print(r"\begin{tabular}{>{\ttfamily}p{2.5cm} r r r r r}")
            print(
                r"\toprule \normalfont URL & \multicolumn{2}{c}{Trackers} & \multicolumn{2}{c}{Total Req.} & $\Delta$ \\"
            )
            print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}")
            print(r"\normalfont & Pre & Post & Pre & Post & \\ \midrule")
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
                print(
                    rf"  {latex_escape(url)} & "
                    rf"{pre_t_s} & {post_t_s} & "
                    rf"{pre_tot_s} & {post_tot_s} & "
                    rf"{delta_s} \\"
                )
        print(r"\bottomrule\end{tabular}")
        print(r"\end{table*}")


if __name__ == "__main__":
    import sys

    from .utils import open_merged

    db_names = sys.argv[1:] if len(sys.argv) > 1 else None
    conn, db_paths = open_merged(db_names)
    try:
        run(conn)
    finally:
        conn.close()
