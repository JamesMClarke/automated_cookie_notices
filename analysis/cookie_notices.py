import sqlite3
import sys

from .utils import q, latex_escape, classify_label, NOT_FP, DB_PATH

_EMPH_OTHER_SQL = (
    "SELECT url FROM chrome_scans "
    "WHERE cookie_notice_detected=1 AND {not_fp} "
    "AND COALESCE(manual_cookie_control_type, cookie_control_type) != 'informational_only' "
    "AND COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option) = 'other' "
    "ORDER BY url"
)

_EMPH_NONE_SQL = (
    "SELECT url FROM chrome_scans "
    "WHERE cookie_notice_detected=1 AND {not_fp} "
    "AND COALESCE(manual_cookie_control_type, cookie_control_type) != 'informational_only' "
    "AND COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option) = 'none' "
    "ORDER BY url"
)


def _urls_for_db(db_path, sql):
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            return [r[0] for r in conn.execute(sql.format(not_fp=NOT_FP)).fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def run(conn, db_paths=None):
    cookie_detected = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP}")[0][0]

    print(r"\subsection{Cookie Notice Classification}")

    # Position
    print(r"\subsubsection{Position}")

    pos_rows = q(
        conn,
        f"SELECT COALESCE(manual_cookie_position, cookie_position,'unknown'), COUNT(*) "
        f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"GROUP BY COALESCE(manual_cookie_position, cookie_position) ORDER BY COUNT(*) DESC",
    )
    top_pos, top_pos_cnt = pos_rows[0]
    second_pos, second_pos_cnt = pos_rows[1] if len(pos_rows) > 1 else ("", 0)

    print(
        rf"Cookie notices appeared most commonly as a \textbf{{{latex_escape(classify_label(top_pos))}}} "
        rf"({top_pos_cnt} of {cookie_detected} sites, "
        rf"{top_pos_cnt/cookie_detected*100:.0f}\,\%), "
        rf"followed by \texttt{{{latex_escape(classify_label(second_pos))}}} ({second_pos_cnt} sites). "
    )
    print()

    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{Cookie notice position}\label{tab:position}")
    print(r"\begin{tabular}{lrr}")
    print(r"\toprule Position & $n$ & \% \\ \midrule")
    for pos, cnt in pos_rows:
        print(rf"  \texttt{{{latex_escape(classify_label(pos))}}} & {cnt} & {cnt/cookie_detected*100:.0f}\,\% \\")
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")

    # Control Type
    print(r"\subsubsection{Control Type (Response Options)}")

    ctrl_rows = q(
        conn,
        f"SELECT COALESCE(manual_cookie_control_type, cookie_control_type,'unknown'), COUNT(*) "
        f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"GROUP BY COALESCE(manual_cookie_control_type, cookie_control_type) ORDER BY COUNT(*) DESC",
    )

    print(
        rf"The most common control type was \textbf{{{latex_escape(classify_label(ctrl_rows[0][0]))}}} ({ctrl_rows[0][1] if ctrl_rows else 0} sites, "
        rf"{ctrl_rows[0][1]/cookie_detected*100:.0f}\,\%), meaning \textcolor{{red}}{{update}}. "
    )
    print()

    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{Cookie notice control type}\label{tab:control}")
    print(r"\begin{tabular}{lrr}")
    print(r"\toprule Control type & $n$ & \% \\ \midrule")
    for ctrl, cnt in ctrl_rows:
        print(rf"  \texttt{{{latex_escape(classify_label(ctrl))}}} & {cnt} & {cnt/cookie_detected*100:.0f}\,\% \\")
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")

    # Emphasised Option
    print(r"\subsubsection{Emphasised Option}")

    ctrl_info = "COALESCE(manual_cookie_control_type, cookie_control_type)"
    emph_rows = q(
        conn,
        f"SELECT COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option,'unknown'), COUNT(*) "
        f"FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND {ctrl_info} != 'informational_only' "
        f"GROUP BY COALESCE(manual_cookie_emphasized_option, cookie_emphasized_option) ORDER BY COUNT(*) DESC",
    )
    info_only_cnt = q(
        conn,
        f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} "
        f"AND {ctrl_info} = 'informational_only'",
    )[0][0]

    equal_cnt = next((cnt for e, cnt in emph_rows if e == "equal"), 0)
    eph_cnt   = next((cnt for e, cnt in emph_rows if e == "emphasized"), 0)

    print(
        rf"Among notices that offer a choice, {equal_cnt} presented accept and "
        r"reject options with \textbf{equal} visual weight --- a positive finding indicating "
        r"no deliberate dark pattern was detected in these cases."
        rf"In total, \textbf{{{eph_cnt}}} notices emphasised the accept option over reject, "
        rf"potentially nudging users towards consent. "
    )
    print()

    print(r"\begin{table}[ht]\centering\footnotesize")
    print(r"\caption{Emphasised option on cookie notices}\label{tab:emph}")
    print(r"\begin{tabular}{lrr}")
    print(r"\toprule Emphasised option & $n$ & \% \\ \midrule")
    for emph, cnt in emph_rows:
        print(rf"  \texttt{{{latex_escape(classify_label(emph))}}} & {cnt} & {cnt/cookie_detected*100:.0f}\,\% \\")
    print(r"  \addlinespace[2pt]\hdashline\addlinespace[2pt]")
    print(rf"  \textit{{N/A (informational only)}} & {info_only_cnt} & {info_only_cnt/cookie_detected*100:.0f}\,\% \\")
    print(r"\bottomrule\end{tabular}")
    print(r"\end{table}")

    # Diagnostic: list "other" and "none" emphasised-option sites to stderr
    for label, sql in [
        ("Emphasised option = other", _EMPH_OTHER_SQL),
        ("Emphasised option = needs manually classifying", _EMPH_NONE_SQL),
    ]:
        print(f"{label}:", file=sys.stderr)
        if db_paths:
            for db_path in db_paths:
                for url in _urls_for_db(db_path, sql):
                    print(f"  {url}  ({db_path.name})", file=sys.stderr)
        else:
            for (url,) in q(conn, sql.format(not_fp=NOT_FP)):
                print(f"  {url}", file=sys.stderr)

    # Additional Features
    print(r"\subsubsection{Additional Features}")

    has_reject   = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND COALESCE(manual_cookie_has_reject,   cookie_has_reject)=1")[0][0]
    has_settings = q(conn, f"SELECT COUNT(*) FROM chrome_scans WHERE cookie_notice_detected=1 AND {NOT_FP} AND COALESCE(manual_cookie_has_settings, cookie_has_settings)=1")[0][0]

    print(
        rf"A reject button or link was present on {has_reject} of {cookie_detected} notices "
        rf"({has_reject/cookie_detected*100:.0f}\,\%), and a settings or preferences link on "
        rf"{has_settings} ({has_settings/cookie_detected*100:.0f}\,\%). "
    )
    print()


if __name__ == "__main__":
    from .utils import open_merged
    db_names = sys.argv[1:] if len(sys.argv) > 1 else None
    conn, db_paths = open_merged(db_names)
    try:
        run(conn, db_paths=db_paths)
    finally:
        conn.close()
