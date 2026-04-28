"""
mark_verified.py
================
Set the manually_verified flag on a chrome_scans row.

Usage:
    python mark_verified.py top-100.db <scan_id> <1|0|NULL>

    1    = manually confirmed the cookie notice was accepted
    0    = manually confirmed it was NOT accepted
    NULL = clear the flag (back to unreviewed)

Example — mark scan 94 as accepted:
    python mark_verified.py top-100.db 94 1
"""

import sqlite3
import sys


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)

    db_path  = sys.argv[1]
    try:
        scan_id = int(sys.argv[2])
    except ValueError:
        print(f"[!] scan_id must be an integer, got: {sys.argv[2]!r}")
        sys.exit(1)

    raw_val = sys.argv[3].upper()
    if raw_val == "NULL":
        value = None
    elif raw_val == "1":
        value = 1
    elif raw_val == "0":
        value = 0
    else:
        print(f"[!] value must be 1, 0, or NULL, got: {sys.argv[3]!r}")
        sys.exit(1)

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT url, cookie_notice_accepted, cookie_accept_attempted "
        "FROM chrome_scans WHERE id=?", (scan_id,)
    ).fetchone()

    if row is None:
        print(f"[!] No row with id={scan_id} in {db_path}")
        con.close()
        sys.exit(1)

    url, accepted, attempted = row
    if accepted:
        print(f"[!] Scan {scan_id} ({url}) was already auto-confirmed accepted — "
              "manually_verified is not needed but will be set anyway.")
    if not attempted:
        print(f"[!] Scan {scan_id} ({url}) had no click attempted — "
              "setting manually_verified will have no effect on analysis.")

    con.execute("UPDATE chrome_scans SET manually_verified=? WHERE id=?", (value, scan_id))
    con.commit()
    con.close()

    val_label = {None: "NULL (unreviewed)", 1: "1 (accepted)", 0: "0 (not accepted)"}[value]
    print(f"[+] Set manually_verified={val_label} for scan {scan_id} ({url})")


if __name__ == "__main__":
    main()
