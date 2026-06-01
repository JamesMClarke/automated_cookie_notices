import sqlite3
import sys

from .utils import q, latex_escape, NOT_FP, DB_PATH

_CTRL_TYPE_SQL = (
    "SELECT DISTINCT url FROM chrome_scans "
    "WHERE COALESCE(manual_cookie_control_type, cookie_control_type)='{ctrl_type}' "
    "AND cookie_notice_detected=1 AND {not_fp}"
)


def _sites_for_db(db_path, ctrl_type):
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            sql = _CTRL_TYPE_SQL.format(ctrl_type=ctrl_type, not_fp=NOT_FP)
            return [r[0] for r in conn.execute(sql).fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def run(conn, db_paths=None):
    ct_rows = q(
        conn,
        f"""SELECT COALESCE(manual_cookie_control_type, cookie_control_type),
                  COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option),
                  COUNT(*)
           FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}
           GROUP BY COALESCE(manual_cookie_control_type, cookie_control_type),
                    COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option)""",
    )

    no_notice_chrome = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE (cookie_notice_detected=0 OR NOT {NOT_FP}) AND is_error_page=0",
    )[0][0]

    total = sum(cnt for _, _, cnt in ct_rows) + no_notice_chrome

    print(r"\subsection{Cookie Notice Control Options and GDPR}")
    print(
        r"Table~\ref{tab:options} classifies all reachable sites by the control options "
        r"offered to visitors, using the taxonomy from the paper. "
        r"The \textbf{GDPR violation} column indicates whether the notice design "
        r"satisfies the GDPR requirement for freely given, unambiguous consent: "
        r"notices that offer no explicit reject path are considered non-compliant."
    )
    print()

    print(r"\begin{table*}[t]\centering\footnotesize")
    print(r"\caption{Cookie notice control options and GDPR compliance.}\label{tab:options}")
    print(r"\begin{tabular}{llrrr} \toprule")
    print(r"  \textbf{Control options} & \textbf{Emphasised option} & \textbf{Sites} & \textbf{\%} & \textbf{GDPR violation} \\ \midrule")
    sorted_ct_rows = sorted(
        ct_rows,
        key=lambda row: (-row[2], str(row[0] or ""), str(row[1] or "")),
    )
    for ctrl, emph, cnt in sorted_ct_rows:
        ctrl_label = latex_escape(ctrl if ctrl is not None else "unknown")
        emph_label = latex_escape(emph if emph is not None else "unknown")
        gdpr_violation = "No" if ctrl in ("accept_or_reject", "accept_reject_or_settings") else "Yes"
        print(rf"  {ctrl_label} & {emph_label} & {cnt} & {cnt/total*100:.0f}\,\% & {gdpr_violation} \\")
    print(rf"  \multicolumn{{2}}{{l}}{{(v) No Notice}} & {no_notice_chrome} & {no_notice_chrome/total*100:.0f}\,\% & Yes \\")
    print(r"  \bottomrule\end{tabular}")
    print(r"\end{table*}")

    # for label, ctrl_type in [
    #     ("Accept only notices", "accept_only"),
    #     ("Informational only notices", "informational_only"),
    # ]:
    #     print(f"{label}:", file=sys.stderr)
    #     if db_paths:
    #         for db_path in db_paths:
    #             for url in _sites_for_db(db_path, ctrl_type):
    #                 print(f"  {url}  ({db_path.name})", file=sys.stderr)
    #     else:
    #         sql = _CTRL_TYPE_SQL.format(ctrl_type=ctrl_type, not_fp=NOT_FP)
    #         for (url,) in q(conn, sql):
    #             print(f"  {url}", file=sys.stderr)


if __name__ == "__main__":
    import sys
    from .utils import open_merged
    db_names = sys.argv[1:] if len(sys.argv) > 1 else None
    conn, db_paths = open_merged(db_names)
    try:
        run(conn, db_paths=db_paths)
    finally:
        conn.close()
