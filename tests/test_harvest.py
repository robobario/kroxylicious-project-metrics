import json
from datetime import datetime, timedelta, timezone

import pytest
import requests
import responses as responses_lib

from harvest import (
    _parse_next_link,
    extract_linked_issues,
    extract_metadata,
    extract_pr_events,
    extract_timeline_events,
    fetch_issue_metadata,
    harvest,
    harvest_linked_issues,
    iter_prs,
    load_repos,
    merge_events,
    migrate_flat_layout,
    since_from_state,
)

UTC = timezone.utc
GITHUB_API = "https://api.github.com"

OPEN_PR = {
    "number": 1,
    "title": "Add feature",
    "state": "open",
    "user": {"login": "alice"},
    "base": {"ref": "main"},
    "labels": [{"name": "enhancement"}],
    "author_association": "CONTRIBUTOR",
    "draft": False,
    "created_at": "2024-01-10T10:00:00Z",
    "updated_at": "2024-01-15T12:00:00Z",
    "closed_at": None,
    "merged_at": None,
    "merged_by": None,
}

MERGED_PR = {
    **OPEN_PR,
    "number": 2,
    "title": "Fix bug",
    "state": "closed",
    "labels": [{"name": "bug"}],
    "closed_at": "2024-01-15T12:00:00Z",
    "merged_at": "2024-01-15T12:00:00Z",
    "merged_by": {"login": "bob"},
}

CLOSED_UNMERGED_PR = {
    **OPEN_PR,
    "number": 3,
    "title": "Work in progress",
    "state": "closed",
    "labels": [],
    "closed_at": "2024-01-14T09:00:00Z",
    "merged_at": None,
    "merged_by": None,
}


# --- extract_linked_issues ---


def test_extract_linked_issues_none_body():
    assert extract_linked_issues(None) == []


def test_extract_linked_issues_empty_body():
    assert extract_linked_issues("") == []


def test_extract_linked_issues_no_keywords():
    assert extract_linked_issues("This PR adds a new feature, see #42 for context") == []


@pytest.mark.parametrize("keyword", [
    "close", "closes", "closed",
    "fix", "fixes", "fixed",
    "resolve", "resolves", "resolved",
])
def test_extract_linked_issues_all_keywords(keyword):
    assert extract_linked_issues(f"{keyword} #99") == [99]


def test_extract_linked_issues_case_insensitive():
    assert extract_linked_issues("FIXES #10") == [10]
    assert extract_linked_issues("Closes #20") == [20]


def test_extract_linked_issues_multiple_distinct():
    body = "Closes #10\nFixes #20\nResolves #30"
    assert extract_linked_issues(body) == [10, 20, 30]


def test_extract_linked_issues_deduplicates():
    body = "Closes #5\nFixes #5"
    assert extract_linked_issues(body) == [5]


def test_extract_linked_issues_returns_sorted():
    body = "Closes #30\nFixes #10\nResolves #20"
    assert extract_linked_issues(body) == [10, 20, 30]


# --- extract_metadata ---


def test_extract_metadata_open_pr():
    meta = extract_metadata(OPEN_PR)
    assert meta["number"] == 1
    assert meta["author"] == "alice"
    assert meta["labels"] == ["enhancement"]
    assert meta["target_branch"] == "main"
    assert meta["state"] == "open"
    assert meta["merged"] is False


def test_extract_metadata_merged_pr():
    meta = extract_metadata(MERGED_PR)
    assert meta["merged"] is True
    assert meta["state"] == "closed"


def test_extract_metadata_no_labels():
    meta = extract_metadata(CLOSED_UNMERGED_PR)
    assert meta["labels"] == []


def test_extract_metadata_draft_captured():
    meta = extract_metadata({**OPEN_PR, "draft": True})
    assert meta["draft"] is True


def test_extract_metadata_draft_defaults_false():
    pr = {k: v for k, v in OPEN_PR.items() if k != "draft"}
    assert extract_metadata(pr)["draft"] is False


def test_extract_metadata_linked_issues_populated():
    pr = {**OPEN_PR, "body": "Fixes #42\nCloses #7"}
    assert extract_metadata(pr)["linked_issues"] == [7, 42]


def test_extract_metadata_linked_issues_empty_when_no_body():
    pr = {k: v for k, v in OPEN_PR.items() if k != "body"}
    assert extract_metadata(pr)["linked_issues"] == []


# --- extract_pr_events ---


def test_extract_pr_events_open():
    events = extract_pr_events(OPEN_PR)
    assert len(events) == 1
    assert events[0]["type"] == "created"
    assert events[0]["timestamp"] == "2024-01-10T10:00:00Z"
    assert events[0]["actor"] == "alice"


def test_extract_pr_events_merged():
    events = extract_pr_events(MERGED_PR)
    types = [e["type"] for e in events]
    assert types == ["created", "closed_merged"]
    closed = next(e for e in events if e["type"] == "closed_merged")
    assert closed["timestamp"] == "2024-01-15T12:00:00Z"
    assert closed["actor"] == "bob"


def test_extract_pr_events_merged_by_unknown():
    pr = {**MERGED_PR, "merged_by": None}
    events = extract_pr_events(pr)
    closed = next(e for e in events if e["type"] == "closed_merged")
    assert closed["actor"] is None


def test_extract_pr_events_closed_unmerged():
    events = extract_pr_events(CLOSED_UNMERGED_PR)
    types = [e["type"] for e in events]
    assert types == ["created", "closed_unmerged"]
    closed = next(e for e in events if e["type"] == "closed_unmerged")
    assert closed["timestamp"] == "2024-01-14T09:00:00Z"
    assert closed["actor"] is None


# --- extract_timeline_events ---


def test_extract_timeline_events_reopened():
    timeline = [{"event": "reopened", "created_at": "2024-01-12T08:00:00Z", "actor": {"login": "carol"}}]
    events = extract_timeline_events(timeline)
    assert len(events) == 1
    assert events[0] == {"type": "reopened", "timestamp": "2024-01-12T08:00:00Z", "actor": "carol"}


def test_extract_timeline_events_review_requested():
    timeline = [{"event": "review_requested", "created_at": "2024-01-11T09:00:00Z", "actor": {"login": "alice"}}]
    events = extract_timeline_events(timeline)
    assert events[0]["type"] == "review_requested"
    assert events[0]["actor"] == "alice"


def test_extract_timeline_events_reviewed():
    timeline = [{"event": "reviewed", "submitted_at": "2024-01-13T14:00:00Z", "user": {"login": "dave"}, "state": "approved"}]
    events = extract_timeline_events(timeline)
    assert events[0] == {"type": "reviewed", "timestamp": "2024-01-13T14:00:00Z", "actor": "dave"}


def test_extract_timeline_events_reviewed_pending_skipped():
    timeline = [{"event": "reviewed", "user": {"login": "dave"}, "state": "pending"}]
    events = extract_timeline_events(timeline)
    assert events == []


def test_extract_timeline_events_comment():
    timeline = [{"event": "commented", "created_at": "2024-01-11T11:00:00Z", "actor": {"login": "eve"}}]
    events = extract_timeline_events(timeline)
    assert events[0] == {"type": "comment", "timestamp": "2024-01-11T11:00:00Z", "actor": "eve"}


def test_extract_timeline_events_skips_unknown():
    timeline = [
        {"event": "labeled", "created_at": "2024-01-11T10:00:00Z", "actor": {"login": "alice"}},
        {"event": "commented", "created_at": "2024-01-11T11:00:00Z", "actor": {"login": "bob"}},
    ]
    events = extract_timeline_events(timeline)
    assert len(events) == 1
    assert events[0]["type"] == "comment"


def test_extract_timeline_events_null_actor():
    timeline = [{"event": "reopened", "created_at": "2024-01-12T08:00:00Z", "actor": None}]
    events = extract_timeline_events(timeline)
    assert events[0]["actor"] is None


# --- merge_events ---


def test_merge_events_deduplicates():
    existing = [{"type": "created", "timestamp": "2024-01-10T10:00:00Z", "actor": "alice"}]
    new = [{"type": "created", "timestamp": "2024-01-10T10:00:00Z", "actor": "alice"}]
    result = merge_events(existing, new)
    assert len(result) == 1


def test_merge_events_adds_new():
    existing = [{"type": "created", "timestamp": "2024-01-10T10:00:00Z", "actor": "alice"}]
    new = [{"type": "closed_merged", "timestamp": "2024-01-15T12:00:00Z", "actor": "bob"}]
    result = merge_events(existing, new)
    assert len(result) == 2


def test_merge_events_sorted_by_timestamp():
    existing = [{"type": "closed_merged", "timestamp": "2024-01-15T12:00:00Z", "actor": "bob"}]
    new = [{"type": "created", "timestamp": "2024-01-10T10:00:00Z", "actor": "alice"}]
    result = merge_events(existing, new)
    assert result[0]["type"] == "created"
    assert result[1]["type"] == "closed_merged"


def test_merge_events_empty_existing():
    new = [{"type": "created", "timestamp": "2024-01-10T10:00:00Z", "actor": "alice"}]
    result = merge_events([], new)
    assert result == new


# --- _parse_next_link ---


def test_parse_next_link_present():
    header = '<https://api.github.com/repos/o/r/pulls?page=2>; rel="next", <https://api.github.com/repos/o/r/pulls?page=5>; rel="last"'
    assert _parse_next_link(header) == "https://api.github.com/repos/o/r/pulls?page=2"


def test_parse_next_link_absent():
    header = '<https://api.github.com/repos/o/r/pulls?page=4>; rel="prev", <https://api.github.com/repos/o/r/pulls?page=5>; rel="last"'
    assert _parse_next_link(header) is None


def test_parse_next_link_empty():
    assert _parse_next_link("") is None
    assert _parse_next_link(None) is None


# --- since_from_state ---


def test_since_from_state_with_last_run():
    ts = "2024-01-01T00:00:00+00:00"
    result = since_from_state(ts)
    assert result == datetime(2024, 1, 1, tzinfo=UTC)


def test_since_from_state_null_uses_project_epoch():
    from harvest import PROJECT_EPOCH
    assert since_from_state(None) == PROJECT_EPOCH


# --- iter_prs (HTTP mocked) ---


@responses_lib.activate
def test_iter_prs_yields_prs_newer_than_since():
    since = datetime(2024, 1, 12, tzinfo=UTC)
    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/pulls",
        json=[
            {**OPEN_PR, "number": 10, "updated_at": "2024-01-15T00:00:00Z"},
            {**OPEN_PR, "number": 9, "updated_at": "2024-01-13T00:00:00Z"},
            {**OPEN_PR, "number": 8, "updated_at": "2024-01-11T00:00:00Z"},
        ],
        status=200,
    )
    session = requests.Session()
    result = list(iter_prs(session, "owner", "repo", since))
    assert [pr["number"] for pr in result] == [10, 9]


@responses_lib.activate
def test_iter_prs_follows_pagination():
    since = datetime(2024, 1, 1, tzinfo=UTC)
    page2_url = f"{GITHUB_API}/repos/owner/repo/pulls?page=2"
    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/pulls",
        json=[{**OPEN_PR, "number": 10, "updated_at": "2024-01-15T00:00:00Z"}],
        headers={"Link": f'<{page2_url}>; rel="next"'},
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        page2_url,
        json=[{**OPEN_PR, "number": 9, "updated_at": "2024-01-14T00:00:00Z"}],
        status=200,
    )
    session = requests.Session()
    result = list(iter_prs(session, "owner", "repo", since))
    assert [pr["number"] for pr in result] == [10, 9]


# --- fetch_issue_metadata ---


@responses_lib.activate
def test_fetch_issue_metadata_returns_fields():
    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/issues/42",
        json={
            "number": 42,
            "title": "Some bug",
            "state": "open",
            "labels": [{"name": "bug"}],
        },
        status=200,
    )
    session = requests.Session()
    meta = fetch_issue_metadata(session, "owner", "repo", 42)
    assert meta == {"number": 42, "title": "Some bug", "state": "open", "labels": ["bug"]}


@responses_lib.activate
def test_fetch_issue_metadata_no_labels():
    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/issues/7",
        json={"number": 7, "title": "Unlabelled", "state": "closed", "labels": []},
        status=200,
    )
    meta = fetch_issue_metadata(requests.Session(), "owner", "repo", 7)
    assert meta["labels"] == []


# --- harvest_linked_issues ---


@responses_lib.activate
def test_harvest_linked_issues_writes_file(tmp_path):
    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/issues/10",
        json={"number": 10, "title": "A bug", "state": "open", "labels": []},
        status=200,
    )
    harvest_linked_issues(requests.Session(), "owner", "repo", [10], tmp_path)
    issue_path = tmp_path / "issues" / "10" / "metadata.json"
    assert issue_path.exists()
    assert json.loads(issue_path.read_text())["title"] == "A bug"


@responses_lib.activate
def test_harvest_linked_issues_skips_existing(tmp_path):
    issue_path = tmp_path / "issues" / "10" / "metadata.json"
    issue_path.parent.mkdir(parents=True)
    issue_path.write_text(json.dumps({"number": 10, "title": "Old", "state": "open", "labels": []}))

    harvest_linked_issues(requests.Session(), "owner", "repo", [10], tmp_path)
    assert len(responses_lib.calls) == 0
    assert json.loads(issue_path.read_text())["title"] == "Old"


@responses_lib.activate
def test_harvest_linked_issues_empty_list_no_requests(tmp_path):
    harvest_linked_issues(requests.Session(), "owner", "repo", [], tmp_path)
    assert len(responses_lib.calls) == 0


# --- harvest (integration) ---


@responses_lib.activate
def test_harvest_writes_files(tmp_path):
    (tmp_path / "prs").mkdir()
    (tmp_path / "last_harvest.json").write_text('{"last_run": "2024-01-01T00:00:00+00:00"}')

    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/pulls",
        json=[MERGED_PR],
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/issues/2/timeline",
        json=[{"event": "review_requested", "created_at": "2024-01-12T09:00:00Z", "actor": {"login": "dave"}}],
        status=200,
    )

    session = requests.Session()
    run_start = datetime(2024, 1, 16, tzinfo=UTC)
    harvest(session, "owner", "repo", tmp_path, run_start)

    metadata = json.loads((tmp_path / "prs" / "2" / "metadata.json").read_text())
    assert metadata["number"] == 2
    assert metadata["merged"] is True

    events = json.loads((tmp_path / "prs" / "2" / "events.json").read_text())
    event_types = [e["type"] for e in events]
    assert "created" in event_types
    assert "closed_merged" in event_types
    assert "review_requested" in event_types

    state = json.loads((tmp_path / "last_harvest.json").read_text())
    assert state["last_run"] == "2024-01-16T00:00:00+00:00"


# --- load_repos ---


def test_load_repos_parses_owner_repo(tmp_path):
    f = tmp_path / "repos.txt"
    f.write_text("kroxylicious/kroxylicious\n")
    assert load_repos(f) == [("kroxylicious", "kroxylicious")]


def test_load_repos_multiple(tmp_path):
    f = tmp_path / "repos.txt"
    f.write_text("org/repo-a\norg/repo-b\n")
    assert load_repos(f) == [("org", "repo-a"), ("org", "repo-b")]


def test_load_repos_skips_comments_and_blanks(tmp_path):
    f = tmp_path / "repos.txt"
    f.write_text("# comment\n\norg/repo\n")
    assert load_repos(f) == [("org", "repo")]


def test_load_repos_skips_malformed_lines(tmp_path):
    f = tmp_path / "repos.txt"
    f.write_text("noslash\norg/repo\n")
    assert load_repos(f) == [("org", "repo")]


# --- migrate_flat_layout ---


def test_migrate_flat_layout_moves_prs_and_state(tmp_path):
    prs = tmp_path / "prs" / "1"
    prs.mkdir(parents=True)
    (prs / "metadata.json").write_text("{}")
    (tmp_path / "last_harvest.json").write_text('{"last_run": null}')

    migrate_flat_layout(tmp_path, "org", "repo")

    assert not (tmp_path / "prs").exists()
    assert not (tmp_path / "last_harvest.json").exists()
    assert (tmp_path / "org" / "repo" / "prs" / "1" / "metadata.json").exists()
    assert (tmp_path / "org" / "repo" / "last_harvest.json").exists()


def test_migrate_flat_layout_no_op_when_already_migrated(tmp_path):
    (tmp_path / "org" / "repo" / "prs").mkdir(parents=True)
    migrate_flat_layout(tmp_path, "org", "repo")
    assert (tmp_path / "org" / "repo" / "prs").exists()


def test_migrate_flat_layout_missing_state_file(tmp_path):
    (tmp_path / "prs").mkdir()
    migrate_flat_layout(tmp_path, "org", "repo")
    assert (tmp_path / "org" / "repo" / "prs").exists()
    assert not (tmp_path / "org" / "repo" / "last_harvest.json").exists()


@responses_lib.activate
def test_harvest_writes_linked_issue_metadata(tmp_path):
    pr_with_link = {**MERGED_PR, "body": "Fixes #55"}
    (tmp_path / "prs").mkdir()
    (tmp_path / "last_harvest.json").write_text('{"last_run": "2024-01-01T00:00:00+00:00"}')

    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/pulls",
        json=[pr_with_link],
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/issues/2/timeline",
        json=[],
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/issues/55",
        json={"number": 55, "title": "The linked issue", "state": "open", "labels": []},
        status=200,
    )

    harvest(requests.Session(), "owner", "repo", tmp_path, datetime(2024, 1, 16, tzinfo=UTC))

    issue_path = tmp_path / "issues" / "55" / "metadata.json"
    assert issue_path.exists()
    assert json.loads(issue_path.read_text())["title"] == "The linked issue"


@responses_lib.activate
def test_harvest_since_override_bypasses_last_harvest(tmp_path):
    (tmp_path / "prs").mkdir()
    # last_run is after MERGED_PR.updated_at; without override iter_prs would skip the PR
    (tmp_path / "last_harvest.json").write_text('{"last_run": "2024-01-16T00:00:00+00:00"}')

    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/pulls",
        json=[MERGED_PR],
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/issues/2/timeline",
        json=[],
        status=200,
    )

    run_start = datetime(2024, 1, 20, tzinfo=UTC)
    since_override = datetime(2024, 1, 1, tzinfo=UTC)
    harvest(requests.Session(), "owner", "repo", tmp_path, run_start, since=since_override)

    assert (tmp_path / "prs" / "2" / "metadata.json").exists()


@responses_lib.activate
def test_harvest_merges_existing_events(tmp_path):
    pr_dir = tmp_path / "prs" / "2"
    pr_dir.mkdir(parents=True)
    existing_events = [{"type": "created", "timestamp": "2024-01-10T10:00:00Z", "actor": "alice"}]
    (pr_dir / "events.json").write_text(json.dumps(existing_events))
    (tmp_path / "last_harvest.json").write_text('{"last_run": "2024-01-01T00:00:00+00:00"}')

    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/pulls",
        json=[MERGED_PR],
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        f"{GITHUB_API}/repos/owner/repo/issues/2/timeline",
        json=[],
        status=200,
    )

    session = requests.Session()
    harvest(session, "owner", "repo", tmp_path, datetime(2024, 1, 16, tzinfo=UTC))

    events = json.loads((pr_dir / "events.json").read_text())
    event_types = [e["type"] for e in events]
    assert event_types.count("created") == 1
    assert "closed_merged" in event_types
