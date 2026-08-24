from collections import Counter, defaultdict
from statistics import median as _median_stat

from .utils import (
    ACCEPTED,
    NORM_CAT_SQL,
    NOT_FP,
    SHOW_PER,
    ck_delta,
    cookie_party,
    fmt,
    latex_escape,
    q_safe,
    read_storage_from_path,
)


def _median(vals):
    return _median_stat(vals) if vals else None


def _mode(vals):
    return Counter(vals).most_common(1)[0][0] if vals else None


def run(conn):
    _acc_scan_id_sql = f"SELECT id FROM chrome_scans WHERE {ACCEPTED} AND is_error_page=0 AND {NOT_FP}"
    _rej_scan_id_sql = f"SELECT id FROM chrome_scans WHERE cookie_notice_rejected=1 AND is_error_page=0 AND {NOT_FP}"

    _cc_acc_agg = q_safe(
        conn,
        f"SELECT COUNT(*), COUNT(DISTINCT scan_id) FROM cookie_classifications WHERE phase='post_accept' AND scan_id IN ({_acc_scan_id_sql})",
    )
    _cc_rej_agg = q_safe(
        conn,
        f"SELECT COUNT(*), COUNT(DISTINCT scan_id) FROM cookie_classifications WHERE phase='post_reject' AND scan_id IN ({_rej_scan_id_sql})",
    )
    acc_total = (_cc_acc_agg[0][0] or 0) if _cc_acc_agg else 0
    acc_sites = (_cc_acc_agg[0][1] or 0) if _cc_acc_agg else 0
    rej_total = (_cc_rej_agg[0][0] or 0) if _cc_rej_agg else 0
    rej_sites = (_cc_rej_agg[0][1] or 0) if _cc_rej_agg else 0
    has_rej_cookies = rej_sites > 0

    _nn_scan_id_sql = (
        "SELECT id FROM chrome_scans WHERE (cookie_notice_detected=0 OR false_positive=1) AND is_error_page=0"
    )
    _nn_scan_id_set = {r[0] for r in q_safe(conn, _nn_scan_id_sql)}
    _nn_url_set = {
        r[0]
        for r in q_safe(
            conn,
            "SELECT url FROM chrome_scans WHERE (cookie_notice_detected=0 OR false_positive=1) AND is_error_page=0",
        )
    }
    _nn_site_count = len(_nn_url_set)
    _nn_cc_agg = q_safe(
        conn,
        f"SELECT COUNT(*), COUNT(DISTINCT scan_id) FROM cookie_classifications WHERE phase='pre' AND scan_id IN ({_nn_scan_id_sql})",
    )
    nn_total = _nn_cc_agg[0][0] if _nn_cc_agg else 0
    nn_sites = _nn_cc_agg[0][1] if _nn_cc_agg else 0
    avg_nn = nn_total / _nn_site_count if _nn_site_count else None

    # With-notice sites only (excludes cookie_notice_detected=0 sites to avoid double-counting)
    _wn_scan_id_sql = f"SELECT id FROM chrome_scans WHERE (cookie_notice_detected IS NULL OR cookie_notice_detected!=0) AND is_error_page=0 AND {NOT_FP}"
    _wn_url_set = {
        r[0]
        for r in q_safe(
            conn,
            f"SELECT url FROM chrome_scans WHERE (cookie_notice_detected IS NULL OR cookie_notice_detected!=0) AND is_error_page=0 AND {NOT_FP}",
        )
    }
    _wn_pre_agg = q_safe(
        conn,
        f"SELECT COUNT(*), COUNT(DISTINCT scan_id) FROM cookie_classifications WHERE phase='pre' AND scan_id IN ({_wn_scan_id_sql})",
    )
    pre_total = _wn_pre_agg[0][0] if _wn_pre_agg else 0
    pre_sites = _wn_pre_agg[0][1] if _wn_pre_agg else 0
    _avg_pre_denom = q_safe(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE (cookie_notice_detected IS NULL OR cookie_notice_detected!=0) AND is_error_page=0 AND {NOT_FP}",
    )[0][0]
    _avg_acc_denom = q_safe(
        conn, f"SELECT COUNT(*) FROM chrome_scans WHERE {ACCEPTED} AND is_error_page=0 AND {NOT_FP}"
    )[0][0]
    _avg_rej_denom = q_safe(
        conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_rejected=1 AND is_error_page=0 AND {NOT_FP}"
    )[0][0]
    avg_pre_db = pre_total / _avg_pre_denom if _avg_pre_denom else None
    avg_acc_db = acc_total / _avg_acc_denom if _avg_acc_denom else None
    avg_rej_db = rej_total / _avg_rej_denom if _avg_rej_denom else None

    _storage_scan_rows = q_safe(
        conn,
        """SELECT url, pre_storage_path, post_accept_storage_path, post_reject_storage_path
           FROM chrome_scans WHERE is_error_page=0
           ORDER BY url""",
    )
    _storage_data = []
    for _url, _pre_p, _acc_p, _rej_p in _storage_scan_rows:
        _pls, _pss = read_storage_from_path(_pre_p)
        _als, _ass = read_storage_from_path(_acc_p)
        _rls, _rss = read_storage_from_path(_rej_p)
        _storage_data.append((_url, _pls, _pss, _als, _ass, _rls, _rss))
    _acc_url_set = {
        r[0]
        for r in q_safe(conn, f"SELECT url FROM chrome_scans WHERE {ACCEPTED} AND is_error_page=0 AND {NOT_FP}") or []
    }
    _rej_url_set = {
        r[0]
        for r in q_safe(
            conn, f"SELECT url FROM chrome_scans WHERE cookie_notice_rejected=1 AND is_error_page=0 AND {NOT_FP}"
        )
        or []
    }

    _measured_s = [r for r in _storage_data if r[1] is not None]
    _acc_s = [r for r in _measured_s if r[3] is not None and r[0] in _acc_url_set]
    _rej_s = [r for r in _measured_s if r[5] is not None and r[0] in _rej_url_set]

    if not _measured_s and bool(
        q_safe(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='storage_classifications'")
    ):
        for row in q_safe(
            conn,
            """
                SELECT cs.url,
                       COALESCE(pre_ls.n, 0), COALESCE(pre_ss.n, 0),
                       acc_ls.n, acc_ss.n,
                       rej_ls.n, rej_ss.n
                FROM (SELECT DISTINCT scan_id FROM storage_classifications WHERE phase='pre') base
                JOIN chrome_scans cs ON cs.id = base.scan_id
                LEFT JOIN (SELECT scan_id, COUNT(*) n FROM storage_classifications
                           WHERE phase='pre' AND storage_type='local' GROUP BY scan_id) pre_ls
                       ON pre_ls.scan_id = base.scan_id
                LEFT JOIN (SELECT scan_id, COUNT(*) n FROM storage_classifications
                           WHERE phase='pre' AND storage_type='session' GROUP BY scan_id) pre_ss
                       ON pre_ss.scan_id = base.scan_id
                LEFT JOIN (SELECT scan_id, COUNT(*) n FROM storage_classifications
                           WHERE phase='post_accept' AND storage_type='local' GROUP BY scan_id) acc_ls
                       ON acc_ls.scan_id = base.scan_id
                LEFT JOIN (SELECT scan_id, COUNT(*) n FROM storage_classifications
                           WHERE phase='post_accept' AND storage_type='session' GROUP BY scan_id) acc_ss
                       ON acc_ss.scan_id = base.scan_id
                LEFT JOIN (SELECT scan_id, COUNT(*) n FROM storage_classifications
                           WHERE phase='post_reject' AND storage_type='local' GROUP BY scan_id) rej_ls
                       ON rej_ls.scan_id = base.scan_id
                LEFT JOIN (SELECT scan_id, COUNT(*) n FROM storage_classifications
                           WHERE phase='post_reject' AND storage_type='session' GROUP BY scan_id) rej_ss
                       ON rej_ss.scan_id = base.scan_id
                ORDER BY cs.url""",
        ):
            _storage_data.append(tuple(row))
        _measured_s = [r for r in _storage_data if r[1] is not None]
        _acc_s = [r for r in _measured_s if r[3] is not None and r[0] in _acc_url_set]
        _rej_s = [r for r in _measured_s if r[5] is not None and r[0] in _rej_url_set]

    _has_storage_files = bool(_measured_s)
    _has_rej_storage = bool(_rej_s)
    _nn_measured_s = [r for r in _measured_s if r[0] in _nn_url_set]
    _wn_measured_s = [r for r in _measured_s if r[0] not in _nn_url_set]
    _sum_pre_ls = sum(r[1] for r in _wn_measured_s)
    _sum_pre_ss = sum(r[2] for r in _wn_measured_s)
    _sum_acc_ls = sum((r[3] or 0) for r in _acc_s)
    _sum_acc_ss = sum((r[4] or 0) for r in _acc_s)
    _sum_rej_ls = sum((r[5] or 0) for r in _rej_s)
    _sum_rej_ss = sum((r[6] or 0) for r in _rej_s)
    _ls_pre_sites = sum(1 for r in _wn_measured_s if (r[1] or 0) > 0)
    _ss_pre_sites = sum(1 for r in _wn_measured_s if (r[2] or 0) > 0)
    _ls_acc_sites = sum(1 for r in _acc_s if (r[3] or 0) > 0)
    _ss_acc_sites = sum(1 for r in _acc_s if (r[4] or 0) > 0)
    _ls_rej_sites = sum(1 for r in _rej_s if (r[5] or 0) > 0)
    _ss_rej_sites = sum(1 for r in _rej_s if (r[6] or 0) > 0)
    _sum_nn_ls = sum(r[1] for r in _nn_measured_s if r[1] is not None)
    _sum_nn_ss = sum(r[2] for r in _nn_measured_s if r[2] is not None)
    _ls_nn_sites = sum(1 for r in _nn_measured_s if (r[1] or 0) > 0)
    _ss_nn_sites = sum(1 for r in _nn_measured_s if (r[2] or 0) > 0)
    # All-measured-site counts (denominator for averages, matching cookie avg approach)
    _all_nn_s = len(_nn_measured_s)
    _all_pre_s = len(_wn_measured_s)
    _all_acc_s = len(_acc_s)
    _all_rej_s = len(_rej_s)
    _avg_pre_ls = _sum_pre_ls / _all_pre_s if _all_pre_s else None
    _avg_pre_ss = _sum_pre_ss / _all_pre_s if _all_pre_s else None
    _avg_acc_ls = _sum_acc_ls / _all_acc_s if _all_acc_s else None
    _avg_acc_ss = _sum_acc_ss / _all_acc_s if _all_acc_s else None
    _avg_rej_ls = _sum_rej_ls / _all_rej_s if _all_rej_s else None
    _avg_rej_ss = _sum_rej_ss / _all_rej_s if _all_rej_s else None
    _avg_nn_ls = _sum_nn_ls / _all_nn_s if _all_nn_s else None
    _avg_nn_ss = _sum_nn_ss / _all_nn_s if _all_nn_s else None
    _ls_increased_acc = sum(1 for r in _acc_s if r[3] > r[1])
    _ls_reduced_acc = sum(1 for r in _acc_s if r[3] < r[1])
    _ls_increased_rej = sum(1 for r in _rej_s if r[5] > r[1])
    _ls_reduced_rej = sum(1 for r in _rej_s if r[5] < r[1])

    cat_rows = q_safe(
        conn,
        f"""SELECT phase, {NORM_CAT_SQL} AS cat, COUNT(*) AS n, COUNT(DISTINCT scan_id) AS sites
           FROM cookie_classifications cc
           JOIN chrome_scans cs ON cs.id=cc.scan_id
           WHERE cs.is_error_page=0 AND {NOT_FP} AND cc.phase='pre'
           GROUP BY phase, cat
        UNION ALL
        SELECT phase, {NORM_CAT_SQL} AS cat, COUNT(*) AS n, COUNT(DISTINCT scan_id) AS sites
           FROM cookie_classifications
           WHERE phase='post_accept' AND scan_id IN ({_acc_scan_id_sql})
           GROUP BY phase, cat
        UNION ALL
        SELECT phase, {NORM_CAT_SQL} AS cat, COUNT(*) AS n, COUNT(DISTINCT scan_id) AS sites
           FROM cookie_classifications
           WHERE phase='post_reject' AND scan_id IN ({_rej_scan_id_sql})
           GROUP BY phase, cat
        ORDER BY 1, 3 DESC""",
    )
    cat_by_phase = defaultdict(dict)
    for _phase, _cat, _n, _sites in cat_rows:
        cat_by_phase[_phase][_cat] = (_n, _sites)
    # Override pre with with-notice sites only (cat_rows includes all sites for pre)
    _wn_cat_sql = (
        f"SELECT {NORM_CAT_SQL} AS cat, COUNT(*) AS n, COUNT(DISTINCT scan_id) AS sites"
        f" FROM cookie_classifications"
        f" WHERE phase='pre' AND scan_id IN ({_wn_scan_id_sql})"
        f" GROUP BY cat ORDER BY n DESC"
    )
    _wn_cat_rows = q_safe(conn, _wn_cat_sql)
    cat_by_phase["pre"] = {cat: (n, sites) for cat, n, sites in _wn_cat_rows}

    all_cats = sorted(
        {cat for _, cat, _, _ in cat_rows},
        key=lambda c: (
            -(cat_by_phase.get("pre", {}).get(c, (0, 0))[0] + cat_by_phase.get("post_accept", {}).get(c, (0, 0))[0])
        ),
    )

    _nn_cat_rows = q_safe(
        conn,
        f"""
        SELECT {NORM_CAT_SQL} AS cat, COUNT(*) AS n, COUNT(DISTINCT scan_id) AS sites
        FROM cookie_classifications
        WHERE phase='pre' AND scan_id IN ({_nn_scan_id_sql})
        GROUP BY cat ORDER BY n DESC""",
    )
    nn_cat = {cat: (n, sites) for cat, n, sites in _nn_cat_rows}

    print(r"\subsection{Browser Cookies: Pre-Accept, Post-Accept, and Post-Reject}")
    print(
        rf"Cookie classifications were drawn from the \texttt{{cookie\_classifications}} table, "
        rf"which records each cookie's name, domain, and category for every scan phase. "
        rf"In the pre-accept phase, \textbf{{{pre_total}}} cookies were observed across "
        rf"\textbf{{{_avg_pre_denom}}} sites (avg.\ {fmt(avg_pre_db)} per site). "
        rf"After accepting the cookie notice, this rose to \textbf{{{acc_total}}} cookies "
        rf"across \textbf{{{_avg_acc_denom}}} sites (avg.\ {fmt(avg_acc_db)} per site). "
    )
    if has_rej_cookies:
        print(
            rf"After rejecting, \textbf{{{rej_total}}} cookies were observed across "
            rf"\textbf{{{_avg_rej_denom}}} sites (avg.\ {fmt(avg_rej_db)} per site), "
            rf"compared with {fmt(avg_pre_db)} pre-accept --- suggesting rejection "
            r"does not fully prevent cookie setting."
        )
    print()

    # Master summary table: cookies, localStorage, sessionStorage
    _has_rej_any = has_rej_cookies or _has_rej_storage
    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{Client-side storage counts by type and phase}\label{tab:storage_master}")
    if _has_rej_any:
        print(r"\begin{tabular}{lrrrrrrrrrrrrr}")
        print(
            r"\toprule Type & \multicolumn{3}{c}{No notice} & \multicolumn{3}{c}{Pre} & \multicolumn{3}{c}{Post-accept} & \multicolumn{3}{c}{Post-reject} \\"
        )
        print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}\cmidrule(lr){11-13}")
        print(
            r"& Total & Sites & Avg/site & Total & Sites & Avg/site & Total & Sites & Avg/site & Total & Sites & Avg/site \\ \midrule"
        )
        print(
            rf"Browser cookies & {nn_total} & {_nn_site_count} & {fmt(avg_nn)} & {pre_total} & {_avg_pre_denom} & {fmt(avg_pre_db)} & {acc_total} & {_avg_acc_denom} & {fmt(avg_acc_db)} & {rej_total} & {_avg_rej_denom} & {fmt(avg_rej_db)} \\"
        )
        if _has_storage_files:
            print(
                rf"localStorage    & {_sum_nn_ls} & {_all_nn_s} & {fmt(_avg_nn_ls)} & {_sum_pre_ls} & {_all_pre_s} & {fmt(_avg_pre_ls)} & {_sum_acc_ls} & {_all_acc_s} & {fmt(_avg_acc_ls)} & {_sum_rej_ls} & {_all_rej_s} & {fmt(_avg_rej_ls)} \\"
            )
            print(
                rf"sessionStorage  & {_sum_nn_ss} & {_all_nn_s} & {fmt(_avg_nn_ss)} & {_sum_pre_ss} & {_all_pre_s} & {fmt(_avg_pre_ss)} & {_sum_acc_ss} & {_all_acc_s} & {fmt(_avg_acc_ss)} & {_sum_rej_ss} & {_all_rej_s} & {fmt(_avg_rej_ss)} \\"
            )
    else:
        print(r"\begin{tabular}{lrrrrrrrrrr}")
        print(
            r"\toprule Type & \multicolumn{3}{c}{No notice} & \multicolumn{3}{c}{Pre} & \multicolumn{3}{c}{Post-accept} \\"
        )
        print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}")
        print(r"& Total & Sites & Avg/site & Total & Sites & Avg/site & Total & Sites & Avg/site \\ \midrule")
        print(
            rf"Browser cookies & {nn_total} & {_nn_site_count} & {fmt(avg_nn)} & {pre_total} & {_avg_pre_denom} & {fmt(avg_pre_db)} & {acc_total} & {_avg_acc_denom} & {fmt(avg_acc_db)} \\"
        )
        if _has_storage_files:
            print(
                rf"localStorage    & {_sum_nn_ls} & {_all_nn_s} & {fmt(_avg_nn_ls)} & {_sum_pre_ls} & {_all_pre_s} & {fmt(_avg_pre_ls)} & {_sum_acc_ls} & {_all_acc_s} & {fmt(_avg_acc_ls)} \\"
            )
            print(
                rf"sessionStorage  & {_sum_nn_ss} & {_all_nn_s} & {fmt(_avg_nn_ss)} & {_sum_pre_ss} & {_all_pre_s} & {fmt(_avg_pre_ss)} & {_sum_acc_ss} & {_all_acc_s} & {fmt(_avg_acc_ss)} \\"
            )
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")

    # Category breakdown table
    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{Cookie categories by scan phase (count of cookies)}\label{tab:cookie_cats}")
    if has_rej_cookies:
        print(r"\begin{tabular}{lrrrrrrrrrrrrr}")
        print(
            r"\toprule Category & \multicolumn{3}{c}{No notice} & \multicolumn{3}{c}{Pre} & \multicolumn{3}{c}{Post-accept} & \multicolumn{3}{c}{Post-reject} \\"
        )
        print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}\cmidrule(lr){11-13}")
        print(r"& $n$ & Sites & \% & $n$ & Sites & \% & $n$ & Sites & \% & $n$ & Sites & \% \\ \midrule")
        for cat in all_cats:
            nn_n, nn_s = nn_cat.get(cat, (0, 0))
            pre_n, pre_s = cat_by_phase["pre"].get(cat, (0, 0))
            acc_n, acc_s = cat_by_phase["post_accept"].get(cat, (0, 0))
            rej_n, rej_s = cat_by_phase["post_reject"].get(cat, (0, 0))
            nn_pct = nn_n / nn_total * 100 if nn_total else 0
            pre_pct = pre_n / pre_total * 100 if pre_total else 0
            acc_pct = acc_n / acc_total * 100 if acc_total else 0
            rej_pct = rej_n / rej_total * 100 if rej_total else 0
            print(
                rf"  {latex_escape(cat)} & {nn_n} & {nn_s} & {nn_pct:.0f}\,\% & "
                rf"{pre_n} & {pre_s} & {pre_pct:.0f}\,\% & "
                rf"{acc_n} & {acc_s} & {acc_pct:.0f}\,\% & "
                rf"{rej_n} & {rej_s} & {rej_pct:.0f}\,\% \\"
            )
        print(r"\midrule")
        print(
            rf"  \textbf{{Total}} & \textbf{{{nn_total}}} & \textbf{{{nn_sites}}} & & \textbf{{{pre_total}}} & \textbf{{{pre_sites}}} & & \textbf{{{acc_total}}} & \textbf{{{acc_sites}}} & & \textbf{{{rej_total}}} & \textbf{{{rej_sites}}} & \\"
        )
    else:
        print(r"\begin{tabular}{lrrrrrrrrrr}")
        print(
            r"\toprule Category & \multicolumn{3}{c}{No notice} & \multicolumn{3}{c}{Pre} & \multicolumn{3}{c}{Post-accept} \\"
        )
        print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}")
        print(r"& $n$ & Sites & \% & $n$ & Sites & \% & $n$ & Sites & \% \\ \midrule")
        for cat in all_cats:
            nn_n, nn_s = nn_cat.get(cat, (0, 0))
            pre_n, pre_s = cat_by_phase["pre"].get(cat, (0, 0))
            acc_n, acc_s = cat_by_phase["post_accept"].get(cat, (0, 0))
            nn_pct = nn_n / nn_total * 100 if nn_total else 0
            pre_pct = pre_n / pre_total * 100 if pre_total else 0
            acc_pct = acc_n / acc_total * 100 if acc_total else 0
            print(
                rf"  {latex_escape(cat)} & {nn_n} & {nn_s} & {nn_pct:.0f}\,\% & "
                rf"{pre_n} & {pre_s} & {pre_pct:.0f}\,\% & "
                rf"{acc_n} & {acc_s} & {acc_pct:.0f}\,\% \\"
            )
        print(r"\midrule")
        print(
            rf"  \textbf{{Total}} & \textbf{{{nn_total}}} & \textbf{{{nn_sites}}} & & \textbf{{{pre_total}}} & \textbf{{{pre_sites}}} & & \textbf{{{acc_total}}} & \textbf{{{acc_sites}}} & \\"
        )
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")

    # Appendix: per-site cookie counts
    site_cookie_rows = q_safe(
        conn,
        """SELECT cs.url,
                  COALESCE(SUM(CASE WHEN cc.phase='pre'         THEN 1 ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN cc.phase='post_accept' THEN 1 ELSE 0 END), 0),
                  COALESCE(SUM(CASE WHEN cc.phase='post_reject' THEN 1 ELSE 0 END), 0),
                  CASE WHEN (cs.cookie_notice_accepted=1 OR cs.manually_verified=1) AND (cs.false_positive IS NULL OR cs.false_positive=0) THEN 1 ELSE 0 END,
                  CASE WHEN cs.cookie_notice_rejected=1 AND (cs.false_positive IS NULL OR cs.false_positive=0) THEN 1 ELSE 0 END
           FROM chrome_scans cs
           LEFT JOIN cookie_classifications cc ON cc.scan_id = cs.id
           WHERE cs.is_error_page=0
           GROUP BY cs.id
           ORDER BY cs.url""",
    )
    site_cookie_data = [
        (url, pre_n, acc_n, rej_n, did_accept, did_reject)
        for url, pre_n, acc_n, rej_n, did_accept, did_reject in site_cookie_rows
    ]

    if SHOW_PER:
        print(r"\begin{table}[ht]\centering\footnotesize")
        if has_rej_cookies:
            print(r"\caption{Per-site cookie counts by phase}\label{tab:cookie_persite}")
            print(r"\begin{tabular}{>{\ttfamily}p{2.6cm} r r r r r}")
            print(r"\toprule \normalfont URL & Pre & Post-acc & Post-rej & $\Delta$\,Acc & $\Delta$\,Rej \\ \midrule")
            for url, pre_n, acc_n, rej_n, *_ in site_cookie_data:
                print(
                    rf"  {latex_escape(url)} & {pre_n} & {acc_n} & {rej_n} & "
                    rf"{ck_delta(pre_n, acc_n)} & {ck_delta(pre_n, rej_n)} \\"
                )
        else:
            print(r"\caption{Per-site cookie counts by phase}\label{tab:cookie_persite}")
            print(r"\begin{tabular}{>{\ttfamily}p{3cm} r r r}")
            print(r"\toprule \normalfont URL & Pre & Post-accept & $\Delta$ \\ \midrule")
            for url, pre_n, acc_n, rej_n, *_ in site_cookie_data:
                print(rf"  {latex_escape(url)} & {pre_n} & {acc_n} & {ck_delta(pre_n, acc_n)} \\")
        print(r"\bottomrule\end{tabular}")
        print(r"\end{table}")

    # Bucketed distribution of per-site cookie counts
    _BUCKETS = [(0, 0), (1, 5), (6, 10), (11, 25), (26, 50), (51, None)]
    _BUCKET_LABELS = ["0", "1--5", "6--10", "11--25", "26--50", "51+"]

    def _bucket_counts(values):
        counts = []
        for lo, hi in _BUCKETS:
            if hi is None:
                counts.append(sum(1 for v in values if v >= lo))
            else:
                counts.append(sum(1 for v in values if lo <= v <= hi))
        return counts

    _pre_vals = [r[1] for r in site_cookie_data if r[0] not in _nn_url_set]
    _acc_vals = [r[2] for r in site_cookie_data if r[4]]
    _rej_vals = [r[3] for r in site_cookie_data if r[5]]
    _nn_vals = [r[1] for r in site_cookie_data if r[0] in _nn_url_set]
    _med_pre_ck = _median(_pre_vals)
    _mode_pre_ck = _mode(_pre_vals)
    _med_acc_ck = _median(_acc_vals)
    _mode_acc_ck = _mode(_acc_vals)
    _med_rej_ck = _median(_rej_vals)
    _mode_rej_ck = _mode(_rej_vals)
    _med_nn_ck = _median(_nn_vals)
    _mode_nn_ck = _mode(_nn_vals)
    _pre_buckets = _bucket_counts(_pre_vals)
    _acc_buckets = _bucket_counts(_acc_vals)
    _rej_buckets = _bucket_counts(_rej_vals)
    _nn_buckets = _bucket_counts(_nn_vals)
    _n_pre_sites = len(_pre_vals)
    _n_acc_sites = len(_acc_vals)
    _n_rej_sites = len(_rej_vals)
    _n_nn_sites = len(_nn_vals)

    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{Distribution of sites by cookie count}\label{tab:cookie_buckets}")
    if has_rej_cookies:
        print(r"\begin{tabular}{lrrrrrrrr}")
        print(
            r"\toprule Cookie count & \multicolumn{2}{c}{No notice} & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} & \multicolumn{2}{c}{Post-reject} \\"
        )
        print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}\cmidrule(lr){8-9}")
        print(r"& Sites & \% & Sites & \% & Sites & \% & Sites & \% \\ \midrule")
        for label, nb, pb, ab, rb in zip(_BUCKET_LABELS, _nn_buckets, _pre_buckets, _acc_buckets, _rej_buckets):
            np_ = nb / _n_nn_sites * 100 if _n_nn_sites else 0
            pp = pb / _n_pre_sites * 100 if _n_pre_sites else 0
            ap = ab / _n_acc_sites * 100 if _n_acc_sites else 0
            rp = rb / _n_rej_sites * 100 if _n_rej_sites else 0
            print(
                rf"  {label} & {nb} & {np_:.0f}\,\% & {pb} & {pp:.0f}\,\% & {ab} & {ap:.0f}\,\% & {rb} & {rp:.0f}\,\% \\"
            )
        print(r"\midrule")
        print(
            rf"  \textbf{{Total}} & \textbf{{{_n_nn_sites}}} & & \textbf{{{_n_pre_sites}}} & & \textbf{{{_n_acc_sites}}} & & \textbf{{{_n_rej_sites}}} & \\"
        )
        print(
            rf"  Median & {fmt(_med_nn_ck, 1)} & & {fmt(_med_pre_ck, 1)} & & {fmt(_med_acc_ck, 1)} & & {fmt(_med_rej_ck, 1)} & \\"
        )
        print(
            rf"  Mode   & {fmt(_mode_nn_ck, 0)} & & {fmt(_mode_pre_ck, 0)} & & {fmt(_mode_acc_ck, 0)} & & {fmt(_mode_rej_ck, 0)} & \\"
        )
    else:
        print(r"\begin{tabular}{lrrrrrr}")
        print(
            r"\toprule Cookie count & \multicolumn{2}{c}{No notice} & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} \\"
        )
        print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
        print(r"& Sites & \% & Sites & \% & Sites & \% \\ \midrule")
        for label, nb, pb, ab in zip(_BUCKET_LABELS, _nn_buckets, _pre_buckets, _acc_buckets):
            np_ = nb / _n_nn_sites * 100 if _n_nn_sites else 0
            pp = pb / _n_pre_sites * 100 if _n_pre_sites else 0
            ap = ab / _n_acc_sites * 100 if _n_acc_sites else 0
            print(rf"  {label} & {nb} & {np_:.0f}\,\% & {pb} & {pp:.0f}\,\% & {ab} & {ap:.0f}\,\% \\")
        print(r"\midrule")
        print(
            rf"  \textbf{{Total}} & \textbf{{{_n_nn_sites}}} & & \textbf{{{_n_pre_sites}}} & & \textbf{{{_n_acc_sites}}} & \\"
        )
        print(rf"  Median & {fmt(_med_nn_ck, 1)} & & {fmt(_med_pre_ck, 1)} & & {fmt(_med_acc_ck, 1)} & \\")
        print(rf"  Mode   & {fmt(_mode_nn_ck, 0)} & & {fmt(_mode_pre_ck, 0)} & & {fmt(_mode_acc_ck, 0)} & \\")
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")

    # =========================================================
    # First-party vs Third-party
    # =========================================================
    _fp_rows = q_safe(
        conn,
        f"""
        SELECT cc.phase, cc.cookie_domain, cs.url, {NORM_CAT_SQL} AS cat, cc.scan_id
        FROM cookie_classifications cc JOIN chrome_scans cs ON cs.id=cc.scan_id
        WHERE cs.is_error_page=0 AND cc.phase='pre'
    UNION ALL
        SELECT cc.phase, cc.cookie_domain, cs.url, {NORM_CAT_SQL} AS cat, cc.scan_id
        FROM cookie_classifications cc JOIN chrome_scans cs ON cs.id=cc.scan_id
        WHERE cc.phase='post_accept' AND cc.scan_id IN ({_acc_scan_id_sql})
    UNION ALL
        SELECT cc.phase, cc.cookie_domain, cs.url, {NORM_CAT_SQL} AS cat, cc.scan_id
        FROM cookie_classifications cc JOIN chrome_scans cs ON cs.id=cc.scan_id
        WHERE cc.phase='post_reject' AND cc.scan_id IN ({_rej_scan_id_sql})
    """,
    )

    _fp_phase_party = defaultdict(lambda: [0, set()])
    _fp_cat_party = defaultdict(lambda: [0, set()])
    _fp_nn_party = defaultdict(lambda: [0, set()])

    for _ph, _ckd, _url, _cat, _sid in _fp_rows:
        _p = cookie_party(_url, _ckd)
        if _p is None:
            continue
        if _ph == "pre" and _sid in _nn_scan_id_set:
            _fp_nn_party[_p][0] += 1
            _fp_nn_party[_p][1].add(_sid)
        else:
            _fp_phase_party[(_ph, _p)][0] += 1
            _fp_phase_party[(_ph, _p)][1].add(_sid)
            _fp_cat_party[(_ph, _p, _cat)][0] += 1
            _fp_cat_party[(_ph, _p, _cat)][1].add(_sid)

    def _fp_n(ph, p):
        return _fp_phase_party[(ph, p)][0]

    def _fp_s(ph, p):
        return len(_fp_phase_party[(ph, p)][1])

    def _fp_cn(ph, p, cat):
        return _fp_cat_party[(ph, p, cat)][0]

    _fp_pre_tot = _fp_n("pre", "first") + _fp_n("pre", "third")
    _fp_acc_tot = _fp_n("post_accept", "first") + _fp_n("post_accept", "third")
    _fp_rej_tot = _fp_n("post_reject", "first") + _fp_n("post_reject", "third")
    _fp_nn_1 = _fp_nn_party["first"][0]
    _fp_nn_3 = _fp_nn_party["third"][0]
    _fp_nn_s1 = len(_fp_nn_party["first"][1])
    _fp_nn_s3 = len(_fp_nn_party["third"][1])
    _fp_nn_tot = _fp_nn_1 + _fp_nn_3

    if _fp_pre_tot > 0:
        _fp1_pre_pct = _fp_n("pre", "first") / _fp_pre_tot * 100
        _fp3_pre_pct = _fp_n("pre", "third") / _fp_pre_tot * 100
        _fp_par = (
            rf"Breaking cookies down by party origin: of the \textbf{{{_fp_pre_tot}}} classifiable "
            rf"cookies observed pre-accept, \textbf{{{_fp_n('pre', 'first')}}} ({_fp1_pre_pct:.0f}\,\%) "
            rf"were first-party and \textbf{{{_fp_n('pre', 'third')}}} ({_fp3_pre_pct:.0f}\,\%) were "
            rf"third-party."
        )
        if _fp_acc_tot:
            _fp1_acc_pct = _fp_n("post_accept", "first") / _fp_acc_tot * 100
            _fp3_acc_pct = _fp_n("post_accept", "third") / _fp_acc_tot * 100
            _fp_par += (
                rf" After accepting, \textbf{{{_fp_n('post_accept', 'first')}}} ({_fp1_acc_pct:.0f}\,\%) "
                rf"were first-party and \textbf{{{_fp_n('post_accept', 'third')}}} "
                rf"({_fp3_acc_pct:.0f}\,\%) were third-party."
            )
        if has_rej_cookies and _fp_rej_tot:
            _fp1_rej_pct = _fp_n("post_reject", "first") / _fp_rej_tot * 100
            _fp3_rej_pct = _fp_n("post_reject", "third") / _fp_rej_tot * 100
            _fp_par += (
                rf" After rejecting, \textbf{{{_fp_n('post_reject', 'first')}}} ({_fp1_rej_pct:.0f}\,\%) "
                rf"were first-party and \textbf{{{_fp_n('post_reject', 'third')}}} "
                rf"({_fp3_rej_pct:.0f}\,\%) were third-party."
            )
        print(_fp_par)
        print()

        # Summary table: first/third by phase
        print(r"\begin{table}[ht]\centering\footnotesize")
        print(r"\caption{First- and third-party cookies by phase}\label{tab:cookie_party}")
        if has_rej_cookies and _fp_rej_tot:
            print(r"\begin{tabular}{lrrrrrrrrrrrrr}")
            print(
                r"\toprule Party & \multicolumn{3}{c}{No notice} & \multicolumn{3}{c}{Pre} & \multicolumn{3}{c}{Post-accept} & \multicolumn{3}{c}{Post-reject} \\"
            )
            print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}\cmidrule(lr){11-13}")
            print(r"& $n$ & Sites & \% & $n$ & Sites & \% & $n$ & Sites & \% & $n$ & Sites & \% \\ \midrule")
            for _pl, _pk, _nn, _nns in [
                ("First-party", "first", _fp_nn_1, _fp_nn_s1),
                ("Third-party", "third", _fp_nn_3, _fp_nn_s3),
            ]:
                _pn = _fp_n("pre", _pk)
                _an = _fp_n("post_accept", _pk)
                _rn = _fp_n("post_reject", _pk)
                _np = _nn / _fp_nn_tot * 100 if _fp_nn_tot else 0
                _pp = _pn / _fp_pre_tot * 100 if _fp_pre_tot else 0
                _ap = _an / _fp_acc_tot * 100 if _fp_acc_tot else 0
                _rp = _rn / _fp_rej_tot * 100 if _fp_rej_tot else 0
                print(
                    rf"  {_pl} & {_nn} & {_nns} & {_np:.0f}\,\% & "
                    rf"{_pn} & {_fp_s('pre', _pk)} & {_pp:.0f}\,\% & "
                    rf"{_an} & {_fp_s('post_accept', _pk)} & {_ap:.0f}\,\% & "
                    rf"{_rn} & {_fp_s('post_reject', _pk)} & {_rp:.0f}\,\% \\"
                )
            print(r"\midrule")
            print(
                rf"  \textbf{{Total}} & \textbf{{{_fp_nn_tot}}} & & & "
                rf"\textbf{{{_fp_pre_tot}}} & & & "
                rf"\textbf{{{_fp_acc_tot}}} & & & \textbf{{{_fp_rej_tot}}} & & \\"
            )
        else:
            print(r"\begin{tabular}{lrrrrrrrrrr}")
            print(
                r"\toprule Party & \multicolumn{3}{c}{No notice} & \multicolumn{3}{c}{Pre} & \multicolumn{3}{c}{Post-accept} \\"
            )
            print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}")
            print(r"& $n$ & Sites & \% & $n$ & Sites & \% & $n$ & Sites & \% \\ \midrule")
            for _pl, _pk, _nn, _nns in [
                ("First-party", "first", _fp_nn_1, _fp_nn_s1),
                ("Third-party", "third", _fp_nn_3, _fp_nn_s3),
            ]:
                _pn = _fp_n("pre", _pk)
                _an = _fp_n("post_accept", _pk)
                _np = _nn / _fp_nn_tot * 100 if _fp_nn_tot else 0
                _pp = _pn / _fp_pre_tot * 100 if _fp_pre_tot else 0
                _ap = _an / _fp_acc_tot * 100 if _fp_acc_tot else 0
                print(
                    rf"  {_pl} & {_nn} & {_nns} & {_np:.0f}\,\% & "
                    rf"{_pn} & {_fp_s('pre', _pk)} & {_pp:.0f}\,\% & "
                    rf"{_an} & {_fp_s('post_accept', _pk)} & {_ap:.0f}\,\% \\"
                )
            print(r"\midrule")
            print(
                rf"  \textbf{{Total}} & \textbf{{{_fp_nn_tot}}} & & & \textbf{{{_fp_pre_tot}}} & & & \textbf{{{_fp_acc_tot}}} & & \\"
            )
        print(r"\bottomrule\end{tabular}")
        print(r"\end{table}")

        # Category x party table
        _fp_all_cats = sorted(
            {cat for (_, _, cat) in _fp_cat_party},
            key=lambda c: (
                -(
                    _fp_cn("pre", "first", c)
                    + _fp_cn("pre", "third", c)
                    + _fp_cn("post_accept", "first", c)
                    + _fp_cn("post_accept", "third", c)
                )
            ),
        )
        if _fp_all_cats:
            print(r"\begin{table}[ht]\centering\footnotesize")
            if has_rej_cookies and _fp_rej_tot:
                print(r"\caption{Cookie categories by party and phase}\label{tab:cookie_cat_party}")
                print(r"\begin{tabular}{lrrrrrr}")
                print(
                    r"\toprule Category & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} & \multicolumn{2}{c}{Post-reject} \\"
                )
                print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
                print(r"& 1st & 3rd & 1st & 3rd & 1st & 3rd \\ \midrule")
                for cat in _fp_all_cats:
                    print(
                        rf"  {latex_escape(cat)} & "
                        rf"{_fp_cn('pre', 'first', cat)} & {_fp_cn('pre', 'third', cat)} & "
                        rf"{_fp_cn('post_accept', 'first', cat)} & {_fp_cn('post_accept', 'third', cat)} & "
                        rf"{_fp_cn('post_reject', 'first', cat)} & {_fp_cn('post_reject', 'third', cat)} \\"
                    )
                print(r"\midrule")
                print(
                    rf"  \textbf{{Total}} & \textbf{{{_fp_n('pre', 'first')}}} & \textbf{{{_fp_n('pre', 'third')}}} & "
                    rf"\textbf{{{_fp_n('post_accept', 'first')}}} & \textbf{{{_fp_n('post_accept', 'third')}}} & "
                    rf"\textbf{{{_fp_n('post_reject', 'first')}}} & \textbf{{{_fp_n('post_reject', 'third')}}} \\"
                )
            else:
                print(r"\caption{Cookie categories by party and phase}\label{tab:cookie_cat_party}")
                print(r"\begin{tabular}{lrrrrrrrr}")
                print(r"\toprule Category & \multicolumn{4}{c}{Pre} & \multicolumn{4}{c}{Post-accept} \\")
                print(r"\cmidrule(lr){2-5}\cmidrule(lr){6-9}")
                print(r"& 1st & \% & 3rd & \% & 1st & \% & 3rd & \% \\ \midrule")
                for cat in _fp_all_cats:
                    p1 = _fp_cn("pre", "first", cat)
                    p3 = _fp_cn("pre", "third", cat)
                    a1 = _fp_cn("post_accept", "first", cat)
                    a3 = _fp_cn("post_accept", "third", cat)
                    p1p = p1 / _fp_pre_tot * 100 if _fp_pre_tot else 0
                    p3p = p3 / _fp_pre_tot * 100 if _fp_pre_tot else 0
                    a1p = a1 / _fp_acc_tot * 100 if _fp_acc_tot else 0
                    a3p = a3 / _fp_acc_tot * 100 if _fp_acc_tot else 0
                    print(
                        rf"  {latex_escape(cat)} & "
                        rf"{p1} & {p1p:.0f}\,\% & {p3} & {p3p:.0f}\,\% & "
                        rf"{a1} & {a1p:.0f}\,\% & {a3} & {a3p:.0f}\,\% \\"
                    )
                print(r"\midrule")
                print(
                    rf"  \textbf{{Total}} & \textbf{{{_fp_n('pre', 'first')}}} & & "
                    rf"\textbf{{{_fp_n('pre', 'third')}}} & & "
                    rf"\textbf{{{_fp_n('post_accept', 'first')}}} & & "
                    rf"\textbf{{{_fp_n('post_accept', 'third')}}} & \\"
                )
            print(r"\bottomrule\end{tabular}")
            print(r"\end{table}")

    # Storage classifications data loading
    _has_storage_class = bool(
        q_safe(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='storage_classifications'")
    )
    sc_phase_type_totals = {}
    sc_cat_by_phase_type = defaultdict(dict)
    sc_all_cats_local = []
    sc_all_cats_session = []
    if _has_storage_class:
        _sc_valid_sql = f"""(
            (phase='pre'         AND scan_id IN ({_wn_scan_id_sql}))
         OR (phase='post_accept' AND scan_id IN ({_acc_scan_id_sql}))
         OR (phase='post_reject' AND scan_id IN ({_rej_scan_id_sql}))
        )"""
        for row in q_safe(
            conn,
            f"""
                SELECT phase, storage_type, COUNT(*), COUNT(DISTINCT scan_id)
                FROM storage_classifications
                WHERE {_sc_valid_sql}
                GROUP BY phase, storage_type""",
        ):
            sc_phase_type_totals[(row[0], row[1])] = (row[2], row[3])
        for row in q_safe(
            conn,
            f"""
                SELECT phase, storage_type, {NORM_CAT_SQL}, COUNT(*), COUNT(DISTINCT scan_id)
                FROM storage_classifications
                WHERE {_sc_valid_sql}
                GROUP BY phase, storage_type, 3 ORDER BY phase, storage_type, 4 DESC""",
        ):
            sc_cat_by_phase_type[(row[0], row[1])][row[2]] = (row[3], row[4])
        _all_sc_cats = {cat for (_, _), d in sc_cat_by_phase_type.items() for cat in d}
        sc_all_cats_local = sorted(
            _all_sc_cats,
            key=lambda c: (
                -(
                    sc_cat_by_phase_type.get(("pre", "local"), {}).get(c, (0, 0))[0]
                    + sc_cat_by_phase_type.get(("post_accept", "local"), {}).get(c, (0, 0))[0]
                )
            ),
        )
        sc_all_cats_session = sorted(
            _all_sc_cats,
            key=lambda c: (
                -(
                    sc_cat_by_phase_type.get(("pre", "session"), {}).get(c, (0, 0))[0]
                    + sc_cat_by_phase_type.get(("post_accept", "session"), {}).get(c, (0, 0))[0]
                )
            ),
        )

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

    if not _measured_s:
        return

    _ss_increased_acc = sum(1 for r in _acc_s if r[4] > r[2])
    _ss_reduced_acc = sum(1 for r in _acc_s if r[4] < r[2])
    _ss_increased_rej = sum(1 for r in _rej_s if r[6] > r[2])
    _ss_reduced_rej = sum(1 for r in _rej_s if r[6] < r[2])

    _ls_pre_vals = [r[1] for r in _wn_measured_s if r[1] is not None]
    _ls_acc_vals = [r[3] for r in _acc_s if r[3] is not None]
    _ls_rej_vals = [r[5] for r in _rej_s if r[5] is not None]
    _ls_nn_vals = [r[1] for r in _nn_measured_s if r[1] is not None]
    _ls_n_pre, _ls_n_acc, _ls_n_rej, _ls_n_nn = (
        len(_ls_pre_vals),
        len(_ls_acc_vals),
        len(_ls_rej_vals),
        len(_ls_nn_vals),
    )
    _med_ls_pre = _median(_ls_pre_vals)
    _mode_ls_pre = _mode(_ls_pre_vals)
    _med_ls_acc = _median(_ls_acc_vals)
    _mode_ls_acc = _mode(_ls_acc_vals)
    _med_ls_rej = _median(_ls_rej_vals)
    _mode_ls_rej = _mode(_ls_rej_vals)
    _med_ls_nn = _median(_ls_nn_vals)
    _mode_ls_nn = _mode(_ls_nn_vals)
    _ls_pre_b = _bucket_counts(_ls_pre_vals)
    _ls_acc_b = _bucket_counts(_ls_acc_vals)
    _ls_rej_b = _bucket_counts(_ls_rej_vals)
    _ls_nn_b = _bucket_counts(_ls_nn_vals)
    _ls_has_rej = bool(_rej_s) and _ls_n_rej > 0

    _ss_pre_vals = [r[2] for r in _wn_measured_s if r[2] is not None]
    _ss_acc_vals = [r[4] for r in _acc_s if r[4] is not None]
    _ss_rej_vals = [r[6] for r in _rej_s if r[6] is not None]
    _ss_nn_vals = [r[2] for r in _nn_measured_s if r[2] is not None]
    _ss_n_pre, _ss_n_acc, _ss_n_rej, _ss_n_nn = (
        len(_ss_pre_vals),
        len(_ss_acc_vals),
        len(_ss_rej_vals),
        len(_ss_nn_vals),
    )
    _med_ss_pre = _median(_ss_pre_vals)
    _mode_ss_pre = _mode(_ss_pre_vals)
    _med_ss_acc = _median(_ss_acc_vals)
    _mode_ss_acc = _mode(_ss_acc_vals)
    _med_ss_rej = _median(_ss_rej_vals)
    _mode_ss_rej = _mode(_ss_rej_vals)
    _med_ss_nn = _median(_ss_nn_vals)
    _mode_ss_nn = _mode(_ss_nn_vals)
    _ss_pre_b = _bucket_counts(_ss_pre_vals)
    _ss_acc_b = _bucket_counts(_ss_acc_vals)
    _ss_rej_b = _bucket_counts(_ss_rej_vals)
    _ss_nn_b = _bucket_counts(_ss_nn_vals)
    _ss_has_rej = bool(_rej_s) and _ss_n_rej > 0

    def _s(v):
        return str(v) if v is not None else "---"

    def _d(a, b):
        if a is None or b is None:
            return "---"
        d = b - a
        return rf"\textbf{{{d:+d}}}" if d != 0 else "0"

    # =========================================================
    # localStorage subsubsection
    # =========================================================
    print(r"\subsubsection{localStorage}")
    _ls_par = (
        rf"In the pre-interaction phase, \textbf{{{_sum_pre_ls}}} localStorage entries were observed "
        rf"across \textbf{{{_all_pre_s}}} sites (avg.\ {_avg_pre_ls:.1f} per site). "
    )
    if _avg_nn_ls is not None:
        _ls_par += (
            rf"Sites without a cookie notice averaged \textbf{{{_avg_nn_ls:.1f}}} localStorage entries per site "
            rf"(total \textbf{{{_sum_nn_ls}}} across \textbf{{{_all_nn_s}}} sites). "
        )
    if _avg_acc_ls is not None:
        _ls_par += (
            rf"After accepting the cookie notice, this rose to \textbf{{{_sum_acc_ls}}} entries "
            rf"across \textbf{{{_all_acc_s}}} sites (avg.\ {_avg_acc_ls:.1f} per site), "
            rf"with {_ls_increased_acc} sites seeing an increase and {_ls_reduced_acc} a decrease. "
        )
    if _avg_rej_ls is not None:
        _ls_par += (
            rf"After rejecting, \textbf{{{_sum_rej_ls}}} entries were observed across "
            rf"\textbf{{{_all_rej_s}}} sites (avg.\ {_avg_rej_ls:.1f} per site), "
            rf"compared with {_avg_pre_ls:.1f} pre-interaction --- suggesting rejection "
            r"does not fully prevent sites from writing to localStorage. "
        )
    _ls_par += (
        r"See \autoref{tab:localStorage_buckets} for the full distribution of sites by entry-count band across phases."
    )
    print(_ls_par)
    print()
    print(r"\begin{table}[ht]\centering\footnotesize")
    if _ls_has_rej:
        print(r"\caption{Distribution of sites by localStorage entry count}\label{tab:localStorage_buckets}")
        print(r"\begin{tabular}{lrrrrrrrr}")
        print(
            r"\toprule Entry count & \multicolumn{2}{c}{No notice} & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} & \multicolumn{2}{c}{Post-reject} \\"
        )
        print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}\cmidrule(lr){8-9}")
        print(r"& Sites & \% & Sites & \% & Sites & \% & Sites & \% \\ \midrule")
        for lbl, nb, pb, ab, rb in zip(_BUCKET_LABELS, _ls_nn_b, _ls_pre_b, _ls_acc_b, _ls_rej_b):
            np_ = nb / _ls_n_nn * 100 if _ls_n_nn else 0
            pp = pb / _ls_n_pre * 100 if _ls_n_pre else 0
            ap = ab / _ls_n_acc * 100 if _ls_n_acc else 0
            rp = rb / _ls_n_rej * 100 if _ls_n_rej else 0
            print(
                rf"  {lbl} & {nb} & {np_:.0f}\,\% & {pb} & {pp:.0f}\,\% & {ab} & {ap:.0f}\,\% & {rb} & {rp:.0f}\,\% \\"
            )
        print(r"\midrule")
        print(
            rf"  \textbf{{Total}} & \textbf{{{_ls_n_nn}}} & & \textbf{{{_ls_n_pre}}} & & \textbf{{{_ls_n_acc}}} & & \textbf{{{_ls_n_rej}}} & \\"
        )
        print(
            rf"  Median & {fmt(_med_ls_nn, 1)} & & {fmt(_med_ls_pre, 1)} & & {fmt(_med_ls_acc, 1)} & & {fmt(_med_ls_rej, 1)} & \\"
        )
        print(
            rf"  Mode   & {fmt(_mode_ls_nn, 0)} & & {fmt(_mode_ls_pre, 0)} & & {fmt(_mode_ls_acc, 0)} & & {fmt(_mode_ls_rej, 0)} & \\"
        )
    else:
        print(r"\caption{Distribution of sites by localStorage entry count}\label{tab:localStorage_buckets}")
        print(r"\begin{tabular}{lrrrrrr}")
        print(
            r"\toprule Entry count & \multicolumn{2}{c}{No notice} & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} \\"
        )
        print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
        print(r"& Sites & \% & Sites & \% & Sites & \% \\ \midrule")
        for lbl, nb, pb, ab in zip(_BUCKET_LABELS, _ls_nn_b, _ls_pre_b, _ls_acc_b):
            np_ = nb / _ls_n_nn * 100 if _ls_n_nn else 0
            pp = pb / _ls_n_pre * 100 if _ls_n_pre else 0
            ap = ab / _ls_n_acc * 100 if _ls_n_acc else 0
            print(rf"  {lbl} & {nb} & {np_:.0f}\,\% & {pb} & {pp:.0f}\,\% & {ab} & {ap:.0f}\,\% \\")
        print(r"\midrule")
        print(rf"  \textbf{{Total}} & \textbf{{{_ls_n_nn}}} & & \textbf{{{_ls_n_pre}}} & & \textbf{{{_ls_n_acc}}} & \\")
        print(rf"  Median & {fmt(_med_ls_nn, 1)} & & {fmt(_med_ls_pre, 1)} & & {fmt(_med_ls_acc, 1)} & \\")
        print(rf"  Mode   & {fmt(_mode_ls_nn, 0)} & & {fmt(_mode_ls_pre, 0)} & & {fmt(_mode_ls_acc, 0)} & \\")
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")

    if _has_storage_class and pre_ls_total + acc_ls_total > 0:
        _ls_pre_d = sc_cat_by_phase_type.get(("pre", "local"), {})
        _ls_acc_d = sc_cat_by_phase_type.get(("post_accept", "local"), {})
        _ls_rej_d = sc_cat_by_phase_type.get(("post_reject", "local"), {})
        _ls_cls_par = (
            rf"localStorage keys were also matched against the Open Cookie Database: "
            rf"\textbf{{{pre_ls_total}}} key records were classified pre-accept"
        )
        if acc_ls_total:
            _ls_cls_par += rf", rising to \textbf{{{acc_ls_total}}} after accepting"
        if has_rej_sc and rej_ls_total:
            _ls_cls_par += rf" and \textbf{{{rej_ls_total}}} after rejecting"
        if sc_all_cats_local:
            _ls_cls_par += r"; the category breakdown is shown in Table~\ref{tab:localStorage_cats}."
        else:
            _ls_cls_par += "."
        print(_ls_cls_par)
        print()
        if sc_all_cats_local:
            print(r"\begin{table}[ht]\centering\footnotesize")
            if has_rej_sc and rej_ls_total:
                print(r"\caption{localStorage key categories by scan phase}\label{tab:localStorage_cats}")
                print(r"\begin{tabular}{lrrrrrrrrr}")
                print(
                    r"\toprule Category & \multicolumn{3}{c}{Pre} & \multicolumn{3}{c}{Post-accept} & \multicolumn{3}{c}{Post-reject} \\"
                )
                print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}")
                print(r"& $n$ & Sites & \% & $n$ & Sites & \% & $n$ & Sites & \% \\ \midrule")
                for cat in sc_all_cats_local:
                    pn, ps = _ls_pre_d.get(cat, (0, 0))
                    an, as_ = _ls_acc_d.get(cat, (0, 0))
                    rn, rs = _ls_rej_d.get(cat, (0, 0))
                    pp = pn / pre_ls_total * 100 if pre_ls_total else 0
                    ap = an / acc_ls_total * 100 if acc_ls_total else 0
                    rp = rn / rej_ls_total * 100 if rej_ls_total else 0
                    print(
                        rf"  {latex_escape(cat)} & {pn} & {ps} & {pp:.0f}\,\% & {an} & {as_} & {ap:.0f}\,\% & {rn} & {rs} & {rp:.0f}\,\% \\"
                    )
                print(r"\midrule")
                print(
                    rf"  \textbf{{Total}} & \textbf{{{pre_ls_total}}} & \textbf{{{_sc_sites('pre', 'local')}}} & & \textbf{{{acc_ls_total}}} & \textbf{{{_sc_sites('post_accept', 'local')}}} & & \textbf{{{rej_ls_total}}} & \textbf{{{_sc_sites('post_reject', 'local')}}} & \\"
                )
            else:
                print(r"\caption{localStorage key categories by scan phase}\label{tab:localStorage_cats}")
                print(r"\begin{tabular}{lrrrrrr}")
                print(r"\toprule Category & \multicolumn{3}{c}{Pre} & \multicolumn{3}{c}{Post-accept} \\")
                print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}")
                print(r"& $n$ & Sites & \% & $n$ & Sites & \% \\ \midrule")
                for cat in sc_all_cats_local:
                    pn, ps = _ls_pre_d.get(cat, (0, 0))
                    an, as_ = _ls_acc_d.get(cat, (0, 0))
                    pp = pn / pre_ls_total * 100 if pre_ls_total else 0
                    ap = an / acc_ls_total * 100 if acc_ls_total else 0
                    print(rf"  {latex_escape(cat)} & {pn} & {ps} & {pp:.0f}\,\% & {an} & {as_} & {ap:.0f}\,\% \\")
                print(r"\midrule")
                print(
                    rf"  \textbf{{Total}} & \textbf{{{pre_ls_total}}} & \textbf{{{_sc_sites('pre', 'local')}}} & & \textbf{{{acc_ls_total}}} & \textbf{{{_sc_sites('post_accept', 'local')}}} & \\"
                )
            print(r"\bottomrule\end{tabular}")
            print(r"\end{table}")

    if SHOW_PER:
        print(r"\begin{table}[ht]\centering\footnotesize")
        if _ls_has_rej:
            print(r"\caption{Per-site localStorage entry counts}\label{tab:localStorage_persite}")
            print(r"\begin{tabular}{>{\ttfamily}p{2.8cm} r r r r r}")
            print(
                r"\toprule \normalfont URL & Pre & Post-accept & Post-reject & $\Delta$\,Acc & $\Delta$\,Rej \\ \midrule"
            )
            for _url, _pls, _pss, _als, _ass, _rls, _rss in _storage_data:
                print(
                    rf"  {latex_escape(_url)} & {_s(_pls)} & {_s(_als)} & {_s(_rls)} & {_d(_pls, _als)} & {_d(_pls, _rls)} \\"
                )
        else:
            print(r"\caption{Per-site localStorage entry counts}\label{tab:localStorage_persite}")
            print(r"\begin{tabular}{>{\ttfamily}p{3.2cm} r r r}")
            print(r"\toprule \normalfont URL & Pre & Post-accept & $\Delta$ \\ \midrule")
            for _url, _pls, _pss, _als, _ass, _rls, _rss in _storage_data:
                print(rf"  {latex_escape(_url)} & {_s(_pls)} & {_s(_als)} & {_d(_pls, _als)} \\")
        print(r"\bottomrule\end{tabular}")
        print(r"\end{table}")

    # =========================================================
    # sessionStorage subsubsection
    # =========================================================
    print(r"\subsubsection{sessionStorage}")
    _ss_par = (
        rf"In the pre-interaction phase, \textbf{{{_sum_pre_ss}}} sessionStorage entries were observed "
        rf"across \textbf{{{_all_pre_s}}} sites (avg.\ {_avg_pre_ss:.1f} per site). "
    )
    if _avg_nn_ss is not None:
        _ss_par += (
            rf"Sites without a cookie notice averaged \textbf{{{_avg_nn_ss:.1f}}} sessionStorage entries per site "
            rf"(total \textbf{{{_sum_nn_ss}}} across \textbf{{{_all_nn_s}}} sites). "
        )
    if _avg_acc_ss is not None:
        _ss_par += (
            rf"After accepting the cookie notice, this was \textbf{{{_sum_acc_ss}}} entries "
            rf"across \textbf{{{_all_acc_s}}} sites (avg.\ {_avg_acc_ss:.1f} per site), "
            rf"with {_ss_increased_acc} sites seeing an increase and {_ss_reduced_acc} a decrease. "
        )
    if _avg_rej_ss is not None:
        _ss_par += (
            rf"After rejecting, \textbf{{{_sum_rej_ss}}} entries were observed across "
            rf"\textbf{{{_all_rej_s}}} sites (avg.\ {_avg_rej_ss:.1f} per site), "
            rf"compared with {_avg_pre_ss:.1f} pre-interaction --- suggesting rejection "
            r"does not fully prevent sites from writing to sessionStorage. "
        )
    _ss_par += r"See \autoref{tab:sessionStorage_buckets} for the full distribution of sites by entry-count band across phases."
    print(_ss_par)
    print()
    print(r"\begin{table}[ht]\centering\footnotesize")
    if _ss_has_rej:
        print(r"\caption{Distribution of sites by sessionStorage entry count}\label{tab:sessionStorage_buckets}")
        print(r"\begin{tabular}{lrrrrrrrr}")
        print(
            r"\toprule Entry count & \multicolumn{2}{c}{No notice} & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} & \multicolumn{2}{c}{Post-reject} \\"
        )
        print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}\cmidrule(lr){8-9}")
        print(r"& Sites & \% & Sites & \% & Sites & \% & Sites & \% \\ \midrule")
        for lbl, nb, pb, ab, rb in zip(_BUCKET_LABELS, _ss_nn_b, _ss_pre_b, _ss_acc_b, _ss_rej_b):
            np_ = nb / _ss_n_nn * 100 if _ss_n_nn else 0
            pp = pb / _ss_n_pre * 100 if _ss_n_pre else 0
            ap = ab / _ss_n_acc * 100 if _ss_n_acc else 0
            rp = rb / _ss_n_rej * 100 if _ss_n_rej else 0
            print(
                rf"  {lbl} & {nb} & {np_:.0f}\,\% & {pb} & {pp:.0f}\,\% & {ab} & {ap:.0f}\,\% & {rb} & {rp:.0f}\,\% \\"
            )
        print(r"\midrule")
        print(
            rf"  \textbf{{Total}} & \textbf{{{_ss_n_nn}}} & & \textbf{{{_ss_n_pre}}} & & \textbf{{{_ss_n_acc}}} & & \textbf{{{_ss_n_rej}}} & \\"
        )
        print(
            rf"  Median & {fmt(_med_ss_nn, 1)} & & {fmt(_med_ss_pre, 1)} & & {fmt(_med_ss_acc, 1)} & & {fmt(_med_ss_rej, 1)} & \\"
        )
        print(
            rf"  Mode   & {fmt(_mode_ss_nn, 0)} & & {fmt(_mode_ss_pre, 0)} & & {fmt(_mode_ss_acc, 0)} & & {fmt(_mode_ss_rej, 0)} & \\"
        )
    else:
        print(r"\caption{Distribution of sites by sessionStorage entry count}\label{tab:sessionStorage_buckets}")
        print(r"\begin{tabular}{lrrrrrr}")
        print(
            r"\toprule Entry count & \multicolumn{2}{c}{No notice} & \multicolumn{2}{c}{Pre} & \multicolumn{2}{c}{Post-accept} \\"
        )
        print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
        print(r"& Sites & \% & Sites & \% & Sites & \% \\ \midrule")
        for lbl, nb, pb, ab in zip(_BUCKET_LABELS, _ss_nn_b, _ss_pre_b, _ss_acc_b):
            np_ = nb / _ss_n_nn * 100 if _ss_n_nn else 0
            pp = pb / _ss_n_pre * 100 if _ss_n_pre else 0
            ap = ab / _ss_n_acc * 100 if _ss_n_acc else 0
            print(rf"  {lbl} & {nb} & {np_:.0f}\,\% & {pb} & {pp:.0f}\,\% & {ab} & {ap:.0f}\,\% \\")
        print(r"\midrule")
        print(rf"  \textbf{{Total}} & \textbf{{{_ss_n_nn}}} & & \textbf{{{_ss_n_pre}}} & & \textbf{{{_ss_n_acc}}} & \\")
        print(rf"  Median & {fmt(_med_ss_nn, 1)} & & {fmt(_med_ss_pre, 1)} & & {fmt(_med_ss_acc, 1)} & \\")
        print(rf"  Mode   & {fmt(_mode_ss_nn, 0)} & & {fmt(_mode_ss_pre, 0)} & & {fmt(_mode_ss_acc, 0)} & \\")
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")

    if _has_storage_class and pre_ss_total + acc_ss_total > 0:
        _ss_pre_d = sc_cat_by_phase_type.get(("pre", "session"), {})
        _ss_acc_d = sc_cat_by_phase_type.get(("post_accept", "session"), {})
        _ss_rej_d = sc_cat_by_phase_type.get(("post_reject", "session"), {})
        _ss_cls_par = (
            rf"sessionStorage keys were also matched against the Open Cookie Database: "
            rf"\textbf{{{pre_ss_total}}} key records were classified pre-accept"
        )
        if acc_ss_total:
            _ss_cls_par += rf", rising to \textbf{{{acc_ss_total}}} after accepting"
        if has_rej_sc and rej_ss_total:
            _ss_cls_par += rf" and \textbf{{{rej_ss_total}}} after rejecting"
        if sc_all_cats_session:
            _ss_cls_par += r"; the category breakdown is shown in Table~\ref{tab:sessionStorage_cats}."
        else:
            _ss_cls_par += "."
        print(_ss_cls_par)
        print()
        if sc_all_cats_session:
            print(r"\begin{table}[ht]\centering\footnotesize")
            if has_rej_sc and rej_ss_total:
                print(r"\caption{sessionStorage key categories by scan phase}\label{tab:sessionStorage_cats}")
                print(r"\begin{tabular}{lrrrrrrrrr}")
                print(
                    r"\toprule Category & \multicolumn{3}{c}{Pre} & \multicolumn{3}{c}{Post-accept} & \multicolumn{3}{c}{Post-reject} \\"
                )
                print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}")
                print(r"& $n$ & Sites & \% & $n$ & Sites & \% & $n$ & Sites & \% \\ \midrule")
                for cat in sc_all_cats_session:
                    pn, ps = _ss_pre_d.get(cat, (0, 0))
                    an, as_ = _ss_acc_d.get(cat, (0, 0))
                    rn, rs = _ss_rej_d.get(cat, (0, 0))
                    pp = pn / pre_ss_total * 100 if pre_ss_total else 0
                    ap = an / acc_ss_total * 100 if acc_ss_total else 0
                    rp = rn / rej_ss_total * 100 if rej_ss_total else 0
                    print(
                        rf"  {latex_escape(cat)} & {pn} & {ps} & {pp:.0f}\,\% & {an} & {as_} & {ap:.0f}\,\% & {rn} & {rs} & {rp:.0f}\,\% \\"
                    )
                print(r"\midrule")
                print(
                    rf"  \textbf{{Total}} & \textbf{{{pre_ss_total}}} & \textbf{{{_sc_sites('pre', 'session')}}} & & \textbf{{{acc_ss_total}}} & \textbf{{{_sc_sites('post_accept', 'session')}}} & & \textbf{{{rej_ss_total}}} & \textbf{{{_sc_sites('post_reject', 'session')}}} & \\"
                )
            else:
                print(r"\caption{sessionStorage key categories by scan phase}\label{tab:sessionStorage_cats}")
                print(r"\begin{tabular}{lrrrrrr}")
                print(r"\toprule Category & \multicolumn{3}{c}{Pre} & \multicolumn{3}{c}{Post-accept} \\")
                print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}")
                print(r"& $n$ & Sites & \% & $n$ & Sites & \% \\ \midrule")
                for cat in sc_all_cats_session:
                    pn, ps = _ss_pre_d.get(cat, (0, 0))
                    an, as_ = _ss_acc_d.get(cat, (0, 0))
                    pp = pn / pre_ss_total * 100 if pre_ss_total else 0
                    ap = an / acc_ss_total * 100 if acc_ss_total else 0
                    print(rf"  {latex_escape(cat)} & {pn} & {ps} & {pp:.0f}\,\% & {an} & {as_} & {ap:.0f}\,\% \\")
                print(r"\midrule")
                print(
                    rf"  \textbf{{Total}} & \textbf{{{pre_ss_total}}} & \textbf{{{_sc_sites('pre', 'session')}}} & & \textbf{{{acc_ss_total}}} & \textbf{{{_sc_sites('post_accept', 'session')}}} & \\"
                )
            print(r"\bottomrule\end{tabular}")
            print(r"\end{table}")

    if SHOW_PER:
        print(r"\begin{table}[ht]\centering\footnotesize")
        if _ss_has_rej:
            print(r"\caption{Per-site sessionStorage entry counts}\label{tab:sessionStorage_persite}")
            print(r"\begin{tabular}{>{\ttfamily}p{2.8cm} r r r r r}")
            print(
                r"\toprule \normalfont URL & Pre & Post-accept & Post-reject & $\Delta$\,Acc & $\Delta$\,Rej \\ \midrule"
            )
            for _url, _pls, _pss, _als, _ass, _rls, _rss in _storage_data:
                print(
                    rf"  {latex_escape(_url)} & {_s(_pss)} & {_s(_ass)} & {_s(_rss)} & {_d(_pss, _ass)} & {_d(_pss, _rss)} \\"
                )
        else:
            print(r"\caption{Per-site sessionStorage entry counts}\label{tab:sessionStorage_persite}")
            print(r"\begin{tabular}{>{\ttfamily}p{3.2cm} r r r}")
            print(r"\toprule \normalfont URL & Pre & Post-accept & $\Delta$ \\ \midrule")
            for _url, _pls, _pss, _als, _ass, _rls, _rss in _storage_data:
                print(rf"  {latex_escape(_url)} & {_s(_pss)} & {_s(_ass)} & {_d(_pss, _ass)} \\")
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
