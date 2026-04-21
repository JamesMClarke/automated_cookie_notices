"""
Generates review.html — a visual report of every detected cookie notice
alongside its classification, for quick manual verification.

Usage:
    python review_classifications.py top-50.db
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("top-50.db")

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
rows = con.execute("""
    SELECT url,
           cookie_notice_detected,
           cookie_notice_accepted,
           cookie_position,
           cookie_control_type,
           cookie_emphasized_option,
           cookie_has_reject,
           cookie_has_settings,
           cookie_pre_selected,
           pre_screenshot_path
    FROM chrome_scans
    ORDER BY cookie_notice_detected DESC, url
""").fetchall()
con.close()

DB_DIR = DB_PATH.resolve().parent

def resolve_path(raw):
    """Return an absolute Path for a DB-stored path (relative or absolute, Windows or Unix)."""
    p = Path(raw.replace("\\", "/"))
    if not p.is_absolute():
        p = DB_DIR / p
    return p.resolve()

def badge(label, value, ok_values=()):
    colour = "#2a9d8f" if value in ok_values else "#e76f51" if value else "#aaa"
    return f'<span style="background:{colour};color:#fff;padding:2px 6px;border-radius:3px;font-size:12px;margin:2px">{label}: {value}</span>'

def yn(value):
    return "yes" if value else "no"

cards = []
for r in rows:
    img_tag = ""
    if r["pre_screenshot_path"]:
        p = resolve_path(r["pre_screenshot_path"])
        if p.exists():
            img_tag = f'<img src="{p.as_uri()}" style="width:100%;border:1px solid #ccc;border-radius:4px">'
        else:
            img_tag = f'<div style="color:#999;font-size:12px">screenshot not found:<br>{p}</div>'

    if not r["cookie_notice_detected"]:
        notice_html = '<span style="color:#aaa">No cookie notice detected</span>'
    else:
        notice_html = "".join([
            badge("position",  r["cookie_position"],  ("bottom_overlay", "top_overlay", "middle_overlay", "overall", "corner_overlay", "left_overlay", "right_overlay")),
            badge("control",   r["cookie_control_type"], ("accept_or_reject", "accept_reject_or_settings", "accept_only", "accept_or_settings")),
            badge("emphasis",  r["cookie_emphasized_option"]),
            badge("has_reject",   yn(r["cookie_has_reject"])),
            badge("has_settings", yn(r["cookie_has_settings"])),
            badge("pre_selected", yn(r["cookie_pre_selected"])),
        ])

    cards.append(f"""
    <div style="border:1px solid #ddd;border-radius:6px;padding:12px;background:#fafafa">
      <div style="font-weight:bold;margin-bottom:6px;font-size:14px">{r['url']}</div>
      {img_tag}
      <div style="margin-top:8px">{notice_html}</div>
    </div>""")

html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Cookie Notice Classification Review</title>
  <style>
    body {{ font-family: sans-serif; margin: 20px; background: #f5f5f5; }}
    h1   {{ font-size: 20px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }}
  </style>
</head>
<body>
  <h1>Cookie Notice Classification Review &mdash; {DB_PATH.name}</h1>
  <p>{sum(1 for r in rows if r['cookie_notice_detected'])} notices detected out of {len(rows)} sites.</p>
  <div class="grid">{''.join(cards)}</div>
</body>
</html>"""

out = Path("review.html")
out.write_text(html, encoding="utf-8")
print(f"Written {out.resolve()}")
print("Open it in a browser to review.")
