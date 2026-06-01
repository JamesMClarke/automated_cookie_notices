#!/usr/bin/env python3
"""Run all analysis sections against a merged view of one or more SQLite databases.

Usage:
    python -m analysis                              # merge all default DBs, print to stdout
    python -m analysis top-1000.sqlite              # single DB
    python -m analysis crawl_two.sqlite crawl_three.sqlite
"""
import sys

from . import (
    accessibility,
    accessibility_issues,
    control_options,
    cookie_notices,
    cookies,
    errors,
    overview,
    post_reject,
    screen_reader,
    trackers,
)
from .utils import open_merged


def main():
    db_names = sys.argv[1:] if len(sys.argv) > 1 else None
    conn, db_paths = open_merged(db_names)
    try:
        overview.run(conn)
        errors.run(conn, db_paths=db_paths)
        cookie_notices.run(conn, db_paths=db_paths)
        trackers.run(conn)
        cookies.run(conn)
        accessibility.run(conn)
        control_options.run(conn, db_paths=db_paths)
        accessibility_issues.run(conn)
        post_reject.run(conn)
        screen_reader.run(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
