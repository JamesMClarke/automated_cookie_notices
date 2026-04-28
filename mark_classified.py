"""
mark_classified.py
==================
Set a manual classification override on a chrome_scans row.

Usage:
    python mark_classified.py top-100.db <scan_id> <field> <value>

Fields and valid values:
    false_positive    : 1 (mark as false positive) | NULL (clear)
    position          : bottom_overlay | top_overlay | middle_overlay | corner_overlay |
                        left_overlay | right_overlay | overall | none | NULL
    control_type      : accept_only | accept_or_reject | accept_or_settings |
                        accept_reject_or_settings | informational_only |
                        accept_or_pay | reject_or_pay | close_only | none | NULL
    emphasized_option : accept | other | equal | none | NULL
    has_reject        : 0 | 1 | NULL
    has_settings      : 0 | 1 | NULL
    pre_selected      : 0 | 1 | NULL

    NULL clears the manual override (reverts to the auto-classification value).

Examples:
    python mark_classified.py top-100.db 12 position bottom_overlay
    python mark_classified.py top-100.db 12 control_type informational_only
    python mark_classified.py top-100.db 12 emphasized_option none
    python mark_classified.py top-100.db 12 has_reject 1
    python mark_classified.py top-100.db 12 false_positive 1
    python mark_classified.py top-100.db 12 false_positive NULL
    python mark_classified.py top-100.db 12 position NULL
"""

import sqlite3
import sys

FIELD_MAP = {
    "false_positive":    "false_positive",
    "position":          "manual_cookie_position",
    "control_type":      "manual_cookie_control_type",
    "emphasized_option": "manual_cookie_emphasized_option",
    "has_reject":        "manual_cookie_has_reject",
    "has_settings":      "manual_cookie_has_settings",
    "pre_selected":      "manual_cookie_pre_selected",
}

VALID_VALUES = {
    "false_positive": {"1"},
    "position": {
        "bottom_overlay", "top_overlay", "middle_overlay", "corner_overlay",
        "left_overlay", "right_overlay", "overall", "none",
    },
    "control_type": {
        "accept_only", "accept_or_reject", "accept_or_settings",
        "accept_reject_or_settings", "informational_only",
        "accept_or_pay", "reject_or_pay", "close_only", "none",
    },
    "emphasized_option": {"accept", "other", "equal", "none"},
    "has_reject":        {"0", "1"},
    "has_settings":      {"0", "1"},
    "pre_selected":      {"0", "1"},
}


def main():
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(1)

    db_path  = sys.argv[1]
    try:
        scan_id = int(sys.argv[2])
    except ValueError:
        print(f"[!] scan_id must be an integer, got: {sys.argv[2]!r}")
        sys.exit(1)

    field    = sys.argv[3].lower()
    raw_val  = sys.argv[4]

    if field not in FIELD_MAP:
        print(f"[!] Unknown field {field!r}. Valid fields: {', '.join(FIELD_MAP)}")
        sys.exit(1)

    if raw_val.upper() == "NULL":
        value = None
    elif raw_val in VALID_VALUES[field]:
        value = int(raw_val) if field in ("false_positive", "has_reject", "has_settings", "pre_selected") else raw_val
    else:
        valid = ", ".join(sorted(VALID_VALUES[field])) + ", NULL"
        print(f"[!] Invalid value {raw_val!r} for field {field!r}. Valid: {valid}")
        sys.exit(1)

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT url, cookie_notice_detected FROM chrome_scans WHERE id=?", (scan_id,)
    ).fetchone()

    if row is None:
        print(f"[!] No row with id={scan_id} in {db_path}")
        con.close()
        sys.exit(1)

    url, detected = row
    if not detected:
        print(f"[!] Scan {scan_id} ({url}) has no detected cookie notice — "
              "setting a manual classification may have no effect on analysis.")

    col = FIELD_MAP[field]
    con.execute(f"UPDATE chrome_scans SET {col}=? WHERE id=?", (value, scan_id))
    con.commit()
    con.close()

    val_label = "NULL (cleared)" if value is None else repr(value)
    print(f"[+] Set {col}={val_label} for scan {scan_id} ({url})")


if __name__ == "__main__":
    main()
