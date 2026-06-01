from .utils import q, q_safe, latex_escape, fmt, delta, wilcoxon_p, fmt_p, NOT_FP, ACCEPTED, SHOW_PER, DB_PATH


def run(conn):
    cookie_detected = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}")[0][0]
    no_cookie_detected = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=0 AND is_error_page=0 AND {NOT_FP}")[0][0]

    avg_no_cookie = q(
        conn,
        f"""SELECT
             ROUND(AVG(pre_lh_score),1),
             ROUND(AVG(pre_wave_error),1),
             ROUND(AVG(pre_wave_contrast),1),
             ROUND(AVG(pre_wave_alert),1)
           FROM chrome_scans WHERE cookie_notice_detected=0 AND {NOT_FP}""",
    )[0]

    # --- Section 7: Accessibility Metrics (Chrome) ---
    print(r"\subsection{Accessibility Metrics (Chrome)}")

    avg = q(
        conn,
        f"""SELECT
             ROUND(AVG(pre_lh_score),1),         ROUND(AVG(post_accept_lh_score),1),
             ROUND(AVG(pre_wave_error),1),        ROUND(AVG(post_accept_wave_error),1),
             ROUND(AVG(pre_wave_contrast),1),     ROUND(AVG(post_accept_wave_contrast),1),
             ROUND(AVG(pre_wave_alert),1),        ROUND(AVG(post_accept_wave_alert),1)
           FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}""",
    )[0]

    avg_paired = q(
        conn,
        f"""SELECT
             ROUND(AVG(pre_lh_score),1),         ROUND(AVG(post_accept_lh_score),1),
             ROUND(AVG(pre_wave_error),1),        ROUND(AVG(post_accept_wave_error),1),
             ROUND(AVG(pre_wave_contrast),1),     ROUND(AVG(post_accept_wave_contrast),1),
             ROUND(AVG(pre_wave_alert),1),        ROUND(AVG(post_accept_wave_alert),1),
             COUNT(*)
           FROM chrome_scans
           WHERE cookie_notice_detected=1 AND is_error_page=0 AND {NOT_FP}
           AND pre_lh_score IS NOT NULL AND post_accept_lh_score IS NOT NULL""",
    )[0]
    n_paired = avg_paired[8]

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

    we_improved = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND post_accept_wave_error < pre_wave_error",
    )[0][0]
    we_worsened = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND post_accept_wave_error > pre_wave_error",
    )[0][0]
    we_measured = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND pre_wave_error IS NOT NULL AND post_accept_wave_error IS NOT NULL",
    )[0][0]

    wc_improved = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND post_accept_wave_contrast < pre_wave_contrast",
    )[0][0]
    wc_worsened = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND post_accept_wave_contrast > pre_wave_contrast",
    )[0][0]

    wa_improved = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND post_accept_wave_alert < pre_wave_alert",
    )[0][0]
    wa_worsened = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND post_accept_wave_alert > pre_wave_alert",
    )[0][0]

    print(
        r"Lighthouse and WAVE accessibility tools were run before accepting, after accepting, "
        + (r"and after rejecting " if has_reject_a11y else r"")
        + r"the cookie notice, allowing a direct comparison of the notice's impact on accessibility. "
        rf"Average Lighthouse scores were \textbf{{{fmt(avg_paired[0])}}} pre-interaction and "
        rf"\textbf{{{fmt(avg_paired[1])}}} post-interaction across the {n_paired} sites where both scores were available. "
        + (rf"The post-reject average was \textbf{{{fmt(avg_rej[0])}}}. " if has_reject_a11y else "")
        + rf"Of the {lh_measured} sites where both pre-interaction and post-accept scores were available, "
        rf"{lh_improved} improved after acceptance and {lh_declined} declined."
    )
    print()
    print(
        rf"WAVE reported an average of \textbf{{{fmt(avg_paired[2])}}} errors per page pre-interaction "
        rf"and \textbf{{{fmt(avg_paired[3])}}} post-accept"
        + (rf", and \textbf{{{fmt(avg_rej[1])}}} post-reject" if has_reject_a11y else "")
        + rf". Of the {we_measured} sites with WAVE error data, {we_improved} saw fewer errors "
        rf"post-accept and {we_worsened} saw more. "
        rf"For contrast errors, {wc_improved} sites improved post-accept and {wc_worsened} worsened. "
        rf"For alerts, {wa_improved} sites improved post-accept and {wa_worsened} worsened."
    )
    print()

    print(
        rf"To contextualise these figures, sites \textit{{without}} a cookie notice ({no_cookie_detected} sites) "
        rf"had a mean pre-interaction Lighthouse score of \textbf{{{fmt(avg_no_cookie[0])}}}, "
        rf"compared with \textbf{{{fmt(avg[0])}}} for sites with a notice. "
        rf"WAVE errors averaged \textbf{{{fmt(avg_no_cookie[1])}}} (no notice) vs "
        rf"\textbf{{{fmt(avg[2])}}} (with notice); "
        rf"contrast errors \textbf{{{fmt(avg_no_cookie[2])}}} vs \textbf{{{fmt(avg[4])}}}; "
        rf"and alerts \textbf{{{fmt(avg_no_cookie[3])}}} vs \textbf{{{fmt(avg[6])}}}."
    )
    print()

    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{Mean pre-interaction accessibility metrics: sites with vs without a cookie notice}")
    print(r"\begin{tabular}{lrr} \toprule")
    print(r"  \textbf{Metric} & \textbf{With notice} & \textbf{Without notice} \\ \midrule")
    print(rf"  Lighthouse score    & {fmt(avg[0])} & {fmt(avg_no_cookie[0])} \\")
    print(rf"  WAVE errors         & {fmt(avg[2])} & {fmt(avg_no_cookie[1])} \\")
    print(rf"  WAVE contrast errs  & {fmt(avg[4])} & {fmt(avg_no_cookie[2])} \\")
    print(rf"  WAVE alerts         & {fmt(avg[6])} & {fmt(avg_no_cookie[3])} \\")
    print(r"  \bottomrule\end{tabular}")
    print(r"\end{table}")
    print()

    print(r"\begin{table}[ht]\centering\footnotesize")
    if has_reject_a11y:
        print(r"\caption{Average accessibility metrics --- sites with cookie notices}")
        print(r"\begin{tabular}{lrrr}")
        print(r"\toprule Metric & Pre & Post-accept & Post-reject \\ \midrule")
        print(rf"Lighthouse score & {fmt(avg[0])} & {fmt(avg[1])} & {fmt(avg_rej[0])} \\")
        print(rf"WAVE errors & {fmt(avg[2])} & {fmt(avg[3])} & {fmt(avg_rej[1])} \\")
        print(rf"WAVE contrast errors & {fmt(avg[4])} & {fmt(avg[5])} & {fmt(avg_rej[2])} \\")
        print(rf"WAVE alerts & {fmt(avg[6])} & {fmt(avg[7])} & {fmt(avg_rej[3])} \\")
    else:
        print(r"\caption{Average accessibility metrics --- sites with cookie notices}")
        print(r"\begin{tabular}{lrr}")
        print(r"\toprule Metric & Pre & Post \\ \midrule")
        print(rf"Lighthouse score & {fmt(avg[0])} & {fmt(avg[1])} \\")
        print(rf"WAVE errors & {fmt(avg[2])} & {fmt(avg[3])} \\")
        print(rf"WAVE contrast errors & {fmt(avg[4])} & {fmt(avg[5])} \\")
        print(rf"WAVE alerts & {fmt(avg[6])} & {fmt(avg[7])} \\")
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")

    lh_rows = q_safe(
        conn,
        f"""SELECT url, pre_lh_score, post_accept_lh_score, post_reject_lh_score,
                  pre_wave_error, post_accept_wave_error, post_reject_wave_error,
                  pre_wave_contrast, post_accept_wave_contrast, post_reject_wave_contrast
           FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}
           ORDER BY url""",
    )
    if not lh_rows:
        lh_rows_base = q(
            conn,
            f"""SELECT url, pre_lh_score, post_accept_lh_score,
                      pre_wave_error, post_accept_wave_error,
                      pre_wave_contrast, post_accept_wave_contrast
               FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}
               ORDER BY url""",
        )
        lh_rows = [(url, pre_lh, post_lh, None, pre_we, post_we, None, pre_wc, post_wc, None)
                   for url, pre_lh, post_lh, pre_we, post_we, pre_wc, post_wc in lh_rows_base]

    worst_wave = max(lh_rows, key=lambda r: (r[4] or 0))
    worst_contrast = max(lh_rows, key=lambda r: (r[7] or 0))

    print(
        rf"The site with the most WAVE errors pre-interaction was "
        rf"\texttt{{{latex_escape(worst_wave[0])}}} ({fmt(worst_wave[4],0)} errors). "
        rf"The worst contrast errors were on \texttt{{{latex_escape(worst_contrast[0])}}} "
        rf"({fmt(worst_contrast[7],0)} contrast errors). "
        r"Per-site figures are in Table~\ref{tab:lh}."
    )
    print()

    # Appendix: per-site accessibility table
    if SHOW_PER:
        print(r"\begin{table*}[ht]\centering\footnotesize")
        if has_reject_a11y:
            print(r"\caption{Per-site Chrome accessibility (cookie-notice sites)}\label{tab:lh}")
            print(r"\begin{tabular}{>{\ttfamily}p{2cm} r r r r r r r r r}")
            print(r"\toprule")
            print(r"\normalfont URL & \multicolumn{3}{c}{LH} & \multicolumn{3}{c}{Err} & \multicolumn{3}{c}{Con} \\")
            print(r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}")
            print(r"\normalfont & Pre-interaction & Accept & Reject & Pre-interaction & Accept & Reject & Pre-interaction & Accept & Reject \\ \midrule")
            for url, pre_lh, post_lh, rej_lh, pre_we, post_we, rej_we, pre_wc, post_wc, rej_wc in lh_rows:
                print(
                    rf"  {latex_escape(url)} & "
                    rf"{fmt(pre_lh)} & {fmt(post_lh)} & {fmt(rej_lh)} & "
                    rf"{fmt(pre_we,0)} & {fmt(post_we,0)} & {fmt(rej_we,0)} & "
                    rf"{fmt(pre_wc,0)} & {fmt(post_wc,0)} & {fmt(rej_wc,0)} \\"
                )
        else:
            print(r"\caption{Per-site Chrome accessibility (cookie-notice sites)}\label{tab:lh}")
            print(r"\begin{tabular}{>{\ttfamily}p{2.2cm} r r r r r r}")
            print(r"\toprule")
            print(r"\normalfont URL & \multicolumn{2}{c}{LH} & \multicolumn{2}{c}{Err} & \multicolumn{2}{c}{Con} \\")
            print(r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
            print(r"\normalfont & Pre-interaction & Post-interaction & Pre-interaction & Post-interaction & Pre-interaction & Post-interaction \\ \midrule")
            for url, pre_lh, post_lh, rej_lh, pre_we, post_we, rej_we, pre_wc, post_wc, rej_wc in lh_rows:
                print(
                    rf"  {latex_escape(url)} & "
                    rf"{fmt(pre_lh)} & {fmt(post_lh)} & "
                    rf"{fmt(pre_we,0)} & {fmt(post_we,0)} & "
                    rf"{fmt(pre_wc,0)} & {fmt(post_wc,0)} \\"
                )
        print(r"\bottomrule\end{tabular}")
        print(r"\end{table*}")

    # --- Section 9: Pre-Accept, Post-Accept, and Post-Reject Comparison ---
    print(r"\subsection{Accessibility: Pre-interaction, Post-interaction, and Post-Reject Comparison}")

    pre_post = q(
        conn,
        f"""SELECT
             ROUND(AVG(pre_lh_score),1),         ROUND(AVG(post_accept_lh_score),1),
             ROUND(AVG(pre_wave_error),1),        ROUND(AVG(post_accept_wave_error),1),
             ROUND(AVG(pre_wave_contrast),1),     ROUND(AVG(post_accept_wave_contrast),1),
             ROUND(AVG(pre_wave_alert),1),        ROUND(AVG(post_accept_wave_alert),1),
             COUNT(*)
           FROM chrome_scans
           WHERE cookie_notice_detected=1 AND is_error_page=0 AND {NOT_FP}
           AND pre_lh_score IS NOT NULL AND post_accept_lh_score IS NOT NULL""",
    )[0]

    pre_lh,  post_lh  = pre_post[0], pre_post[1]
    pre_we,  post_we  = pre_post[2], pre_post[3]
    pre_wc,  post_wc  = pre_post[4], pre_post[5]
    pre_wa,  post_wa  = pre_post[6], pre_post[7]
    n_compared = pre_post[8]

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

    lh_rej_improved = q_safe(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND post_reject_lh_score > pre_lh_score")
    lh_rej_declined = q_safe(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND post_reject_lh_score < pre_lh_score")
    we_rej_improved = q_safe(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND post_reject_wave_error < pre_wave_error")
    we_rej_worsened = q_safe(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND post_reject_wave_error > pre_wave_error")
    wc_rej_improved = q_safe(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND post_reject_wave_contrast < pre_wave_contrast")
    wc_rej_worsened = q_safe(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND post_reject_wave_contrast > pre_wave_contrast")
    wa_rej_improved = q_safe(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND post_reject_wave_alert < pre_wave_alert")
    wa_rej_worsened = q_safe(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND post_reject_wave_alert > pre_wave_alert")
    lh_rej_improved = lh_rej_improved[0][0] if lh_rej_improved else None
    lh_rej_declined = lh_rej_declined[0][0] if lh_rej_declined else None
    we_rej_improved = we_rej_improved[0][0] if we_rej_improved else None
    we_rej_worsened = we_rej_worsened[0][0] if we_rej_worsened else None
    wc_rej_improved = wc_rej_improved[0][0] if wc_rej_improved else None
    wc_rej_worsened = wc_rej_worsened[0][0] if wc_rej_worsened else None
    wa_rej_improved = wa_rej_improved[0][0] if wa_rej_improved else None
    wa_rej_worsened = wa_rej_worsened[0][0] if wa_rej_worsened else None

    # --- Wilcoxon signed-rank tests (pre vs post-accept) ---
    acc_pairs_raw = q(
        conn,
        f"""SELECT pre_lh_score, post_accept_lh_score,
                   pre_wave_error, post_accept_wave_error,
                   pre_wave_contrast, post_accept_wave_contrast,
                   pre_wave_alert, post_accept_wave_alert
             FROM chrome_scans
             WHERE cookie_notice_detected=1 AND is_error_page=0 AND {NOT_FP}""",
    )
    def _col(rows, i):
        return [r[i] for r in rows]

    _, p_lh_acc = wilcoxon_p(_col(acc_pairs_raw, 0), _col(acc_pairs_raw, 1))
    _, p_we_acc = wilcoxon_p(_col(acc_pairs_raw, 2), _col(acc_pairs_raw, 3))
    _, p_wc_acc = wilcoxon_p(_col(acc_pairs_raw, 4), _col(acc_pairs_raw, 5))
    _, p_wa_acc = wilcoxon_p(_col(acc_pairs_raw, 6), _col(acc_pairs_raw, 7))

    p_lh_rej = p_we_rej = p_wc_rej = p_wa_rej = None
    if has_rej_a11y_cmp:
        rej_pairs_raw = q_safe(
            conn,
            f"""SELECT pre_lh_score, post_reject_lh_score,
                       pre_wave_error, post_reject_wave_error,
                       pre_wave_contrast, post_reject_wave_contrast,
                       pre_wave_alert, post_reject_wave_alert
                 FROM chrome_scans
                 WHERE cookie_notice_detected=1 AND is_error_page=0 AND {NOT_FP}
                 AND post_reject_lh_score IS NOT NULL""",
        )
        if rej_pairs_raw:
            _, p_lh_rej = wilcoxon_p(_col(rej_pairs_raw, 0), _col(rej_pairs_raw, 1))
            _, p_we_rej = wilcoxon_p(_col(rej_pairs_raw, 2), _col(rej_pairs_raw, 3))
            _, p_wc_rej = wilcoxon_p(_col(rej_pairs_raw, 4), _col(rej_pairs_raw, 5))
            _, p_wa_rej = wilcoxon_p(_col(rej_pairs_raw, 6), _col(rej_pairs_raw, 7))

    print(
        rf"Table~\ref{{tab:a11y}} compares accessibility metrics before and after "
        rf"accepting the cookie notice for the {n_compared} sites where both pre- and "
        r"post-accept Lighthouse scores were available. "
        + (
            rf"Post-reject metrics are shown for the {n_rej_compared} sites where rejection "
            r"succeeded and Lighthouse ran. "
            if has_rej_a11y_cmp else ""
        )
        + r"$\Delta$ columns show the change relative to pre-accept. "
        + r"$p$-values are from two-sided Wilcoxon signed-rank tests on paired observations."
    )
    print()

    print(r"\begin{table*}[t]\centering\footnotesize")
    if has_rej_a11y_cmp:
        print(
            rf"\caption{{Mean accessibility metrics. "
            r"LH\,=\,Lighthouse score (0--100); higher is better. "
            r"WAVE metrics: lower is better. "
            r"$p$-values: two-sided Wilcoxon signed-rank test (pre vs.\ post).}\label{tab:a11y}"
        )
        print(r"\begin{tabular}{lrrrrlrrl} \toprule")
        print(r"  \textbf{Metric} & \textbf{Pre} & \textbf{Post-acc} & $\Delta$\,\textbf{Acc} & $p_\text{acc}$ & \textbf{Post-rej} & $\Delta$\,\textbf{Rej} & $p_\text{rej}$ \\ \midrule")
        print(rf"  Lighthouse score    & {fmt(pre_lh)} & {fmt(post_lh)} & {delta(pre_lh, post_lh)} & {fmt_p(p_lh_acc)} & {fmt(rej_lh)} & {delta(pre_lh, rej_lh)} & {fmt_p(p_lh_rej)} \\")
        print(rf"  WAVE errors         & {fmt(pre_we)} & {fmt(post_we)} & {delta(pre_we, post_we)} & {fmt_p(p_we_acc)} & {fmt(rej_we)} & {delta(pre_we, rej_we)} & {fmt_p(p_we_rej)} \\")
        print(rf"  WAVE contrast errs  & {fmt(pre_wc)} & {fmt(post_wc)} & {delta(pre_wc, post_wc)} & {fmt_p(p_wc_acc)} & {fmt(rej_wc)} & {delta(pre_wc, rej_wc)} & {fmt_p(p_wc_rej)} \\")
        print(rf"  WAVE alerts         & {fmt(pre_wa)} & {fmt(post_wa)} & {delta(pre_wa, post_wa)} & {fmt_p(p_wa_acc)} & {fmt(rej_wa)} & {delta(pre_wa, rej_wa)} & {fmt_p(p_wa_rej)} \\")
    else:
        print(
            rf"\caption{{Mean accessibility metrics across {n_compared} sites"
            r" (cookie-notice sites only)."
            r" LH\,=\,Lighthouse score (0--100); higher is better."
            r" WAVE metrics: lower is better."
            r" $p$-values: two-sided Wilcoxon signed-rank test (pre vs.\ post-accept).}\label{tab:a11y}"
        )
        print(r"\begin{tabular}{lrrrl} \toprule")
        print(r"  \textbf{Metric} & \textbf{Pre-interaction} & \textbf{Post-accept} & $\Delta$\,\textbf{Post} & $p$ (Wilcoxon) \\ \midrule")
        print(rf"  Lighthouse score    & {fmt(pre_lh)} & {fmt(post_lh)} & {delta(pre_lh, post_lh)} & {fmt_p(p_lh_acc)} \\")
        print(rf"  WAVE errors         & {fmt(pre_we)} & {fmt(post_we)} & {delta(pre_we, post_we)} & {fmt_p(p_we_acc)} \\")
        print(rf"  WAVE contrast errs  & {fmt(pre_wc)} & {fmt(post_wc)} & {delta(pre_wc, post_wc)} & {fmt_p(p_wc_acc)} \\")
        print(rf"  WAVE alerts         & {fmt(pre_wa)} & {fmt(post_wa)} & {delta(pre_wa, post_wa)} & {fmt_p(p_wa_acc)} \\")
    print(r"  \bottomrule\end{tabular}")
    print(r"\end{table*}")
    print()

    # Prose: report statistical significance of the directional claims
    sig_metrics_acc = []
    if p_lh_acc is not None and p_lh_acc < 0.05:
        sig_metrics_acc.append(rf"Lighthouse scores ({fmt_p(p_lh_acc)})")
    if p_we_acc is not None and p_we_acc < 0.05:
        sig_metrics_acc.append(rf"WAVE errors ({fmt_p(p_we_acc)})")
    if p_wc_acc is not None and p_wc_acc < 0.05:
        sig_metrics_acc.append(rf"WAVE contrast errors ({fmt_p(p_wc_acc)})")
    if p_wa_acc is not None and p_wa_acc < 0.05:
        sig_metrics_acc.append(rf"WAVE alerts ({fmt_p(p_wa_acc)})")

    if sig_metrics_acc:
        sig_list = ", ".join(sig_metrics_acc[:-1]) + (" and " if len(sig_metrics_acc) > 1 else "") + sig_metrics_acc[-1]
        print(
            rf"Two-sided Wilcoxon signed-rank tests confirm that the pre-/post-accept "
            rf"differences are statistically significant for {sig_list}."
        )
    else:
        print(
            r"Two-sided Wilcoxon signed-rank tests did not detect a statistically significant "
            r"pre-/post-accept difference for any metric at the $\alpha=0.05$ level "
            r"(see Table~\ref{tab:a11y})."
        )
    print()


    print(r"\begin{table*}[t]\centering\footnotesize")
    if has_rej_a11y_cmp:
        print(
            r"\caption{Number of sites improved or worsened per metric. "
            r"LH\,=\,Lighthouse score (0--100); higher is better. "
            r"WAVE metrics: lower is better.}\label{tab:a11y-counts}"
        )
        print(r"\begin{tabular}{lrrrr} \toprule")
        print(r"  \textbf{Metric} & \textbf{Improved (post-accept)} & \textbf{Worsened (post-accept)} & \textbf{Improved (post-reject)} & \textbf{Worsened (post-reject)} \\ \midrule")
        print(rf"  Lighthouse score    & {fmt(lh_improved,0)} & {fmt(lh_declined,0)} & {fmt(lh_rej_improved,0)} & {fmt(lh_rej_declined,0)} \\")
        print(rf"  WAVE errors         & {fmt(we_improved,0)} & {fmt(we_worsened,0)} & {fmt(we_rej_improved,0)} & {fmt(we_rej_worsened,0)} \\")
        print(rf"  WAVE contrast errs  & {fmt(wc_improved,0)} & {fmt(wc_worsened,0)} & {fmt(wc_rej_improved,0)} & {fmt(wc_rej_worsened,0)} \\")
        print(rf"  WAVE alerts         & {fmt(wa_improved,0)} & {fmt(wa_worsened,0)} & {fmt(wa_rej_improved,0)} & {fmt(wa_rej_worsened,0)} \\")
    else:
        print(
            r"\caption{Number of sites improved or worsened per metric (post-accept). "
            r"LH\,=\,Lighthouse score (0--100); higher is better. "
            r"WAVE metrics: lower is better.}\label{tab:a11y-counts}"
        )
        print(r"\begin{tabular}{lrr} \toprule")
        print(r"  \textbf{Metric} & \textbf{Improved} & \textbf{Worsened} \\ \midrule")
        print(rf"  Lighthouse score    & {fmt(lh_improved,0)} & {fmt(lh_declined,0)} \\")
        print(rf"  WAVE errors         & {fmt(we_improved,0)} & {fmt(we_worsened,0)} \\")
        print(rf"  WAVE contrast errs  & {fmt(wc_improved,0)} & {fmt(wc_worsened,0)} \\")
        print(rf"  WAVE alerts         & {fmt(wa_improved,0)} & {fmt(wa_worsened,0)} \\")
    print(r"  \bottomrule\end{tabular}")
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
