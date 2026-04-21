#!/usr/bin/env python3
"""
Build the NVDA add-on package (.nvda-addon file)
"""

import zipfile
from pathlib import Path

ADDON_DIR = Path(__file__).parent / "nvda_addon"
OUTPUT_FILE = Path(__file__).parent / "crawler.nvda-addon"


def build_addon():
    """Create the .nvda-addon zip file with proper encoding."""
    print(f"Building NVDA add-on from: {ADDON_DIR}")

    # Remove old file if exists
    if OUTPUT_FILE.exists():
        OUTPUT_FILE.unlink()

    with zipfile.ZipFile(OUTPUT_FILE, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in ADDON_DIR.rglob("*"):
            if file_path.is_file():
                # Skip __pycache__, .pyc files, and macOS junk
                if "__pycache__" in str(file_path) or file_path.suffix == ".pyc":
                    continue
                if file_path.name in (".DS_Store", ".gitignore", "Thumbs.db"):
                    continue

                # Get relative path within addon
                arc_name = file_path.relative_to(ADDON_DIR)

                # Read content
                if file_path.suffix in (".ini", ".py"):
                    # Read as text and ensure proper line endings
                    content = file_path.read_text(encoding="utf-8")
                    # Convert to Windows line endings for Windows compatibility
                    content = content.replace("\r\n", "\n").replace("\n", "\r\n")

                    print(f"  Adding: {arc_name}")
                    zf.writestr(str(arc_name), content.encode("utf-8"))
                else:
                    print(f"  Adding: {arc_name}")
                    zf.write(file_path, arc_name)

    print(f"\nCreated: {OUTPUT_FILE}")
    print(f"Size: {OUTPUT_FILE.stat().st_size} bytes")

    # Verify contents
    print("\nVerifying contents:")
    with zipfile.ZipFile(OUTPUT_FILE, "r") as zf:
        for info in zf.infolist():
            print(f"  {info.filename} ({info.file_size} bytes)")


if __name__ == "__main__":
    build_addon()
