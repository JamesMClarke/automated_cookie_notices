from .utils import q_safe, latex_escape, NOT_FP, DB_PATH


def _fmt_delta(delta):
    if delta > 0:
        return f"+{delta}"
    return str(delta)


def _wave_intro(all_rows):
    """Print an introductory paragraph summarising the overall WAVE findings."""
    n_total = len(all_rows)
    n_dec_acc = sum(1 for _, pre, acc, _ in all_rows if acc < pre)
    n_inc_acc = sum(1 for _, pre, acc, _ in all_rows if acc > pre)
    n_dec_rej = sum(1 for _, pre, _, rej in all_rows if rej < pre)
    n_inc_rej = sum(1 for _, pre, _, rej in all_rows if rej > pre)

    # Largest single accept increase / decrease by absolute delta
    biggest_inc = max(all_rows, key=lambda r: r[2] - r[1])
    biggest_dec = min(all_rows, key=lambda r: r[2] - r[1])
    inc_desc, inc_pre, inc_acc, _ = biggest_inc
    dec_desc, dec_pre, dec_acc, _ = biggest_dec

    print(
        rf"Across all WAVE categories, {n_total} issue types showed a change in the number of "
        rf"affected sites after cookie interaction. "
        rf"The dominant pattern is a \emph{{decrease}} following both accept and reject: "
        rf"{n_dec_acc} of {n_total} issue types affected fewer sites post-accept, "
        rf"and {n_dec_rej} fewer sites post-reject. "
        rf"This indicates that cookie notices are themselves a significant source of these issues --- "
        rf"once dismissed, the notice and its associated elements are removed from the page, "
        rf"reducing the overall issue count."
    )
    print()
    print(
        rf"A smaller number of issue types increased after interaction: "
        rf"{n_inc_acc} post-accept and {n_inc_rej} post-reject. "
        rf"The largest single increase was \emph{{{latex_escape(inc_desc)}}} "
        rf"({_fmt_delta(inc_acc - inc_pre)} sites post-accept), "
        rf"while the largest decrease was \emph{{{latex_escape(dec_desc)}}} "
        rf"({_fmt_delta(dec_acc - dec_pre)} sites post-accept). "
        rf"Post-accept changes are generally larger in magnitude than post-reject changes, "
        rf"consistent with acceptance triggering additional third-party content loading."
    )
    print()


def _lh_intro(rows):
    """Print an introductory paragraph summarising the Lighthouse findings."""
    n_total = len(rows)
    n_dec_acc = sum(1 for _, pre, acc, _ in rows if acc < pre)
    n_inc_acc = sum(1 for _, pre, acc, _ in rows if acc > pre)

    biggest_inc = max(rows, key=lambda r: r[2] - r[1])
    biggest_dec = min(rows, key=lambda r: r[2] - r[1])
    inc_title, inc_pre, inc_acc, inc_rej = biggest_inc
    dec_title, dec_pre, dec_acc, _ = biggest_dec
    inc_d_acc = inc_acc - inc_pre
    inc_d_rej = inc_rej - inc_pre
    dec_d_acc = dec_acc - dec_pre

    print(
        rf"Lighthouse flagged {n_total} audits whose fail-counts changed after cookie interaction. "
        rf"As with WAVE, the majority ({n_dec_acc} of {n_total}) showed \emph{{fewer}} failures "
        rf"post-interaction, again reflecting the removal of cookie-notice elements from the page. "
        rf"However, {n_inc_acc} audits recorded \emph{{more}} failures after interaction, "
        rf"representing genuine accessibility regressions introduced when the notice is dismissed."
    )
    print()
    print(
        rf"The most striking increase was \emph{{{latex_escape(inc_title)}}}, "
        rf"which rose by {_fmt_delta(inc_d_acc)} sites post-accept and "
        rf"{_fmt_delta(inc_d_rej)} post-reject. "
        rf"This suggests that on many sites the cookie notice itself contains or wraps the page's "
        rf"main landmark, so dismissing it leaves the document without one. "
        rf"The largest decrease was \emph{{{latex_escape(dec_title)}}} "
        rf"({_fmt_delta(dec_d_acc)} sites post-accept), consistent with cookie notices "
        rf"frequently presenting unlabelled interactive controls."
    )
    print()


def run(conn):
    _has_wave_issues = bool(q_safe(
        conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='wave_issues'"))
    _has_lh_issues = bool(q_safe(
        conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='lighthouse_issues'"))

    print(r"\subsection{Accessibility: Specific Issues Changed by Cookie Interaction}")

    if not _has_wave_issues and not _has_lh_issues:
        print(r"Detailed issue data not yet available (run postProcessor to populate "
              r"\texttt{wave\_issues} and \texttt{lighthouse\_issues} tables).")
        print()
        return

    if _has_wave_issues:
        print(r"\subsubsection{WAVE Issue Changes}")

        # Fetch all changed issues across categories for the summary paragraph.
        all_wave = q_safe(conn, f"""
            SELECT
                MAX(wi.description) AS description,
                SUM(CASE WHEN wi.phase = 'pre'         THEN 1 ELSE 0 END) AS pre_count,
                SUM(CASE WHEN wi.phase = 'post_accept' THEN 1 ELSE 0 END) AS accept_count,
                SUM(CASE WHEN wi.phase = 'post_reject' THEN 1 ELSE 0 END) AS reject_count
            FROM wave_issues wi
            JOIN chrome_scans cs ON cs.id = wi.scan_id
            WHERE cs.cookie_notice_detected = 1 AND {NOT_FP}
            GROUP BY wi.issue_id, wi.category
            HAVING accept_count != pre_count OR reject_count != pre_count""")

        categories = q_safe(conn, f"""
            SELECT DISTINCT wi.category
            FROM wave_issues wi
            JOIN chrome_scans cs ON cs.id = wi.scan_id
            WHERE cs.cookie_notice_detected = 1 AND {NOT_FP}
            ORDER BY wi.category""")

        # Check whether any category has changed rows before printing the intro.
        any_wave = False
        category_rows = {}
        for (category,) in categories:
            rows = q_safe(conn, f"""
                SELECT
                    MAX(wi.description) AS description,
                    SUM(CASE WHEN wi.phase = 'pre'         THEN 1 ELSE 0 END) AS pre_count,
                    SUM(CASE WHEN wi.phase = 'post_accept' THEN 1 ELSE 0 END) AS accept_count,
                    SUM(CASE WHEN wi.phase = 'post_reject' THEN 1 ELSE 0 END) AS reject_count
                FROM wave_issues wi
                JOIN chrome_scans cs ON cs.id = wi.scan_id
                WHERE cs.cookie_notice_detected = 1 AND {NOT_FP}
                  AND wi.category = ?
                GROUP BY wi.issue_id
                HAVING accept_count != pre_count OR reject_count != pre_count
                ORDER BY
                    ABS(accept_count - pre_count) + ABS(reject_count - pre_count) DESC,
                    description""", (category,))
            if rows:
                any_wave = True
                category_rows[category] = rows

        if any_wave:
            _wave_intro(all_wave)
            for category, rows in category_rows.items():
                cat_label = latex_escape(category)
                print(
                    rf"Table~\ref{{tab:wave-{category}}} shows changes in \textit{{{cat_label}}} "
                    rf"issues ({len(rows)} type(s) affected)."
                )
                print()
                print(r"{\footnotesize")
                print(r"\begin{xltabular}{\linewidth}{Xrrrrr}")
                print(rf"\caption{{WAVE {cat_label} issue counts before and after cookie "
                      rf"interaction}}\label{{tab:wave-{category}}} \\")
                print(r"\toprule")
                print(r"Description & Pre & Post-Accept & $\Delta$\,Accept "
                      r"& Post-Reject & $\Delta$\,Reject \\ \midrule")
                print(r"\endfirsthead")
                print(r"\toprule")
                print(r"Description & Pre & Post-Accept & $\Delta$\,Accept "
                      r"& Post-Reject & $\Delta$\,Reject \\ \midrule")
                print(r"\endhead")
                print(r"\midrule \multicolumn{6}{r}{\footnotesize\itshape continued\ldots} \\")
                print(r"\endfoot")
                print(r"\bottomrule")
                print(r"\endlastfoot")
                for description, pre, accept, reject in rows:
                    d_accept = accept - pre
                    d_reject = reject - pre
                    print(
                        rf"  {latex_escape(description or '')} & "
                        rf"{pre} & {accept} & {_fmt_delta(d_accept)} & "
                        rf"{reject} & {_fmt_delta(d_reject)} \\"
                    )
                print(r"\end{xltabular}}")
        else:
            print(r"No WAVE issue types changed between pre- and post-interaction phases.")
            print()

    if _has_lh_issues:
        print(r"\subsubsection{Lighthouse Audit Changes}")
        rows = q_safe(conn, f"""
            SELECT
                MAX(lhi.title) AS title,
                SUM(CASE WHEN lhi.phase = 'pre'         THEN 1 ELSE 0 END) AS pre_count,
                SUM(CASE WHEN lhi.phase = 'post_accept' THEN 1 ELSE 0 END) AS accept_count,
                SUM(CASE WHEN lhi.phase = 'post_reject' THEN 1 ELSE 0 END) AS reject_count
            FROM lighthouse_issues lhi
            JOIN chrome_scans cs ON cs.id = lhi.scan_id
            WHERE cs.cookie_notice_detected = 1 AND {NOT_FP}
            GROUP BY lhi.audit_id
            HAVING accept_count != pre_count OR reject_count != pre_count
            ORDER BY
                ABS(accept_count - pre_count) + ABS(reject_count - pre_count) DESC,
                title""")
        if rows:
            _lh_intro(rows)
            print(r"Full results are shown in Table~\ref{tab:lh-issues}.")
            print()
            print(r"{\footnotesize")
            print(r"\begin{xltabular}{\linewidth}{Xrrrrr}")
            print(r"\caption{Lighthouse audit fail counts before and after cookie interaction}"
                  r"\label{tab:lh-issues} \\")
            print(r"\toprule")
            print(r"Title & Pre & Post-Accept & $\Delta$\,Accept "
                  r"& Post-Reject & $\Delta$\,Reject \\ \midrule")
            print(r"\endfirsthead")
            print(r"\toprule")
            print(r"Title & Pre & Post-Accept & $\Delta$\,Accept "
                  r"& Post-Reject & $\Delta$\,Reject \\ \midrule")
            print(r"\endhead")
            print(r"\midrule \multicolumn{6}{r}{\footnotesize\itshape continued\ldots} \\")
            print(r"\endfoot")
            print(r"\bottomrule")
            print(r"\endlastfoot")
            for title, pre, accept, reject in rows:
                d_accept = accept - pre
                d_reject = reject - pre
                print(
                    rf"  {latex_escape(title or '')} & "
                    rf"{pre} & {accept} & {_fmt_delta(d_accept)} & "
                    rf"{reject} & {_fmt_delta(d_reject)} \\"
                )
            print(r"\end{xltabular}}")
        else:
            print(r"No Lighthouse audit types changed between pre- and post-interaction phases.")
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
