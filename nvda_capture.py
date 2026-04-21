"""
nvda_capture.py
===============
Async helper for requesting a transcript from the NVDA addon (trancoCapture).

Protocol:
  - Write {"action": "navigate", "timestamp": <ISO>} to COMMAND_FILE
  - Poll RESULT_FILE until its "timestamp" field is newer than the command
  - Save the full result JSON to the caller-specified output path
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

COMM_DIR     = Path.home() / "Desktop" / "nvda_crawl"
COMMAND_FILE = COMM_DIR / "nvda_command.json"
RESULT_FILE  = COMM_DIR / "nvda_result.json"

POLL_INTERVAL = 0.5   # seconds between result-file polls
NVDA_STARTUP_WAIT = 12  # seconds to wait after launching NVDA


async def restart_nvda() -> None:
    """Kill any running NVDA process and start a fresh instance (Windows only).

    Waits long enough for NVDA to load and for the trancoCapture addon's
    file-watcher to auto-start before returning.
    """
    if sys.platform != "win32":
        return

    # Kill existing instance (ignore errors if not running)
    try:
        proc = await asyncio.create_subprocess_exec(
            "taskkill", "/F", "/IM", "nvda.exe",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception:
        pass

    await asyncio.sleep(2)

    # Start a fresh NVDA instance via os.startfile, which routes through the
    # Windows shell (ShellExecute) and handles UAC elevation properly.
    import os
    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: os.startfile(r"C:\Program Files\NVDA\nvda.exe"),
        )
    except Exception as e:
        print(f"       [!] NVDA: failed to start: {e}")
        return

    # Wait for NVDA to load and the addon file-watcher to initialise
    await asyncio.sleep(NVDA_STARTUP_WAIT)


async def capture_nvda_transcript(
    output_path: Path, url: str = "", timeout: int = 120
) -> str | None:
    """
    Ask the NVDA addon to navigate the current page and capture a transcript.

    Saves the full result JSON (segments + navigation) to output_path.
    Returns the full_text string on success, or None on timeout/error.

    Requires the trancoCapture addon to be running in NVDA on the same machine.
    """
    COMM_DIR.mkdir(parents=True, exist_ok=True)

    cmd_time = datetime.now(timezone.utc).isoformat()
    try:
        COMMAND_FILE.write_text(
            json.dumps({"action": "navigate", "timestamp": cmd_time, "url": url}),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"       [!] NVDA: failed to write command: {e}")
        return None

    # Poll until the result file has a timestamp newer than our command
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL)
        if not RESULT_FILE.exists():
            continue
        try:
            result = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
            if result.get("timestamp", "") > cmd_time:
                output_path.write_text(
                    json.dumps(result, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                return result.get("navigation", {}).get("full_text") or None
        except Exception:
            continue

    print(f"       [!] NVDA: timed out after {timeout}s")
    return None
