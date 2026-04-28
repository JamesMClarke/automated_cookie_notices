"""
Generates review.html — a visual report of every detected cookie notice
alongside its classification, for quick manual verification.

Usage:
    python review_classifications.py top-50.db
    python review_classifications.py top-50.db --artifacts /nas.local/backups/cookie_notices_automation/chrome

The --artifacts flag rebases all stored file paths onto a different root,
which is useful when the DB was created on another machine (e.g. Windows)
and the artifact files are now accessible at a different location.
"""

import argparse
import struct
import sqlite3
import sys
from pathlib import Path

# Viewport the scanner uses — bbox x/y coordinates are relative to this size.
# (Used as fallback when the screenshot cannot be read.)
VP_W = 1920
VP_H = 969


def lh_screenshot_path(lh_json_path: str | None) -> Path | None:
    """Return a Lighthouse screenshot path if one exists next to the report JSON."""
    if not lh_json_path:
        return None
    p = resolve_path(lh_json_path)

    exts = ("png", "webp", "jpeg", "jpg")

    # Newer naming used by chrome_scan.py (e.g. pre_lighthouse.json -> lighthouse_pre.png)
    if p.stem.endswith("_lighthouse"):
        phase = p.stem[:-len("_lighthouse")]
        for ext in exts:
            candidate = p.parent / f"lighthouse_{phase}.{ext}"
            if candidate.exists():
                return candidate

    # Single-phase naming used by brave_scan.py.
    for ext in exts:
        candidate = p.parent / f"lighthouse.{ext}"
        if candidate.exists():
            return candidate

    # Legacy naming fallback (e.g. lighthouse_pre_lighthouse_screenshot.webp)
    for ext in exts:
        candidate = p.parent / f"lighthouse_{p.stem}_screenshot.{ext}"
        if candidate.exists():
            return candidate

    return None


def png_dimensions(path: Path) -> tuple[int, int] | None:
    """Return (width, height) of a PNG file without any external library."""
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            f.read(4)   # IHDR length
            if f.read(4) != b"IHDR":
                return None
            w = struct.unpack(">I", f.read(4))[0]
            h = struct.unpack(">I", f.read(4))[0]
            return w, h
    except Exception:
        return None

_parser = argparse.ArgumentParser(description="Generate cookie notice classification review")
_parser.add_argument("db", nargs="?", default="top-50.db", help="Path to SQLite database")
_parser.add_argument(
    "--artifacts", type=Path, default=None,
    help="Override base directory for artifact files (useful when DB was "
         "created on another machine and files are now at a different path)",
)
_args = _parser.parse_args()

DB_PATH = Path(_args.db)
ARTIFACTS_ROOT: Path | None = _args.artifacts

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
rows = con.execute("""
    SELECT id,
           url,
           http_status,
           cookie_notice_detected,
           cookie_notice_accepted,
           cookie_accept_attempted,
           manually_verified,
           cookie_position,
           cookie_control_type,
           cookie_emphasized_option,
           cookie_has_reject,
           cookie_has_settings,
           cookie_pre_selected,
           cookie_bbox_x,
           cookie_bbox_y,
           cookie_bbox_width,
           cookie_bbox_height,
           pre_screenshot_path,
           post_screenshot_path,
           pre_lh_path,
           post_lh_path,
           pre_lh_score,
           post_lh_score,
           pre_wave_error,
           pre_wave_contrast,
           pre_wave_alert,
           pre_wave_feature,
           pre_wave_structure,
           pre_wave_aria,
           post_wave_error,
           post_wave_contrast,
           post_wave_alert,
           post_wave_feature,
           post_wave_structure,
           post_wave_aria,
           manual_cookie_position,
           manual_cookie_control_type,
           manual_cookie_emphasized_option,
           manual_cookie_has_reject,
           manual_cookie_has_settings,
           manual_cookie_pre_selected,
           false_positive
    FROM chrome_scans
    ORDER BY cookie_notice_detected DESC, url
""").fetchall()
con.close()

DB_DIR = DB_PATH.resolve().parent

def resolve_path(raw):
    """Return an absolute Path for a DB-stored path (relative or absolute).

    When ARTIFACTS_ROOT is set the stored path is rebased onto it.  The
    function searches for an 'artifacts' component in the stored path and
    takes everything after it as the tail, so the scanner subfolder (e.g.
    'chrome') and site directory are preserved:

        stored:  artifacts\\chrome\\00002_bit.ly\\pre_screenshot.png
        root:    /Volumes/Backups/cookie_notices_automation/artifacts
        result:  /Volumes/Backups/.../artifacts/chrome/00002_bit.ly/pre_screenshot.png

    Works for both relative paths and absolute Windows paths (C:\\...).
    """
    p = Path(raw.replace("\\", "/"))
    if ARTIFACTS_ROOT is not None:
        parts = p.parts
        # Find the 'artifacts' directory component and keep everything after it.
        try:
            idx = next(i for i, part in enumerate(parts) if part.lower() == "artifacts")
            tail = "/".join(parts[idx + 1:])
        except StopIteration:
            # Fallback: no 'artifacts' component found — use <site_dir>/<file>.
            tail = f"{p.parent.name}/{p.name}"
        return (ARTIFACTS_ROOT / tail).resolve()
    if not p.is_absolute():
        p = DB_DIR / p
    return p.resolve()

def badge(label, value, ok_values=()):
    colour = "#2a9d8f" if value in ok_values else "#e76f51" if value else "#aaa"
    return (f'<span style="background:{colour};color:#fff;padding:2px 6px;'
            f'border-radius:3px;font-size:12px;margin:2px">{label}: {value}</span>')

def yn(value):
    return "yes" if value else "no"


def fmt_score(value):
    return "n/a" if value is None else f"{value:.1f}"


def _wv(val):
    """Format a WAVE stat: None and -1 both mean the run failed → 'n/a'."""
    return "n/a" if val is None or val == -1 else val

def wave_triplet(label, row, prefix):
    return (
        f'<div style="font-size:12px;color:#444;margin:2px 0">'
        f'{label}: '
        f'err={_wv(row[f"{prefix}_wave_error"])}, '
        f'contrast={_wv(row[f"{prefix}_wave_contrast"])}, '
        f'alert={_wv(row[f"{prefix}_wave_alert"])}, '
        f'feature={_wv(row[f"{prefix}_wave_feature"])}, '
        f'structure={_wv(row[f"{prefix}_wave_structure"])}, '
        f'aria={_wv(row[f"{prefix}_wave_aria"])}'
        f'</div>'
    )

cards = []
for r in rows:
    if r["http_status"] == 404:
        cards.append(f"""
    <div style="border:1px solid #ddd;border-radius:6px;padding:12px;background:#fafafa">
      <div style="font-weight:bold;margin-bottom:6px;font-size:14px">{r['url']}</div>
      <div style="margin-top:8px;color:#888">Page returned 404 (Not Found).</div>
    </div>""")
        continue

    def render_image_block(path: Path | None, overlay_html: str = "") -> str:
        if not path:
            return '<div style="color:#999;font-size:12px">not available</div>'
        if not path.exists():
            return f'<div style="color:#999;font-size:12px">screenshot not found:<br>{path}</div>'
        return (
            f'<div style="position:relative;line-height:0">'
            f'<img src="{path.as_uri()}" style="width:100%;border:1px solid #ccc;'
            f'border-radius:4px;display:block">'
            f'{overlay_html}'
            f'</div>'
        )

    normal_pre = resolve_path(r["pre_screenshot_path"]) if r["pre_screenshot_path"] else None
    normal_post = resolve_path(r["post_screenshot_path"]) if r["post_screenshot_path"] else None
    lh_pre = lh_screenshot_path(r["pre_lh_path"])
    lh_post = lh_screenshot_path(r["post_lh_path"])

    # Build bbox overlay for normal pre screenshot when coordinates are available.
    bbox_overlay = ""
    if normal_pre and normal_pre.exists():
        x, y, w, h = (r["cookie_bbox_x"], r["cookie_bbox_y"],
                      r["cookie_bbox_width"], r["cookie_bbox_height"])
        if None not in (x, y, w, h) and w > 0 and h > 0:
            dims = png_dimensions(normal_pre)
            img_w = dims[0] if dims else VP_W
            img_h = dims[1] if dims else VP_H
            # Clip to viewport: getBoundingClientRect can return coordinates
            # that extend beyond the visible area (e.g. a modal taller than
            # the viewport, or a full-page CMP overlay). Clamp before converting.
            x = max(0.0, min(x, VP_W))
            y = max(0.0, min(y, VP_H))
            w = max(0.0, min(w, VP_W - x))
            h = max(0.0, min(h, VP_H - y))
            if w > 0 and h > 0:
                left   = x / img_w * 100
                top    = y / img_h * 100
                width  = w / img_w * 100
                height = h / img_h * 100
                bbox_overlay = (
                    f'<div style="position:absolute;'
                    f'left:{left:.4f}%;top:{top:.4f}%;'
                    f'width:{width:.4f}%;height:{height:.4f}%;'
                    f'outline:3px solid #e63946;'
                    f'box-shadow:0 0 0 1px rgba(0,0,0,0.6);'
                    f'pointer-events:none;box-sizing:border-box;">'
                    f'</div>'
                )

    normal_pre_block = render_image_block(normal_pre, bbox_overlay)
    normal_post_block = render_image_block(normal_post)
    lh_pre_block = render_image_block(lh_pre)
    lh_post_block = render_image_block(lh_post)

    img_block = (
        '<div style="margin-bottom:8px">'
        '<div style="font-size:12px;color:#555;margin-bottom:4px;font-weight:600">Normal</div>'
        '<div style="display:grid;grid-template-columns:1fr;gap:8px">'
        '<div style="margin-bottom:4px">'
        '<div style="font-size:11px;color:#666;margin-bottom:3px">Pre</div>'
        f'{normal_pre_block}'
        '</div>'
        '<div>'
        '<div style="font-size:11px;color:#666;margin-bottom:3px">Post</div>'
        f'{normal_post_block}'
        '</div>'
        '</div>'
        '</div>'
        '<div class="lighthouse-section" style="margin-top:10px">'
        '<div style="font-size:12px;color:#555;margin-bottom:4px;font-weight:600">Lighthouse</div>'
        '<div style="display:grid;grid-template-columns:1fr;gap:8px">'
        '<div style="margin-bottom:4px">'
        '<div style="font-size:11px;color:#666;margin-bottom:3px">Pre</div>'
        f'{lh_pre_block}'
        '</div>'
        '<div>'
        '<div style="font-size:11px;color:#666;margin-bottom:3px">Post</div>'
        f'{lh_post_block}'
        '</div>'
        '</div>'
        '</div>'
    )

    audit_block = (
        '<div style="margin-top:10px;padding:8px;border:1px solid #e5e5e5;border-radius:4px;background:#fff">'
        '<div style="font-size:12px;color:#555;margin-bottom:4px;font-weight:600">Audit scores</div>'
        f'<div style="font-size:12px;color:#444;margin:2px 0">Lighthouse: pre={fmt_score(r["pre_lh_score"])}, post={fmt_score(r["post_lh_score"])}</div>'
        f'{wave_triplet("WAVE pre", r, "pre")}'
        f'{wave_triplet("WAVE post", r, "post")}'
        '</div>'
    )

    # Effective classification: manual override takes precedence over auto value
    def eff(auto_key, manual_key):
        """Return manual value if set, else auto value."""
        mv = r[manual_key]
        return mv if mv is not None else r[auto_key]

    eff_position   = eff("cookie_position",          "manual_cookie_position")
    eff_control    = eff("cookie_control_type",       "manual_cookie_control_type")
    eff_emphasis   = eff("cookie_emphasized_option",  "manual_cookie_emphasized_option")
    eff_has_reject = eff("cookie_has_reject",         "manual_cookie_has_reject")
    eff_has_sett   = eff("cookie_has_settings",       "manual_cookie_has_settings")
    eff_pre_sel    = eff("cookie_pre_selected",       "manual_cookie_pre_selected")

    # Show a (manual) indicator next to overridden fields
    def badge_eff(label, auto_key, manual_key, ok_values=()):
        val = eff(auto_key, manual_key)
        is_manual = r[manual_key] is not None
        suffix = " ✎" if is_manual else ""
        colour = "#2a9d8f" if val in ok_values else "#e76f51" if val else "#aaa"
        return (f'<span style="background:{colour};color:#fff;padding:2px 6px;'
                f'border-radius:3px;font-size:12px;margin:2px" '
                f'title="{"manually set" if is_manual else "auto-classified"}">'
                f'{label}: {val}{suffix}</span>')

    if not r["cookie_notice_detected"]:
        notice_html = '<span style="color:#aaa">No cookie notice detected</span>'
    else:
        notice_html = "".join([
            badge_eff("position",  "cookie_position", "manual_cookie_position",
                      ("bottom_overlay", "top_overlay", "middle_overlay",
                       "corner_overlay", "left_overlay", "right_overlay")),
            badge_eff("control",   "cookie_control_type", "manual_cookie_control_type",
                      ("accept_or_reject", "accept_reject_or_settings",
                       "accept_only", "accept_or_settings")),
            badge_eff("emphasis",     "cookie_emphasized_option", "manual_cookie_emphasized_option"),
            badge_eff("has_reject",   "cookie_has_reject",   "manual_cookie_has_reject"),
            badge_eff("has_settings", "cookie_has_settings", "manual_cookie_has_settings"),
            badge_eff("pre_selected", "cookie_pre_selected", "manual_cookie_pre_selected"),
        ])

    # Acceptance status badge + manual-verify button for attempted-but-unconfirmed
    scan_id     = r["id"]
    accepted    = r["cookie_notice_accepted"]
    attempted   = r["cookie_accept_attempted"]
    mv          = r["manually_verified"]   # None / 0 / 1
    fp          = r["false_positive"]      # None / 1

    if fp:
        accept_status = ('<span style="background:#888;color:#fff;padding:2px 8px;'
                        'border-radius:3px;font-size:12px">false positive — excluded from analysis</span>')
    elif accepted:
        accept_status = ('<span style="background:#2a9d8f;color:#fff;padding:2px 8px;'
                        'border-radius:3px;font-size:12px">accepted</span>')
    elif attempted and mv == 1:
        accept_status = ('<span style="background:#2a9d8f;color:#fff;padding:2px 8px;'
                        'border-radius:3px;font-size:12px">accepted (manually verified)</span>')
    elif attempted and mv == 0:
        accept_status = ('<span style="background:#e76f51;color:#fff;padding:2px 8px;'
                        'border-radius:3px;font-size:12px">not accepted (manually verified)</span>')
    elif attempted:
        accept_status = ('<span style="background:#e9c46a;color:#333;padding:2px 8px;'
                        'border-radius:3px;font-size:12px">attempted — unverified</span>')
    elif r["cookie_notice_detected"]:
        accept_status = ('<span style="background:#e76f51;color:#fff;padding:2px 8px;'
                        'border-radius:3px;font-size:12px">not accepted</span>')
    else:
        accept_status = ""

    verify_btn = ""
    if attempted and not accepted and not fp:
        verify_btn = (
            f'<div style="margin-top:6px" class="verify-controls" data-scan-id="{scan_id}">'
            f'<span style="font-size:12px;color:#555;margin-right:6px">Manual verify:</span>'
            f'<button onclick="setVerified({scan_id},1)" style="font-size:12px;padding:2px 8px;'
            f'margin-right:4px;cursor:pointer;background:#2a9d8f;color:#fff;border:none;border-radius:3px">'
            f'Mark accepted</button>'
            f'<button onclick="setVerified({scan_id},0)" style="font-size:12px;padding:2px 8px;'
            f'cursor:pointer;background:#e76f51;color:#fff;border:none;border-radius:3px">'
            f'Mark rejected</button>'
            f'<button onclick="setVerified({scan_id},null)" style="font-size:12px;padding:2px 8px;'
            f'margin-left:4px;cursor:pointer;background:#aaa;color:#fff;border:none;border-radius:3px">'
            f'Clear</button>'
            f'</div>'
        )

    # False-positive toggle button (shown for any detected notice)
    fp_btn = ""
    if r["cookie_notice_detected"]:
        if fp:
            fp_btn = (
                f'<div style="margin-top:6px">'
                f'<button onclick="setClassification({scan_id},\'false_positive\',\'NULL\')" '
                f'style="font-size:12px;padding:2px 8px;cursor:pointer;'
                f'background:#aaa;color:#fff;border:none;border-radius:3px">'
                f'Clear false positive</button>'
                f'</div>'
            )
        else:
            fp_btn = (
                f'<div style="margin-top:6px">'
                f'<button onclick="setClassification({scan_id},\'false_positive\',\'1\')" '
                f'style="font-size:12px;padding:2px 8px;cursor:pointer;'
                f'background:#888;color:#fff;border:none;border-radius:3px">'
                f'Mark false positive</button>'
                f'</div>'
            )

    # Show manual classification panel when notice detected but auto-classification
    # returned no useful result (position NULL or 'none') OR manual overrides exist.
    # Never show on false positives — there's nothing to classify.
    needs_classify = (
        r["cookie_notice_detected"] and not fp and
        (eff_position is None or eff_position == "none" or
         r["manual_cookie_position"] is not None)
    )
    classify_panel = ""
    if needs_classify:
        def _sel(fid, options, current):
            opts = '<option value="NULL">NULL (clear)</option>'
            for v in options:
                sel = ' selected' if v == current else ''
                opts += f'<option value="{v}"{sel}>{v}</option>'
            return (
                f'<select id="cls-{fid}-{scan_id}" style="font-size:11px;padding:1px 3px">'
                f'{opts}</select> '
                f'<button onclick="setClassification({scan_id},\'{fid}\','
                f'document.getElementById(\'cls-{fid}-{scan_id}\').value)" '
                f'style="font-size:11px;padding:1px 6px;cursor:pointer;'
                f'background:#555;color:#fff;border:none;border-radius:3px">Set</button>'
            )

        pos_opts  = ["bottom_overlay","top_overlay","middle_overlay","corner_overlay",
                     "left_overlay","right_overlay","overall","none"]
        ctrl_opts = ["accept_only","accept_or_reject","accept_or_settings",
                     "accept_reject_or_settings","informational_only",
                     "accept_or_pay","reject_or_pay","close_only","none"]
        emph_opts = ["accept","other","equal","none"]
        bool_opts = ["0","1"]

        classify_panel = (
            f'<div class="classify-controls" style="margin-top:8px;padding:8px;'
            f'border:1px dashed #ccc;border-radius:4px;background:#fffbe6">'
            f'<div style="font-size:12px;color:#555;font-weight:600;margin-bottom:6px">'
            f'Classify manually (✎ = override active):</div>'
            f'<div style="font-size:11px;color:#444;line-height:2">'
            f'<b>position</b> {_sel("position", pos_opts, r["manual_cookie_position"])}<br>'
            f'<b>control_type</b> {_sel("control_type", ctrl_opts, r["manual_cookie_control_type"])}<br>'
            f'<b>emphasized_option</b> {_sel("emphasized_option", emph_opts, r["manual_cookie_emphasized_option"])}<br>'
            f'<b>has_reject</b> {_sel("has_reject", bool_opts, str(r["manual_cookie_has_reject"]) if r["manual_cookie_has_reject"] is not None else None)}<br>'
            f'<b>has_settings</b> {_sel("has_settings", bool_opts, str(r["manual_cookie_has_settings"]) if r["manual_cookie_has_settings"] is not None else None)}<br>'
            f'<b>pre_selected</b> {_sel("pre_selected", bool_opts, str(r["manual_cookie_pre_selected"]) if r["manual_cookie_pre_selected"] is not None else None)}'
            f'</div></div>'
        )

    cards.append(f"""
    <div style="border:1px solid #ddd;border-radius:6px;padding:12px;background:#fafafa">
      <div style="font-weight:bold;margin-bottom:4px;font-size:14px">{r['url']}</div>
      <div style="margin-bottom:6px">{accept_status}</div>
      {img_block}
            {audit_block}
      <div style="margin-top:8px">{notice_html}</div>
      {verify_btn}
      {fp_btn}
      {classify_panel}
    </div>""")

html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Cookie Notice Classification Review</title>
  <style>
    body {{ font-family: sans-serif; margin: 20px; background: #f5f5f5; }}
    h1   {{ font-size: 20px; }}
        .controls {{ margin: 8px 0 14px; }}
        .controls label {{ font-size: 14px; color: #333; user-select: none; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }}
  </style>
</head>
<body>
  <h1>Cookie Notice Classification Review &mdash; {DB_PATH.name}</h1>
  <p>
    {sum(1 for r in rows if r['cookie_notice_detected'] and not r['false_positive'])} notices detected out of {len(rows)} sites
    ({sum(1 for r in rows if r['false_positive'])} marked as false positives).
    {sum(1 for r in rows if r['cookie_accept_attempted'] and not r['cookie_notice_accepted'] and not r['false_positive'])} attempted but unconfirmed
    (yellow = awaiting manual verification).
    {sum(1 for r in rows if r['cookie_notice_detected'] and not r['false_positive'] and (not r['cookie_position'] or r['cookie_position'] == 'none') and not r['manual_cookie_position'])} unclassified
    (yellow panel = awaiting manual classification).
  </p>
    <div class="controls">
        <label>
            <input type="checkbox" id="toggle-lighthouse" checked>
            Show Lighthouse images
        </label>
        &nbsp;&nbsp;
        <label>
            <input type="checkbox" id="toggle-unverified" checked>
            Show unverified attempts
        </label>
        &nbsp;&nbsp;
        <label>
            <input type="checkbox" id="toggle-unclassified" checked>
            Show unclassified notices
        </label>
    </div>
  <div class="grid">{''.join(cards)}</div>
    <script>
        const DB_PATH = {repr(str(DB_PATH.resolve()))};

        const toggle = document.getElementById("toggle-lighthouse");
        const toggleUnverified = document.getElementById("toggle-unverified");

        const lighthouseSections = Array.from(
            document.querySelectorAll(".lighthouse-section")
        );

        function setLighthouseVisibility(show) {{
            for (const section of lighthouseSections) {{
                section.style.display = show ? "" : "none";
            }}
        }}

        if (toggle) {{
            setLighthouseVisibility(toggle.checked);
            toggle.addEventListener("change", (event) => {{
                setLighthouseVisibility(event.target.checked);
            }});
        }}

        if (toggleUnverified) {{
            toggleUnverified.addEventListener("change", (event) => {{
                const show = event.target.checked;
                for (const el of document.querySelectorAll(".verify-controls")) {{
                    const card = el.closest("div[style*='border:1px']");
                    if (card) card.style.display = show ? "" : "none";
                }}
            }});
        }}

        const toggleUnclassified = document.getElementById("toggle-unclassified");
        if (toggleUnclassified) {{
            toggleUnclassified.addEventListener("change", (event) => {{
                const show = event.target.checked;
                for (const el of document.querySelectorAll(".classify-controls")) {{
                    const card = el.closest("div[style*='border:1px']");
                    if (card) card.style.display = show ? "" : "none";
                }}
            }});
        }}

        function setVerified(scanId, value) {{
            const val = value === null ? "NULL" : value;
            const cmd = `python mark_verified.py "${{DB_PATH}}" ${{scanId}} ${{val}}`;
            navigator.clipboard.writeText(cmd).then(() => {{
                alert("Copied to clipboard — paste and run in your terminal:\\n\\n" + cmd);
            }}).catch(() => {{
                prompt("Run this command in your terminal:", cmd);
            }});
        }}

        function setClassification(scanId, field, value) {{
            if (!value) {{ alert("Please select a value first."); return; }}
            const val = value === "NULL" ? "NULL" : value;
            const cmd = `python mark_classified.py "${{DB_PATH}}" ${{scanId}} ${{field}} ${{val}}`;
            navigator.clipboard.writeText(cmd).then(() => {{
                alert("Copied to clipboard — paste and run in your terminal:\\n\\n" + cmd);
            }}).catch(() => {{
                prompt("Run this command in your terminal:", cmd);
            }});
        }}
    </script>
</body>
</html>"""

out = Path("review.html")
out.write_text(html, encoding="utf-8")
print(f"Written {out.resolve()}")
print("Open it in a browser to review.")
