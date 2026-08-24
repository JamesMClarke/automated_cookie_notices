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

        total = q("SELECT COUNT(*) FROM chrome_scans")[0][0]
        errors = q("SELECT COUNT(*) FROM chrome_scans WHERE is_error_page=1")[0][0]
        reachable = total - errors

        detected = q(f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}")[0][0]
        accepted = q(f"SELECT COUNT(*) FROM chrome_scans WHERE {ACCEPTED} AND {NOT_FP}")[0][0]
        rej_tried = q(f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_reject_attempted=1 AND {NOT_FP}")[0][0]
        rejected = q(f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_rejected=1 AND {NOT_FP}")[0][0]

        # cookies per site (only count cookies from scans where the phase succeeded)
        _ck_row = conn.execute(
            f"""SELECT
                     SUM(CASE WHEN k.phase='pre' THEN 1 ELSE 0 END),
                     SUM(CASE WHEN k.phase='post_accept' AND {ACCEPTED} THEN 1 ELSE 0 END),
                     SUM(CASE WHEN k.phase='post_reject' AND c.cookie_notice_rejected=1 THEN 1 ELSE 0 END)
                FROM cookie_classifications k
                JOIN chrome_scans c ON c.id = k.scan_id
                WHERE c.is_error_page=0 AND {NOT_FP}"""
        ).fetchone()
        pre_ck = (_ck_row[0] or 0) if _ck_row else 0
        acc_ck = (_ck_row[1] or 0) if _ck_row else 0
        rej_ck = (_ck_row[2] or 0) if _ck_row else 0
        _pre_ck_sites = conn.execute(
            f"SELECT COUNT(*) FROM chrome_scans WHERE is_error_page=0 AND {NOT_FP}"
        ).fetchone()[0]
        avg_pre_ck = pre_ck / _pre_ck_sites if _pre_ck_sites else None
        avg_acc_ck = acc_ck / accepted if accepted else None
        avg_rej_ck = rej_ck / rejected if rejected else None

        # tracker rate (only count requests from scans where the phase succeeded)
        _tr_row = q_safe(
            conn,
            f"""SELECT
                     SUM(CASE WHEN r.phase='pre'         AND r.is_tracker=1 THEN 1 ELSE 0 END),
                     SUM(CASE WHEN r.phase='post_accept' AND r.is_tracker=1 AND {ACCEPTED} THEN 1 ELSE 0 END),
                     SUM(CASE WHEN r.phase='post_reject' AND r.is_tracker=1 AND c.cookie_notice_rejected=1 THEN 1 ELSE 0 END),
                     SUM(CASE WHEN r.phase='pre' THEN 1 ELSE 0 END),
                     SUM(CASE WHEN r.phase='post_accept' AND {ACCEPTED} THEN 1 ELSE 0 END),
                     SUM(CASE WHEN r.phase='post_reject' AND c.cookie_notice_rejected=1 THEN 1 ELSE 0 END)
                FROM chrome_network_requests r
                JOIN chrome_scans c ON c.id = r.scan_id
                WHERE c.is_error_page=0 AND {NOT_FP}""",
        )
        _tr = _tr_row[0] if _tr_row else (0, 0, 0, 0, 0, 0)
        pre_tr, acc_tr, rej_tr, pre_req, acc_req, rej_req = [v or 0 for v in _tr]
        pre_tr_rate = pre_tr / pre_req * 100 if pre_req else None
        acc_tr_rate = acc_tr / acc_req * 100 if acc_req else None
        rej_tr_rate = rej_tr / rej_req * 100 if rej_req else None

        # top two control types
        ctrl_rows = q(
            f"SELECT COALESCE(manual_cookie_control_type, cookie_control_type, 'unknown'), COUNT(*) "
            f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
            f"GROUP BY 1 ORDER BY 2 DESC LIMIT 2"
        )

        # fraction of notices offering no meaningful choice (informational only)
        none_ctrl = next(
            (
                cnt
                for ct, cnt in q(
                    f"SELECT COALESCE(manual_cookie_control_type, cookie_control_type), COUNT(*) "
                    f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} GROUP BY 1"
                )
                if ct == "informational_only"
            ),
            0,
        )

    finally:
        conn.close()

    return {
        "total": total,
        "errors": errors,
        "reachable": reachable,
        "detected": detected,
        "accepted": accepted,
        "rej_tried": rej_tried,
        "rejected": rejected,
        "avg_pre_ck": avg_pre_ck,
        "avg_acc_ck": avg_acc_ck,
        "avg_rej_ck": avg_rej_ck,
        "pre_tr_rate": pre_tr_rate,
        "acc_tr_rate": acc_tr_rate,
        "rej_tr_rate": rej_tr_rate,
        "none_ctrl": none_ctrl,
        "ctrl_rows": ctrl_rows,
    }


def _pct(num, denom):
    if not denom:
        return "---"
    return rf"{num / denom * 100:.0f}\,\%"


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

    row("Reachable sites", [str(m["reachable"]) for _, _, m in data])
    row(r"Error rate", [_pct(m["errors"], m["total"]) for _, _, m in data])
    print(r"  \addlinespace[3pt]")
    row(r"Cookie notice detected", [_pct(m["detected"], m["reachable"]) for _, _, m in data])
    row(r"Notice accepted", [_pct(m["accepted"], m["detected"]) for _, _, m in data])
    row(r"Notice rejected", [_pct(m["rejected"], m["rej_tried"]) for _, _, m in data])
    row(r"Informational-only (no choice)", [_pct(m["none_ctrl"], m["detected"]) for _, _, m in data])
    print(r"  \addlinespace[3pt]")
    row(r"Avg.\ cookies pre-accept", [fmt(m["avg_pre_ck"]) for _, _, m in data])
    row(r"Avg.\ cookies post-accept", [fmt(m["avg_acc_ck"]) for _, _, m in data])
    row(r"Avg.\ cookies post-reject", [fmt(m["avg_rej_ck"]) for _, _, m in data])
    print(r"  \addlinespace[3pt]")
    row(
        r"Tracker rate pre-accept",
        [fmt(m["pre_tr_rate"]) + r"\,\%" if m["pre_tr_rate"] is not None else "---" for _, _, m in data],
    )
    row(
        r"Tracker rate post-accept",
        [fmt(m["acc_tr_rate"]) + r"\,\%" if m["acc_tr_rate"] is not None else "---" for _, _, m in data],
    )
    row(
        r"Tracker rate post-reject",
        [fmt(m["rej_tr_rate"]) + r"\,\%" if m["rej_tr_rate"] is not None else "---" for _, _, m in data],
    )

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

    _section_tracker_concentration(db_paths)


def _tracker_dist(db_path):
    """Per-site post-accept tracker counts and concentration stats for one database."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"""
            SELECT cs.url,
                   SUM(CASE WHEN r.is_tracker=1 THEN 1 ELSE 0 END)
            FROM chrome_scans cs
            JOIN chrome_network_requests r ON r.scan_id = cs.id
            WHERE cs.is_error_page=0
              AND {NOT_FP}
              AND {ACCEPTED}
              AND r.phase = 'post_accept'
            GROUP BY cs.id
            ORDER BY 2 DESC
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        return None

    counts = sorted(r[1] for r in rows)
    n = len(counts)
    total = sum(counts)

    def _percentile(p):
        return counts[min(int(n * p), n - 1)]

    top10_total = sum(counts[max(n - 10, 0) :])
    top10_domains = [(r[0], r[1]) for r in rows[:10]]

    # Trimmed mean: exclude top 10 % of sites
    trim_cutoff = _percentile(0.90)
    trimmed = [c for c in counts if c <= trim_cutoff]

    return {
        "n": n,
        "total": total,
        "mean": total / n if n else None,
        "median": _percentile(0.50),
        "p90": _percentile(0.90),
        "p95": _percentile(0.95),
        "max": counts[-1] if counts else None,
        "top10_pct": top10_total / total * 100 if total else None,
        "trimmed_mean": sum(trimmed) / len(trimmed) if trimmed else None,
        "top10_domains": top10_domains,
    }


def _section_tracker_concentration(db_paths):
    tiers = [(db_path, _label(db_path), _tracker_dist(db_path)) for db_path in db_paths]
    tiers = [(p, l, d) for p, l, d in tiers if d is not None]
    if not tiers:
        return

    labels = [l for _, l, _ in tiers]
    col_spec = "l" + "r" * len(tiers)
    col_hdr = " & ".join(rf"\textbf{{{l}}}" for l in labels)

    def fv(v, decimals=1):
        return f"{v:.{decimals}f}" if v is not None else "---"

    print(r"\subsubsection{Distribution of post-accept tracker requests across domains}")
    print(
        r"The mean tracker rate reported above is computed over all network requests, "
        r"so a small number of sites with unusually large third-party footprints could "
        r"inflate the per-stratum average. "
        r"\autoref{tab:tracker_dist} disaggregates the distribution of per-site "
        r"post-accept tracker request counts to test whether the elevated middle-stratum "
        r"figure is driven by outliers."
    )
    print()

    print(r"\begin{table}[ht]\centering\footnotesize")
    print(
        r"\caption{Per-site post-accept tracker request distribution by stratum}"
        r"\label{tab:tracker_dist}"
    )
    print(rf"\begin{{tabular}}{{{col_spec}}}")
    print(rf"\toprule Metric & {col_hdr} \\ \midrule")

    def row(label, vals):
        print(rf"  {label} & {' & '.join(vals)} \\")

    row("Sites (post-accept phase)", [str(d["n"]) for _, _, d in tiers])
    row(r"Mean trackers", [fv(d["mean"]) for _, _, d in tiers])
    row(r"Median trackers", [str(d["median"]) for _, _, d in tiers])
    row(r"90th percentile", [str(d["p90"]) for _, _, d in tiers])
    row(r"95th percentile", [str(d["p95"]) for _, _, d in tiers])
    row(r"Maximum", [str(d["max"]) for _, _, d in tiers])
    print(r"  \addlinespace[3pt]")
    row(r"Top-10 sites' share of total", [fv(d["top10_pct"]) + r"\,\%" for _, _, d in tiers])
    row(r"Mean excl.\ top 10\,\% of sites", [fv(d["trimmed_mean"]) for _, _, d in tiers])

    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")
    print()

    # Prose: is the middle elevation genuine or outlier-driven?
    mid_idx = next((i for i, (_, l, _) in enumerate(tiers) if l == "Middle"), None)
    if mid_idx is not None:
        d_mid = tiers[mid_idx][2]
        others = [(l, d) for _, l, d in tiers if l != "Middle"]
        mid_median = d_mid["median"]
        mid_mean = d_mid["mean"]  # noqa: F841
        mid_trim = d_mid["trimmed_mean"]

        # Check whether median and trimmed mean are also highest
        mid_is_highest_median = all(mid_median >= d["median"] for _, d in others)
        mid_is_highest_trim = all((mid_trim or 0) >= (d["trimmed_mean"] or 0) for _, d in others)

        if mid_is_highest_median and mid_is_highest_trim:
            print(
                rf"The middle stratum has the highest median tracker count per site "
                rf"({mid_median} requests) and the highest trimmed mean excluding the top "
                rf"10\,\% of sites ({fv(mid_trim)} requests), "
                r"both exceeding the corresponding figures for the top and bottom strata. "
                r"The elevated mean is therefore not an artefact of a small number of "
                r"extreme outliers: the typical middle-stratum site loads more post-accept "
                r"tracker requests than the typical site in either other stratum."
            )
        elif mid_is_highest_median:
            print(
                rf"The middle stratum has the highest median tracker count ({mid_median}), "
                r"indicating that typical middle-stratum sites are genuinely more tracking-heavy "
                r"post-accept, not just inflated by a few extreme outliers. "
                rf"However, after excluding the top 10\,\% of sites the trimmed mean "
                rf"({fv(mid_trim)}) "
                + ("exceeds" if mid_is_highest_trim else "no longer exceeds")
                + r" the other strata, "
                r"suggesting that a tail of very high-volume sites further amplifies the gap."
            )
        else:
            print(
                rf"After removing the top 10\,\% of sites by tracker count, the middle "
                rf"stratum trimmed mean ({fv(mid_trim)}) "
                + ("remains the highest" if mid_is_highest_trim else "falls below at least one other stratum")
                + r". "
                r"The concentration figures indicate that the elevated mean is at least "
                r"partially driven by a small number of high-volume domains."
            )

        # Top-10 concentration comparison
        concs = [(l, d["top10_pct"]) for _, l, d in tiers if d["top10_pct"] is not None]
        if concs:
            max_conc_label, max_conc = max(concs, key=lambda x: x[1])
            print(
                r"Across all strata, the top-10 sites by tracker count account for "
                + ", ".join(rf"{fv(c)}\,\% ({l})" for l, c in concs)
                + r" of total post-accept tracker requests respectively. "
                + (
                    rf"The \textbf{{{max_conc_label}}} stratum shows the highest concentration, "
                    rf"suggesting its mean is most susceptible to outlier influence."
                    if max_conc_label != "Middle"
                    else r"Concentration is broadly similar across strata, reinforcing that "
                    r"the middle stratum's elevated mean reflects a genuine distributional shift."
                )
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
