#!/usr/bin/env python3
"""Read harvested PR data, compute statistics, and emit site/index.html."""

import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc

HIST_BUCKETS = [
    (0,   1,          "<1d"),
    (1,   3,          "1-3d"),
    (3,   7,          "3-7d"),
    (7,   14,         "7-14d"),
    (14,  30,         "14-30d"),
    (30,  60,         "30-60d"),
    (60,  float("inf"), "60d+"),
]

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Kroxylicious PR Metrics</title>
  <style>
    :root {
      --surface-1: #fcfcfb;
      --page:      #f9f9f7;
      --ink-1:     #0b0b0b;
      --ink-2:     #52514e;
      --ink-m:     #898781;
      --grid:      #e1e0d9;
      --s1:        #2a78d6;
      --s2:        #008300;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --surface-1: #1a1a19;
        --page:      #0d0d0d;
        --ink-1:     #ffffff;
        --ink-2:     #c3c2b7;
        --ink-m:     #898781;
        --grid:      #2c2c2a;
        --s1:        #3987e5;
        --s2:        #008300;
      }
    }
    *, *::before, *::after { box-sizing: border-box; }
    body {
      margin: 0; padding: 2rem;
      background: var(--page);
      color: var(--ink-1);
      font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    }
    h1 { font-size: 1.5rem; font-weight: 600; margin: 0 0 0.25rem; }
    h2 { font-size: 1rem; font-weight: 600; margin: 0 0 1rem; color: var(--ink-2); }
    .meta { font-size: 0.8rem; color: var(--ink-m); margin: 0 0 2rem; }
    .kpi-row { display: flex; gap: 2rem; margin-bottom: 3rem; flex-wrap: wrap; }
    .kpi {
      background: var(--surface-1);
      border: 1px solid var(--grid);
      border-radius: 8px;
      padding: 1.5rem 2rem;
      min-width: 200px;
    }
    .kpi-value { font-size: 3rem; font-weight: 700; line-height: 1; color: var(--s1); margin-bottom: 0.5rem; }
    .kpi-value.ftc { color: var(--s2); }
    .kpi-value.engagement { color: var(--ink-2); }
    .kpi-label { font-size: 0.9rem; font-weight: 600; color: var(--ink-2); }
    .kpi-sub   { font-size: 0.8rem; color: var(--ink-m); margin-top: 0.25rem; }
    .panel {
      background: var(--surface-1);
      border: 1px solid var(--grid);
      border-radius: 8px;
      padding: 1.5rem;
      margin-bottom: 2rem;
    }
    .chart-wrap { position: relative; height: 320px; }
    .no-data { color: var(--ink-m); font-size: 0.9rem; padding: 1rem 0; }
  </style>
</head>
<body>
  <h1>Kroxylicious PR Metrics</h1>
  <p class="meta">Generated __GENERATED_AT__</p>

  <div class="kpi-row">
    <div class="kpi">
      <div class="kpi-value">__MEDIAN__</div>
      <div class="kpi-label">Median time to resolution</div>
      <div class="kpi-sub">__TOTAL_RESOLVED__ resolved PRs</div>
    </div>
    <div class="kpi">
      <div class="kpi-value ftc">__FTC_COUNT__</div>
      <div class="kpi-label">First-time contributor PRs</div>
      <div class="kpi-sub">__FTC_PCT__% of resolved PRs</div>
    </div>
    <div class="kpi">
      <div class="kpi-value engagement">__ENGAGEMENT__</div>
      <div class="kpi-label">Median time to first Committer engagement</div>
      <div class="kpi-sub">member or owner</div>
    </div>
  </div>

  <div class="panel">
    <h2>Resolution time distribution</h2>
    __HIST_BODY__
  </div>

  <div class="panel">
    <h2>Monthly median resolution time (days)</h2>
    __TREND_BODY__
  </div>

  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
  <script>
    const s = getComputedStyle(document.documentElement);
    const C = {
      s1:   s.getPropertyValue('--s1').trim(),
      s2:   s.getPropertyValue('--s2').trim(),
      grid: s.getPropertyValue('--grid').trim(),
      ink2: s.getPropertyValue('--ink-2').trim(),
      inkm: s.getPropertyValue('--ink-m').trim(),
    };
    const baseScales = {
      x: { grid: { color: C.grid }, ticks: { color: C.inkm } },
      y: { grid: { color: C.grid }, ticks: { color: C.inkm }, beginAtZero: true },
    };

    __CHART_JS__
  </script>
</body>
</html>
"""

_HIST_CHART = """\
new Chart(document.getElementById('histogram'), {
  type: 'bar',
  data: {
    labels: __LABELS__,
    datasets: [{ data: __COUNTS__, backgroundColor: C.s1, borderRadius: 4, borderSkipped: 'bottom' }],
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: ctx => ctx.parsed.y + ' PRs' } },
    },
    scales: baseScales,
  },
});"""

_TREND_CHART = """\
new Chart(document.getElementById('trend'), {
  type: 'line',
  data: {
    labels: __LABELS__,
    datasets: [
      {
        label: 'Other contributors',
        data: __NON_FTC_VALUES__,
        borderColor: C.s1, borderWidth: 2,
        pointBackgroundColor: C.s1, pointRadius: 6, pointHoverRadius: 8,
        fill: false, tension: 0.2, spanGaps: false,
      },
      {
        label: 'First-time contributors',
        data: __FTC_VALUES__,
        borderColor: C.s2, borderWidth: 2,
        pointBackgroundColor: C.s2, pointRadius: 6, pointHoverRadius: 8,
        fill: false, tension: 0.2, spanGaps: false,
      },
    ],
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { display: true, labels: { color: C.ink2 } },
      tooltip: { callbacks: { label: ctx => ctx.dataset.label + ': ' + ctx.parsed.y + ' days' } },
    },
    scales: baseScales,
  },
});"""


def _parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def resolution_time_days(events):
    """Returns (close_datetime, days_float) or None if the PR is not closed."""
    created = None
    close_event = None
    for e in events:
        if e["type"] == "created" and created is None:
            created = _parse_ts(e["timestamp"])
        if e["type"] in ("closed_merged", "closed_unmerged") and close_event is None:
            close_event = e
    if created is None or close_event is None:
        return None
    closed = _parse_ts(close_event["timestamp"])
    return closed, (closed - created).total_seconds() / 86400


def histogram(days_list):
    """Returns (labels, counts) bucketed by HIST_BUCKETS."""
    counts = [0] * len(HIST_BUCKETS)
    for days in days_list:
        for i, (lo, hi, _) in enumerate(HIST_BUCKETS):
            if lo <= days < hi:
                counts[i] += 1
                break
    labels = [label for _, _, label in HIST_BUCKETS]
    return labels, counts


def load_committers(path):
    """Returns frozenset of committer GitHub usernames from a plain text file."""
    if not path.exists():
        return frozenset()
    return frozenset(
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )


def time_to_engagement_days(events, committers):
    """Returns days from created to first committer comment or review, or None."""
    created = None
    for e in events:
        if e["type"] == "created" and created is None:
            created = _parse_ts(e["timestamp"])
            continue
        if (
            created is not None
            and e["type"] in ("reviewed", "comment")
            and e.get("actor") in committers
        ):
            return (_parse_ts(e["timestamp"]) - created).total_seconds() / 86400
    return None


def monthly_medians_by_group(resolved):
    """
    resolved: list of (close_datetime, days_float, is_ftc, engagement_days)
    Returns (months, ftc_medians, non_ftc_medians) in chronological order.
    Months where a group has no data carry None.
    """
    ftc_by_month = defaultdict(list)
    non_ftc_by_month = defaultdict(list)
    for close_dt, days, is_ftc, _ in resolved:
        key = close_dt.strftime("%Y-%m")
        (ftc_by_month if is_ftc else non_ftc_by_month)[key].append(days)

    months = sorted(ftc_by_month.keys() | non_ftc_by_month.keys())
    ftc_medians = [
        round(statistics.median(ftc_by_month[m]), 1) if m in ftc_by_month else None
        for m in months
    ]
    non_ftc_medians = [
        round(statistics.median(non_ftc_by_month[m]), 1) if m in non_ftc_by_month else None
        for m in months
    ]
    return months, ftc_medians, non_ftc_medians


def compute_stats(resolved):
    all_days = [days for _, days, _, _ in resolved]
    ftc_count = sum(1 for _, _, is_ftc, _ in resolved if is_ftc)
    engagement_times = [e for _, _, _, e in resolved if e is not None]
    total = len(resolved)

    median = round(statistics.median(all_days), 1) if all_days else None
    median_engagement = round(statistics.median(engagement_times), 1) if engagement_times else None
    hist_labels, hist_counts = histogram(all_days)
    trend_months, trend_ftc_medians, trend_non_ftc_medians = monthly_medians_by_group(resolved)

    return {
        "total_resolved": total,
        "ftc_count": ftc_count,
        "ftc_pct": round(100 * ftc_count / total) if total else 0,
        "median_days": median,
        "median_engagement_days": median_engagement,
        "hist_labels": hist_labels,
        "hist_counts": hist_counts,
        "trend_months": trend_months,
        "trend_ftc_medians": trend_ftc_medians,
        "trend_non_ftc_medians": trend_non_ftc_medians,
    }


def load_resolved(data_dir, committers=frozenset()):
    resolved = []
    prs_dir = data_dir / "prs"
    if not prs_dir.exists():
        return resolved
    for pr_dir in sorted(prs_dir.iterdir()):
        events_path = pr_dir / "events.json"
        metadata_path = pr_dir / "metadata.json"
        if not events_path.exists():
            continue
        events = json.loads(events_path.read_text())
        result = resolution_time_days(events)
        if result is None:
            continue
        is_ftc = False
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text())
            is_ftc = metadata.get("author_association") == "FIRST_TIME_CONTRIBUTOR"
        engagement = time_to_engagement_days(events, committers)
        resolved.append((*result, is_ftc, engagement))
    return resolved


def render_html(stats, generated_at):
    median = stats["median_days"]
    median_str = f"{median} days" if median is not None else "—"
    engagement = stats["median_engagement_days"]
    engagement_str = f"{engagement} days" if engagement is not None else "—"
    ftc_count = stats["ftc_count"]
    ftc_pct = stats["ftc_pct"]

    if stats["total_resolved"] == 0:
        hist_body = '<p class="no-data">No resolved PRs yet.</p>'
        trend_body = '<p class="no-data">No resolved PRs yet.</p>'
        chart_js = ""
    else:
        hist_body = '<div class="chart-wrap"><canvas id="histogram"></canvas></div>'
        trend_body = '<div class="chart-wrap"><canvas id="trend"></canvas></div>'
        hist_js = (
            _HIST_CHART
            .replace("__LABELS__", json.dumps(stats["hist_labels"]))
            .replace("__COUNTS__", json.dumps(stats["hist_counts"]))
        )
        trend_js = (
            _TREND_CHART
            .replace("__LABELS__", json.dumps(stats["trend_months"]))
            .replace("__FTC_VALUES__", json.dumps(stats["trend_ftc_medians"]))
            .replace("__NON_FTC_VALUES__", json.dumps(stats["trend_non_ftc_medians"]))
        )
        chart_js = hist_js + "\n\n    " + trend_js

    return (
        _HTML
        .replace("__GENERATED_AT__", generated_at)
        .replace("__MEDIAN__", median_str)
        .replace("__TOTAL_RESOLVED__", str(stats["total_resolved"]))
        .replace("__FTC_COUNT__", str(ftc_count))
        .replace("__FTC_PCT__", str(ftc_pct))
        .replace("__ENGAGEMENT__", engagement_str)
        .replace("__HIST_BODY__", hist_body)
        .replace("__TREND_BODY__", trend_body)
        .replace("__CHART_JS__", chart_js)
    )


def build_site(data_dir, site_dir, generated_at, committers=frozenset()):
    resolved = load_resolved(data_dir, committers)
    stats = compute_stats(resolved)
    html = render_html(stats, generated_at)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(html)
    return stats


def main():
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    site_dir = Path(os.environ.get("SITE_DIR", "site"))
    committers_file = Path(os.environ.get("COMMITTERS_FILE", "committers.txt"))
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    committers = load_committers(committers_file)
    stats = build_site(data_dir, site_dir, generated_at, committers)
    print(f"Site built. {stats['total_resolved']} resolved PRs. Median: {stats['median_days']} days. FTC: {stats['ftc_count']} ({stats['ftc_pct']}%).")


if __name__ == "__main__":
    main()
