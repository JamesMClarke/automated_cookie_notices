import sqlite3
import sys
from pathlib import Path

from .utils import NOT_FP, ACCEPTED, fmt, latex_escape, q_safe, DEFAULT_DBS


_DB_LABELS = {
    "top-1000.sqlite":    "Top",
    "crawl_two.sqlite":   "Middle",
    "crawl_three.sqlite": "Bottom",
}


def _label(db_path):
    return _DB_LABELS.get(Path(db_path).name, latex_escape(Path(db_path).stem))


def _metrics(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        def q(sql):
            return conn.execute(sql).fetchall()

        total       = q("SELECT COUNT(*) FROM chrome_scans")[0][0]
        errors      = q("SELECT COUNT(*) FROM chrome_scans WHERE is_error_page=1")[0][0]
        reachable   = total - errors

        detected    = q(f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}")[0][0]
        accepted    = q(f"SELECT COUNT(*) FROM chrome_scans WHERE {ACCEPTED} AND {NOT_FP}")[0][0]
        rej_tried   = q(f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_reject_attempted=1 AND {NOT_FP}")[0][0]
        rejected    = q(f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_rejected=1 AND {NOT_FP}")[0][0]

        # cookies per site
        ck_totals = {
            r[0]: r[1]
            for r in q("SELECT phase, COUNT(*) FROM cookie_classifications GROUP BY phase")
        }
        pre_ck = ck_totals.get("pre",         0)
        acc_ck = ck_totals.get("post_accept", 0)
        rej_ck = ck_totals.get("post_reject", 0)
        _pre_ck_sites = conn.execute(
            f"SELECT COUNT(*) FROM chrome_scans WHERE is_error_page=0 AND {NOT_FP}"
        ).fetchone()[0]
        avg_pre_ck = pre_ck / _pre_ck_sites if _pre_ck_sites else None
        avg_acc_ck = acc_ck / accepted       if accepted      else None
        avg_rej_ck = rej_ck / rejected       if rejected      else None

        # tracker rate
        tr_rows = q_safe(
            conn,
            """SELECT phase,
                      SUM(CASE WHEN is_tracker=1 THEN 1 ELSE 0 END),
                      COUNT(*)
               FROM chrome_network_requests r
               JOIN chrome_scans c ON c.id = r.scan_id
               WHERE c.is_error_page=0
               GROUP BY phase""",
        )
        tr = {r[0]: (r[1] or 0, r[2] or 0) for r in tr_rows}
        pre_tr,  pre_req  = tr.get("pre",        (0, 0))
        acc_tr,  acc_req  = tr.get("post_accept", (0, 0))
        rej_tr,  rej_req  = tr.get("post_reject", (0, 0))
        pre_tr_rate  = pre_tr  / pre_req  * 100 if pre_req  else None
        acc_tr_rate  = acc_tr  / acc_req  * 100 if acc_req  else None
        rej_tr_rate  = rej_tr  / rej_req  * 100 if rej_req  else None

        # top two control types
        ctrl_rows = q(
            f"SELECT COALESCE(manual_cookie_control_type, cookie_control_type, 'unknown'), COUNT(*) "
            f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
            f"GROUP BY 1 ORDER BY 2 DESC LIMIT 2"
        )

        # fraction of notices offering no meaningful choice (informational only)
        none_ctrl = next(
            (cnt for ct, cnt in q(
                f"SELECT COALESCE(manual_cookie_control_type, cookie_control_type), COUNT(*) "
                f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} GROUP BY 1"
            ) if ct == "informational_only"),
            0,
        )

    finally:
        conn.close()

    return {
        "total": total, "errors": errors, "reachable": reachable,
        "detected": detected, "accepted": accepted,
        "rej_tried": rej_tried, "rejected": rejected,
        "avg_pre_ck": avg_pre_ck, "avg_acc_ck": avg_acc_ck, "avg_rej_ck": avg_rej_ck,
        "pre_tr_rate": pre_tr_rate, "acc_tr_rate": acc_tr_rate, "rej_tr_rate": rej_tr_rate,
        "none_ctrl": none_ctrl, "ctrl_rows": ctrl_rows,
    }


def _pct(num, denom):
    if not denom:
        return "---"
    return rf"{num/denom*100:.0f}\,\%"


def run(db_paths):
    if len(db_paths) < 2:
        print(r"% privacy_differences: need at least two databases", file=sys.stderr)
        return

    data = [(db_path, _label(db_path), _metrics(db_path)) for db_path in db_paths]

    labels = [label for _, label, _ in data]
    col_spec = "l" + "r" * len(data)
    col_header = " & ".join(rf"\textbf{{{l}}}" for l in labels)

    print(r"\subsection{Differences Across Crawl Strata}")
    print(
        r"The three crawls cover distinct strata of the Tranco ranking, referred to here as the "
        r"\textit{top} (ranks 1--1{,}000), \textit{middle} (a random sample from ranks "
        r"1{,}001--10{,}000), and \textit{bottom} (a random sample from beyond rank 10{,}000). "
        r"Comparing them reveals how cookie notice prevalence, compliance, and tracking behaviour "
        r"vary with site popularity."
    )
    print()

    # ── main comparison table ─────────────────────────────────────────────────
    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{Key metrics by Tranco rank stratum}\label{tab:strata}")
    print(rf"\begin{{tabular}}{{{col_spec}}}")
    print(rf"\toprule Metric & {col_header} \\ \midrule")

    def row(label, vals):
        print(rf"  {label} & {' & '.join(vals)} \\")

    row("Reachable sites",
        [str(m["reachable"]) for _, _, m in data])
    row(r"Error rate",
        [_pct(m["errors"], m["total"]) for _, _, m in data])
    print(r"  \addlinespace[3pt]")
    row(r"Cookie notice detected",
        [_pct(m["detected"], m["reachable"]) for _, _, m in data])
    row(r"Notice accepted",
        [_pct(m["accepted"], m["detected"]) for _, _, m in data])
    row(r"Notice rejected",
        [_pct(m["rejected"], m["rej_tried"]) for _, _, m in data])
    row(r"Informational-only (no choice)",
        [_pct(m["none_ctrl"], m["detected"]) for _, _, m in data])
    print(r"  \addlinespace[3pt]")
    row(r"Avg.\ cookies pre-accept",
        [fmt(m["avg_pre_ck"]) for _, _, m in data])
    row(r"Avg.\ cookies post-accept",
        [fmt(m["avg_acc_ck"]) for _, _, m in data])
    row(r"Avg.\ cookies post-reject",
        [fmt(m["avg_rej_ck"]) for _, _, m in data])
    print(r"  \addlinespace[3pt]")
    row(r"Tracker rate pre-accept",
        [fmt(m["pre_tr_rate"]) + r"\,\%" if m["pre_tr_rate"] is not None else "---" for _, _, m in data])
    row(r"Tracker rate post-accept",
        [fmt(m["acc_tr_rate"]) + r"\,\%" if m["acc_tr_rate"] is not None else "---" for _, _, m in data])
    row(r"Tracker rate post-reject",
        [fmt(m["rej_tr_rate"]) + r"\,\%" if m["rej_tr_rate"] is not None else "---" for _, _, m in data])

    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")
    print()

    # ── prose observations ────────────────────────────────────────────────────
    m0, m1, m2 = [m for _, _, m in data]
    detect_pcts = [m["detected"] / m["reachable"] * 100 if m["reachable"] else 0 for _, _, m in data]
    acc_tr_rates = [m["acc_tr_rate"] or 0 for _, _, m in data]
    peak_tr_label = labels[acc_tr_rates.index(max(acc_tr_rates))]
    none_pcts = [m["none_ctrl"] / m["detected"] * 100 if m["detected"] else 0 for _, _, m in data]

    print(
        rf"Cookie notice detection falls steadily with Tranco rank: "
        rf"{detect_pcts[0]:.0f}\,\% of reachable top-stratum sites showed a notice, "
        rf"dropping to {detect_pcts[1]:.0f}\,\% in the middle stratum and "
        rf"{detect_pcts[2]:.0f}\,\% in the bottom stratum. "
        r"This likely reflects the concentration of GDPR-facing European and multinational "
        r"services at the top of the ranking."
    )
    print()

    rej_pcts = [m["rejected"] / m["rej_tried"] * 100 if m["rej_tried"] else 0 for _, _, m in data]
    print(
        rf"Automated rejection success also declines with rank "
        rf"({rej_pcts[0]:.0f}\,\% \(\to\) {rej_pcts[1]:.0f}\,\% \(\to\) {rej_pcts[2]:.0f}\,\%), "
        r"suggesting that lower-ranked sites use less standardised notice designs that are "
        r"harder for automation to navigate. "
        rf"The proportion of purely informational notices (offering no choice at all) "
        rf"rises from {none_pcts[0]:.0f}\,\% in the top stratum to {none_pcts[2]:.0f}\,\% "
        rf"in the bottom stratum."
    )
    print()

    print(
        rf"Post-accept cookie load and tracker rate peak in the \textbf{{{peak_tr_label}}} stratum "
        rf"(avg.\ {fmt(m1['avg_acc_ck'])} cookies post-accept, "
        rf"{fmt(m1['acc_tr_rate'])}\,\% tracker rate), exceeding both the top stratum "
        rf"({fmt(m0['avg_acc_ck'])} cookies, {fmt(m0['acc_tr_rate'])}\,\%) "
        rf"and the bottom stratum "
        rf"({fmt(m2['avg_acc_ck'])} cookies, {fmt(m2['acc_tr_rate'])}\,\%). "
        r"Middle-stratum sites therefore appear to deploy the most aggressive post-consent tracking, "
        r"despite being less studied than the top stratum."
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
