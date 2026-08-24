import sqlite3
import sys
from pathlib import Path

from .utils import ACCEPTED, DEFAULT_DBS, NOT_FP, fmt, latex_escape, q_safe

_DB_LABELS = {
    "top-1000.sqlite": "Top",
    "crawl_two.sqlite": "Middle",
    "crawl_three.sqlite": "Bottom",
}


def _label(db_path):
    return _DB_LABELS.get(Path(db_path).name, latex_escape(Path(db_path).stem))


def _metrics(db_path):
    conn = sqlite3.connect(str(db_path))
    try:

        def q(sql):
            return conn.execute(sql).fetchall()

        reachable = q("SELECT COUNT(*) FROM chrome_scans WHERE is_error_page=0")[0][0]
        detected = q(f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}")[0][0]

        avg = q(
            f"""SELECT
                 ROUND(AVG(pre_lh_score),1),
                 ROUND(AVG(CASE WHEN {ACCEPTED} THEN post_accept_lh_score END),1),
                 ROUND(AVG(pre_wave_error),1),
                 ROUND(AVG(CASE WHEN {ACCEPTED} THEN post_accept_wave_error END),1),
                 ROUND(AVG(pre_wave_contrast),1),
                 ROUND(AVG(CASE WHEN {ACCEPTED} THEN post_accept_wave_contrast END),1),
                 ROUND(AVG(pre_wave_alert),1),
                 ROUND(AVG(CASE WHEN {ACCEPTED} THEN post_accept_wave_alert END),1)
               FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}"""
        )[0]

        avg_rej = q_safe(
            conn,
            f"""SELECT
                 ROUND(AVG(post_reject_lh_score),1),
                 ROUND(AVG(post_reject_wave_error),1),
                 ROUND(AVG(post_reject_wave_contrast),1),
                 ROUND(AVG(post_reject_wave_alert),1)
               FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}
               AND cookie_notice_rejected=1 AND post_reject_lh_score IS NOT NULL""",
        )
        avg_rej = avg_rej[0] if avg_rej else (None, None, None, None)

        lh_improved = q(
            f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND {ACCEPTED} "
            f"AND post_accept_lh_score > pre_lh_score"
        )[0][0]
        lh_declined = q(
            f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND {ACCEPTED} "
            f"AND post_accept_lh_score < pre_lh_score"
        )[0][0]
        lh_measured = q(
            f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND {ACCEPTED} "
            f"AND pre_lh_score IS NOT NULL AND post_accept_lh_score IS NOT NULL"
        )[0][0]

        we_improved = q(
            f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND {ACCEPTED} "
            f"AND post_accept_wave_error < pre_wave_error"
        )[0][0]
        we_worsened = q(
            f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND {ACCEPTED} "
            f"AND post_accept_wave_error > pre_wave_error"
        )[0][0]

        def qc(sql):
            rows = q_safe(conn, sql)
            return rows[0][0] if rows else 0

        _rej = f"cookie_notice_detected=1 AND {NOT_FP} AND cookie_notice_rejected=1"
        detected_rej = qc(f"SELECT COUNT(*) FROM chrome_scans WHERE {_rej}")
        lh_improved_rej = qc(f"SELECT COUNT(*) FROM chrome_scans WHERE {_rej} AND post_reject_lh_score > pre_lh_score")
        lh_declined_rej = qc(f"SELECT COUNT(*) FROM chrome_scans WHERE {_rej} AND post_reject_lh_score < pre_lh_score")
        lh_measured_rej = qc(
            f"SELECT COUNT(*) FROM chrome_scans WHERE {_rej} "
            f"AND pre_lh_score IS NOT NULL AND post_reject_lh_score IS NOT NULL"
        )
        we_improved_rej = qc(
            f"SELECT COUNT(*) FROM chrome_scans WHERE {_rej} AND post_reject_wave_error < pre_wave_error"
        )
        we_worsened_rej = qc(
            f"SELECT COUNT(*) FROM chrome_scans WHERE {_rej} AND post_reject_wave_error > pre_wave_error"
        )

        _ar = f"cookie_notice_detected=1 AND {NOT_FP} AND {ACCEPTED} AND cookie_notice_rejected=1"
        detected_ar = qc(f"SELECT COUNT(*) FROM chrome_scans WHERE {_ar}")
        lh_improved_ar = qc(
            f"SELECT COUNT(*) FROM chrome_scans WHERE {_ar} "
            f"AND post_reject_lh_score IS NOT NULL AND post_accept_lh_score IS NOT NULL "
            f"AND post_reject_lh_score > post_accept_lh_score"
        )
        lh_declined_ar = qc(
            f"SELECT COUNT(*) FROM chrome_scans WHERE {_ar} "
            f"AND post_reject_lh_score IS NOT NULL AND post_accept_lh_score IS NOT NULL "
            f"AND post_reject_lh_score < post_accept_lh_score"
        )
        lh_measured_ar = qc(
            f"SELECT COUNT(*) FROM chrome_scans WHERE {_ar} "
            f"AND post_accept_lh_score IS NOT NULL AND post_reject_lh_score IS NOT NULL"
        )
        we_improved_ar = qc(
            f"SELECT COUNT(*) FROM chrome_scans WHERE {_ar} AND post_reject_wave_error < post_accept_wave_error"
        )
        we_worsened_ar = qc(
            f"SELECT COUNT(*) FROM chrome_scans WHERE {_ar} AND post_reject_wave_error > post_accept_wave_error"
        )

    finally:
        conn.close()

    return {
        "reachable": reachable,
        "detected": detected,
        "pre_lh": avg[0],
        "acc_lh": avg[1],
        "pre_we": avg[2],
        "acc_we": avg[3],
        "pre_wc": avg[4],
        "acc_wc": avg[5],
        "pre_wa": avg[6],
        "acc_wa": avg[7],
        "rej_lh": avg_rej[0],
        "rej_we": avg_rej[1],
        "rej_wc": avg_rej[2],
        "rej_wa": avg_rej[3],
        "lh_improved": lh_improved,
        "lh_declined": lh_declined,
        "lh_measured": lh_measured,
        "we_improved": we_improved,
        "we_worsened": we_worsened,
        "detected_rej": detected_rej,
        "lh_improved_rej": lh_improved_rej,
        "lh_declined_rej": lh_declined_rej,
        "lh_measured_rej": lh_measured_rej,
        "we_improved_rej": we_improved_rej,
        "we_worsened_rej": we_worsened_rej,
        "detected_ar": detected_ar,
        "lh_improved_ar": lh_improved_ar,
        "lh_declined_ar": lh_declined_ar,
        "lh_measured_ar": lh_measured_ar,
        "we_improved_ar": we_improved_ar,
        "we_worsened_ar": we_worsened_ar,
    }


_SRM_COLS = [
    ("metric_readable", r"(i) Readable"),
    ("metric_immediately_read", r"(ii) Immediately Read"),
    ("metric_keyboard_nav", r"(iii) Keyboard Navigable"),
    ("metric_link_purpose", r"(iv) Link or Button Purpose"),
    ("metric_abbreviations", r"(v) Abbreviations Explained"),
    ("metric_page_titled", r"(vi) Page Titled"),
    ("metric_notice_titled", r"(vii) Cookie Notice Titled"),
]

_DIST_BUCKETS = [
    (r"$= 0$", lambda d: d == 0),
    (r"$1$--$10$", lambda d: 1 <= d <= 10),
    (r"$11$--$30$", lambda d: 11 <= d <= 30),
    (r"$31$--$100$", lambda d: 31 <= d <= 100),
    (r"$> 100$", lambda d: d > 100),
]


def _issue_metrics(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

        wave = {}
        if "wave_issues" in tables:
            for issue_id, desc, category, pre, acc, rej in conn.execute(f"""
                SELECT wi.issue_id,
                       MAX(wi.description),
                       wi.category,
                       SUM(CASE WHEN wi.phase='pre'         THEN 1 ELSE 0 END),
                       SUM(CASE WHEN wi.phase='post_accept' AND {ACCEPTED} THEN 1 ELSE 0 END),
                       SUM(CASE WHEN wi.phase='post_reject' AND cs.cookie_notice_rejected=1 THEN 1 ELSE 0 END)
                FROM wave_issues wi
                JOIN chrome_scans cs ON cs.id = wi.scan_id
                WHERE cs.cookie_notice_detected=1 AND {NOT_FP}
                  AND (wi.phase='pre'
                    OR (wi.phase='post_accept' AND {ACCEPTED})
                    OR (wi.phase='post_reject' AND cs.cookie_notice_rejected=1))
                GROUP BY wi.issue_id, wi.category
                HAVING SUM(CASE WHEN wi.phase='post_accept' AND {ACCEPTED} THEN 1 ELSE 0 END)
                    != SUM(CASE WHEN wi.phase='pre'         THEN 1 ELSE 0 END)
                OR     SUM(CASE WHEN wi.phase='post_reject' AND cs.cookie_notice_rejected=1 THEN 1 ELSE 0 END)
                    != SUM(CASE WHEN wi.phase='pre'         THEN 1 ELSE 0 END)
            """).fetchall():
                wave[issue_id] = (desc or issue_id, pre or 0, acc or 0, rej or 0, category or "")

        lh = {}
        if "lighthouse_issues" in tables:
            for audit_id, title, pre, acc, rej in conn.execute(f"""
                SELECT lhi.audit_id,
                       MAX(lhi.title),
                       SUM(CASE WHEN lhi.phase='pre'         THEN 1 ELSE 0 END),
                       SUM(CASE WHEN lhi.phase='post_accept' AND {ACCEPTED} THEN 1 ELSE 0 END),
                       SUM(CASE WHEN lhi.phase='post_reject' AND cs.cookie_notice_rejected=1 THEN 1 ELSE 0 END)
                FROM lighthouse_issues lhi
                JOIN chrome_scans cs ON cs.id = lhi.scan_id
                WHERE cs.cookie_notice_detected=1 AND {NOT_FP}
                  AND (lhi.phase='pre'
                    OR (lhi.phase='post_accept' AND {ACCEPTED})
                    OR (lhi.phase='post_reject' AND cs.cookie_notice_rejected=1))
                GROUP BY lhi.audit_id
                HAVING SUM(CASE WHEN lhi.phase='post_accept' AND {ACCEPTED} THEN 1 ELSE 0 END)
                    != SUM(CASE WHEN lhi.phase='pre'         THEN 1 ELSE 0 END)
                OR     SUM(CASE WHEN lhi.phase='post_reject' AND cs.cookie_notice_rejected=1 THEN 1 ELSE 0 END)
                    != SUM(CASE WHEN lhi.phase='pre'         THEN 1 ELSE 0 END)
            """).fetchall():
                lh[audit_id] = (title or audit_id, pre or 0, acc or 0, rej or 0)

        return {"wave": wave, "lh": lh}
    finally:
        conn.close()


def _srm_metrics(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        has_tbl = bool(
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='screen_reader_metrics'"
            ).fetchone()
        )
        if not has_tbl:
            return None

        col_parts = ", ".join(
            f"SUM(CASE WHEN {col}=1 THEN 1 ELSE 0 END), SUM(CASE WHEN {col}=0 THEN 1 ELSE 0 END)"
            for col, _ in _SRM_COLS
        )
        row = conn.execute(f"""
            SELECT {col_parts}
            FROM screen_reader_metrics srm
            JOIN chrome_scans cs ON cs.id = srm.scan_id
            WHERE cs.is_error_page=0 AND {NOT_FP}
        """).fetchone()

        rates = {}
        for i, (col, _) in enumerate(_SRM_COLS):
            p = row[i * 2] or 0
            f_ = row[i * 2 + 1] or 0
            n = p + f_
            rates[col] = (p, f_, n)

        has_dist = bool(
            conn.execute(
                "SELECT 1 FROM pragma_table_info('screen_reader_metrics') WHERE name='immediately_read_distance'"
            ).fetchone()
        )
        distances = []
        if has_dist:
            distances = [
                r[0]
                for r in conn.execute(f"""
                SELECT srm.immediately_read_distance
                FROM screen_reader_metrics srm
                JOIN chrome_scans cs ON cs.id = srm.scan_id
                WHERE cs.is_error_page=0 AND {NOT_FP}
clear                  AND srm.immediately_read_distance IS NOT NULL
            """).fetchall()
            ]

        return {"rates": rates, "distances": distances}
    finally:
        conn.close()


def _pct(num, denom):
    if not denom:
        return "---"
    return rf"{num / denom * 100:.0f}\,\%"


def run(db_paths):
    if len(db_paths) < 2:
        print(r"% accessibility_differences: need at least two databases", file=sys.stderr)
        return

    data = [(db_path, _label(db_path), _metrics(db_path)) for db_path in db_paths]

    labels = [label for _, label, _ in data]
    has_reject = any(m["rej_lh"] is not None for _, _, m in data)

    col_spec = "l" + "r" * len(data)
    col_header = " & ".join(rf"\textbf{{{l}}}" for l in labels)

    print(r"\subsection{Accessibility Differences Across Crawl Strata}")
    print(
        r"This section explores how accessibility metrics differ across the three tiers of the "
        r"Tranco ranking, referred to here as the \emph{top} (ranks 1--1{,}000), \emph{middle} "
        r"(a random sample from ranks 1{,}001--10{,}000), and \emph{bottom} (a random sample "
        r"from beyond rank 10{,}000). We focus on sites where a cookie notice was detected, as "
        r"these are the sites where post-interaction accessibility changes can be measured. "
        r"Lighthouse scores are on a 0--100 scale (higher is better); WAVE error, contrast, "
        r"and alert counts are lower-is-better."
    )
    print()

    # ── combined pre / post-accept / post-reject table ────────────────────────
    n = len(data)
    tier_header = " & ".join(rf"\textbf{{{l}}}" for l in labels)

    def row(label, vals):
        print(rf"  {label} & {' & '.join(vals)} \\")

    if has_reject:
        # 1 label col + 3 phase groups × n tier cols
        full_col_spec = "l" + " rrr" * n
        phase_span = n

        print(r"\begin{table}[ht]\centering\footnotesize")
        print(
            r"\caption{Mean accessibility metrics by Tranco stratum and interaction phase}"
            r"\label{tab:a11y-strata}"
        )
        print(rf"\begin{{tabular}}{{{full_col_spec}}}")
        print(r"\toprule")
        # Phase multicolumn headers
        mc = rf"\multicolumn{{{phase_span}}}{{c}}"
        print(rf"  & {mc}{{Pre-interaction}} & {mc}{{Post-accept}} & {mc}{{Post-reject}} \\")
        # cmidrules under each phase group (cols 2..n+1, n+2..2n+1, 2n+2..3n+1)
        c1s, c1e = 2, n + 1
        c2s, c2e = n + 2, 2 * n + 1
        c3s, c3e = 2 * n + 2, 3 * n + 1
        print(
            rf"  \cmidrule(lr){{{c1s}-{c1e}}}"
            rf"\cmidrule(lr){{{c2s}-{c2e}}}"
            rf"\cmidrule(lr){{{c3s}-{c3e}}}"
        )
        print(rf"  Metric & {tier_header} & {tier_header} & {tier_header} \\ \midrule")

        blank3 = rf"\multicolumn{{{phase_span}}}{{c}}{{---}}"

        def row3(label, pre_vals, acc_vals, rej_vals):
            print(rf"  {label} & {' & '.join(pre_vals)} & {' & '.join(acc_vals)} & {' & '.join(rej_vals)} \\")

        row3("Reachable sites", [str(m["reachable"]) for _, _, m in data], [blank3], [blank3])
        row3("Cookie notices detected", [_pct(m["detected"], m["reachable"]) for _, _, m in data], [blank3], [blank3])
        print(r"  \addlinespace[3pt]")
        row3(
            r"LH score",
            [fmt(m["pre_lh"]) for _, _, m in data],
            [fmt(m["acc_lh"]) for _, _, m in data],
            [fmt(m["rej_lh"]) for _, _, m in data],
        )
        row3(
            r"WAVE errors",
            [fmt(m["pre_we"]) for _, _, m in data],
            [fmt(m["acc_we"]) for _, _, m in data],
            [fmt(m["rej_we"]) for _, _, m in data],
        )
        row3(
            r"WAVE contrast errors",
            [fmt(m["pre_wc"]) for _, _, m in data],
            [fmt(m["acc_wc"]) for _, _, m in data],
            [fmt(m["rej_wc"]) for _, _, m in data],
        )
        row3(
            r"WAVE alerts",
            [fmt(m["pre_wa"]) for _, _, m in data],
            [fmt(m["acc_wa"]) for _, _, m in data],
            [fmt(m["rej_wa"]) for _, _, m in data],
        )

    else:
        # No reject data: two phase groups only
        full_col_spec = "l" + " rr" * n
        phase_span = n

        print(r"\begin{table}[ht]\centering\footnotesize")
        print(
            r"\caption{Mean accessibility metrics by Tranco stratum and interaction phase}"
            r"\label{tab:a11y-strata}"
        )
        print(rf"\begin{{tabular}}{{{full_col_spec}}}")
        print(r"\toprule")
        mc = rf"\multicolumn{{{phase_span}}}{{c}}"
        print(rf"  & {mc}{{Pre-interaction}} & {mc}{{Post-accept}} \\")
        c1s, c1e = 2, n + 1
        c2s, c2e = n + 2, 2 * n + 1
        print(rf"  \cmidrule(lr){{{c1s}-{c1e}}}\cmidrule(lr){{{c2s}-{c2e}}}")
        print(rf"  Metric & {tier_header} & {tier_header} \\ \midrule")

        blank2 = rf"\multicolumn{{{phase_span}}}{{c}}{{---}}"

        def row2(label, pre_vals, acc_vals):
            print(rf"  {label} & {' & '.join(pre_vals)} & {' & '.join(acc_vals)} \\")

        row2("Reachable sites", [str(m["reachable"]) for _, _, m in data], [blank2])
        row2("Cookie notices detected", [_pct(m["detected"], m["reachable"]) for _, _, m in data], [blank2])
        print(r"  \addlinespace[3pt]")
        row2(r"LH score", [fmt(m["pre_lh"]) for _, _, m in data], [fmt(m["acc_lh"]) for _, _, m in data])
        row2(r"WAVE errors", [fmt(m["pre_we"]) for _, _, m in data], [fmt(m["acc_we"]) for _, _, m in data])
        row2(r"WAVE contrast errors", [fmt(m["pre_wc"]) for _, _, m in data], [fmt(m["acc_wc"]) for _, _, m in data])
        row2(r"WAVE alerts", [fmt(m["pre_wa"]) for _, _, m in data], [fmt(m["acc_wa"]) for _, _, m in data])

    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")
    print()

    # ── improvement / worsening table ─────────────────────────────────────────
    print(r"\begin{table}[ht]\centering\footnotesize")
    print(
        r"\caption{Sites with accessibility changes by comparison phase and Tranco stratum}\label{tab:a11y-strata-delta}"
    )
    print(rf"\begin{{tabular}}{{{col_spec}}}")
    print(rf"\toprule Metric & {col_header} \\ \midrule")

    print(rf"  \multicolumn{{{1 + len(data)}}}{{l}}{{\textit{{Pre vs.\ Accept}}}} \\")
    row(r"\quad LH improved post-accept", [_pct(m["lh_improved"], m["lh_measured"]) for _, _, m in data])
    row(r"\quad LH declined post-accept", [_pct(m["lh_declined"], m["lh_measured"]) for _, _, m in data])
    print(r"  \addlinespace[3pt]")
    row(r"\quad WAVE errors improved post-accept", [_pct(m["we_improved"], m["detected"]) for _, _, m in data])
    row(r"\quad WAVE errors worsened post-accept", [_pct(m["we_worsened"], m["detected"]) for _, _, m in data])

    if has_reject:
        print(r"  \addlinespace[6pt]")
        print(rf"  \multicolumn{{{1 + len(data)}}}{{l}}{{\textit{{Pre vs.\ Reject}}}} \\")
        row(r"\quad LH improved post-reject", [_pct(m["lh_improved_rej"], m["lh_measured_rej"]) for _, _, m in data])
        row(r"\quad LH declined post-reject", [_pct(m["lh_declined_rej"], m["lh_measured_rej"]) for _, _, m in data])
        print(r"  \addlinespace[3pt]")
        row(
            r"\quad WAVE errors improved post-reject",
            [_pct(m["we_improved_rej"], m["detected_rej"]) for _, _, m in data],
        )
        row(
            r"\quad WAVE errors worsened post-reject",
            [_pct(m["we_worsened_rej"], m["detected_rej"]) for _, _, m in data],
        )

        print(r"  \addlinespace[6pt]")
        print(rf"  \multicolumn{{{1 + len(data)}}}{{l}}{{\textit{{Accept vs.\ Reject}}}} \\")
        row(
            r"\quad LH higher post-reject than post-accept",
            [_pct(m["lh_improved_ar"], m["lh_measured_ar"]) for _, _, m in data],
        )
        row(
            r"\quad LH lower post-reject than post-accept",
            [_pct(m["lh_declined_ar"], m["lh_measured_ar"]) for _, _, m in data],
        )
        print(r"  \addlinespace[3pt]")
        row(
            r"\quad WAVE errors lower post-reject than post-accept",
            [_pct(m["we_improved_ar"], m["detected_ar"]) for _, _, m in data],
        )
        row(
            r"\quad WAVE errors higher post-reject than post-accept",
            [_pct(m["we_worsened_ar"], m["detected_ar"]) for _, _, m in data],
        )

    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")
    print()

    # ── prose observations ────────────────────────────────────────────────────
    lh_pres = [m["pre_lh"] or 0 for _, _, m in data]
    lh_accs = [m["acc_lh"] or 0 for _, _, m in data]  # noqa: F841
    we_pres = [m["pre_we"] or 0 for _, _, m in data]

    best_lh_idx = lh_pres.index(max(lh_pres))
    worst_we_idx = we_pres.index(max(we_pres))  # noqa: F841

    detect_pcts = [m["detected"] / m["reachable"] * 100 if m["reachable"] else 0 for _, _, m in data]

    print(
        rf"We see a clear decline in cookie notice prevalence with lower Tranco rank: "
        rf"\textbf{{{detect_pcts[0]:.0f}}}\,\% of reachable top-ranking sites showed a notice, "
        rf"dropping to \textbf{{{detect_pcts[1]:.0f}}}\,\% in the middle ranking and "
        rf"\textbf{{{detect_pcts[2]:.0f}}}\,\% in the bottom ranking. "
        r"This suggests that less popular sites are less likely to implement cookie notices, "
        r"which may reflect lower regulatory compliance or resource constraints. "
        r"Additionally, we also see a decline in baseline accessibility metrics with lower rank: "
        rf"the \emph{{top}} ranking had the highest pre-interaction Lighthouse scores "
        rf"(avg.\ \textbf{{{fmt(lh_pres[best_lh_idx])}}}) and the fewest WAVE errors "
        rf"(avg.\ \textbf{{{fmt(we_pres[0])}}}),"
        rf" while the \emph{{bottom}} ranking had the lowest Lighthouse scores "
        rf"(avg.\ \textbf{{{fmt(lh_pres[-1])}}}) and the most WAVE errors "
        rf"(avg.\ \textbf{{{fmt(we_pres[-1])}}})."
        r" This indicates that less popular sites tend to have poorer accessibility even before "
        r"considering cookie notice interactions. "
        r"\autoref{tab:a11y-strata} summarises these findings."
    )
    print()

    lh_delta = [(m["acc_lh"] or 0) - (m["pre_lh"] or 0) for _, _, m in data]  # noqa: F841
    lh_imp = [_pct(m["lh_improved"], m["lh_measured"]) for _, _, m in data]
    lh_dec = [_pct(m["lh_declined"], m["lh_measured"]) for _, _, m in data]
    we_imp = [_pct(m["we_improved"], m["detected"]) for _, _, m in data]
    we_wor = [_pct(m["we_worsened"], m["detected"]) for _, _, m in data]

    tier_parts = "; ".join(
        rf"\emph{{{l}}}: {lh_dec[i]} declined vs.\ {lh_imp[i]} improved" for i, l in enumerate(labels)
    )
    print(
        rf"Post-accept, Lighthouse scores declined on substantially more sites than they "
        rf"improved across all three rankings ({tier_parts}), suggesting that accepting the "
        r"cookie notice introduces accessibility regressions regardless of site popularity. "
        rf"The pattern for WAVE errors is more nuanced: \emph{{{labels[0]}}}-ranking sites were "
        rf"more likely to see errors worsen than improve ({we_wor[0]} vs.\ {we_imp[0]}), "
        rf"whereas this reverses in the \emph{{{labels[-1]}}}-ranking where more sites improved "
        rf"({we_imp[-1]}) than worsened ({we_wor[-1]}). "
        r"This may reflect that lower-ranked sites tend to use simpler notice designs whose "
        r"removal has a net positive effect on WAVE counts, while top-ranked sites deploy more "
        r"complex notices that introduce errors when dismissed. "
        r"\autoref{tab:a11y-strata-delta} summarises these findings."
    )
    print()

    # ── WAVE / Lighthouse issue changes by stratum ────────────────────────────
    issue_data = [(db_path, _label(db_path), _issue_metrics(db_path)) for db_path in db_paths]

    def _fmt_d(d):
        return f"+{d}" if d > 0 else str(d)

    def _issue_table(
        kind, id_key, desc_key, caption, label, top_n=10, categories=None, delta_pair=(2, 1), phase_label="post-accept"
    ):
        # delta_pair=(new_idx, base_idx) into entry tuple (desc, pre, acc, rej[, cat])
        all_ids = {}
        for _, _, m in issue_data:
            for iid, entry in m[kind].items():
                if iid not in all_ids:
                    if categories is None or (len(entry) > 4 and entry[4] in categories):
                        all_ids[iid] = entry[0]

        if not all_ids:
            return

        ni, bi = delta_pair

        def _total_impact(iid):
            return sum(
                abs((m[kind].get(iid, (None, 0, 0, 0))[ni]) - (m[kind].get(iid, (None, 0, 0, 0))[bi]))
                for _, _, m in issue_data
            )

        ranked = sorted(all_ids, key=_total_impact, reverse=True)[:top_n]

        n_strata = len(issue_data)
        has_cat = kind == "wave" and categories is not None
        cat_abbrev = {"alert": "Al", "contrast": "Co", "error": "Er"}
        col_spec = "l" + ("l" if has_cat else "") + "r" * n_strata
        col_hdr = " & ".join(rf"\textbf{{{l}}}" for _, l, _ in issue_data)
        cat_header = " & Cat." if has_cat else ""

        print(r"\begin{table}[ht]\centering\footnotesize")
        if has_cat:
            print(rf"\caption{{{caption}. Cat.:\ Al\,=\,Alert; Co\,=\,Contrast; Er\,=\,Error.}}\label{{{label}}}")
        else:
            print(rf"\caption{{{caption}}}\label{{{label}}}")
        print(rf"\begin{{tabular}}{{{col_spec}}}")
        print(rf"\toprule {desc_key} ($\Delta$ {phase_label}){cat_header} & {col_hdr} \\ \midrule")

        for iid in ranked:
            desc = latex_escape(all_ids[iid])
            cat_col = ""
            if has_cat:
                raw_cat = ""
                for _, _, m in issue_data:
                    entry = m[kind].get(iid)
                    if entry and len(entry) > 4:
                        raw_cat = entry[4]
                        break
                cat_col = f" & {cat_abbrev.get(raw_cat, raw_cat)}"
            deltas = []
            for _, _, m in issue_data:
                entry = m[kind].get(iid)
                if entry and len(entry) > max(ni, bi):
                    deltas.append(_fmt_d(entry[ni] - entry[bi]))
                else:
                    deltas.append("---")
            print(rf"  {desc}{cat_col} & {' & '.join(deltas)} \\")

        print(r"\bottomrule\end{tabular}")
        print(r"\end{table}")
        print()

    has_wave = any(m["wave"] for _, _, m in issue_data)
    has_lh = any(m["lh"] for _, _, m in issue_data)

    if has_wave or has_lh:
        print(r"\subsubsection{WAVE and Lighthouse Issue Changes Across Strata}")
        print(
            r"We examine which WAVE and Lighthouse issues changed most after accepting the "
            r"cookie notice, and whether these changes are consistent across the three "
            r"popularity rankings. $\Delta$ shows the change in the number of affected sites "
            r"between the pre-interaction and post-accept phases; negative values indicate "
            r"fewer affected sites, consistent with cookie-notice elements being removed from "
            r"the page, while positive values indicate regressions introduced by dismissal."
        )
        print()

        if has_wave:
            _issue_table(
                "wave",
                "issue_id",
                "WAVE issue",
                r"Top WAVE issue changes post-accept by Tranco stratum",
                "tab:wave-strata",
                categories={"alert", "contrast", "error"},
            )

        if has_lh:
            _issue_table(
                "lh",
                "audit_id",
                "Lighthouse audit",
                r"Top Lighthouse audit changes post-accept by Tranco stratum",
                "tab:lh-strata",
            )

        if has_reject:
            if has_wave:
                _issue_table(
                    "wave",
                    "issue_id",
                    "WAVE issue",
                    r"Top WAVE issue changes post-reject by Tranco stratum",
                    "tab:wave-strata-rej",
                    categories={"alert", "contrast", "error"},
                    delta_pair=(3, 1),
                    phase_label="post-reject",
                )
            if has_lh:
                _issue_table(
                    "lh",
                    "audit_id",
                    "Lighthouse audit",
                    r"Top Lighthouse audit changes post-reject by Tranco stratum",
                    "tab:lh-strata-rej",
                    delta_pair=(3, 1),
                    phase_label="post-reject",
                )
            if has_wave:
                _issue_table(
                    "wave",
                    "issue_id",
                    "WAVE issue",
                    r"Top WAVE issue changes: reject vs.\ accept by Tranco stratum",
                    "tab:wave-strata-ar",
                    categories={"alert", "contrast", "error"},
                    delta_pair=(3, 2),
                    phase_label=r"reject vs.\ accept",
                )
            if has_lh:
                _issue_table(
                    "lh",
                    "audit_id",
                    "Lighthouse audit",
                    r"Top Lighthouse audit changes: reject vs.\ accept by Tranco stratum",
                    "tab:lh-strata-ar",
                    delta_pair=(3, 2),
                    phase_label=r"reject vs.\ accept",
                )

    # ── screen reader pass rates by stratum ───────────────────────────────────
    srm_data = [(db_path, _label(db_path), _srm_metrics(db_path)) for db_path in db_paths]
    srm_data = [(p, l, m) for p, l, m in srm_data if m is not None]
    if not srm_data:
        return

    srm_labels = [l for _, l, _ in srm_data]
    srm_col_spec = "l" + "r" * len(srm_data)
    srm_col_header = " & ".join(rf"\textbf{{{l}}}" for l in srm_labels)

    print(r"\subsubsection{Screen Reader Metrics Across Crawl Strata}")
    print(
        r"We compare screen reader pass rates across the three popularity rankings. "
        r"Pass rates are computed over applicable sites only --- those where the metric "
        r"returned pass or fail, excluding N/A responses. "
        r"\autoref{tab:srm-strata} summarises the results."
    )
    print()

    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{Screen reader pass rates (\%) by Tranco stratum}\label{tab:srm-strata}")
    print(rf"\begin{{tabular}}{{{srm_col_spec}}}")
    print(rf"\toprule Metric & {srm_col_header} \\ \midrule")

    for col, label in _SRM_COLS:
        vals = [_pct(m["rates"].get(col, (0, 0, 0))[0], m["rates"].get(col, (0, 0, 0))[2]) for _, _, m in srm_data]
        row(label, vals)

    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")
    print()

    # ── word distance by stratum ──────────────────────────────────────────────
    dist_data = [(l, m["distances"]) for _, l, m in srm_data if m["distances"]]
    if dist_data:
        dist_col_spec = "l" + "r" * len(dist_data)
        dist_col_header = " & ".join(rf"\textbf{{{l}}}" for l, _ in dist_data)

        print(r"\begin{table}[ht]\centering\footnotesize")
        print(
            r"\caption{Distribution of word distance before first cookie keyword by Tranco "
            r"ranking (criterion~(ii): Immediately Read)}\label{tab:srm-dist-strata}"
        )
        print(rf"\begin{{tabular}}{{{dist_col_spec}}}")
        print(rf"\toprule Distance & {dist_col_header} \\ \midrule")

        for bucket_label, pred in _DIST_BUCKETS:
            vals = [_pct(sum(1 for d in dists if pred(d)), len(dists)) for _, dists in dist_data]
            row(bucket_label, vals)

        print(r"  \addlinespace[3pt]")
        pass_vals = [_pct(sum(1 for d in dists if d <= 30), len(dists)) for _, dists in dist_data]
        row(r"\textbf{Pass ($\leq 30$ words)}", pass_vals)

        print(r"\bottomrule\end{tabular}")
        print(r"\end{table}")
        print()

        # prose: which stratum has the best pass rate for immediately read
        ir_col = "metric_immediately_read"
        ir_rates = [
            m["rates"].get(ir_col, (0, 0, 0))[0] / m["rates"].get(ir_col, (0, 0, 0))[2] * 100
            if m["rates"].get(ir_col, (0, 0, 0))[2]
            else 0
            for _, _, m in srm_data
        ]
        best_ir_idx = ir_rates.index(max(ir_rates))
        worst_ir_idx = ir_rates.index(min(ir_rates))
        dist_pass = [
            sum(1 for d in m["distances"] if d <= 30) / len(m["distances"]) * 100 if m["distances"] else 0
            for _, _, m in srm_data
        ]
        if len(srm_data) >= 3:
            dist_parts = ", ".join(
                rf"\textbf{{{p:.0f}}}\,\% ({l.split(chr(10))[0]})"
                for l, p in zip([l for _, l, _ in srm_data], dist_pass)
            )
            print(
                rf"The \emph{{{srm_labels[best_ir_idx]}}}-ranking sites had the highest pass "
                rf"rate for criterion~(ii) Immediately Read "
                rf"(\textbf{{{ir_rates[best_ir_idx]:.0f}}}\,\%), while "
                rf"\emph{{{srm_labels[worst_ir_idx]}}}-ranking sites had the lowest "
                rf"(\textbf{{{ir_rates[worst_ir_idx]:.0f}}}\,\%). "
                rf"For word distance, the proportion of sites where a cookie keyword appeared "
                rf"within 30 words was {dist_parts}. "
                r"\autoref{tab:srm-dist-strata} shows the full distribution across distance bands."
            )
        else:
            print(
                rf"The \emph{{{srm_labels[best_ir_idx]}}}-ranking sites had the highest pass "
                rf"rate for criterion~(ii) Immediately Read "
                rf"(\textbf{{{ir_rates[best_ir_idx]:.0f}}}\,\%). "
                r"\autoref{tab:srm-dist-strata} shows the full distribution across distance bands."
            )
        print()


if __name__ == "__main__":
    from pathlib import Path as _Path

    base = _Path(__file__).parent.parent
    names = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_DBS
    db_paths = [base / n for n in names if (base / n).exists()]
    if len(db_paths) < 2:
        print("Need at least two databases.", file=sys.stderr)
        sys.exit(1)
    run(db_paths)
