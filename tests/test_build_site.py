import json
from datetime import datetime, timezone

import pytest

from build_site import (
    compute_stats,
    histogram,
    monthly_medians,
    render_html,
    resolution_time_days,
    build_site,
)

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


# --- monthly_medians ---


def test_monthly_medians_single_month():
    resolved = [
        (datetime(2024, 1, 10, tzinfo=UTC), 5.0),
        (datetime(2024, 1, 20, tzinfo=UTC), 3.0),
    ]
    months, medians = monthly_medians(resolved)
    assert months == ["2024-01"]
    assert medians == [4.0]


def test_monthly_medians_multiple_months():
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 4.0),
        (datetime(2024, 2, 10, tzinfo=UTC), 6.0),
        (datetime(2024, 2, 20, tzinfo=UTC), 8.0),
    ]
    months, medians = monthly_medians(resolved)
    assert months == ["2024-01", "2024-02"]
    assert medians[0] == 4.0
    assert medians[1] == 7.0


def test_monthly_medians_sorted():
    resolved = [
        (datetime(2024, 3, 1, tzinfo=UTC), 2.0),
        (datetime(2024, 1, 1, tzinfo=UTC), 1.0),
    ]
    months, _ = monthly_medians(resolved)
    assert months == ["2024-01", "2024-03"]


def test_monthly_medians_empty():
    assert monthly_medians([]) == ([], [])


# --- compute_stats ---


def test_compute_stats_empty():
    stats = compute_stats([])
    assert stats["total_resolved"] == 0
    assert stats["median_days"] is None
    assert all(c == 0 for c in stats["hist_counts"])
    assert stats["trend_months"] == []


def test_compute_stats_with_data():
    resolved = [
        (datetime(2024, 1, 15, tzinfo=UTC), 5.0),
        (datetime(2024, 1, 20, tzinfo=UTC), 3.0),
        (datetime(2024, 2, 5, tzinfo=UTC), 10.0),
    ]
    stats = compute_stats(resolved)
    assert stats["total_resolved"] == 3
    assert stats["median_days"] == 5.0
    assert len(stats["hist_labels"]) == 7
    assert len(stats["trend_months"]) == 2


# --- render_html ---


def _stats(median=5.0, total=10, hist_counts=None, trend_months=None, trend_medians=None):
    labels = ["<1d", "1-3d", "3-7d", "7-14d", "14-30d", "30-60d", "60d+"]
    return {
        "median_days": median,
        "total_resolved": total,
        "hist_labels": labels,
        "hist_counts": hist_counts or [0, 2, 5, 2, 1, 0, 0],
        "trend_months": trend_months or ["2024-01", "2024-02"],
        "trend_medians": trend_medians or [4.0, 6.0],
    }


def test_render_html_shows_median():
    html = render_html(_stats(median=7.5), "2024-01-15T12:00:00Z")
    assert "7.5 days" in html


def test_render_html_shows_resolved_count():
    html = render_html(_stats(total=42), "2024-01-15T12:00:00Z")
    assert "42" in html


def test_render_html_shows_generated_at():
    html = render_html(_stats(), "2024-01-15T12:00:00Z")
    assert "2024-01-15T12:00:00Z" in html


def test_render_html_no_data_shows_placeholder():
    html = render_html(_stats(median=None, total=0, hist_counts=[0]*7, trend_months=[], trend_medians=[]), "2024-01-15T12:00:00Z")
    assert "No resolved PRs" in html
    assert "<canvas" not in html


def test_render_html_with_data_has_canvas():
    html = render_html(_stats(), "2024-01-15T12:00:00Z")
    assert "<canvas" in html


def test_render_html_embeds_chart_data():
    html = render_html(_stats(trend_months=["2024-01"], trend_medians=[4.5]), "2024-01-15T12:00:00Z")
    assert '"2024-01"' in html
    assert "4.5" in html


# --- build_site (integration) ---


def test_build_site_writes_index(tmp_path):
    prs_dir = tmp_path / "prs" / "1"
    prs_dir.mkdir(parents=True)
    events = [
        {"type": "created",       "timestamp": "2024-01-10T10:00:00Z", "actor": "alice"},
        {"type": "closed_merged", "timestamp": "2024-01-15T10:00:00Z", "actor": "bob"},
    ]
    (prs_dir / "events.json").write_text(json.dumps(events))

    site_dir = tmp_path / "site"
    build_site(tmp_path, site_dir, "2024-01-16T00:00:00Z")

    index = site_dir / "index.html"
    assert index.exists()
    content = index.read_text()
    assert "5.0 days" in content


def test_build_site_no_data(tmp_path):
    (tmp_path / "prs").mkdir()
    site_dir = tmp_path / "site"
    stats = build_site(tmp_path, site_dir, "2024-01-16T00:00:00Z")
    assert stats["total_resolved"] == 0
    assert "No resolved PRs" in (site_dir / "index.html").read_text()
