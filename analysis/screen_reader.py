from .utils import q_safe, col_exists, latex_escape, DB_PATH


def run(conn):
    _has_srm = bool(q_safe(
        conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='screen_reader_metrics'"))

    if not _has_srm:
        return

    print(r"\subsection{Screen Reader Accessibility Metrics}")

    srm_metrics = [
        ("metric_readable",         r"(i) Readable"),
        ("metric_immediately_read", r"(ii) Immediately Read"),
        ("metric_keyboard_nav",     r"(iii) Keyboard Navigable"),
        ("metric_link_purpose",     r"(iv) Link or Button Purpose"),
        ("metric_abbreviations",    r"(v) Abbreviations Explained"),
        ("metric_page_titled",      r"(vi) Page Titled"),
        ("metric_notice_titled",    r"(vii) Cookie Notice Titled"),
    ]

    srm_total = q_safe(conn, """
        SELECT COUNT(*) FROM screen_reader_metrics srm
        JOIN chrome_scans cs ON cs.id = srm.scan_id
        WHERE cs.is_error_page=0""")[0][0]
    srm_with_notice = q_safe(conn, """
        SELECT COUNT(*) FROM screen_reader_metrics srm
        JOIN chrome_scans cs ON cs.id = srm.scan_id
        WHERE cs.is_error_page=0 AND srm.metric_readable != -1""")
    srm_with_notice = srm_with_notice[0][0] if srm_with_notice else 0

    print(
        rf"Each of the {srm_total} scanned sites was evaluated against nine "
        r"screen reader accessibility criteria. "
        rf"Metrics scoped to the cookie notice (i)--(v), (vii)--(viii) are marked "
        r"\textit{N/A} for the "
        rf"{srm_total - srm_with_notice} sites where no cookie notice was detected. "
        r"Pass rates below are computed over sites where the metric was applicable."
    )
    print()

    col_parts = ", ".join(
        f"SUM(CASE WHEN {col}=1 THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN {col}=0 THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN {col}=-1 THEN 1 ELSE 0 END)"
        for col, _ in srm_metrics
    )
    srm_row = q_safe(conn, f"""
        SELECT {col_parts} FROM screen_reader_metrics srm
        JOIN chrome_scans cs ON cs.id = srm.scan_id
        WHERE cs.is_error_page=0""")
    srm_row = srm_row[0] if srm_row else ([0] * (len(srm_metrics) * 3))

    srm_data = []
    for i, (_, label) in enumerate(srm_metrics):
        p  = srm_row[i * 3]     or 0
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
        best  = max(applicable_metrics, key=lambda r: r[1] / (r[1] + r[2]))
        worst = min(applicable_metrics, key=lambda r: r[1] / (r[1] + r[2]))
        best_rate  = best[1]  / (best[1]  + best[2])  * 100
        worst_rate = worst[1] / (worst[1] + worst[2]) * 100
        print(
            rf"The highest pass rate was for \textbf{{{best[0]}}} "
            rf"({best[1]} of {best[1]+best[2]} applicable sites, {best_rate:.0f}\,\%). "
            rf"The lowest was \textbf{{{worst[0]}}} "
            rf"({worst[1]} of {worst[1]+worst[2]} applicable sites, {worst_rate:.0f}\,\%)."
        )
        print()

    if col_exists(conn, "screen_reader_metrics", "immediately_read_distance"):
        dist_rows = q_safe(
            conn,
            """SELECT srm.immediately_read_distance FROM screen_reader_metrics srm
               JOIN chrome_scans cs ON cs.id = srm.scan_id
               WHERE cs.is_error_page=0 AND srm.immediately_read_distance IS NOT NULL
               ORDER BY srm.immediately_read_distance""",
        )
        distances = [r[0] for r in dist_rows]
        if distances:
            n_dist = len(distances)
            mean_d = sum(distances) / n_dist
            mid = n_dist // 2
            median_d = distances[mid] if n_dist % 2 else (distances[mid - 1] + distances[mid]) / 2

            buckets = [
                (r"$= 0$",    sum(1 for d in distances if d == 0)),
                (r"$1$--$10$",  sum(1 for d in distances if 1  <= d <= 10)),
                (r"$11$--$30$", sum(1 for d in distances if 11 <= d <= 30)),
                (r"$31$--$100$",sum(1 for d in distances if 31 <= d <= 100)),
                (r"$> 100$",   sum(1 for d in distances if d > 100)),
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
                pct = f"{count / n_dist * 100:.0f}\\,\\%" if n_dist else "---"
                print(rf"  {label} & {count} & {pct} \\")
            print(r"\midrule")
            passing = sum(1 for d in distances if d <= 30)
            print(
                rf"  \textbf{{Pass ($\leq 30$)}} & \textbf{{{passing}}} "
                rf"& \textbf{{{passing / n_dist * 100:.0f}\,\%}} \\"
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
