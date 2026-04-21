"""
scan.py
=======
Combined runner: runs Chrome then Brave scans for each URL, writing results
to separate tables (brave_scans, chrome_scans) in a single SQLite database.

Usage:
    python scan.py sites.csv results.db
    python scan.py sites.csv results.db --timeout 30
    python scan.py sites.csv results.db --no-lighthouse
    python scan.py sites.csv results.db --no-wave
    python scan.py sites.csv results.db --brave-only
    python scan.py sites.csv results.db --chrome-only
"""

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from brave_scan import (
    brave_process_url,
    init_db as brave_init_db,
    launch_brave,
    load_urls,
)
from chrome_scan import (
    chrome_process_url,
    init_db as chrome_init_db,
)


async def main(
    csv_path: Path,
    db_path: Path,
    artifacts_root: Path,
    timeout: int,
    brave_dwell: int,
    run_wave_flag: bool,
    run_lighthouse_flag: bool,
    run_nvda_flag: bool,
    run_brave: bool,
    run_chrome: bool,
) -> None:
    urls = load_urls(csv_path)
    if not urls:
        print("[!] No URLs found in CSV — exiting.")
        sys.exit(1)

    artifacts_root.mkdir(parents=True, exist_ok=True)

    brave_artifacts = artifacts_root / "brave"
    chrome_artifacts = artifacts_root / "chrome"
    if run_brave:
        brave_artifacts.mkdir(parents=True, exist_ok=True)
    if run_chrome:
        chrome_artifacts.mkdir(parents=True, exist_ok=True)

    # Both scanners share the same DB file but use separate tables.
    # init_db is idempotent (CREATE TABLE IF NOT EXISTS).
    if run_brave:
        brave_con = brave_init_db(db_path)
    if run_chrome:
        chrome_con = chrome_init_db(db_path)

    chrome_shared = dict(
        timeout=timeout,
        run_wave_flag=run_wave_flag,
        run_lighthouse_flag=run_lighthouse_flag,
        run_nvda_flag=run_nvda_flag,
    )
    brave_shared = dict(
        timeout=timeout,
        dwell=brave_dwell,
        run_wave_flag=run_wave_flag,
        run_lighthouse_flag=run_lighthouse_flag,
        run_nvda_flag=run_nvda_flag,
    )
    print(f"[!] Starting scans at {asyncio.get_event_loop().time():.2f} seconds.")
    print(f"\n{'='*60}")
    print(f"  Combined Scanner  ({len(urls)} URLs)")
    print(f"{'='*60}")
    print(f"  Input:       {csv_path}")
    print(f"  Database:    {db_path}")
    print(f"  Artifacts:   {artifacts_root}")
    print(f"  Timeout:     {timeout}s  |  Brave dwell: {brave_dwell}s")
    print(f"  WAVE:        {'yes' if run_wave_flag else 'no'}")
    print(f"  Lighthouse:  {'yes' if run_lighthouse_flag else 'no'}")
    print(f"  NVDA:        {'yes' if run_nvda_flag else 'no'}")
    print(f"  Brave scan:  {'yes' if run_brave else 'no'}")
    print(f"  Chrome scan: {'yes' if run_chrome else 'no'}\n")

    async with async_playwright() as p:
        try:
            for i, url in enumerate(urls, 1):
                print(f"[{i}/{len(urls)}] {url}")

                if run_chrome:
                    await chrome_process_url(
                        chrome_con, p, url, chrome_artifacts, **chrome_shared
                    )

                if run_brave:
                    brave_context, _ = await launch_brave(p)
                    try:
                        await brave_process_url(
                            brave_con, brave_context, url, brave_artifacts, **brave_shared
                        )
                    finally:
                        await brave_context.close()
        finally:
            if run_brave:
                brave_con.close()
            if run_chrome:
                chrome_con.close()

    print(f"\n[+] Done. Results saved to {db_path}")
    print(f"[+] Artifacts saved to {artifacts_root}")
    print(f"[!] Scans finished at {asyncio.get_event_loop().time():.2f} seconds.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run Brave and Chrome accessibility scans into a single database."
    )
    parser.add_argument("csv", type=Path, help="CSV file of URLs")
    parser.add_argument("db",  type=Path, help="SQLite output file")
    parser.add_argument(
        "--artifacts", type=Path, default=None,
        help="Directory for artifacts (default: <db_dir>/artifacts/)",
    )
    parser.add_argument("--timeout", type=int, default=30,
                        help="Navigation timeout in seconds (default: 30)")
    parser.add_argument("--brave-dwell", type=int, default=60,
                        help="Seconds Brave dwells after page load (default: 60)")
    parser.add_argument("--no-wave",       action="store_true",
                        help="Skip WAVE accessibility injection")
    parser.add_argument("--no-lighthouse", action="store_true",
                        help="Skip Lighthouse accessibility audit")
    parser.add_argument("--no-nvda",       action="store_true",
                        help="Skip NVDA screen reader transcript")
    parser.add_argument("--brave-only",  action="store_true",
                        help="Run Brave scan only")
    parser.add_argument("--chrome-only", action="store_true",
                        help="Run Chrome scan only")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"[!] CSV file not found: {args.csv}")
        sys.exit(1)

    if args.brave_only and args.chrome_only:
        print("[!] --brave-only and --chrome-only are mutually exclusive")
        sys.exit(1)

    artifacts_root = args.artifacts or args.db.parent / "artifacts"

    # Clean up any existing DB file to avoid confusion (since we append to the same tables).
    if args.db.exists():
        print(f"[!] Removing existing database file: {args.db}")
        args.db.unlink()
    # Clean up any existing artifacts directory to avoid confusion.
    if artifacts_root.exists():
        print(f"[!] Removing existing artifacts directory: {artifacts_root}")
        for item in artifacts_root.iterdir():
            if item.is_file():
                item.unlink()
            else:
                shutil.rmtree(item)

    asyncio.run(main(
        csv_path=args.csv,
        db_path=args.db,
        artifacts_root=artifacts_root,
        timeout=args.timeout,
        brave_dwell=args.brave_dwell,
        run_wave_flag=not args.no_wave,
        run_lighthouse_flag=not args.no_lighthouse,
        run_nvda_flag=not args.no_nvda,
        run_brave=not args.chrome_only,
        run_chrome=not args.brave_only,
    ))
