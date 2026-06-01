from .utils import q, q_safe, col_exists, latex_escape, fmt, delta, ck_delta, count_cookies_from_path, NOT_FP, SHOW_PER, DB_PATH


def run(conn):
    has_tracker_col = col_exists(conn, "chrome_network_requests", "is_tracker")

    cookie_detected = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}")[0][0]

    reject_attempted_total = q_safe(
        conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_reject_attempted=1 AND {NOT_FP}"
    )
    reject_attempted_total = reject_attempted_total[0][0] if reject_attempted_total else 0

    reject_succeeded_total = q_safe(
        conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_rejected=1 AND {NOT_FP}"
    )
    reject_succeeded_total = reject_succeeded_total[0][0] if reject_succeeded_total else 0

    reject_rate = reject_succeeded_total / reject_attempted_total * 100 if reject_attempted_total else 0

    print(r"\subsection{Post-Reject Analysis}")
    print(
        rf"Of the {cookie_detected} sites where a cookie notice was detected, "
        rf"rejection was attempted on \textbf{{{reject_attempted_total}}} sites. "
        rf"The reject button was successfully clicked and confirmed dismissed on "
        rf"\textbf{{{reject_succeeded_total}}} of these "
        rf"({reject_rate:.0f}\,\%). "
        r"The remaining sites either had no visible reject button, required a multi-step "
        r"flow that could not be resolved, or the banner remained visible after clicking."
    )
    print()

    # Cookie counts: pre / post-accept / post-reject
    print(r"\subsubsection{Cookie Counts: Pre, Post-Accept, and Post-Reject}")

    rej_cookie_rows = q_safe(
        conn,
        f"""SELECT url, pre_cookies_path, post_accept_cookies_path, post_reject_cookies_path
            FROM chrome_scans
            WHERE cookie_notice_rejected=1 AND {NOT_FP}
            ORDER BY url""",
    )
    rej_cookie_data = [
        (url,
         count_cookies_from_path(pre_p),
         count_cookies_from_path(acc_p),
         count_cookies_from_path(rej_p))
        for url, pre_p, acc_p, rej_p in rej_cookie_rows
    ]
    rej_cookie_valid = [(url, pre, acc, rej) for url, pre, acc, rej in rej_cookie_data
                        if pre is not None and rej is not None]

    if rej_cookie_valid:
        avg_pre_ck  = sum(r[1] for r in rej_cookie_valid) / len(rej_cookie_valid)
        avg_acc_ck  = sum(r[2] for r in rej_cookie_valid if r[2] is not None) / \
                      max(1, sum(1 for r in rej_cookie_valid if r[2] is not None))
        avg_rej_ck  = sum(r[3] for r in rej_cookie_valid) / len(rej_cookie_valid)
        ck_reduced   = sum(1 for _, pre, _, rej in rej_cookie_valid if rej < pre)
        ck_same      = sum(1 for _, pre, _, rej in rej_cookie_valid if rej == pre)
        ck_increased = sum(1 for _, pre, _, rej in rej_cookie_valid if rej > pre)

        print(
            rf"For the {len(rej_cookie_valid)} sites where rejection succeeded and cookie "
            r"files were captured, the average cookie count was "
            rf"\textbf{{{avg_pre_ck:.1f}}} before interaction, "
            rf"\textbf{{{avg_acc_ck:.1f}}} post-accept (where measured), and "
            rf"\textbf{{{avg_rej_ck:.1f}}} post-reject. "
            rf"Rejecting reduced the cookie count on {ck_reduced} sites, "
            rf"left it unchanged on {ck_same}, and increased it on {ck_increased}."
        )
        print()

        if SHOW_PER:
            print(r"\begin{table*}[ht]\centering\footnotesize")
            print(r"\caption{Cookie counts on successfully rejected sites}\label{tab:reject_cookies}")
            print(r"\begin{tabular}{>{\ttfamily}p{2.8cm} r r r r r}")
            print(r"\toprule \normalfont URL & Pre & Post-acc & Post-rej & $\Delta$\,acc & $\Delta$\,rej \\ \midrule")
            for url, pre, acc, rej in sorted(rej_cookie_data, key=lambda r: r[0]):
                pre_s = str(pre) if pre is not None else "---"
                acc_s = str(acc) if acc is not None else "---"
                rej_s = str(rej) if rej is not None else "---"
                d_acc = ck_delta(pre, acc)
                d_rej = ck_delta(pre, rej)
                print(rf"  {latex_escape(url)} & {pre_s} & {acc_s} & {rej_s} & {d_acc} & {d_rej} \\")
            print(r"\bottomrule\end{tabular}")
            print(r"\end{table*}")
    else:
        print(r"Cookie count files were not available for the rejected sites in this environment.")
        print()

    # Network requests: pre vs post-reject
    print(r"\subsubsection{Network Requests: Pre vs Post-Reject}")

    if has_tracker_col:
        req_phase_rows = q_safe(
            conn,
            f"""SELECT c.url,
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

        print(
            rf"Across the {len(req_phase_rows)} successfully rejected sites, "
            rf"an average of \textbf{{{avg_pre_req:.1f}}} network requests were made in the "
            rf"pre-reject phase and \textbf{{{avg_rej_req:.1f}}} in the post-reject phase."
            + tracker_note +
            rf" Request counts decreased on {req_reduced} of {len(req_phase_rows)} sites after rejection."
        )
        print()

        if SHOW_PER:
            print(r"\begin{table*}[ht]\centering\footnotesize")
            if has_tracker_col:
                print(r"\caption{Network requests on rejected sites (pre vs post-reject)}\label{tab:reject_requests}")
                print(r"\begin{tabular}{>{\ttfamily}p{2.5cm} r r r r r r}")
                print(r"\toprule \normalfont URL & \multicolumn{2}{c}{Total req.} & \multicolumn{2}{c}{Trackers} & \multicolumn{2}{c}{$\Delta$} \\")
                print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
                print(r"\normalfont & Pre & Post-rej & Pre & Post-rej & Total & Tracker \\ \midrule")
                for url, pre_r, rej_r, pre_t, rej_t in req_phase_rows:
                    d_total   = (rf"\textbf{{{(rej_r or 0) - (pre_r or 0):+d}}}" if (rej_r or 0) != (pre_r or 0) else "0") \
                        if pre_r is not None and rej_r is not None else "---"
                    d_tracker = (rf"\textbf{{{(rej_t or 0) - (pre_t or 0):+d}}}" if (rej_t or 0) != (pre_t or 0) else "0") \
                        if pre_t is not None and rej_t is not None else "---"
                    print(
                        rf"  {latex_escape(url)} & "
                        rf"{pre_r or '---'} & {rej_r or '---'} & "
                        rf"{pre_t or '---'} & {rej_t or '---'} & "
                        rf"{d_total} & {d_tracker} \\"
                    )
            else:
                print(r"\caption{Network requests on rejected sites (pre vs post-reject)}\label{tab:reject_requests}")
                print(r"\begin{tabular}{>{\ttfamily}p{3cm} r r r}")
                print(r"\toprule \normalfont URL & Pre & Post-rej & $\Delta$ \\ \midrule")
                for url, pre_r, rej_r, _, _ in req_phase_rows:
                    d = (rf"\textbf{{{(rej_r or 0) - (pre_r or 0):+d}}}" if (rej_r or 0) != (pre_r or 0) else "0") \
                        if pre_r is not None and rej_r is not None else "---"
                    print(rf"  {latex_escape(url)} & {pre_r or '---'} & {rej_r or '---'} & {d} \\")
            print(r"\bottomrule\end{tabular}")
            print(r"\end{table*}")
    else:
        print(r"No network request data was found for the post-reject phase.")
        print()

    # Accessibility: pre vs post-reject
    print(r"\subsubsection{Accessibility: Pre vs Post-Reject}")

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
        avg_pre_lh_r  = sum((r[1] or 0) for r in rej_a11y_rows) / len(rej_a11y_rows)
        avg_rej_lh_r  = sum((r[2] or 0) for r in rej_a11y_rows) / len(rej_a11y_rows)
        avg_pre_we_r  = sum((r[3] or 0) for r in rej_a11y_rows) / len(rej_a11y_rows)
        avg_rej_we_r  = sum((r[4] or 0) for r in rej_a11y_rows) / len(rej_a11y_rows)
        lh_improved_r = sum(1 for r in rej_a11y_rows if (r[2] or 0) > (r[1] or 0))
        lh_declined_r = sum(1 for r in rej_a11y_rows if (r[2] or 0) < (r[1] or 0))

        print(
            rf"Lighthouse and WAVE metrics were captured for {len(rej_a11y_rows)} sites "
            rf"where rejection succeeded. "
            rf"The average Lighthouse score was \textbf{{{avg_pre_lh_r:.1f}}} before "
            rf"and \textbf{{{avg_rej_lh_r:.1f}}} after rejection. "
            + rf"Scores improved on {lh_improved_r} sites and declined on {lh_declined_r}. "
            + rf"Average WAVE errors moved from \textbf{{{avg_pre_we_r:.1f}}} to "
            rf"\textbf{{{avg_rej_we_r:.1f}}}."
        )
        print()

        if SHOW_PER:
            print(r"\begin{table*}[ht]\centering\footnotesize")
            print(r"\caption{Per-site accessibility metrics before and after rejection}\label{tab:reject_a11y}")
            print(r"\begin{tabular}{>{\ttfamily}p{2.5cm} r r r r r r r r}")
            print(r"\toprule \normalfont URL & \multicolumn{3}{c}{Lighthouse} & \multicolumn{3}{c}{WAVE err} & \multicolumn{2}{c}{Contrast} \\")
            print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-9}")
            print(r"\normalfont & Pre & Post-rej & $\Delta$ & Pre & Post-rej & $\Delta$ & Pre & Post-rej \\ \midrule")
            for url, pre_lh, rej_lh, pre_we, rej_we, pre_wc, rej_wc, pre_wa, rej_wa in rej_a11y_rows:
                d_lh = delta(pre_lh, rej_lh)
                d_we = delta(pre_we, rej_we)
                print(
                    rf"  {latex_escape(url)} & "
                    rf"{fmt(pre_lh)} & {fmt(rej_lh)} & {d_lh} & "
                    rf"{fmt(pre_we,0)} & {fmt(rej_we,0)} & {d_we} & "
                    rf"{fmt(pre_wc,0)} & {fmt(rej_wc,0)} \\"
                )
            print(r"\bottomrule\end{tabular}")
            print(r"\end{table*}")
    else:
        print(r"No post-reject Lighthouse data was available for this dataset.")
        print()


if __name__ == "__main__":
    import sys
    from .utils import open_merged
    db_names = sys.argv[1:] if len(sys.argv) > 1 else None
    conn, db_paths = open_merged(db_names)
    try:
        run(conn)
    finally:
        conn.close()
