#!/usr/bin/env python3
"""Print which NVDA transcripts are blank (empty or image-placeholder-only)."""

import argparse
import json
import os
import sqlite3


def resolve_artifact_path(stored: str, artifact_path: str) -> str:
    """Mirror Go's resolveArtifactPath: re-root the path under artifact_path."""
    norm = stored.replace("\\", "/")
    parts = norm.split("/")
    pivots = {"artifacts", os.path.basename(artifact_path.rstrip("/\\")).lower()}
    for i, part in enumerate(parts):
        if part.lower() in pivots:
            rel_parts = [p for p in parts[i + 1 :] if p]
            if rel_parts:
                return os.path.join(artifact_path, *rel_parts)
    if os.path.isabs(stored):
        return stored
    return os.path.join(os.path.dirname(artifact_path), stored)


def is_nvda_blank(full_text: str) -> bool:
    """Mirror Go's isNVDABlank: blank if only image placeholders and whitespace."""
    stripped = full_text.replace("￼", "").strip()
    return stripped == ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", help="Path to the SQLite database")
    parser.add_argument("artifact_path", help="Path to the artifacts directory")
    args = parser.parse_args()

    con = sqlite3.connect(args.db_path)
    rows = con.execute("SELECT id, url, pre_nvda_path FROM chrome_scans ORDER BY id").fetchall()
    con.close()

    blank, missing, total_with_path = [], [], 0

    for scan_id, url, nvda_path in rows:
        if not nvda_path:
            continue
        total_with_path += 1
        resolved = resolve_artifact_path(nvda_path, args.artifact_path)
        if not os.path.exists(resolved):
            missing.append((scan_id, url, resolved))
            continue
        try:
            data = json.loads(open(resolved).read())
            full_text = data.get("navigation", {}).get("full_text", "")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [warn] scan {scan_id}: could not parse {resolved}: {e}")
            continue
        if is_nvda_blank(full_text):
            blank.append((scan_id, url, resolved))

    print(f"Scans with an NVDA path: {total_with_path}")
    print(f"Blank transcripts:       {len(blank)}")
    print(f"Missing files:           {len(missing)}")

    if blank:
        print("\n--- Blank transcripts ---")
        for scan_id, url, path in blank:
            print(f"  scan {scan_id:4d}  {url}  ({path})")

    if missing:
        print("\n--- Missing files ---")
        for scan_id, url, path in missing:
            print(f"  scan {scan_id:4d}  {url}  ({path})")


if __name__ == "__main__":
    main()
