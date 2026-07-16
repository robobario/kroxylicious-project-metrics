import json
from datetime import datetime, timedelta, timezone

import pytest

from build_site import (
    compute_ftc_pr_numbers,
    compute_stats,
    filter_resolved_since,
    histogram,
    load_committers,
    monthly_medians_by_group,
    render_html,
    resolution_time_days,
    time_to_engagement_days,
    weekly_medians_by_group,
    build_site,
)

COMMITTERS = frozenset({"maintainer", "k-wall"})
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


def test_time_to_engagement_committer_review():
    events = _engagement_events("2024-01-10T10:00:00Z", reviews=[("2024-01-12T10:00:00Z", "maintainer")])
    assert time_to_engagement_days(events, COMMITTERS) == pytest.approx(2.0)


def test_time_to_engagement_committer_comment():
    events = _engagement_events("2024-01-10T10:00:00Z", comments=[("2024-01-11T10:00:00Z", "k-wall")])
    assert time_to_engagement_days(events, COMMITTERS) == pytest.approx(1.0)


def test_time_to_engagement_uses_first_qualifying():
    events = _engagement_events(
        "2024-01-10T10:00:00Z",
        comments=[("2024-01-11T10:00:00Z", "k-wall"), ("2024-01-13T10:00:00Z", "maintainer")],
    )
    assert time_to_engagement_days(events, COMMITTERS) == pytest.approx(1.0)


def test_time_to_engagement_non_committer_ignored():
    events = _engagement_events("2024-01-10T10:00:00Z", reviews=[("2024-01-12T10:00:00Z", "external")])
    assert time_to_engagement_days(events, COMMITTERS) is None


def test_time_to_engagement_author_self_comment_not_counted():
    # PR author is a committer but their own comment should not count
    committers_incl_author = COMMITTERS | {"alice"}
    events = _engagement_events(
        "2024-01-10T10:00:00Z",
        comments=[("2024-01-11T10:00:00Z", "alice")],  # author commenting on own PR
    )
    assert time_to_engagement_days(events, committers_incl_author) is None


def test_time_to_engagement_self_then_other_uses_other():
    # Author comments first, then a different committer — only the second should count
    committers_incl_author = COMMITTERS | {"alice"}
    events = _engagement_events(
        "2024-01-10T10:00:00Z",
        comments=[
            ("2024-01-11T10:00:00Z", "alice"),       # self — skip
            ("2024-01-13T10:00:00Z", "maintainer"),  # other committer — counts
        ],
    )
    assert time_to_engagement_days(events, committers_incl_author) == pytest.approx(3.0)


def test_time_to_engagement_no_reviews():
    events = _events("2024-01-10T10:00:00Z", "closed_merged", "2024-01-15T10:00:00Z")
    assert time_to_engagement_days(events, COMMITTERS) is None


def test_time_to_engagement_empty_committers():
    events = _engagement_events("2024-01-10T10:00:00Z", reviews=[("2024-01-12T10:00:00Z", "maintainer")])
    assert time_to_engagement_days(events, frozenset()) is None


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


# --- monthly_medians_by_group ---


def test_monthly_medians_by_group_single_month_mixed():
    resolved = [
        (datetime(2024, 1, 10, tzinfo=UTC), 5.0, False, None),
        (datetime(2024, 1, 20, tzinfo=UTC), 3.0, True,  1.0),
    ]
    labels, ftc, non_ftc = monthly_medians_by_group(resolved)
    assert labels == ["2024-01"]
    assert ftc == [3.0]
    assert non_ftc == [5.0]


def test_monthly_medians_by_group_ftc_absent_in_month():
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 4.0, False, None),
        (datetime(2024, 2, 10, tzinfo=UTC), 6.0, False, 2.0),
        (datetime(2024, 2, 20, tzinfo=UTC), 2.0, True,  0.5),
    ]
    labels, ftc, non_ftc = monthly_medians_by_group(resolved)
    assert labels == ["2024-01", "2024-02"]
    assert ftc == [None, 2.0]
    assert non_ftc == [4.0, 6.0]


def test_monthly_medians_by_group_empty():
    assert monthly_medians_by_group([]) == ([], [], [])


# --- weekly_medians_by_group ---


def test_weekly_medians_by_group_single_week():
    # Jan 15 (Mon) and Jan 17 (Wed) are in the same week
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, None),
        (datetime(2024, 1, 17, tzinfo=UTC), 3.0, True,  None),
    ]
    labels, ftc, non_ftc = weekly_medians_by_group(resolved)
    assert labels == ["Jan 15"]
    assert ftc == [3.0]
    assert non_ftc == [5.0]


def test_weekly_medians_by_group_multiple_weeks():
    resolved = [
        (datetime(2024, 1, 8,  tzinfo=UTC), 4.0, False, None),  # Mon Jan 8
        (datetime(2024, 1, 15, tzinfo=UTC), 6.0, False, None),  # Mon Jan 15
        (datetime(2024, 1, 17, tzinfo=UTC), 2.0, True,  None),  # Wed Jan 17 → week of Jan 15
    ]
    labels, ftc, non_ftc = weekly_medians_by_group(resolved)
    assert labels == ["Jan 08", "Jan 15"]
    assert ftc == [None, 2.0]
    assert non_ftc == [4.0, 6.0]


def test_weekly_medians_by_group_empty():
    assert weekly_medians_by_group([]) == ([], [], [])


def test_weekly_medians_by_group_sunday_in_prior_week():
    # Sunday Jan 14 belongs to the week starting Mon Jan 8
    # Monday Jan 15 starts a new week
    resolved = [
        (datetime(2024, 1, 14, tzinfo=UTC), 1.0, False, None),  # Sun → week of Jan 8
        (datetime(2024, 1, 15, tzinfo=UTC), 2.0, False, None),  # Mon → week of Jan 15
    ]
    labels, _, _ = weekly_medians_by_group(resolved)
    assert len(labels) == 2
    assert labels[0] == "Jan 08"
    assert labels[1] == "Jan 15"


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


# --- compute_stats ---


def test_compute_stats_empty():
    stats = compute_stats([])
    assert stats["total_resolved"] == 0
    assert stats["median_days"] is None
    assert stats["median_engagement_days"] is None
    assert stats["ftc_count"] == 0
    assert stats["ftc_pct"] == 0
    assert all(c == 0 for c in stats["hist_counts"])
    assert stats["trend_labels"] == []


def test_compute_stats_with_data():
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, 1.0),
        (datetime(2024, 1, 20, tzinfo=UTC), 3.0, True,  None),
        (datetime(2024, 2, 5,  tzinfo=UTC), 10.0, False, 2.0),
    ]
    stats = compute_stats(resolved)
    assert stats["total_resolved"] == 3
    assert stats["median_days"] == 5.0
    assert stats["ftc_count"] == 1
    assert stats["ftc_pct"] == 33
    assert stats["median_engagement_days"] == 1.5
    assert len(stats["hist_labels"]) == 7
    assert len(stats["trend_labels"]) == 2


def test_compute_stats_uses_provided_trend_fn():
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, None),
        (datetime(2024, 1, 22, tzinfo=UTC), 3.0, False, None),
    ]
    stats = compute_stats(resolved, trend_fn=weekly_medians_by_group)
    assert len(stats["trend_labels"]) == 2
    assert "-" not in stats["trend_labels"][0]  # weekly labels use spaces not dashes


def test_compute_stats_no_engagement():
    resolved = [(datetime(2024, 1, 15, tzinfo=UTC), 5.0, False, None)]
    assert compute_stats(resolved)["median_engagement_days"] is None


def test_compute_stats_all_ftc():
    resolved = [(datetime(2024, 1, 15, tzinfo=UTC), 4.0, True, 0.5)]
    stats = compute_stats(resolved)
    assert stats["ftc_count"] == 1
    assert stats["ftc_pct"] == 100


# --- render_html ---


def _stats(median=5.0, total=10, ftc_count=2, engagement=3.0,
           hist_counts=None, trend_labels=None, trend_ftc=None, trend_non_ftc=None):
    labels = ["<1d", "1-3d", "3-7d", "7-14d", "14-30d", "30-60d", "60d+"]
    return {
        "median_days": median,
        "total_resolved": total,
        "ftc_count": ftc_count,
        "ftc_pct": round(100 * ftc_count / total) if total else 0,
        "median_engagement_days": engagement,
        "hist_labels": labels,
        "hist_counts": hist_counts or [0, 2, 5, 2, 1, 0, 0],
        "trend_labels": trend_labels or ["2024-01", "2024-02"],
        "trend_ftc_medians": trend_ftc or [None, 4.0],
        "trend_non_ftc_medians": trend_non_ftc or [4.0, 6.0],
    }


def test_render_html_has_both_tabs():
    html = render_html(_stats(), _stats(), "2024-01-15T12:00:00Z")
    assert "Last 3 months" in html
    assert "All time" in html


def test_render_html_shows_median_in_both_tabs():
    html = render_html(_stats(median=7.5), _stats(median=3.0), "2024-01-15T12:00:00Z")
    assert "7.5 days" in html
    assert "3.0 days" in html


def test_render_html_shows_engagement_in_both_tabs():
    html = render_html(_stats(engagement=2.5), _stats(engagement=1.5), "2024-01-15T12:00:00Z")
    assert "2.5 days" in html
    assert "1.5 days" in html


def test_render_html_shows_generated_at():
    html = render_html(_stats(), _stats(), "2024-01-15T12:00:00Z")
    assert "2024-01-15T12:00:00Z" in html


def test_render_html_no_data_shows_placeholder():
    empty = _stats(median=None, total=0, ftc_count=0, engagement=None,
                   hist_counts=[0]*7, trend_labels=[], trend_ftc=[], trend_non_ftc=[])
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


def test_render_html_embeds_both_tabs_data():
    html = render_html(
        _stats(trend_labels=["2024-01"]),
        _stats(trend_labels=["Jan 13"]),
        "2024-01-15T12:00:00Z",
    )
    assert '"2024-01"' in html
    assert '"Jan 13"' in html


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


def test_build_site_writes_index(tmp_path):
    _write_pr(tmp_path / "prs" / "1", "2024-01-10T10:00:00Z", "closed_merged", "2024-01-15T10:00:00Z")
    result = build_site(tmp_path, tmp_path / "site", "2024-01-16T00:00:00Z")
    assert (tmp_path / "site" / "index.html").exists()
    assert result["all"]["total_resolved"] == 1


def test_build_site_recent_filters_old_prs(tmp_path):
    now = datetime.now(UTC)
    recent = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_pr(tmp_path / "prs" / "1", old, "closed_merged",
              (now - timedelta(days=115)).strftime("%Y-%m-%dT%H:%M:%SZ"), author="alice")
    _write_pr(tmp_path / "prs" / "2", recent, "closed_merged",
              (now - timedelta(days=25)).strftime("%Y-%m-%dT%H:%M:%SZ"), author="bob")
    result = build_site(tmp_path, tmp_path / "site", "now")
    assert result["all"]["total_resolved"] == 2
    assert result["recent"]["total_resolved"] == 1


def test_build_site_engagement_computed(tmp_path):
    _write_pr(tmp_path / "prs" / "1", "2024-01-10T10:00:00Z", "closed_merged",
              "2024-01-15T10:00:00Z", reviews=[("2024-01-12T10:00:00Z", "maintainer")])
    result = build_site(tmp_path, tmp_path / "site", "2024-01-16T00:00:00Z", COMMITTERS)
    assert result["all"]["median_engagement_days"] == pytest.approx(2.0)


def test_build_site_ftc_counted(tmp_path):
    _write_pr(tmp_path / "prs" / "1", "2024-01-10T10:00:00Z", "closed_merged", "2024-01-15T10:00:00Z", author="alice")
    _write_pr(tmp_path / "prs" / "2", "2024-01-12T10:00:00Z", "closed_merged", "2024-01-17T10:00:00Z", author="alice")
    _write_pr(tmp_path / "prs" / "3", "2024-01-11T10:00:00Z", "closed_merged", "2024-01-16T10:00:00Z", author="bob")
    result = build_site(tmp_path, tmp_path / "site", "2024-01-20T00:00:00Z")
    assert result["all"]["ftc_count"] == 2
    assert result["all"]["total_resolved"] == 3


def test_build_site_no_data(tmp_path):
    (tmp_path / "prs").mkdir()
    result = build_site(tmp_path, tmp_path / "site", "2024-01-16T00:00:00Z")
    assert result["all"]["total_resolved"] == 0
    assert "No data yet" in (tmp_path / "site" / "index.html").read_text()


# --- compute_ftc_pr_numbers ---


def _write_meta(pr_dir, author, created_at):
    pr_dir.mkdir(parents=True, exist_ok=True)
    (pr_dir / "metadata.json").write_text(json.dumps({
        "number": int(pr_dir.name), "title": "PR", "author": author,
        "labels": [], "target_branch": "main", "created_at": created_at,
        "state": "closed", "merged": True,
    }))


def test_compute_ftc_pr_numbers_first_pr_per_author(tmp_path):
    _write_meta(tmp_path / "prs" / "1", "alice", "2024-01-10T10:00:00Z")
    _write_meta(tmp_path / "prs" / "2", "alice", "2024-01-12T10:00:00Z")
    _write_meta(tmp_path / "prs" / "3", "bob",   "2024-01-11T10:00:00Z")
    result = compute_ftc_pr_numbers(tmp_path)
    assert 1 in result
    assert 2 not in result
    assert 3 in result


def test_compute_ftc_pr_numbers_no_prs_dir(tmp_path):
    assert compute_ftc_pr_numbers(tmp_path) == frozenset()


def test_compute_ftc_pr_numbers_no_metadata(tmp_path):
    (tmp_path / "prs" / "1").mkdir(parents=True)
    assert compute_ftc_pr_numbers(tmp_path) == frozenset()


# --- load_committers ---


def test_load_committers_parses_usernames(tmp_path):
    f = tmp_path / "committers.txt"
    f.write_text("# comment\nalice\nbob\n\ncarol\n")
    assert load_committers(f) == frozenset({"alice", "bob", "carol"})


def test_load_committers_missing_file(tmp_path):
    assert load_committers(tmp_path / "missing.txt") == frozenset()
