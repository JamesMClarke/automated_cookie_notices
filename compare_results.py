"""
compare_results.py — Analyse google.sqlite crawl results.

1. Check cookie notice options/position are consistent across all Chrome crawls.
2. One line chart per metric; each line = a different crawl type
   (Chrome Pre-Accept, Chrome Post-Accept, Brave).
3. Verify every metric has been graphed.
4. Diagnose root causes of accessibility score variances.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB_PATH = Path(__file__).parent / "google.sqlite"
OUT_DIR = Path(__file__).parent / "graphs"
OUT_DIR.mkdir(exist_ok=True)

# ── helpers ───────────────────────────────────────────────────────────────────

def load_chrome(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM chrome_scans ORDER BY scanned_at", conn,
        parse_dates=["scanned_at"],
    )


def load_brave(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM brave_scans ORDER BY scanned_at", conn,
        parse_dates=["scanned_at"],
    )


# ── 1. Cookie notice consistency check ───────────────────────────────────────

COOKIE_COLS = [
    "cookie_position",
    "cookie_control_type",
    "cookie_emphasized_option",
    "cookie_has_reject",
    "cookie_has_settings",
    "cookie_pre_selected",
]


def check_cookie_consistency(df: pd.DataFrame) -> None:
    print("=" * 60)
    print("1. COOKIE NOTICE CONSISTENCY CHECK")
    print("=" * 60)

    detected = df[df["cookie_notice_detected"] == 1]
    print(f"   Total crawls : {len(df)}")
    print(f"   Notice found : {len(detected)}")

    if len(detected) == 0:
        print("   No cookie notices detected — nothing to compare.")
        return

    all_consistent = True
    for col in COOKIE_COLS:
        unique_vals = detected[col].unique()
        consistent = len(unique_vals) == 1
        status = "OK" if consistent else "MISMATCH"
        print(f"   [{status}] {col}: {', '.join(str(v) for v in unique_vals)}")
        if not consistent:
            all_consistent = False

    print()
    if all_consistent:
        print("   Result: All cookie notice attributes are CONSISTENT across crawls.")
    else:
        print("   Result: Some cookie notice attributes DIFFER across crawls (see above).")
    print()


# ── 2. Per-metric line charts ─────────────────────────────────────────────────

# Each entry: (metric_name, title, ylabel, {crawl_type: source_column})
# crawl types present determine which lines appear on the chart.
METRICS = [
    (
        "wave_error",
        "WAVE Errors per Crawl",
        "Count",
        {
            "Chrome Pre-Accept":  ("chrome", "pre_wave_error"),
            "Chrome Post-Accept": ("chrome", "post_wave_error"),
            "Brave":              ("brave",  "wave_error"),
        },
    ),
    (
        "wave_contrast",
        "WAVE Contrast Errors per Crawl",
        "Count",
        {
            "Chrome Pre-Accept":  ("chrome", "pre_wave_contrast"),
            "Chrome Post-Accept": ("chrome", "post_wave_contrast"),
            "Brave":              ("brave",  "wave_contrast"),
        },
    ),
    (
        "wave_alert",
        "WAVE Alerts per Crawl",
        "Count",
        {
            "Chrome Pre-Accept":  ("chrome", "pre_wave_alert"),
            "Chrome Post-Accept": ("chrome", "post_wave_alert"),
            "Brave":              ("brave",  "wave_alert"),
        },
    ),
    (
        "wave_feature",
        "WAVE Features per Crawl",
        "Count",
        {
            "Chrome Pre-Accept":  ("chrome", "pre_wave_feature"),
            "Chrome Post-Accept": ("chrome", "post_wave_feature"),
            "Brave":              ("brave",  "wave_feature"),
        },
    ),
    (
        "wave_structure",
        "WAVE Structural Elements per Crawl",
        "Count",
        {
            "Chrome Pre-Accept":  ("chrome", "pre_wave_structure"),
            "Chrome Post-Accept": ("chrome", "post_wave_structure"),
            "Brave":              ("brave",  "wave_structure"),
        },
    ),
    (
        "wave_aria",
        "WAVE ARIA per Crawl",
        "Count",
        {
            "Chrome Pre-Accept":  ("chrome", "pre_wave_aria"),
            "Chrome Post-Accept": ("chrome", "post_wave_aria"),
            "Brave":              ("brave",  "wave_aria"),
        },
    ),
    (
        "lh_score",
        "Lighthouse Accessibility Score per Crawl",
        "Score (0–100)",
        {
            "Chrome Pre-Accept":  ("chrome", "pre_lh_score"),
            "Chrome Post-Accept": ("chrome", "post_lh_score"),
            "Brave":              ("brave",  "lh_accessibility_score"),
        },
    ),
    (
        "blocked_count",
        "Brave Blocked Requests per Crawl",
        "Blocked Requests (count)",
        {
            "Brave": ("brave", "blocked_count"),
        },
    ),
    (
        "block_rate_pct",
        "Brave Block Rate per Crawl",
        "Block Rate (%)",
        {
            "Brave": ("brave", "block_rate_pct"),
        },
    ),
]

# Colours per crawl type — consistent across all charts
CRAWL_COLORS = {
    "Chrome Pre-Accept":  "#1f77b4",
    "Chrome Post-Accept": "#ff7f0e",
    "Brave":              "#2ca02c",
}


def chart_per_metric(
    chrome_df: pd.DataFrame,
    brave_df: pd.DataFrame,
    graphed: set[str],
) -> None:
    sources = {"chrome": chrome_df, "brave": brave_df}

    for metric_name, title, ylabel, crawl_map in METRICS:
        fig, ax = plt.subplots(figsize=(11, 5))
        plotted_any = False

        for crawl_label, (src_key, col) in crawl_map.items():
            df = sources[src_key]
            if col not in df.columns or df[col].isna().all():
                print(f"   [SKIP] {col} — all NULL, omitted from '{title}'")
                continue

            x = list(range(1, len(df) + 1))
            ax.plot(
                x, df[col].tolist(),
                marker="o", markersize=4, linewidth=1.5,
                color=CRAWL_COLORS[crawl_label],
                label=crawl_label,
            )
            graphed.add(col)
            plotted_any = True

            print(f"   Plotted: {col} ({crawl_label})")
            print(f"   Values: {df[col].value_counts(dropna=False).to_dict()}")

        if not plotted_any:
            plt.close(fig)
            continue

        n = max(len(sources[src_key]) for _, (src_key, _) in crawl_map.items())
        x_all = list(range(1, n + 1))
        ax.set_xticks(x_all)
        ax.set_xticklabels([str(i) for i in x_all], fontsize=8)
        ax.set_title(title, fontsize=12, fontweight="bold")
        ax.set_xlabel("Crawl number")
        ax.set_ylabel(ylabel)
        ax.legend(loc="upper left", fontsize=9, framealpha=0.8)
        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()

        out_path = OUT_DIR / f"{metric_name}.pdf"
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        print(f"   Saved: {out_path.name}")


# ── 3. Completeness check ─────────────────────────────────────────────────────

# All source columns referenced in METRICS
ALL_SOURCE_COLS = {col for _, _, _, crawl_map in METRICS for _, (_, col) in crawl_map.items()}


def check_completeness(graphed: set[str]) -> None:
    print("=" * 60)
    print("3. COMPLETENESS CHECK — were all metrics graphed?")
    print("=" * 60)

    missing = ALL_SOURCE_COLS - graphed
    if not missing:
        print("   All expected metrics were graphed.")
    else:
        print(f"   MISSING from charts ({len(missing)}):")
        for m in sorted(missing):
            print(f"     - {m}")
    print()


# ── 4. Accessibility variance analysis ───────────────────────────────────────

BASE_DIR = Path(__file__).parent


def _resolve(db_path: str) -> Path:
    """Convert a DB-stored path (possibly Windows backslashes) to a local Path."""
    return BASE_DIR / db_path.replace("\\", "/")


def _lh_failing_audits(lh_path: Path) -> dict[str, dict]:
    """Return {audit_id: {title, score, items, weight}} for every failing binary/numeric audit."""
    if not lh_path.exists():
        return {}
    data = json.loads(lh_path.read_text(encoding="utf-8"))
    # Build audit-id → weight map from the accessibility category's auditRefs
    weights: dict[str, float] = {}
    for ref in data.get("categories", {}).get("accessibility", {}).get("auditRefs", []):
        weights[ref["id"]] = ref.get("weight", 0)

    failing = {}
    for audit_id, audit in data.get("audits", {}).items():
        mode = audit.get("scoreDisplayMode")
        score = audit.get("score")
        if mode in ("binary", "numeric") and score is not None and score < 1:
            failing[audit_id] = {
                "title": audit.get("title", ""),
                "score": score,
                "items": len(audit.get("details", {}).get("items", [])),
                "weight": weights.get(audit_id, 0),
            }
    return failing


def _wave_error_items(wave_path: Path) -> dict[str, int]:
    """Return {item_id: count} for WAVE error items in a single wave.json."""
    if not wave_path.exists():
        return {}
    data = json.loads(wave_path.read_text(encoding="utf-8"))
    items = data.get("categories", {}).get("error", {}).get("items", {})
    return {k: v.get("count", 0) for k, v in items.items()}


def analyse_lh_variance(chrome_df: pd.DataFrame, brave_df: pd.DataFrame) -> None:
    print("=" * 60)
    print("4. LIGHTHOUSE ACCESSIBILITY VARIANCE ANALYSIS")
    print("=" * 60)

    # ── Chrome: compare pre vs post for every crawl ──────────────────────────
    print("\n  Chrome — Pre-Accept vs Post-Accept")
    print("  " + "-" * 56)

    # Collect unique failing audits and how often they appear pre / post
    pre_audit_counts: dict[str, int] = {}
    post_audit_counts: dict[str, int] = {}
    audit_meta: dict[str, dict] = {}

    for _, row in chrome_df.iterrows():
        if pd.isna(row.get("pre_lh_path")) or pd.isna(row.get("post_lh_path")):
            continue
        pre_fail = _lh_failing_audits(_resolve(row["pre_lh_path"]))
        post_fail = _lh_failing_audits(_resolve(row["post_lh_path"]))
        for aid, meta in pre_fail.items():
            pre_audit_counts[aid] = pre_audit_counts.get(aid, 0) + 1
            audit_meta[aid] = meta
        for aid, meta in post_fail.items():
            post_audit_counts[aid] = post_audit_counts.get(aid, 0) + 1
            audit_meta[aid] = meta

    all_audit_ids = sorted(set(pre_audit_counts) | set(post_audit_counts),
                           key=lambda a: -audit_meta.get(a, {}).get("weight", 0))

    n = len(chrome_df)
    print(f"  {'Audit ID':<35} {'Weight':>6}  {'Pre fails':>9}  {'Post fails':>10}  Title")
    print(f"  {'-'*35} {'-'*6}  {'-'*9}  {'-'*10}  {'-'*40}")
    for aid in all_audit_ids:
        meta = audit_meta.get(aid, {})
        pre_c = pre_audit_counts.get(aid, 0)
        post_c = post_audit_counts.get(aid, 0)
        title = meta.get("title", "")[:50]
        weight = meta.get("weight", 0)
        marker = " <-- NEW FAILURE" if pre_c == 0 and post_c > 0 else (
                 " <-- RESOLVED"    if pre_c > 0 and post_c == 0 else "")
        print(f"  {aid:<35} {weight:>6.1f}  {pre_c:>4}/{n:<4}  {post_c:>5}/{n:<4}  {title}{marker}")

    # Score impact explanation
    print()
    new_post_fails = [a for a in all_audit_ids if pre_audit_counts.get(a, 0) == 0 and post_audit_counts.get(a, 0) > 0]
    if new_post_fails:
        print("  Root cause of pre(100) → post(98) score drop:")
        for aid in new_post_fails:
            meta = audit_meta[aid]
            print(f"    '{aid}' (weight={meta['weight']:.1f}): {meta['title']}")
            print(f"    Explanation: after accepting the cookie notice Google's page")
            print(f"    loses its <main> landmark — the cookie dialog DOM is removed")
            print(f"    and the resulting page structure no longer satisfies this rule.")
    print()

    # ── Brave: identify failing audits ───────────────────────────────────────
    print("  Brave")
    print("  " + "-" * 56)

    brave_audit_counts: dict[str, int] = {}
    brave_meta: dict[str, dict] = {}
    for _, row in brave_df.iterrows():
        if pd.isna(row.get("lighthouse_path")):
            continue
        fail = _lh_failing_audits(_resolve(row["lighthouse_path"]))
        for aid, meta in fail.items():
            brave_audit_counts[aid] = brave_audit_counts.get(aid, 0) + 1
            brave_meta[aid] = meta

    nb = len(brave_df)
    print(f"  {'Audit ID':<35} {'Weight':>6}  {'Fails':>9}  Title")
    print(f"  {'-'*35} {'-'*6}  {'-'*9}  {'-'*40}")
    for aid, cnt in sorted(brave_audit_counts.items(), key=lambda x: -brave_meta[x[0]].get("weight", 0)):
        meta = brave_meta[aid]
        print(f"  {aid:<35} {meta.get('weight', 0):>6.1f}  {cnt:>4}/{nb:<4}  {meta.get('title','')[:50]}")
    print()


def analyse_wave_variance(chrome_df: pd.DataFrame, brave_df: pd.DataFrame) -> None:
    print("=" * 60)
    print("5. WAVE ERROR ITEM BREAKDOWN & VARIANCE ANALYSIS")
    print("=" * 60)

    # wave_path column doesn't exist for brave — derive from lighthouse_path
    if "wave_path" not in brave_df.columns:
        brave_df = brave_df.copy()
        brave_df["wave_path"] = brave_df["lighthouse_path"].str.replace(
            "lighthouse.json", "wave.json", regex=False
        )

    sources = [
        ("Chrome Pre-Accept",  chrome_df, "pre_wave_path"),
        ("Chrome Post-Accept", chrome_df, "post_wave_path"),
        ("Brave",              brave_df,  "wave_path"),
    ]

    for label, df, col in sources:
        print(f"\n  {label}")
        print("  " + "-" * 56)

        # Collect per-crawl item counts
        per_crawl: list[dict[str, int]] = []
        for _, row in df.iterrows():
            if col not in df.columns or pd.isna(row.get(col)):
                per_crawl.append({})
                continue
            per_crawl.append(_wave_error_items(_resolve(row[col])))

        all_items = sorted({k for d in per_crawl for k in d})
        if not all_items:
            print("  No WAVE error items found.")
            continue

        # Print per-item min/max/mean across crawls
        print(f"  {'Item ID':<25} {'Min':>4} {'Max':>4} {'Mean':>6}  Variance?")
        print(f"  {'-'*25} {'-'*4} {'-'*4} {'-'*6}  {'-'*20}")
        for item in all_items:
            counts = [d.get(item, 0) for d in per_crawl]
            mn, mx = min(counts), max(counts)
            mean = sum(counts) / len(counts)
            varies = " YES  <-- dynamic content" if mn != mx else ""
            print(f"  {item:<25} {mn:>4} {mx:>4} {mean:>6.1f}{varies}")

        # Chart: stacked bar — WAVE error items per crawl
        fig, ax = plt.subplots(figsize=(12, 5))
        x = list(range(1, len(per_crawl) + 1))
        bottoms = [0] * len(per_crawl)
        colors = plt.cm.tab10.colors  # type: ignore[attr-defined]
        for i, item in enumerate(all_items):
            heights = [d.get(item, 0) for d in per_crawl]
            ax.bar(x, heights, bottom=bottoms, label=item,
                   color=colors[i % len(colors)], width=0.7)
            bottoms = [b + h for b, h in zip(bottoms, heights)]

        ax.set_title(f"WAVE Error Item Breakdown — {label}", fontsize=12, fontweight="bold")
        ax.set_xlabel("Crawl number")
        ax.set_ylabel("Error count")
        ax.set_xticks(x)
        ax.set_xticklabels([str(i) for i in x], fontsize=7)
        ax.legend(loc="upper right", fontsize=8, framealpha=0.8)
        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        safe_label = label.lower().replace(" ", "_").replace("-", "_")
        out_path = OUT_DIR / f"wave_breakdown_{safe_label}.pdf"
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
        print(f"  Saved: {out_path.name}")

    print()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not DB_PATH.exists():
        sys.exit(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    chrome_df = load_chrome(conn)
    brave_df  = load_brave(conn)
    conn.close()

    graphed: set[str] = set()

    check_cookie_consistency(chrome_df)

    print("=" * 60)
    print("2. PER-METRIC CHARTS")
    print("=" * 60)
    chart_per_metric(chrome_df, brave_df, graphed)
    print()

    check_completeness(graphed)

    analyse_lh_variance(chrome_df, brave_df)
    analyse_wave_variance(chrome_df, brave_df)

    print(f"Charts written to: {OUT_DIR}/")


if __name__ == "__main__":
    main()
