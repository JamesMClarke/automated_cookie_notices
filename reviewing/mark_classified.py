"""
mark_classified.py
==================
Set one or more manual classification overrides on a chrome_scans row.

Usage (single field):
    python reviewing/mark_classified.py top-100.db <scan_id> <field> <value>

Usage (multiple fields at once):
    python reviewing/mark_classified.py top-100.db <scan_id> <field1> <value1> <field2> <value2> ...

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
    python mark_classified.py top-100.db 12 position bottom_overlay control_type accept_only emphasized_option accept has_reject 1 has_settings 0 pre_selected 0
    python mark_classified.py top-100.db 12 false_positive NULL
"""

import sqlite3
import sys

FIELD_MAP = {
    "false_positive": "false_positive",
    "position": "manual_cookie_position",
    "control_type": "manual_cookie_control_type",
    "emphasized_option": "manual_cookie_emphasized_option",
    "has_reject": "manual_cookie_has_reject",
    "has_settings": "manual_cookie_has_settings",
    "pre_selected": "manual_cookie_pre_selected",
}

VALID_VALUES = {
    "false_positive": {"1"},
    "position": {
        "bottom_overlay",
        "top_overlay",
        "middle_overlay",
        "corner_overlay",
        "left_overlay",
        "right_overlay",
        "overall",
        "none",
    },
    "control_type": {
        "accept_only",
        "accept_or_reject",
        "accept_or_settings",
        "accept_reject_or_settings",
        "informational_only",
        "accept_or_pay",
        "reject_or_pay",
        "close_only",
        "none",
    },
    "emphasized_option": {"accept", "other", "equal", "none"},
    "has_reject": {"0", "1"},
    "has_settings": {"0", "1"},
    "pre_selected": {"0", "1"},
}

INT_FIELDS = {"false_positive", "has_reject", "has_settings", "pre_selected"}


def parse_value(field, raw_val):
    if raw_val.upper() == "NULL":
        return None
    if raw_val in VALID_VALUES[field]:
        return int(raw_val) if field in INT_FIELDS else raw_val
    valid = ", ".join(sorted(VALID_VALUES[field])) + ", NULL"
    print(f"[!] Invalid value {raw_val!r} for field {field!r}. Valid: {valid}")
    sys.exit(1)


def main():
    if len(sys.argv) < 5 or (len(sys.argv) - 3) % 2 != 0:
        print(__doc__)
        sys.exit(1)

    db_path = sys.argv[1]
    try:
        scan_id = int(sys.argv[2])
    except ValueError:
        print(f"[!] scan_id must be an integer, got: {sys.argv[2]!r}")
        sys.exit(1)

    # Parse field-value pairs from remaining args
    pairs = []
    args = sys.argv[3:]
    for i in range(0, len(args), 2):
        field = args[i].lower()
        raw_val = args[i + 1]
        if field not in FIELD_MAP:
            print(f"[!] Unknown field {field!r}. Valid fields: {', '.join(FIELD_MAP)}")
            sys.exit(1)
        pairs.append((field, parse_value(field, raw_val)))

    con = sqlite3.connect(db_path)
    row = con.execute("SELECT url, cookie_notice_detected FROM chrome_scans WHERE id=?", (scan_id,)).fetchone()

    if row is None:
        print(f"[!] No row with id={scan_id} in {db_path}")
        con.close()
        sys.exit(1)

    url, detected = row
    if not detected:
        print(
            f"[!] Scan {scan_id} ({url}) has no detected cookie notice — "
            "setting a manual classification may have no effect on analysis."
        )

    for field, value in pairs:
        col = FIELD_MAP[field]
        con.execute(f"UPDATE chrome_scans SET {col}=? WHERE id=?", (value, scan_id))
        val_label = "NULL (cleared)" if value is None else repr(value)
        print(f"[+] Set {col}={val_label} for scan {scan_id} ({url})")

    con.commit()
    con.close()


if __name__ == "__main__":
    main()
