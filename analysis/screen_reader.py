from .utils import NOT_FP, col_exists, q_safe


def run(conn):
    _has_srm = bool(q_safe(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='screen_reader_metrics'"))

    if not _has_srm:
        return

    print(r"\subsection{Screen Reader Accessibility Metrics}")

    # notice_scoped=True  → metric is N/A when no cookie notice detected; false positives
    #                        are treated as "no notice" and counted in N/A.
    # notice_scoped=False → metric applies to every site (e.g. page title check).
    srm_metrics = [
        ("metric_readable", r"(i) Readable", True),
        ("metric_immediately_read", r"(ii) Immediately Read", True),
        ("metric_keyboard_nav", r"(iii) Keyboard Navigable", True),
        ("metric_link_purpose", r"(iv) Link or Button Purpose", True),
        ("metric_abbreviations", r"(v) Abbreviations Explained", True),
        ("metric_page_titled", r"(vi) Page Titled", False),
        ("metric_notice_titled", r"(vii) Cookie Notice Titled", True),
    ]

    # Total includes all reachable non-error sites (including false positives) so that
    # the count is consistent with the overview section.
    srm_total = q_safe(
        conn,
        """
        SELECT COUNT(*) FROM screen_reader_metrics srm
        JOIN chrome_scans cs ON cs.id = srm.scan_id
        WHERE cs.is_error_page=0""",
    )[0][0]

    # Ground-truth count of sites with a genuine cookie notice (consistent with overview).
    srm_with_notice = q_safe(
        conn,
        f"""
        SELECT COUNT(*) FROM screen_reader_metrics srm
        JOIN chrome_scans cs ON cs.id = srm.scan_id
        WHERE cs.is_error_page=0 AND cs.cookie_notice_detected=1 AND {NOT_FP}""",
    )
    srm_with_notice = srm_with_notice[0][0] if srm_with_notice else 0

    # Sites with no genuine notice: either no notice detected, or detection was a false
    # positive.  Both groups are treated as N/A for all notice-scoped criteria.
    srm_no_notice = srm_total - srm_with_notice

    print(
        rf"Each of the {srm_total} scanned sites was evaluated against seven "
        r"screen reader accessibility criteria. "
        rf"Metrics scoped to the cookie notice (i)--(v) and (vii) are marked "
        r"\textit{N/A} for the "
        rf"{srm_no_notice} sites where no cookie notice was detected "
        r"(including sites where automated detection was subsequently classified "
        r"as a false positive). "
        r"Pass rates are computed over sites where the metric was applicable."
    )
    print()

    # For notice-scoped metrics, false-positive sites are counted as N/A: their
    # metric value in the DB is 0 or 1 (the post-processor does not know about FP
    # flags), so we reclassify them here by counting FP sites in the N/A bucket
    # and excluding them from Pass/Fail.
    col_parts_list = []
    for col, _, notice_scoped in srm_metrics:
        if notice_scoped:
            col_parts_list.append(
                f"SUM(CASE WHEN {NOT_FP} AND {col}=1 THEN 1 ELSE 0 END), "
                f"SUM(CASE WHEN {NOT_FP} AND {col}=0 THEN 1 ELSE 0 END), "
                f"SUM(CASE WHEN {col}=-1 OR cs.false_positive=1 THEN 1 ELSE 0 END)"
            )
        else:
            col_parts_list.append(
                f"SUM(CASE WHEN {col}=1 THEN 1 ELSE 0 END), "
                f"SUM(CASE WHEN {col}=0 THEN 1 ELSE 0 END), "
                f"SUM(CASE WHEN {col}=-1 THEN 1 ELSE 0 END)"
            )
    col_parts = ", ".join(col_parts_list)
    srm_row = q_safe(
        conn,
        f"""
        SELECT {col_parts} FROM screen_reader_metrics srm
        JOIN chrome_scans cs ON cs.id = srm.scan_id
        WHERE cs.is_error_page=0""",
    )
    srm_row = srm_row[0] if srm_row else ([0] * (len(srm_metrics) * 3))

    srm_data = []
    for i, (_, label, _ns) in enumerate(srm_metrics):
        p = srm_row[i * 3] or 0
        f_ = srm_row[i * 3 + 1] or 0
        na = srm_row[i * 3 + 2] or 0
        applicable = p + f_
        rate = f"{p / applicable * 100:.0f}\\,\\%" if applicable else "---"
        srm_data.append((label, p, f_, na, rate))

    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{Screen reader accessibility metric results}\label{tab:srm}")
    print(r"\begin{tabular}{lrrrr}")
    print(r"\toprule Metric & Pass & Fail & N/A & Pass rate \\ \midrule")
    for label, p, f_, na, rate in srm_data:
        print(rf"  {label} & {p} & {f_} & {na} & {rate} \\")
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")

    applicable_metrics = [(label, p, f_) for label, p, f_, na, rate in srm_data if p + f_ > 0]
    if applicable_metrics:
        best = max(applicable_metrics, key=lambda r: r[1] / (r[1] + r[2]))
        worst = min(applicable_metrics, key=lambda r: r[1] / (r[1] + r[2]))
        best_rate = best[1] / (best[1] + best[2]) * 100
        worst_rate = worst[1] / (worst[1] + worst[2]) * 100
        print(
            rf"The highest pass rate was for \textbf{{{best[0]}}} "
            rf"({best[1]} of {best[1] + best[2]} applicable sites, {best_rate:.0f}\,\%). "
            rf"The lowest was \textbf{{{worst[0]}}} "
            rf"({worst[1]} of {worst[1] + worst[2]} applicable sites, {worst_rate:.0f}\,\%)."
        )
        print()

    if col_exists(conn, "screen_reader_metrics", "immediately_read_distance"):
        dist_rows = q_safe(
            conn,
            f"""SELECT srm.immediately_read_distance FROM screen_reader_metrics srm
               JOIN chrome_scans cs ON cs.id = srm.scan_id
               WHERE cs.is_error_page=0 AND {NOT_FP} AND srm.immediately_read_distance IS NOT NULL
               ORDER BY srm.immediately_read_distance""",
        )
        distances = [r[0] for r in dist_rows]
        if distances:
            n_dist = len(distances)
            mean_d = sum(distances) / n_dist
            mid = n_dist // 2
            median_d = distances[mid] if n_dist % 2 else (distances[mid - 1] + distances[mid]) / 2

            # Use the applicable count from srm_data (pass + fail for metric_immediately_read)
            # rather than n_dist (sites where a keyword was found). The difference is sites
            # with a cookie notice where no keyword appeared in the transcript at all — these
            # are hard fails and must be included in the denominator.
            ir_idx = next(i for i, (col, _ns, _) in enumerate(srm_metrics) if col == "metric_immediately_read")
            ir_applicable = srm_data[ir_idx][1] + srm_data[ir_idx][2]  # pass + fail

            no_keyword = ir_applicable - n_dist
            buckets = [
                (r"No keyword found", no_keyword),
                (r"$= 0$", sum(1 for d in distances if d == 0)),
                (r"$1$--$10$", sum(1 for d in distances if 1 <= d <= 10)),
                (r"$11$--$30$", sum(1 for d in distances if 11 <= d <= 30)),
                (r"$31$--$100$", sum(1 for d in distances if 31 <= d <= 100)),
                (r"$> 100$", sum(1 for d in distances if d > 100)),
            ]

            print(r"\subsubsection{Immediately Read: Word Distance Analysis}")
            print(
                rf"Among the {n_dist} sites where a cookie-related term was found in the "
                r"NVDA transcript, the median number of words appearing before that term "
                rf"was \textbf{{{median_d:.0f}}} (mean: {mean_d:.1f}). "
                r"Table~\ref{tab:ird} shows the distribution across distance bands; "
                r"sites in the first three bands (0--30 words) pass criterion~(ii)."
            )
            print()
            print(r"\begin{table}[ht]\centering\footnotesize")
            print(r"\caption{Distribution of word distance before first cookie keyword}\label{tab:ird}")
            print(r"\begin{tabular}{lrr}")
            print(r"\toprule Words before cookie keyword & Sites & \% \\ \midrule")
            for label, count in buckets:
                pct = f"{count / ir_applicable * 100:.0f}\\,\\%" if ir_applicable else "---"
                print(rf"  {label} & {count} & {pct} \\")
            print(r"\midrule")
            passing = sum(1 for d in distances if d <= 30)
            print(
                rf"  \textbf{{Pass ($\leq 30$)}} & \textbf{{{passing}}} "
                rf"& \textbf{{{passing / ir_applicable * 100:.0f}\,\%}} \\"
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
