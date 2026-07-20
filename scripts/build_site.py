#!/usr/bin/env python3
"""Read harvested PR data, compute statistics, and emit site/index.html."""

import html as html_lib
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
      --s3:        #eb6834;
      --s4:        #4a3aa7;
      --s5:        #e34948;
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
        --s3:        #d95926;
        --s4:        #9085e9;
        --s5:        #e66767;
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
    .kpi-label { font-size: 0.9rem; font-weight: 600; color: var(--ink-2); }
    .kpi-sub   { font-size: 0.8rem; color: var(--ink-m); margin-top: 0.25rem; }
    .stat-group { background: var(--surface-1); border: 1px solid var(--grid); border-radius: 8px; padding: 1.25rem 1.5rem; flex: 1; min-width: 260px; }
    .stat-group-label { font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.75rem; }
    .stat-group--resolution .stat-group-label,
    .stat-group--resolution .stat-value { color: var(--s1); }
    .stat-group--engagement .stat-group-label,
    .stat-group--engagement .stat-value { color: var(--s3); }
    .stat-row { display: flex; gap: 1.5rem; flex-wrap: wrap; }
    .stat { display: flex; flex-direction: column; gap: 0.2rem; min-width: 52px; }
    .stat-name  { font-size: 0.7rem; color: var(--ink-m); }
    .stat-value { font-size: 1.5rem; font-weight: 700; line-height: 1; }
    .stat-link  { color: inherit; text-decoration: underline; text-underline-offset: 3px; }
    .stat-link:hover { opacity: 0.75; }
    .panel {
      background: var(--surface-1);
      border: 1px solid var(--grid);
      border-radius: 8px;
      padding: 1.5rem;
      margin-bottom: 2rem;
    }
    .chart-wrap { position: relative; height: 320px; }
    .no-data { color: var(--ink-m); font-size: 0.9rem; padding: 1rem 0; }
    .pr-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 0.875rem; }
    .pr-card {
      position: relative;
      overflow: hidden;
      border: 1px solid var(--grid);
      border-radius: 8px;
      padding: 0.875rem 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
    }
    .pr-card::after {
      content: "";
      position: absolute;
      bottom: -4px; right: -4px;
      width: 72px; height: 72px;
      border-radius: 50%;
      background-image: var(--avatar-url);
      background-size: cover;
      opacity: 0.18;
      pointer-events: none;
    }
    .pr-card--ftc        { background: rgba(0,131,0,0.07); }
    .pr-card--external   { background: rgba(235,104,52,0.07); }
    .pr-card--committer  { background: rgba(42,120,214,0.06); }
    .pr-card--bot        { background: var(--surface-1); opacity: 0.7; }
    .pr-card-number { font-size: 0.7rem; font-weight: 600; color: var(--ink-m); }
    .pr-card-title { font-size: 0.8rem; font-weight: 500; color: var(--ink-1); line-height: 1.35;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .pr-card-title a { color: inherit; text-decoration: none; }
    .pr-card-title a:hover { text-decoration: underline; }
    .pr-card-author { font-size: 0.72rem; color: var(--ink-m); }
    .pr-card-author a { color: inherit; text-decoration: none; }
    .pr-card-author a:hover { text-decoration: underline; }
    .pr-card-badges { font-size: 0.85rem; line-height: 1; min-height: 1rem; }
    .pr-card-linked { font-size: 0.68rem; color: var(--ink-m); line-height: 1.4; }
    .pr-card-meta { font-size: 0.7rem; color: var(--ink-m); margin-top: auto; padding-top: 0.4rem; border-top: 1px solid var(--grid); }
    .pr-card-meta .age { font-weight: 600; color: var(--ink-2); }
    .legend { font-size: 0.75rem; color: var(--ink-m); margin-top: 1rem; display: flex; gap: 1.25rem; flex-wrap: wrap; }
    .legend-item { display: flex; align-items: center; gap: 0.3rem; }
    .pr-controls { margin-bottom: 1rem; font-size: 0.875rem; color: var(--ink-2); display: flex; align-items: center; gap: 0.5rem; }
    .pr-controls input[type="checkbox"] { cursor: pointer; }
    #pr-grid-open.hide-ancient .pr-card[data-ancient="true"] { display: none; }
    .repo-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.75rem; }
    .repo-chip {
      padding: 0.25rem 0.625rem;
      border: 1px solid var(--grid);
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 500;
      background: none;
      color: var(--ink-2);
      cursor: pointer;
    }
    .repo-chip.active { background: var(--s1); color: #fff; border-color: var(--s1); }
    .repo-chip:hover:not(.active) { border-color: var(--s1); color: var(--s1); }
    .pr-repo-tag { font-size: 0.65rem; color: var(--ink-m); font-family: monospace; }
  </style>
</head>
<body>
  <h1>Kroxylicious PR Metrics</h1>
  <p class="meta">Generated __GENERATED_AT__</p>

  <nav class="tabs">
    <button class="tab-btn active" data-tab="open">Open PRs</button>
    <button class="tab-btn" data-tab="recent">Last 3 months</button>
    <button class="tab-btn" data-tab="alltime">All time</button>
  </nav>

  <div id="tab-open" class="tab-panel active">
    __OPEN_PRS_CONTENT__
  </div>

  <div id="tab-recent" class="tab-panel">
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
      s3:   s.getPropertyValue('--s3').trim(),
      s4:   s.getPropertyValue('--s4').trim(),
      s5:   s.getPropertyValue('--s5').trim(),
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

    initTab('open');
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


_KNOWN_BOTS = frozenset({
    "kroxylicious-robot",
})


def _is_bot(username):
    return username.endswith("[bot]") or username in _KNOWN_BOTS


def time_to_engagement_days(events):
    """Returns days from created to first human (non-author, non-bot) comment or review, or None."""
    created = None
    author = None
    for e in events:
        if e["type"] == "created" and created is None:
            created = _parse_ts(e["timestamp"])
            author = e.get("actor")
            continue
        actor = e.get("actor") or ""
        if (
            created is not None
            and e["type"] in ("reviewed", "comment")
            and actor
            and actor != author
            and not _is_bot(actor)
        ):
            return (_parse_ts(e["timestamp"]) - created).total_seconds() / 86400
    return None


def _week_monday(dt):
    """Returns the date of the Monday that starts the week containing dt."""
    return (dt - timedelta(days=dt.weekday())).date()


def _period_stats(days_list):
    """Returns (median, mean, p95, p99, max) for a list of floats."""
    sv = sorted(days_list)
    return (
        round(statistics.median(days_list), 1),
        round(sum(days_list) / len(days_list), 1),
        _percentile(sv, 95),
        _percentile(sv, 99),
        round(max(days_list), 1),
    )


def _unzip_stats(keys, rows):
    if not rows:
        return keys, [], [], [], [], []
    medians, means, p95s, p99s, maxes = zip(*rows)
    return keys, list(medians), list(means), list(p95s), list(p99s), list(maxes)


def monthly_stats_trend(resolved):
    """
    resolved: list of (close_datetime, days_float, ...)
    Returns (labels, medians, means, p95s, p99s, maxes) grouped by month (YYYY-MM).
    """
    by_month = defaultdict(list)
    for close_dt, days, *_ in resolved:
        by_month[close_dt.strftime("%Y-%m")].append(days)
    keys = sorted(by_month)
    return _unzip_stats(keys, [_period_stats(by_month[k]) for k in keys])


def weekly_stats_trend(resolved):
    """
    resolved: list of (close_datetime, days_float, ...)
    Returns (labels, medians, means, p95s, p99s, maxes) grouped by ISO week.
    Labels are 'Mon DD' (the Monday of each week).
    """
    by_week = defaultdict(list)
    for close_dt, days, *_ in resolved:
        by_week[_week_monday(close_dt)].append(days)
    weeks = sorted(by_week)
    labels = [w.strftime("%b %d") for w in weeks]
    return _unzip_stats(labels, [_period_stats(by_week[w]) for w in weeks])


def monthly_engagement_trend(resolved):
    """
    resolved: list of (close_datetime, days_float, is_ftc, engagement, pr_number)
    Returns (labels, medians, means, p95s, p99s, maxes) for engagement times, grouped by month.
    Periods with no engagement data are omitted.
    """
    by_month = defaultdict(list)
    for close_dt, _, _, engagement, *_ in resolved:
        if engagement is not None:
            by_month[close_dt.strftime("%Y-%m")].append(engagement)
    keys = sorted(by_month)
    return _unzip_stats(keys, [_period_stats(by_month[k]) for k in keys])


def weekly_engagement_trend(resolved):
    """
    resolved: list of (close_datetime, days_float, is_ftc, engagement, pr_number)
    Returns (labels, medians, means, p95s, p99s, maxes) for engagement times, grouped by ISO week.
    Periods with no engagement data are omitted.
    """
    by_week = defaultdict(list)
    for close_dt, _, _, engagement, *_ in resolved:
        if engagement is not None:
            by_week[_week_monday(close_dt)].append(engagement)
    weeks = sorted(by_week)
    labels = [w.strftime("%b %d") for w in weeks]
    return _unzip_stats(labels, [_period_stats(by_week[w]) for w in weeks])


def filter_resolved_since(resolved, since_dt):
    """Returns only resolved PRs with close_dt >= since_dt."""
    return [item for item in resolved if item[0] >= since_dt]


def _percentile(sorted_vals, p):
    n = len(sorted_vals)
    idx = p / 100 * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    return round(sorted_vals[lo] + (idx - lo) * (sorted_vals[hi] - sorted_vals[lo]), 1)


def _dist_stats(values, pr_urls=None):
    if not values:
        return {"median": None, "mean": None, "p95": None, "p99": None, "max": None, "max_pr_url": None}
    sv = sorted(values)
    max_val = max(values)
    max_pr_url = pr_urls[values.index(max_val)] if pr_urls else None
    return {
        "median":     round(statistics.median(values), 1),
        "mean":       round(sum(values) / len(values), 1),
        "p95":        _percentile(sv, 95),
        "p99":        _percentile(sv, 99),
        "max":        round(max_val, 1),
        "max_pr_url": max_pr_url,
    }


def compute_stats(resolved, trend_fn=None, eng_trend_fn=None):
    if trend_fn is None:
        trend_fn = monthly_stats_trend
    if eng_trend_fn is None:
        eng_trend_fn = monthly_engagement_trend

    all_days    = [days for _, days, *_ in resolved]
    all_pr_urls = [
        f"https://github.com/{repo}/pull/{pr}"
        for _, _, _, _, pr, repo in resolved
    ]
    ftc_count = sum(1 for _, _, is_ftc, *_ in resolved if is_ftc)

    eng_items  = [
        (e, f"https://github.com/{repo}/pull/{pr}")
        for _, _, _, e, pr, repo in resolved
        if e is not None
    ]
    eng_values = [e   for e, _   in eng_items]
    eng_urls   = [url for _, url in eng_items]

    total = len(resolved)
    hist_labels, hist_counts = histogram(all_days)
    trend_labels,  trend_medians,  trend_means,  trend_p95s,  trend_p99s,  trend_maxes  = trend_fn(resolved)
    eng_labels,    eng_medians,    eng_means,    eng_p95s,    eng_p99s,    eng_maxes    = eng_trend_fn(resolved)

    return {
        "total_resolved": total,
        "ftc_count": ftc_count,
        "ftc_pct": round(100 * ftc_count / total) if total else 0,
        "resolution": _dist_stats(all_days, all_pr_urls),
        "engagement": _dist_stats(eng_values, eng_urls),
        "hist_labels": hist_labels,
        "hist_counts": hist_counts,
        "trend_labels":   list(trend_labels),
        "trend_medians":  list(trend_medians),
        "trend_means":    list(trend_means),
        "trend_p95s":     list(trend_p95s),
        "trend_p99s":     list(trend_p99s),
        "trend_maxes":    list(trend_maxes),
        "eng_trend_labels":   list(eng_labels),
        "eng_trend_medians":  list(eng_medians),
        "eng_trend_means":    list(eng_means),
        "eng_trend_p95s":     list(eng_p95s),
        "eng_trend_p99s":     list(eng_p99s),
        "eng_trend_maxes":    list(eng_maxes),
    }


def _iter_repo_prs_dirs(data_dir):
    """Yields (owner, repo, pr_dir) for every PR dir across all repos under data_dir."""
    for owner_dir in sorted(data_dir.iterdir()):
        if not owner_dir.is_dir() or owner_dir.name.startswith("."):
            continue
        for repo_dir in sorted(owner_dir.iterdir()):
            prs_dir = repo_dir / "prs"
            if not prs_dir.is_dir():
                continue
            for pr_dir in sorted(prs_dir.iterdir()):
                if pr_dir.is_dir():
                    yield owner_dir.name, repo_dir.name, pr_dir


def compute_ftc_pr_numbers(data_dir):
    """Returns frozenset of (owner, repo, number) for each author's first PR across all repos."""
    first_by_author = {}
    for owner, repo, pr_dir in _iter_repo_prs_dirs(data_dir):
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
            key = (owner, repo, number)
            if author not in first_by_author or created_at < first_by_author[author][0]:
                first_by_author[author] = (created_at, key)
        except (json.JSONDecodeError, KeyError):
            continue
    return frozenset(key for _, key in first_by_author.values())


def load_resolved(data_dir):
    ftc_pr_numbers = compute_ftc_pr_numbers(data_dir)
    resolved = []
    for owner, repo, pr_dir in _iter_repo_prs_dirs(data_dir):
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
        engagement = time_to_engagement_days(events)
        owner_repo = f"{owner}/{repo}"
        resolved.append((*result, (owner, repo, pr_number) in ftc_pr_numbers, engagement, pr_number, owner_repo))
    return resolved


def _fmt(v):
    return "—" if v is None else f"{v}d"


def _age_class(age_days):
    if age_days < 7:
        return "age-fresh"
    elif age_days < 30:
        return "age-moderate"
    elif age_days < 90:
        return "age-old"
    return "age-stale"


def load_open_prs(data_dir, ftc_pr_numbers, committers, now_dt):
    """Returns open PRs sorted by tier then oldest-first, excluding drafts, each as a dict."""
    open_prs = []
    for owner, repo, pr_dir in _iter_repo_prs_dirs(data_dir):
        metadata_path = pr_dir / "metadata.json"
        events_path   = pr_dir / "events.json"
        if not metadata_path.exists():
            continue
        try:
            meta = json.loads(metadata_path.read_text())
        except json.JSONDecodeError:
            continue
        if meta.get("state") != "open":
            continue
        if meta.get("draft", False):
            continue
        try:
            pr_number = int(pr_dir.name)
        except ValueError:
            continue
        created_at = meta.get("created_at", "")
        if not created_at:
            continue
        events = []
        if events_path.exists():
            try:
                events = json.loads(events_path.read_text())
            except json.JSONDecodeError:
                pass
        author = meta.get("author", "")
        age_days = (now_dt - _parse_ts(created_at)).total_seconds() / 86400
        is_bot = _is_bot(author)
        is_ftc = (owner, repo, pr_number) in ftc_pr_numbers
        is_committer = (author in committers) and not is_bot
        engagement_days = time_to_engagement_days(events)
        linked_issues = _load_linked_issues(
            data_dir / owner / repo, meta.get("linked_issues", [])
        )
        open_prs.append({
            "number":          pr_number,
            "title":           meta.get("title", ""),
            "author":          author,
            "repo":            f"{owner}/{repo}",
            "age_days":        round(age_days, 1),
            "is_bot":          is_bot,
            "is_ftc":          is_ftc,
            "is_committer":    is_committer,
            "engagement_days": engagement_days,
            "linked_issues":   linked_issues,
        })

    def _tier(p):
        if p["is_bot"]:
            return 3
        if p["is_ftc"]:
            return 0  # first-time contributors surface first
        if not p["is_committer"]:
            return 1  # other non-committer humans
        return 2

    open_prs.sort(key=lambda p: (_tier(p), -p["age_days"]))
    return open_prs


def _load_linked_issues(repo_dir, issue_numbers):
    """Returns list of {number, title, state} for each resolvable linked issue number."""
    result = []
    for n in issue_numbers:
        path = repo_dir / "issues" / str(n) / "metadata.json"
        if not path.exists():
            continue
        try:
            meta = json.loads(path.read_text())
            result.append({
                "number": n,
                "title": meta.get("title", ""),
                "state": meta.get("state", ""),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return result


_ANCIENT_DAYS = 90


def _tier_card_class(pr):
    if pr["is_bot"]:
        return "pr-card--bot"
    if pr["is_ftc"]:
        return "pr-card--ftc"
    if not pr["is_committer"]:
        return "pr-card--external"
    return "pr-card--committer"


def _linked_issues_html(linked_issues, repo):
    if not linked_issues:
        return ""
    items = []
    for issue in linked_issues:
        n = issue["number"]
        issue_url = f"https://github.com/{repo}/issues/{n}"
        state = issue.get("state", "")
        state_label = f" ({state})" if state else ""
        items.append(
            f'<span><a href="{issue_url}">Issue #{n}</a>: '
            f'<a href="{issue_url}">{html_lib.escape(issue["title"])}</a>'
            f'{html_lib.escape(state_label)}</span>'
        )
    return '<div class="pr-card-linked">' + " ".join(items) + "</div>"


def _open_prs_html(open_prs):
    if not open_prs:
        return '<p class="no-data">No open PRs found in harvested data.</p>'

    cards = []
    for pr in open_prs:
        badge_spans = []
        if pr["age_days"] < 1.0:
            badge_spans.append('<span title="opened in the last 24 hours">⭐</span>')
        if pr["is_bot"]:
            badge_spans.append('<span title="bot">🤖</span>')
        else:
            if pr["is_ftc"]:
                badge_spans.append('<span title="first-time contributor">🌱</span>')
            elif not pr["is_committer"]:
                badge_spans.append('<span title="non-committer">👤</span>')
        if pr["engagement_days"] is None:
            badge_spans.append('<span title="no engagement yet">👀</span>')

        badges_html = " ".join(badge_spans)
        title = html_lib.escape(pr["title"])
        repo = pr.get("repo", "")
        url = f"https://github.com/{repo}/pull/{pr['number']}" if repo else "#"
        eng_str = _fmt(round(pr["engagement_days"], 1)) if pr["engagement_days"] is not None else "—"
        ancient_attr = ' data-ancient="true"' if pr["age_days"] >= _ANCIENT_DAYS else ""
        tier_class = _tier_card_class(pr)
        author = pr["author"]
        author_url = f"https://github.com/{author}"

        if not pr["is_bot"]:
            avatar_style = f' style="--avatar-url: url(\'https://github.com/{author}.png\')"'
        else:
            avatar_style = ""

        repo_tag = f'<div class="pr-repo-tag">{html_lib.escape(repo)}</div>' if repo else ""
        repo_attr = f' data-repo-filter="{html_lib.escape(repo)}"' if repo else ""
        linked_html = _linked_issues_html(pr.get("linked_issues", []), repo)
        cards.append(
            f'<div class="pr-card {tier_class}"{ancient_attr}{repo_attr}{avatar_style}>'
            f'<div class="pr-card-number"><a href="{url}">#{pr["number"]}</a></div>'
            f'<div class="pr-card-title"><a href="{url}">{title}</a></div>'
            f'<div class="pr-card-author"><a href="{author_url}">@{html_lib.escape(author)}</a></div>'
            f'{repo_tag}'
            f'<div class="pr-card-badges">{badges_html}</div>'
            f'{linked_html}'
            f'<div class="pr-card-meta">'
            f'<span class="age">{_fmt(pr["age_days"])} old</span>'
            f' &middot; engaged {eng_str}'
            f'</div>'
            f'</div>'
        )

    distinct_repos = list(dict.fromkeys(pr.get("repo", "") for pr in open_prs if pr.get("repo")))
    chips_html = ""
    if len(distinct_repos) > 1:
        chip_items = ['<button class="repo-chip active" data-repo="">All</button>']
        for r in distinct_repos:
            label = r.split("/")[-1] if "/" in r else r
            chip_items.append(
                f'<button class="repo-chip" data-repo="{html_lib.escape(r)}">'
                f'{html_lib.escape(label)}</button>'
            )
        chips_html = '<div class="repo-chips">' + "".join(chip_items) + "</div>"

    controls = (
        '<div class="pr-controls">'
        '<input type="checkbox" id="hide-ancient" checked>'
        '<label for="hide-ancient">Hide PRs older than 90 days</label>'
        '</div>'
    )
    legend = (
        '<div class="legend">'
        '<span class="legend-item">🌱 first-time contributor</span>'
        '<span class="legend-item">👤 non-committer</span>'
        '<span class="legend-item">🤖 bot</span>'
        '<span class="legend-item">👀 no engagement yet</span>'
        '<span class="legend-item">⭐ opened in the last 24 hours</span>'
        '</div>'
    )
    return (
        chips_html
        + controls
        + '<div class="pr-grid" id="pr-grid-open">'
        + "".join(cards)
        + '</div>'
        + legend
    )


def _stat_group_html(label, dist, color_class):
    parts = []
    for name, key in [("Median", "median"), ("Mean", "mean"), ("p95", "p95"), ("p99", "p99"), ("Max", "max")]:
        val = _fmt(dist[key])
        if key == "max" and dist.get("max_pr_url"):
            val = f'<a class="stat-link" href="{dist["max_pr_url"]}">{val}</a>'
        parts.append(
            f'<div class="stat"><span class="stat-name">{name}</span>'
            f'<span class="stat-value">{val}</span></div>'
        )
    return (
        f'<div class="stat-group stat-group--{color_class}">'
        f'<div class="stat-group-label">{label}</div>'
        f'<div class="stat-row">{"".join(parts)}</div>'
        f'</div>'
    )


def _kpi_row_html(stats):
    resolution_group = _stat_group_html("Time to resolution", stats["resolution"], "resolution")
    ftc_tile = (
        f'<div class="kpi">'
        f'<div class="kpi-value ftc">{stats["ftc_count"]}</div>'
        f'<div class="kpi-label">First-time contributor PRs</div>'
        f'<div class="kpi-sub">{stats["ftc_pct"]}% of {stats["total_resolved"]} resolved</div>'
        f'</div>'
    )
    engagement_group = _stat_group_html("Time to first engagement", stats["engagement"], "engagement")
    return f'<div class="kpi-row">{resolution_group}{ftc_tile}{engagement_group}</div>'


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


def _trend_chart_js(canvas_id, labels, medians, means, p95s, p99s, maxes):
    if not labels:
        return ""
    d = json.dumps
    pt = "pointRadius:4,pointHoverRadius:7"

    def ds(label, color, data, extra=""):
        return (
            f"{{label:{d(label)},data:{d(list(data))},"
            f"borderColor:{color},borderWidth:2,pointBackgroundColor:{color},{pt},"
            f"fill:false,tension:0.2,spanGaps:false{extra}}}"
        )

    datasets = ",".join([
        ds("Median", "C.s1", medians),
        ds("Mean",   "C.s2", means),
        ds("p95",    "C.s3", p95s),
        ds("p99",    "C.s4", p99s),
        ds("Max",    "C.s5", maxes, ",borderDash:[4,3]"),
    ])
    return (
        f"new Chart(document.getElementById({d(canvas_id)}), {{"
        f"type:'line',data:{{labels:{d(list(labels))},datasets:[{datasets}]}},"
        f"options:{{responsive:true,maintainAspectRatio:false,"
        f"plugins:{{legend:{{display:true,labels:{{color:C.ink2}}}},"
        f"tooltip:{{callbacks:{{label:ctx=>ctx.dataset.label+': '+ctx.parsed.y+' days'}}}}}},"
        f"scales:baseScales}}}});"
    )


def _tab_content_html(stats, hist_title, res_trend_title, eng_trend_title,
                      hist_id, res_trend_id, eng_trend_id):
    has_res_trend = stats["total_resolved"] and bool(stats["trend_labels"])
    has_eng_trend = stats["total_resolved"] and bool(stats["eng_trend_labels"])
    return (
        f"{_kpi_row_html(stats)}"
        f'<div class="panel"><h2>{hist_title}</h2>'
        f"{_chart_area_html(hist_id, stats['total_resolved'])}</div>"
        f'<div class="panel"><h2>{res_trend_title}</h2>'
        f"{_chart_area_html(res_trend_id, has_res_trend)}</div>"
        f'<div class="panel"><h2>{eng_trend_title}</h2>'
        f"{_chart_area_html(eng_trend_id, has_eng_trend)}</div>"
    )


def render_html(all_stats, recent_stats, generated_at, open_prs=None):
    recent_hist_id      = "hist-recent"
    recent_trend_id     = "trend-recent"
    recent_eng_trend_id = "eng-trend-recent"
    all_hist_id         = "hist-alltime"
    all_trend_id        = "trend-alltime"
    all_eng_trend_id    = "eng-trend-alltime"

    recent_content = _tab_content_html(
        recent_stats,
        "Resolution time distribution",
        "Weekly resolution time (days)",
        "Weekly time to first engagement (days)",
        recent_hist_id, recent_trend_id, recent_eng_trend_id,
    )
    all_content = _tab_content_html(
        all_stats,
        "Resolution time distribution",
        "Monthly resolution time (days)",
        "Monthly time to first engagement (days)",
        all_hist_id, all_trend_id, all_eng_trend_id,
    )

    def _tab_js(s, hist_id, res_id, eng_id):
        return (
            _hist_js(hist_id, s)
            + _trend_chart_js(res_id, s["trend_labels"], s["trend_medians"],
                              s["trend_means"], s["trend_p95s"], s["trend_p99s"], s["trend_maxes"])
            + _trend_chart_js(eng_id, s["eng_trend_labels"], s["eng_trend_medians"],
                              s["eng_trend_means"], s["eng_trend_p95s"], s["eng_trend_p99s"], s["eng_trend_maxes"])
        )

    recent_js = _tab_js(recent_stats, recent_hist_id, recent_trend_id, recent_eng_trend_id)
    all_js    = _tab_js(all_stats, all_hist_id, all_trend_id, all_eng_trend_id)

    open_js = (
        "const cb=document.getElementById('hide-ancient'),"
        "g=document.getElementById('pr-grid-open');"
        "if(cb&&g){"
        "g.classList.add('hide-ancient');"
        "cb.addEventListener('change',()=>g.classList.toggle('hide-ancient',cb.checked));}"
        "document.querySelectorAll('.repo-chip').forEach(chip=>{"
        "chip.addEventListener('click',()=>{"
        "document.querySelectorAll('.repo-chip').forEach(c=>c.classList.remove('active'));"
        "chip.classList.add('active');"
        "const r=chip.dataset.repo;"
        "if(r){g.setAttribute('data-repo-filter',r);"
        "g.querySelectorAll('.pr-card').forEach(c=>{"
        "c.style.display=c.dataset.repoFilter===r?'':'none';});}"
        "else{g.removeAttribute('data-repo-filter');"
        "g.querySelectorAll('.pr-card').forEach(c=>{c.style.display='';});}});});"
    )
    chart_js = (
        f"if (name === 'recent') {{ {recent_js} }}"
        f" else if (name === 'alltime') {{ {all_js} }}"
        f" else if (name === 'open') {{ {open_js} }}"
    )

    open_prs_content = _open_prs_html(open_prs or [])

    return (
        _HTML
        .replace("__GENERATED_AT__", generated_at)
        .replace("__RECENT_CONTENT__", recent_content)
        .replace("__ALL_CONTENT__", all_content)
        .replace("__OPEN_PRS_CONTENT__", open_prs_content)
        .replace("__CHART_JS__", chart_js)
    )


def build_site(data_dir, site_dir, generated_at, committers=frozenset()):
    resolved = load_resolved(data_dir)
    recent_cutoff = datetime.now(UTC) - timedelta(days=RECENT_DAYS)
    recent_resolved = filter_resolved_since(resolved, recent_cutoff)

    all_stats    = compute_stats(resolved,        monthly_stats_trend, monthly_engagement_trend)
    recent_stats = compute_stats(recent_resolved, weekly_stats_trend,   weekly_engagement_trend)

    ftc_pr_numbers = compute_ftc_pr_numbers(data_dir)
    now_dt = datetime.now(UTC)
    open_prs = load_open_prs(data_dir, ftc_pr_numbers, committers, now_dt)

    html = render_html(all_stats, recent_stats, generated_at, open_prs)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(html)
    return {"all": all_stats, "recent": recent_stats, "open_prs": open_prs}


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
        f"Site built. All-time: {a['total_resolved']} PRs, median {a['resolution']['median']} days, "
        f"{a['ftc_count']} FTC. Last 3 months: {r['total_resolved']} PRs, median {r['resolution']['median']} days."
    )


if __name__ == "__main__":
    main()
