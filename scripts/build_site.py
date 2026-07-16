#!/usr/bin/env python3
"""Read harvested PR data, compute statistics, and emit site/index.html."""

import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
RECENT_DAYS = 90

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
    .meta { font-size: 0.8rem; color: var(--ink-m); margin: 0 0 1.5rem; }
    .tabs { display: flex; border-bottom: 1px solid var(--grid); margin-bottom: 2rem; }
    .tab-btn {
      padding: 0.625rem 1.25rem;
      background: none;
      border: none;
      border-bottom: 2px solid transparent;
      margin-bottom: -1px;
      font: inherit;
      font-size: 0.875rem;
      font-weight: 500;
      color: var(--ink-m);
      cursor: pointer;
    }
    .tab-btn.active { color: var(--s1); border-bottom-color: var(--s1); }
    .tab-btn:hover:not(.active) { color: var(--ink-2); }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
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

  <nav class="tabs">
    <button class="tab-btn active" data-tab="recent">Last 3 months</button>
    <button class="tab-btn" data-tab="alltime">All time</button>
  </nav>

  <div id="tab-recent" class="tab-panel active">
    __RECENT_CONTENT__
  </div>

  <div id="tab-alltime" class="tab-panel">
    __ALL_CONTENT__
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

    const initialized = new Set();
    function initTab(name) {
      if (initialized.has(name)) return;
      initialized.add(name);
      __CHART_JS__
    }

    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        initTab(btn.dataset.tab);
      });
    });

    initTab('recent');
  </script>
</body>
</html>
"""


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


def _week_monday(dt):
    """Returns the date of the Monday that starts the week containing dt."""
    return (dt - timedelta(days=dt.weekday())).date()


def monthly_medians_by_group(resolved):
    """
    resolved: list of (close_datetime, days_float, is_ftc, engagement_days)
    Returns (labels, ftc_medians, non_ftc_medians) grouped by month (YYYY-MM).
    Months where a group has no data carry None.
    """
    ftc_by = defaultdict(list)
    non_ftc_by = defaultdict(list)
    for close_dt, days, is_ftc, _ in resolved:
        key = close_dt.strftime("%Y-%m")
        (ftc_by if is_ftc else non_ftc_by)[key].append(days)
    keys = sorted(ftc_by.keys() | non_ftc_by.keys())
    ftc_medians = [round(statistics.median(ftc_by[k]), 1) if k in ftc_by else None for k in keys]
    non_ftc_medians = [round(statistics.median(non_ftc_by[k]), 1) if k in non_ftc_by else None for k in keys]
    return keys, ftc_medians, non_ftc_medians


def weekly_medians_by_group(resolved):
    """
    resolved: list of (close_datetime, days_float, is_ftc, engagement_days)
    Returns (labels, ftc_medians, non_ftc_medians) grouped by ISO week.
    Labels are 'Mon DD' (the Monday of each week). Weeks where a group has no data carry None.
    """
    ftc_by = defaultdict(list)
    non_ftc_by = defaultdict(list)
    for close_dt, days, is_ftc, _ in resolved:
        key = _week_monday(close_dt)
        (ftc_by if is_ftc else non_ftc_by)[key].append(days)
    weeks = sorted(ftc_by.keys() | non_ftc_by.keys())
    labels = [w.strftime("%b %d") for w in weeks]
    ftc_medians = [round(statistics.median(ftc_by[w]), 1) if w in ftc_by else None for w in weeks]
    non_ftc_medians = [round(statistics.median(non_ftc_by[w]), 1) if w in non_ftc_by else None for w in weeks]
    return labels, ftc_medians, non_ftc_medians


def filter_resolved_since(resolved, since_dt):
    """Returns only resolved PRs with close_dt >= since_dt."""
    return [item for item in resolved if item[0] >= since_dt]


def compute_stats(resolved, trend_fn=None):
    if trend_fn is None:
        trend_fn = monthly_medians_by_group
    all_days = [days for _, days, _, _ in resolved]
    ftc_count = sum(1 for _, _, is_ftc, _ in resolved if is_ftc)
    engagement_times = [e for _, _, _, e in resolved if e is not None]
    total = len(resolved)

    median = round(statistics.median(all_days), 1) if all_days else None
    median_engagement = round(statistics.median(engagement_times), 1) if engagement_times else None
    hist_labels, hist_counts = histogram(all_days)
    trend_labels, trend_ftc_medians, trend_non_ftc_medians = trend_fn(resolved)

    return {
        "total_resolved": total,
        "ftc_count": ftc_count,
        "ftc_pct": round(100 * ftc_count / total) if total else 0,
        "median_days": median,
        "median_engagement_days": median_engagement,
        "hist_labels": hist_labels,
        "hist_counts": hist_counts,
        "trend_labels": trend_labels,
        "trend_ftc_medians": trend_ftc_medians,
        "trend_non_ftc_medians": trend_non_ftc_medians,
    }


def compute_ftc_pr_numbers(data_dir):
    """Returns the set of PR numbers that are each author's first PR in the dataset."""
    prs_dir = data_dir / "prs"
    if not prs_dir.exists():
        return frozenset()
    first_by_author = {}
    for pr_dir in prs_dir.iterdir():
        metadata_path = pr_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        try:
            meta = json.loads(metadata_path.read_text())
            author = meta.get("author")
            created_at = meta.get("created_at")
            number = meta.get("number")
            if not (author and created_at and number is not None):
                continue
            if author not in first_by_author or created_at < first_by_author[author][0]:
                first_by_author[author] = (created_at, number)
        except (json.JSONDecodeError, KeyError):
            continue
    return frozenset(number for _, number in first_by_author.values())


def load_resolved(data_dir, committers=frozenset()):
    ftc_pr_numbers = compute_ftc_pr_numbers(data_dir)
    resolved = []
    prs_dir = data_dir / "prs"
    if not prs_dir.exists():
        return resolved
    for pr_dir in sorted(prs_dir.iterdir()):
        events_path = pr_dir / "events.json"
        if not events_path.exists():
            continue
        events = json.loads(events_path.read_text())
        result = resolution_time_days(events)
        if result is None:
            continue
        try:
            pr_number = int(pr_dir.name)
        except ValueError:
            continue
        engagement = time_to_engagement_days(events, committers)
        resolved.append((*result, pr_number in ftc_pr_numbers, engagement))
    return resolved


def _kpi_row_html(stats):
    median = stats["median_days"]
    engagement = stats["median_engagement_days"]
    return (
        f'<div class="kpi-row">'
        f'<div class="kpi"><div class="kpi-value">'
        f'{"— " if median is None else f"{median} days"}'
        f'</div><div class="kpi-label">Median time to resolution</div>'
        f'<div class="kpi-sub">{stats["total_resolved"]} resolved PRs</div></div>'
        f'<div class="kpi"><div class="kpi-value ftc">{stats["ftc_count"]}</div>'
        f'<div class="kpi-label">First-time contributor PRs</div>'
        f'<div class="kpi-sub">{stats["ftc_pct"]}% of resolved PRs</div></div>'
        f'<div class="kpi"><div class="kpi-value engagement">'
        f'{"—" if engagement is None else f"{engagement} days"}'
        f'</div><div class="kpi-label">Median time to first Committer engagement</div>'
        f'<div class="kpi-sub">member or owner</div></div>'
        f'</div>'
    )


def _chart_area_html(canvas_id, has_data):
    if not has_data:
        return '<p class="no-data">No data yet.</p>'
    return f'<div class="chart-wrap"><canvas id="{canvas_id}"></canvas></div>'


def _hist_js(canvas_id, stats):
    if not stats["total_resolved"]:
        return ""
    d = json.dumps
    return (
        f"new Chart(document.getElementById({d(canvas_id)}), {{"
        f"type:'bar',data:{{labels:{d(stats['hist_labels'])},datasets:[{{data:{d(stats['hist_counts'])},"
        f"backgroundColor:C.s1,borderRadius:4,borderSkipped:'bottom'}}]}},"
        f"options:{{responsive:true,maintainAspectRatio:false,"
        f"plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>ctx.parsed.y+' PRs'}}}}}},"
        f"scales:baseScales}}}});"
    )


def _trend_js(canvas_id, stats):
    if not stats["total_resolved"] or not stats["trend_labels"]:
        return ""
    d = json.dumps
    return (
        f"new Chart(document.getElementById({d(canvas_id)}), {{"
        f"type:'line',data:{{labels:{d(stats['trend_labels'])},datasets:["
        f"{{label:'Other contributors',data:{d(stats['trend_non_ftc_medians'])},"
        f"borderColor:C.s1,borderWidth:2,pointBackgroundColor:C.s1,pointRadius:6,pointHoverRadius:8,"
        f"fill:false,tension:0.2,spanGaps:false}},"
        f"{{label:'First-time contributors',data:{d(stats['trend_ftc_medians'])},"
        f"borderColor:C.s2,borderWidth:2,pointBackgroundColor:C.s2,pointRadius:6,pointHoverRadius:8,"
        f"fill:false,tension:0.2,spanGaps:false}}"
        f"]}},"
        f"options:{{responsive:true,maintainAspectRatio:false,"
        f"plugins:{{legend:{{display:true,labels:{{color:C.ink2}}}},"
        f"tooltip:{{callbacks:{{label:ctx=>ctx.dataset.label+': '+ctx.parsed.y+' days'}}}}}},"
        f"scales:baseScales}}}});"
    )


def _tab_content_html(stats, hist_title, trend_title, hist_id, trend_id):
    return (
        f"{_kpi_row_html(stats)}"
        f'<div class="panel"><h2>{hist_title}</h2>'
        f"{_chart_area_html(hist_id, stats['total_resolved'])}</div>"
        f'<div class="panel"><h2>{trend_title}</h2>'
        f"{_chart_area_html(trend_id, stats['total_resolved'] and bool(stats['trend_labels']))}</div>"
    )


def render_html(all_stats, recent_stats, generated_at):
    recent_hist_id  = "hist-recent"
    recent_trend_id = "trend-recent"
    all_hist_id     = "hist-alltime"
    all_trend_id    = "trend-alltime"

    recent_content = _tab_content_html(
        recent_stats,
        "Resolution time distribution",
        "Weekly median resolution time (days)",
        recent_hist_id, recent_trend_id,
    )
    all_content = _tab_content_html(
        all_stats,
        "Resolution time distribution",
        "Monthly median resolution time (days)",
        all_hist_id, all_trend_id,
    )

    recent_js = _hist_js(recent_hist_id, recent_stats) + _trend_js(recent_trend_id, recent_stats)
    all_js    = _hist_js(all_hist_id, all_stats) + _trend_js(all_trend_id, all_stats)

    chart_js = (
        f"if (name === 'recent') {{ {recent_js} }}"
        f" else if (name === 'alltime') {{ {all_js} }}"
    )

    return (
        _HTML
        .replace("__GENERATED_AT__", generated_at)
        .replace("__RECENT_CONTENT__", recent_content)
        .replace("__ALL_CONTENT__", all_content)
        .replace("__CHART_JS__", chart_js)
    )


def build_site(data_dir, site_dir, generated_at, committers=frozenset()):
    resolved = load_resolved(data_dir, committers)
    recent_cutoff = datetime.now(UTC) - timedelta(days=RECENT_DAYS)
    recent_resolved = filter_resolved_since(resolved, recent_cutoff)

    all_stats    = compute_stats(resolved,        monthly_medians_by_group)
    recent_stats = compute_stats(recent_resolved, weekly_medians_by_group)

    html = render_html(all_stats, recent_stats, generated_at)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(html)
    return {"all": all_stats, "recent": recent_stats}


def main():
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    site_dir = Path(os.environ.get("SITE_DIR", "site"))
    committers_file = Path(os.environ.get("COMMITTERS_FILE", "committers.txt"))
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    committers = load_committers(committers_file)
    stats = build_site(data_dir, site_dir, generated_at, committers)
    a = stats["all"]
    r = stats["recent"]
    print(
        f"Site built. All-time: {a['total_resolved']} PRs, median {a['median_days']} days, "
        f"{a['ftc_count']} FTC. Last 3 months: {r['total_resolved']} PRs, median {r['median_days']} days."
    )


if __name__ == "__main__":
    main()
