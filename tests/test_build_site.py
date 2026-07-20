import json
from datetime import datetime, timedelta, timezone

import pytest

from build_site import (
    _age_class,
    _dist_stats,
    _iter_repo_prs_dirs,
    _open_prs_html,
    _percentile,
    compute_ftc_pr_numbers,
    compute_stats,
    filter_resolved_since,
    histogram,
    load_committers,
    load_open_prs,
    monthly_engagement_trend,
    monthly_stats_trend,
    render_html,
    resolution_time_days,
    time_to_engagement_days,
    weekly_engagement_trend,
    weekly_stats_trend,
    build_site,
)

COMMITTERS = frozenset({"k-wall", "robobario"})

UTC = timezone.utc


def _events(created, closed_type=None, closed=None):
    evts = [{"type": "created", "timestamp": created, "actor": "alice"}]
    if closed_type:
        evts.append({"type": closed_type, "timestamp": closed, "actor": "bob"})
    return evts


# --- resolution_time_days ---


def test_resolution_time_days_merged():
    events = _events("2024-01-10T10:00:00Z", "closed_merged", "2024-01-15T10:00:00Z")
    result = resolution_time_days(events)
    assert result is not None
    close_dt, days = result
    assert days == pytest.approx(5.0)
    assert close_dt.year == 2024 and close_dt.month == 1 and close_dt.day == 15


def test_resolution_time_days_unmerged():
    events = _events("2024-01-10T10:00:00Z", "closed_unmerged", "2024-01-12T10:00:00Z")
    _, days = resolution_time_days(events)
    assert days == pytest.approx(2.0)


def test_resolution_time_days_open():
    events = _events("2024-01-10T10:00:00Z")
    assert resolution_time_days(events) is None


def test_resolution_time_days_no_created():
    events = [{"type": "closed_merged", "timestamp": "2024-01-15T10:00:00Z", "actor": "bob"}]
    assert resolution_time_days(events) is None


def test_resolution_time_days_uses_first_close():
    events = [
        {"type": "created",        "timestamp": "2024-01-10T00:00:00Z", "actor": "a"},
        {"type": "closed_unmerged","timestamp": "2024-01-12T00:00:00Z", "actor": "b"},
        {"type": "closed_merged",  "timestamp": "2024-01-20T00:00:00Z", "actor": "c"},
    ]
    _, days = resolution_time_days(events)
    assert days == pytest.approx(2.0)


def test_resolution_time_days_sub_day():
    events = _events("2024-01-10T08:00:00Z", "closed_merged", "2024-01-10T20:00:00Z")
    _, days = resolution_time_days(events)
    assert days == pytest.approx(0.5)


# --- time_to_engagement_days ---


def _engagement_events(created, reviews=(), comments=()):
    evts = [{"type": "created", "timestamp": created, "actor": "alice"}]
    for ts, actor in reviews:
        evts.append({"type": "reviewed", "timestamp": ts, "actor": actor})
    for ts, actor in comments:
        evts.append({"type": "comment", "timestamp": ts, "actor": actor})
    return sorted(evts, key=lambda e: e["timestamp"])


def test_time_to_engagement_any_human_review():
    events = _engagement_events("2024-01-10T10:00:00Z", reviews=[("2024-01-12T10:00:00Z", "somereviewer")])
    assert time_to_engagement_days(events) == pytest.approx(2.0)


def test_time_to_engagement_any_human_comment():
    events = _engagement_events("2024-01-10T10:00:00Z", comments=[("2024-01-11T10:00:00Z", "bob")])
    assert time_to_engagement_days(events) == pytest.approx(1.0)


def test_time_to_engagement_uses_first_qualifying():
    events = _engagement_events(
        "2024-01-10T10:00:00Z",
        comments=[("2024-01-11T10:00:00Z", "bob"), ("2024-01-13T10:00:00Z", "carol")],
    )
    assert time_to_engagement_days(events) == pytest.approx(1.0)


def test_time_to_engagement_author_self_comment_not_counted():
    events = _engagement_events(
        "2024-01-10T10:00:00Z",
        comments=[("2024-01-11T10:00:00Z", "alice")],  # alice is the PR author
    )
    assert time_to_engagement_days(events) is None


def test_time_to_engagement_self_then_other_uses_other():
    events = _engagement_events(
        "2024-01-10T10:00:00Z",
        comments=[
            ("2024-01-11T10:00:00Z", "alice"),  # self — skip
            ("2024-01-13T10:00:00Z", "bob"),    # other human — counts
        ],
    )
    assert time_to_engagement_days(events) == pytest.approx(3.0)


def test_time_to_engagement_bot_ignored():
    events = _engagement_events("2024-01-10T10:00:00Z", reviews=[("2024-01-12T10:00:00Z", "dependabot[bot]")])
    assert time_to_engagement_days(events) is None


def test_time_to_engagement_sonatype_bot_ignored():
    events = _engagement_events("2024-01-10T10:00:00Z", comments=[("2024-01-11T10:00:00Z", "sonatype-nexus-community[bot]")])
    assert time_to_engagement_days(events) is None


def test_time_to_engagement_bot_then_human_uses_human():
    events = _engagement_events(
        "2024-01-10T10:00:00Z",
        comments=[
            ("2024-01-11T10:00:00Z", "renovate[bot]"),  # bot — skip
            ("2024-01-13T10:00:00Z", "bob"),             # human — counts
        ],
    )
    assert time_to_engagement_days(events) == pytest.approx(3.0)


def test_time_to_engagement_no_reviews():
    events = _events("2024-01-10T10:00:00Z", "closed_merged", "2024-01-15T10:00:00Z")
    assert time_to_engagement_days(events) is None


# --- histogram ---


def test_histogram_buckets_correct():
    days = [0.5, 2.0, 5.0, 10.0, 20.0, 45.0, 90.0]
    labels, counts = histogram(days)
    assert labels == ["<1d", "1-3d", "3-7d", "7-14d", "14-30d", "30-60d", "60d+"]
    assert counts == [1, 1, 1, 1, 1, 1, 1]


def test_histogram_boundary_lo_inclusive():
    _, counts = histogram([1.0])
    assert counts[1] == 1


def test_histogram_boundary_hi_exclusive():
    _, counts = histogram([3.0])
    assert counts[2] == 1


def test_histogram_empty():
    labels, counts = histogram([])
    assert len(labels) == 7
    assert all(c == 0 for c in counts)


def test_histogram_accumulates():
    _, counts = histogram([0.5, 0.9, 1.1])
    assert counts[0] == 2
    assert counts[1] == 1


# --- monthly_stats_trend ---


def test_monthly_stats_trend_single_month():
    resolved = [
        (datetime(2024, 1, 10, tzinfo=UTC), 3.0, False, None),
        (datetime(2024, 1, 20, tzinfo=UTC), 5.0, False, None),
    ]
    labels, medians, means, p95s, p99s, maxes = monthly_stats_trend(resolved)
    assert labels == ["2024-01"]
    assert medians == [4.0]
    assert means == [4.0]
    assert maxes == [5.0]


def test_monthly_stats_trend_multiple_months():
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 4.0, False, None),
        (datetime(2024, 2, 10, tzinfo=UTC), 6.0, False, None),
    ]
    labels, medians, *_ = monthly_stats_trend(resolved)
    assert labels == ["2024-01", "2024-02"]
    assert medians == [4.0, 6.0]


def test_monthly_stats_trend_empty():
    labels, medians, means, p95s, p99s, maxes = monthly_stats_trend([])
    assert labels == []
    assert list(medians) == []


# --- weekly_stats_trend ---


def test_weekly_stats_trend_groups_by_monday():
    # Jan 15 (Mon) and Jan 17 (Wed) are in the same week
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 3.0, False, None),
        (datetime(2024, 1, 17, tzinfo=UTC), 5.0, False, None),
    ]
    labels, medians, means, *_ = weekly_stats_trend(resolved)
    assert labels == ["Jan 15"]
    assert medians == [4.0]


def test_weekly_stats_trend_multiple_weeks():
    resolved = [
        (datetime(2024, 1, 8,  tzinfo=UTC), 4.0, False, None),  # Mon Jan 8
        (datetime(2024, 1, 15, tzinfo=UTC), 6.0, False, None),  # Mon Jan 15
        (datetime(2024, 1, 17, tzinfo=UTC), 2.0, False, None),  # Wed Jan 17 → week of Jan 15
    ]
    labels, medians, *_ = weekly_stats_trend(resolved)
    assert labels == ["Jan 08", "Jan 15"]
    assert medians[0] == 4.0
    assert medians[1] == 4.0  # median of [6.0, 2.0]


def test_weekly_stats_trend_empty():
    labels, medians, *_ = weekly_stats_trend([])
    assert labels == []


def test_weekly_stats_trend_sunday_in_prior_week():
    resolved = [
        (datetime(2024, 1, 14, tzinfo=UTC), 1.0, False, None),  # Sun → week of Jan 8
        (datetime(2024, 1, 15, tzinfo=UTC), 2.0, False, None),  # Mon → week of Jan 15
    ]
    labels, *_ = weekly_stats_trend(resolved)
    assert labels == ["Jan 08", "Jan 15"]


# --- monthly/weekly_engagement_trend ---


def test_monthly_engagement_trend_skips_none():
    resolved = [
        (datetime(2024, 1, 10, tzinfo=UTC), 5.0, False, None,  1),  # no engagement
        (datetime(2024, 1, 20, tzinfo=UTC), 3.0, False, 2.0,   2),
        (datetime(2024, 1, 25, tzinfo=UTC), 4.0, False, 4.0,   3),
    ]
    labels, medians, means, *_ = monthly_engagement_trend(resolved)
    assert labels == ["2024-01"]
    assert medians == [3.0]   # median of [2.0, 4.0]
    assert means   == [3.0]   # mean of [2.0, 4.0]


def test_monthly_engagement_trend_empty_when_no_engagement():
    resolved = [(datetime(2024, 1, 10, tzinfo=UTC), 5.0, False, None, 1)]
    labels, *_ = monthly_engagement_trend(resolved)
    assert labels == []


def test_weekly_engagement_trend_groups_by_close_date():
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, 1.0, 1),  # Mon Jan 15
        (datetime(2024, 1, 17, tzinfo=UTC), 3.0, False, 3.0, 2),  # Wed Jan 15 week
        (datetime(2024, 1, 22, tzinfo=UTC), 4.0, False, 2.0, 3),  # Mon Jan 22
    ]
    labels, medians, *_ = weekly_engagement_trend(resolved)
    assert labels == ["Jan 15", "Jan 22"]
    assert medians[0] == 2.0   # median of [1.0, 3.0]
    assert medians[1] == 2.0


# --- filter_resolved_since ---


def test_filter_resolved_since_excludes_old():
    cutoff = datetime(2024, 2, 1, tzinfo=UTC)
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, None),
        (datetime(2024, 2, 10, tzinfo=UTC), 3.0, False, None),
    ]
    result = filter_resolved_since(resolved, cutoff)
    assert len(result) == 1
    assert result[0][0] == datetime(2024, 2, 10, tzinfo=UTC)


def test_filter_resolved_since_includes_on_boundary():
    cutoff = datetime(2024, 2, 1, tzinfo=UTC)
    resolved = [(datetime(2024, 2, 1, tzinfo=UTC), 5.0, False, None)]
    assert len(filter_resolved_since(resolved, cutoff)) == 1


def test_filter_resolved_since_empty_input():
    assert filter_resolved_since([], datetime(2024, 1, 1, tzinfo=UTC)) == []


def test_filter_resolved_since_all_excluded():
    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    resolved = [(datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, None)]
    assert filter_resolved_since(resolved, cutoff) == []


# --- _percentile and _dist_stats ---


def test_percentile_midpoint():
    assert _percentile([0.0, 10.0], 50) == pytest.approx(5.0)


def test_percentile_p95_small_list():
    vals = sorted(range(1, 21))  # 1..20; p95 idx=18.05 → 19 + 0.05*(20-19) = 19.05 → 19.1
    result = _percentile(vals, 95)
    assert result == pytest.approx(19.1, abs=0.05)


def test_percentile_single_value():
    assert _percentile([7.0], 99) == pytest.approx(7.0)


def test_dist_stats_empty():
    d = _dist_stats([])
    assert all(v is None for v in d.values())


def test_dist_stats_values():
    d = _dist_stats([2.0, 4.0, 6.0, 8.0, 10.0])
    assert d["median"] == pytest.approx(6.0)
    assert d["mean"]   == pytest.approx(6.0)
    assert d["max"]    == pytest.approx(10.0)
    assert d["p95"] is not None
    assert d["p99"] is not None
    assert d["max_pr_url"] is None  # no pr_urls provided


def test_dist_stats_max_pr_url():
    d = _dist_stats([5.0, 10.0, 3.0], pr_urls=["u42", "u99", "u7"])
    assert d["max"]        == pytest.approx(10.0)
    assert d["max_pr_url"] == "u99"


# --- compute_stats ---


def test_compute_stats_empty():
    stats = compute_stats([])
    assert stats["total_resolved"] == 0
    assert stats["resolution"]["median"] is None
    assert stats["engagement"]["median"] is None
    assert stats["ftc_count"] == 0
    assert stats["ftc_pct"] == 0
    assert all(c == 0 for c in stats["hist_counts"])
    assert stats["trend_labels"] == []


def test_compute_stats_with_data():
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, 1.0, 10, "org/a"),
        (datetime(2024, 1, 20, tzinfo=UTC), 3.0, True,  None, 11, "org/a"),
        (datetime(2024, 2, 5,  tzinfo=UTC), 10.0, False, 2.0, 12, "org/b"),
    ]
    stats = compute_stats(resolved)
    assert stats["total_resolved"] == 3
    assert stats["resolution"]["median"] == 5.0
    assert stats["resolution"]["max"] == 10.0
    assert stats["resolution"]["max_pr_url"] == "https://github.com/org/b/pull/12"
    assert stats["ftc_count"] == 1
    assert stats["ftc_pct"] == 33
    assert stats["engagement"]["median"] == 1.5
    assert stats["engagement"]["max_pr_url"] == "https://github.com/org/b/pull/12"
    assert len(stats["hist_labels"]) == 7
    assert len(stats["trend_labels"]) == 2


def test_compute_stats_uses_provided_trend_fn():
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, None, 1, "org/r"),
        (datetime(2024, 1, 22, tzinfo=UTC), 3.0, False, None, 2, "org/r"),
    ]
    stats = compute_stats(resolved, trend_fn=weekly_stats_trend)
    assert len(stats["trend_labels"]) == 2
    assert "-" not in stats["trend_labels"][0]


def test_compute_stats_trend_has_all_series():
    resolved = [(datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, None, 1, "org/r")]
    stats = compute_stats(resolved)
    for key in ("trend_medians", "trend_means", "trend_p95s", "trend_p99s", "trend_maxes"):
        assert key in stats
        assert len(stats[key]) == 1


def test_compute_stats_engagement_trend_populated():
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, 2.0, 1, "org/r"),
        (datetime(2024, 2, 5,  tzinfo=UTC), 3.0, False, 1.0, 2, "org/r"),
    ]
    stats = compute_stats(resolved)
    for key in ("eng_trend_labels", "eng_trend_medians", "eng_trend_means",
                "eng_trend_p95s", "eng_trend_p99s", "eng_trend_maxes"):
        assert key in stats
    assert len(stats["eng_trend_labels"]) == 2


def test_compute_stats_engagement_trend_empty_when_no_engagement():
    resolved = [(datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, None, 1, "org/r")]
    stats = compute_stats(resolved)
    assert stats["eng_trend_labels"] == []


def test_compute_stats_no_engagement():
    resolved = [(datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, None, 1, "org/r")]
    assert compute_stats(resolved)["engagement"]["median"] is None


def test_compute_stats_all_ftc():
    resolved = [(datetime(2024, 1, 15, tzinfo=UTC), 4.0, True, 0.5, 1, "org/r")]
    stats = compute_stats(resolved)
    assert stats["ftc_count"] == 1
    assert stats["ftc_pct"] == 100


# --- render_html ---


def _dist(median=5.0, mean=6.0, p95=12.0, p99=20.0, max_=25.0, max_pr_url=None):
    return {"median": median, "mean": mean, "p95": p95, "p99": p99, "max": max_, "max_pr_url": max_pr_url}


def _stats(total=10, ftc_count=2, resolution=None, engagement=None,
           hist_counts=None, trend_labels=None,
           trend_medians=None, trend_means=None,
           trend_p95s=None, trend_p99s=None, trend_maxes=None,
           eng_trend_labels=None):
    labels = ["<1d", "1-3d", "3-7d", "7-14d", "14-30d", "30-60d", "60d+"]
    tl  = trend_labels     or ["2024-01", "2024-02"]
    etl = eng_trend_labels or ["2024-01"]
    return {
        "total_resolved": total,
        "ftc_count": ftc_count,
        "ftc_pct": round(100 * ftc_count / total) if total else 0,
        "resolution": resolution or _dist(),
        "engagement": engagement or _dist(median=3.0, mean=4.0, p95=8.0, p99=15.0, max_=20.0),
        "hist_labels": labels,
        "hist_counts": hist_counts or [0, 2, 5, 2, 1, 0, 0],
        "trend_labels":  tl,
        "trend_medians": trend_medians or [4.0] * len(tl),
        "trend_means":   trend_means   or [5.0] * len(tl),
        "trend_p95s":    trend_p95s    or [10.0] * len(tl),
        "trend_p99s":    trend_p99s    or [15.0] * len(tl),
        "trend_maxes":   trend_maxes   or [20.0] * len(tl),
        "eng_trend_labels":   etl,
        "eng_trend_medians":  [2.0] * len(etl),
        "eng_trend_means":    [2.5] * len(etl),
        "eng_trend_p95s":     [5.0] * len(etl),
        "eng_trend_p99s":     [8.0] * len(etl),
        "eng_trend_maxes":    [10.0] * len(etl),
    }


def test_render_html_has_both_tabs():
    html = render_html(_stats(), _stats(), "2024-01-15T12:00:00Z")
    assert "Last 3 months" in html
    assert "All time" in html


def test_render_html_shows_resolution_stats():
    html = render_html(_stats(resolution=_dist(median=7.5, p95=20.0)), _stats(), "2024-01-15T12:00:00Z")
    assert "7.5d" in html
    assert "20.0d" in html


def test_render_html_shows_engagement_stats():
    html = render_html(_stats(engagement=_dist(median=2.5, max_=30.0)), _stats(), "2024-01-15T12:00:00Z")
    assert "2.5d" in html
    assert "30.0d" in html


def test_render_html_shows_stat_group_labels():
    html = render_html(_stats(), _stats(), "2024-01-15T12:00:00Z")
    assert "Time to resolution" in html
    assert "Time to first engagement" in html


def test_render_html_shows_all_stat_names():
    html = render_html(_stats(), _stats(), "2024-01-15T12:00:00Z")
    for name in ("Median", "Mean", "p95", "p99", "Max"):
        assert name in html


def test_render_html_has_engagement_trend_panel():
    html = render_html(_stats(), _stats(), "2024-01-15T12:00:00Z")
    assert "time to first engagement" in html.lower()
    assert "eng-trend-recent" in html
    assert "eng-trend-alltime" in html


def test_render_html_shows_generated_at():
    html = render_html(_stats(), _stats(), "2024-01-15T12:00:00Z")
    assert "2024-01-15T12:00:00Z" in html


def test_render_html_no_data_shows_placeholder():
    empty = _stats(total=0, ftc_count=0,
                   resolution=_dist(median=None, mean=None, p95=None, p99=None, max_=None),
                   engagement=_dist(median=None, mean=None, p95=None, p99=None, max_=None),
                   hist_counts=[0]*7, trend_labels=[],
                   trend_medians=[], trend_means=[], trend_p95s=[], trend_p99s=[], trend_maxes=[],
                   eng_trend_labels=[])
    html = render_html(empty, empty, "2024-01-15T12:00:00Z")
    assert "No data yet" in html
    assert "<canvas" not in html


def test_render_html_with_data_has_canvas():
    html = render_html(_stats(), _stats(), "2024-01-15T12:00:00Z")
    assert "<canvas" in html


def test_render_html_lazy_chart_init():
    html = render_html(_stats(), _stats(), "2024-01-15T12:00:00Z")
    assert "initTab" in html
    assert "initialized" in html


def test_render_html_trend_has_five_series():
    html = render_html(_stats(), _stats(), "2024-01-15T12:00:00Z")
    for name in ("Median", "Mean", "p95", "p99", "Max"):
        assert f'"label":"{name}"' in html or f"label:{json.dumps(name)}" in html


def test_render_html_embeds_both_tabs_data():
    html = render_html(
        _stats(trend_labels=["2024-01"]),
        _stats(trend_labels=["Jan 13"]),
        "2024-01-15T12:00:00Z",
    )
    assert '"2024-01"' in html
    assert '"Jan 13"' in html


def test_render_html_max_links_to_pr():
    url = "https://github.com/org/repo/pull/42"
    stats = _stats(resolution=_dist(max_pr_url=url))
    html = render_html(stats, stats, "2024-01-15T12:00:00Z")
    assert url in html


def test_render_html_max_no_link_when_max_pr_url_none():
    stats = _stats(resolution=_dist(max_pr_url=None))
    html = render_html(stats, stats, "2024-01-15T12:00:00Z")
    assert 'class="stat-link"' not in html


# --- build_site (integration) ---


def _write_pr(pr_dir, created, closed_type, closed, author="alice", reviews=()):
    pr_dir.mkdir(parents=True, exist_ok=True)
    number = int(pr_dir.name)
    events = [{"type": "created", "timestamp": created, "actor": author}]
    for ts, actor in reviews:
        events.append({"type": "reviewed", "timestamp": ts, "actor": actor})
    events.append({"type": closed_type, "timestamp": closed, "actor": "merger"})
    events.sort(key=lambda e: e["timestamp"])
    (pr_dir / "events.json").write_text(json.dumps(events))
    (pr_dir / "metadata.json").write_text(json.dumps({
        "number": number, "title": "PR", "author": author,
        "labels": [], "target_branch": "main", "created_at": created,
        "state": "closed", "merged": closed_type == "closed_merged",
    }))


def _prs_dir(data_dir, owner="org", repo="repo"):
    return data_dir / owner / repo / "prs"


def test_build_site_writes_index(tmp_path):
    _write_pr(_prs_dir(tmp_path) / "1", "2024-01-10T10:00:00Z", "closed_merged", "2024-01-15T10:00:00Z")
    result = build_site(tmp_path, tmp_path / "site", "2024-01-16T00:00:00Z")
    assert (tmp_path / "site" / "index.html").exists()
    assert result["all"]["total_resolved"] == 1


def test_build_site_recent_filters_old_prs(tmp_path):
    now = datetime.now(UTC)
    recent = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_pr(_prs_dir(tmp_path) / "1", old, "closed_merged",
              (now - timedelta(days=115)).strftime("%Y-%m-%dT%H:%M:%SZ"), author="alice")
    _write_pr(_prs_dir(tmp_path) / "2", recent, "closed_merged",
              (now - timedelta(days=25)).strftime("%Y-%m-%dT%H:%M:%SZ"), author="bob")
    result = build_site(tmp_path, tmp_path / "site", "now")
    assert result["all"]["total_resolved"] == 2
    assert result["recent"]["total_resolved"] == 1


def test_build_site_engagement_computed(tmp_path):
    _write_pr(_prs_dir(tmp_path) / "1", "2024-01-10T10:00:00Z", "closed_merged",
              "2024-01-15T10:00:00Z", reviews=[("2024-01-12T10:00:00Z", "maintainer")])
    result = build_site(tmp_path, tmp_path / "site", "2024-01-16T00:00:00Z")
    assert result["all"]["engagement"]["median"] == pytest.approx(2.0)


def test_build_site_ftc_counted(tmp_path):
    _write_pr(_prs_dir(tmp_path) / "1", "2024-01-10T10:00:00Z", "closed_merged", "2024-01-15T10:00:00Z", author="alice")
    _write_pr(_prs_dir(tmp_path) / "2", "2024-01-12T10:00:00Z", "closed_merged", "2024-01-17T10:00:00Z", author="alice")
    _write_pr(_prs_dir(tmp_path) / "3", "2024-01-11T10:00:00Z", "closed_merged", "2024-01-16T10:00:00Z", author="bob")
    result = build_site(tmp_path, tmp_path / "site", "2024-01-20T00:00:00Z")
    assert result["all"]["ftc_count"] == 2
    assert result["all"]["total_resolved"] == 3


def test_build_site_no_data(tmp_path):
    _prs_dir(tmp_path).mkdir(parents=True)
    result = build_site(tmp_path, tmp_path / "site", "2024-01-16T00:00:00Z")
    assert result["all"]["total_resolved"] == 0
    assert "No data yet" in (tmp_path / "site" / "index.html").read_text()


def test_build_site_aggregates_across_repos(tmp_path):
    _write_pr(_prs_dir(tmp_path, "org", "repo-a") / "1", "2024-01-10T10:00:00Z",
              "closed_merged", "2024-01-15T10:00:00Z", author="alice")
    _write_pr(_prs_dir(tmp_path, "org", "repo-b") / "1", "2024-01-11T10:00:00Z",
              "closed_merged", "2024-01-16T10:00:00Z", author="bob")
    result = build_site(tmp_path, tmp_path / "site", "2024-01-20T00:00:00Z")
    assert result["all"]["total_resolved"] == 2


# --- compute_ftc_pr_numbers ---


def _write_meta(pr_dir, author, created_at):
    pr_dir.mkdir(parents=True, exist_ok=True)
    (pr_dir / "metadata.json").write_text(json.dumps({
        "number": int(pr_dir.name), "title": "PR", "author": author,
        "labels": [], "target_branch": "main", "created_at": created_at,
        "state": "closed", "merged": True,
    }))


def test_compute_ftc_pr_numbers_first_pr_per_author(tmp_path):
    base = tmp_path / "org" / "repo" / "prs"
    _write_meta(base / "1", "alice", "2024-01-10T10:00:00Z")
    _write_meta(base / "2", "alice", "2024-01-12T10:00:00Z")
    _write_meta(base / "3", "bob",   "2024-01-11T10:00:00Z")
    result = compute_ftc_pr_numbers(tmp_path)
    assert ("org", "repo", 1) in result
    assert ("org", "repo", 2) not in result
    assert ("org", "repo", 3) in result


def test_compute_ftc_pr_numbers_cross_repo(tmp_path):
    _write_meta(tmp_path / "org" / "repo-a" / "prs" / "1", "alice", "2024-01-10T10:00:00Z")
    _write_meta(tmp_path / "org" / "repo-b" / "prs" / "5", "alice", "2024-02-01T10:00:00Z")
    result = compute_ftc_pr_numbers(tmp_path)
    assert ("org", "repo-a", 1) in result
    assert ("org", "repo-b", 5) not in result  # alice's first is in repo-a


def test_compute_ftc_pr_numbers_no_repos(tmp_path):
    assert compute_ftc_pr_numbers(tmp_path) == frozenset()


def test_compute_ftc_pr_numbers_no_metadata(tmp_path):
    (tmp_path / "org" / "repo" / "prs" / "1").mkdir(parents=True)
    assert compute_ftc_pr_numbers(tmp_path) == frozenset()


# --- _iter_repo_prs_dirs ---


def test_iter_repo_prs_dirs_yields_all(tmp_path):
    (tmp_path / "org" / "repo-a" / "prs" / "1").mkdir(parents=True)
    (tmp_path / "org" / "repo-b" / "prs" / "2").mkdir(parents=True)
    results = list(_iter_repo_prs_dirs(tmp_path))
    assert ("org", "repo-a", tmp_path / "org" / "repo-a" / "prs" / "1") in results
    assert ("org", "repo-b", tmp_path / "org" / "repo-b" / "prs" / "2") in results


def test_iter_repo_prs_dirs_empty_data_dir(tmp_path):
    assert list(_iter_repo_prs_dirs(tmp_path)) == []


# --- _age_class ---


def test_age_class_boundaries():
    assert _age_class(0)    == "age-fresh"
    assert _age_class(6.9)  == "age-fresh"
    assert _age_class(7.0)  == "age-moderate"
    assert _age_class(29.9) == "age-moderate"
    assert _age_class(30.0) == "age-old"
    assert _age_class(89.9) == "age-old"
    assert _age_class(90.0) == "age-stale"
    assert _age_class(200)  == "age-stale"


# --- load_open_prs ---


def _write_open_pr(pr_dir, created, author="alice", title="An open PR",
                   review_actor=None, number=None):
    pr_dir.mkdir(parents=True, exist_ok=True)
    n = number or int(pr_dir.name)
    events = [{"type": "created", "timestamp": created, "actor": author}]
    if review_actor:
        # add a review one day after creation (cheap engagement simulation)
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        review_ts = (_dt.fromisoformat(created.replace("Z", "+00:00")) + _td(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        events.append({"type": "reviewed", "timestamp": review_ts, "actor": review_actor})
    (pr_dir / "events.json").write_text(json.dumps(events))
    (pr_dir / "metadata.json").write_text(json.dumps({
        "number": n, "title": title, "author": author,
        "labels": [], "target_branch": "main", "created_at": created,
        "state": "open", "merged": False,
    }))


def _open_prs_base(tmp_path, owner="org", repo="repo"):
    return tmp_path / owner / repo / "prs"


def test_load_open_prs_basic(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "5", "2024-01-10T10:00:00Z")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), frozenset(), now)
    assert len(result) == 1
    assert result[0]["number"] == 5
    assert result[0]["age_days"] == pytest.approx(10.0, abs=0.5)
    assert result[0]["repo"] == "org/repo"


def test_load_open_prs_skips_closed(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "1", "2024-01-10T10:00:00Z")
    _write_pr(_open_prs_base(tmp_path) / "2", "2024-01-08T10:00:00Z", "closed_merged", "2024-01-12T10:00:00Z")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), frozenset(), now)
    assert len(result) == 1
    assert result[0]["number"] == 1


def test_load_open_prs_sorted_oldest_first_within_tier(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "1", "2024-01-05T10:00:00Z")  # older
    _write_open_pr(_open_prs_base(tmp_path) / "2", "2024-01-10T10:00:00Z")  # newer
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), frozenset(), now)
    assert result[0]["number"] == 1  # oldest non-committer first


def test_load_open_prs_ftc_before_non_committer(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "1", "2024-01-10T10:00:00Z", author="outsider")
    _write_open_pr(_open_prs_base(tmp_path) / "2", "2024-01-15T10:00:00Z", author="newbie")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, {("org", "repo", 2)}, frozenset(), now)
    assert result[0]["number"] == 2   # FTC surfaces first despite being newer
    assert result[1]["number"] == 1


def test_load_open_prs_non_committer_before_committer(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "1", "2024-01-10T10:00:00Z", author="k-wall")
    _write_open_pr(_open_prs_base(tmp_path) / "2", "2024-01-15T10:00:00Z", author="outsider")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), COMMITTERS, now)
    assert result[0]["number"] == 2   # non-committer surfaces first despite being newer
    assert result[1]["number"] == 1


def test_load_open_prs_bot_after_humans(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "1", "2024-01-01T10:00:00Z", author="dependabot[bot]")
    _write_open_pr(_open_prs_base(tmp_path) / "2", "2024-01-18T10:00:00Z", author="outsider")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), frozenset(), now)
    assert result[0]["number"] == 2   # human first
    assert result[1]["number"] == 1   # bot last despite being much older


def test_load_open_prs_ftc_flag(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "7", "2024-01-10T10:00:00Z", author="newbie")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, {("org", "repo", 7)}, frozenset(), now)
    assert result[0]["is_ftc"] is True


def test_load_open_prs_bot_flag(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "3", "2024-01-10T10:00:00Z", author="dependabot[bot]")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), frozenset(), now)
    assert result[0]["is_bot"] is True
    assert result[0]["is_ftc"] is False


def test_load_open_prs_committer_flag(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "4", "2024-01-10T10:00:00Z", author="k-wall")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), COMMITTERS, now)
    assert result[0]["is_committer"] is True


def test_load_open_prs_engagement_computed(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "6", "2024-01-10T10:00:00Z",
                   author="alice", review_actor="bob")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), frozenset(), now)
    assert result[0]["engagement_days"] == pytest.approx(1.0)


def test_load_open_prs_no_engagement(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "8", "2024-01-10T10:00:00Z")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), frozenset(), now)
    assert result[0]["engagement_days"] is None


def test_load_open_prs_draft_excluded(tmp_path):
    pr_dir = _open_prs_base(tmp_path) / "9"
    pr_dir.mkdir(parents=True)
    (pr_dir / "events.json").write_text("[]")
    (pr_dir / "metadata.json").write_text(json.dumps({
        "number": 9, "title": "WIP", "author": "alice",
        "labels": [], "target_branch": "main", "created_at": "2024-01-10T10:00:00Z",
        "state": "open", "merged": False, "draft": True,
    }))
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), frozenset(), now)
    assert result == []


def test_load_open_prs_non_draft_included(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path) / "10", "2024-01-10T10:00:00Z")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), frozenset(), now)
    assert len(result) == 1
    assert "is_draft" not in result[0]


def test_load_open_prs_aggregates_multiple_repos(tmp_path):
    _write_open_pr(_open_prs_base(tmp_path, "org", "repo-a") / "1", "2024-01-10T10:00:00Z")
    _write_open_pr(_open_prs_base(tmp_path, "org", "repo-b") / "2", "2024-01-11T10:00:00Z")
    now = datetime(2024, 1, 20, tzinfo=UTC)
    result = load_open_prs(tmp_path, frozenset(), frozenset(), now)
    repos = {pr["repo"] for pr in result}
    assert repos == {"org/repo-a", "org/repo-b"}


# --- _open_prs_html ---


def _make_pr(number=1, title="Fix it", author="alice", age_days=5.0,
             is_bot=False, is_ftc=False, is_committer=True, engagement_days=None,
             repo="o/r"):
    return {"number": number, "title": title, "author": author, "age_days": age_days,
            "is_bot": is_bot, "is_ftc": is_ftc, "is_committer": is_committer,
            "engagement_days": engagement_days, "repo": repo}


def test_open_prs_html_empty():
    html = _open_prs_html([])
    assert "no-data" in html.lower() or "No open" in html


def test_open_prs_html_title_and_number_present():
    html = _open_prs_html([_make_pr(42, "My PR", repo="o/r")])
    assert "#42" in html
    assert '<a href="https://github.com/o/r/pull/42">My PR</a>' in html


def test_open_prs_html_emojis_after_link():
    html = _open_prs_html([_make_pr(is_ftc=True, is_committer=False)])
    close_a = html.index("</a>")
    assert "🌱" in html[close_a:]


def test_open_prs_html_ftc_emoji_with_tooltip():
    html = _open_prs_html([_make_pr(is_ftc=True, is_committer=False)])
    assert 'title="first-time contributor"' in html
    assert "🌱" in html


def test_open_prs_html_non_committer_emoji_with_tooltip():
    html = _open_prs_html([_make_pr(is_committer=False)])
    assert 'title="non-committer"' in html
    assert "👤" in html


def test_open_prs_html_bot_emoji_with_tooltip():
    html = _open_prs_html([_make_pr(is_bot=True, author="bot[bot]")])
    assert 'title="bot"' in html
    assert "🤖" in html


def test_open_prs_html_no_engagement_emoji_with_tooltip():
    html = _open_prs_html([_make_pr(engagement_days=None)])
    assert 'title="no engagement yet"' in html
    assert "👀" in html


def test_open_prs_html_no_waiting_emoji_when_engaged():
    html = _open_prs_html([_make_pr(engagement_days=2.0)])
    grid_start = html.index('id="pr-grid-open"')
    grid_end   = html.index("</div>", grid_start) + len("</div>")
    assert "👀" not in html[grid_start:grid_end]


def test_open_prs_html_star_for_new_pr():
    html = _open_prs_html([_make_pr(age_days=0.5)])
    grid_start = html.index('id="pr-grid-open"')
    assert "⭐" in html[grid_start:]
    assert 'title="opened in the last 24 hours"' in html


def test_open_prs_html_no_star_for_older_pr():
    html = _open_prs_html([_make_pr(age_days=1.0)])
    grid_start = html.index('id="pr-grid-open"')
    grid_end   = html.index("</div>", grid_start) + len("</div>")
    assert "⭐" not in html[grid_start:grid_end]


def test_open_prs_html_ancient_has_data_attr():
    html = _open_prs_html([_make_pr(age_days=100.0)])
    assert 'data-ancient="true"' in html


def test_open_prs_html_non_ancient_has_no_data_attr():
    html = _open_prs_html([_make_pr(age_days=5.0)])
    assert 'data-ancient="true"' not in html


def test_open_prs_html_author_link():
    html = _open_prs_html([_make_pr(author="bob")])
    assert '<a href="https://github.com/bob">@bob</a>' in html


def test_open_prs_html_avatar_style_on_non_bot():
    html = _open_prs_html([_make_pr(author="alice", is_bot=False)])
    assert "url('https://github.com/alice.png')" in html


def test_open_prs_html_no_avatar_style_on_bot():
    html = _open_prs_html([_make_pr(author="renovate[bot]", is_bot=True)])
    assert "renovate[bot].png" not in html
    assert "--avatar-url" not in html


def test_open_prs_html_has_checkbox():
    html = _open_prs_html([_make_pr()])
    assert 'id="hide-ancient"' in html
    assert "Hide PRs older than 90 days" in html


def test_render_html_open_tab_has_ancient_js():
    html = render_html(_stats(), _stats(), "2024-01-15T12:00:00Z", open_prs=[_make_pr()])
    assert "hide-ancient" in html
    assert "pr-grid-open" in html


# --- load_committers ---


def test_load_committers_parses_usernames(tmp_path):
    f = tmp_path / "committers.txt"
    f.write_text("# comment\nalice\nbob\n\ncarol\n")
    assert load_committers(f) == frozenset({"alice", "bob", "carol"})


def test_load_committers_missing_file(tmp_path):
    assert load_committers(tmp_path / "missing.txt") == frozenset()


