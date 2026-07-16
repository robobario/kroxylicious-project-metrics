import json
from pathlib import Path

import pytest

from lint import lint, lint_events, lint_metadata, lint_pr_dir, lint_state_file

VALID_METADATA = {
    "number": 42,
    "title": "Fix the thing",
    "author": "alice",
    "labels": ["bug"],
    "target_branch": "main",
    "created_at": "2024-01-10T10:00:00Z",
    "state": "closed",
    "merged": True,
}

VALID_EVENTS = [
    {"type": "created", "timestamp": "2024-01-10T10:00:00Z", "actor": "alice"},
    {"type": "review_requested", "timestamp": "2024-01-11T09:00:00Z", "actor": "alice"},
    {"type": "reviewed", "timestamp": "2024-01-12T14:00:00Z", "actor": "bob"},
    {"type": "closed_merged", "timestamp": "2024-01-15T12:00:00Z", "actor": "bob"},
]


# --- lint_state_file ---


def test_lint_state_file_valid(tmp_path):
    (tmp_path / "last_harvest.json").write_text('{"last_run": "2024-01-15T00:00:00+00:00"}')
    assert lint_state_file(tmp_path) == []


def test_lint_state_file_null_last_run(tmp_path):
    (tmp_path / "last_harvest.json").write_text('{"last_run": null}')
    assert lint_state_file(tmp_path) == []


def test_lint_state_file_missing(tmp_path):
    violations = lint_state_file(tmp_path)
    assert len(violations) == 1
    assert "missing" in violations[0]


def test_lint_state_file_missing_key(tmp_path):
    (tmp_path / "last_harvest.json").write_text('{}')
    violations = lint_state_file(tmp_path)
    assert any("last_run" in v for v in violations)


def test_lint_state_file_invalid_json(tmp_path):
    (tmp_path / "last_harvest.json").write_text('not json')
    violations = lint_state_file(tmp_path)
    assert any("invalid JSON" in v for v in violations)


# --- lint_metadata ---


def test_lint_metadata_valid(tmp_path):
    path = tmp_path / "metadata.json"
    assert lint_metadata(path, VALID_METADATA) == []


def test_lint_metadata_missing_field(tmp_path):
    path = tmp_path / "metadata.json"
    data = {k: v for k, v in VALID_METADATA.items() if k != "author"}
    violations = lint_metadata(path, data)
    assert any("author" in v for v in violations)


def test_lint_metadata_wrong_type_for_number(tmp_path):
    path = tmp_path / "metadata.json"
    violations = lint_metadata(path, {**VALID_METADATA, "number": "42"})
    assert any("number" in v and "int" in v for v in violations)


def test_lint_metadata_wrong_type_for_merged(tmp_path):
    path = tmp_path / "metadata.json"
    violations = lint_metadata(path, {**VALID_METADATA, "merged": "true"})
    assert any("merged" in v for v in violations)


def test_lint_metadata_invalid_state(tmp_path):
    path = tmp_path / "metadata.json"
    violations = lint_metadata(path, {**VALID_METADATA, "state": "pending"})
    assert any("state" in v for v in violations)


def test_lint_metadata_invalid_created_at(tmp_path):
    path = tmp_path / "metadata.json"
    violations = lint_metadata(path, {**VALID_METADATA, "created_at": "not-a-date"})
    assert any("created_at" in v for v in violations)


def test_lint_metadata_label_not_string(tmp_path):
    path = tmp_path / "metadata.json"
    violations = lint_metadata(path, {**VALID_METADATA, "labels": [1, 2]})
    assert any("labels[0]" in v for v in violations)


def test_lint_metadata_empty_labels_ok(tmp_path):
    path = tmp_path / "metadata.json"
    assert lint_metadata(path, {**VALID_METADATA, "labels": []}) == []


# --- lint_events ---


def test_lint_events_valid(tmp_path):
    path = tmp_path / "events.json"
    assert lint_events(path, VALID_EVENTS) == []


def test_lint_events_empty_list_ok(tmp_path):
    assert lint_events(tmp_path / "events.json", []) == []


def test_lint_events_not_a_list(tmp_path):
    violations = lint_events(tmp_path / "events.json", {})
    assert any("array" in v for v in violations)


def test_lint_events_missing_type(tmp_path):
    event = {"timestamp": "2024-01-10T10:00:00Z", "actor": "alice"}
    violations = lint_events(tmp_path / "events.json", [event])
    assert any("type" in v for v in violations)


def test_lint_events_invalid_type(tmp_path):
    event = {"type": "unknown_type", "timestamp": "2024-01-10T10:00:00Z", "actor": "alice"}
    violations = lint_events(tmp_path / "events.json", [event])
    assert any("invalid type" in v for v in violations)


def test_lint_events_missing_timestamp(tmp_path):
    event = {"type": "created", "actor": "alice"}
    violations = lint_events(tmp_path / "events.json", [event])
    assert any("timestamp" in v for v in violations)


def test_lint_events_invalid_timestamp(tmp_path):
    event = {"type": "created", "timestamp": "not-a-date", "actor": "alice"}
    violations = lint_events(tmp_path / "events.json", [event])
    assert any("ISO 8601" in v for v in violations)


def test_lint_events_missing_actor(tmp_path):
    event = {"type": "created", "timestamp": "2024-01-10T10:00:00Z"}
    violations = lint_events(tmp_path / "events.json", [event])
    assert any("actor" in v for v in violations)


def test_lint_events_null_actor_ok(tmp_path):
    event = {"type": "closed_unmerged", "timestamp": "2024-01-10T10:00:00Z", "actor": None}
    assert lint_events(tmp_path / "events.json", [event]) == []


def test_lint_events_out_of_order_timestamps(tmp_path):
    events = [
        {"type": "closed_merged", "timestamp": "2024-01-15T12:00:00Z", "actor": "bob"},
        {"type": "created", "timestamp": "2024-01-10T10:00:00Z", "actor": "alice"},
    ]
    violations = lint_events(tmp_path / "events.json", events)
    assert any("sorted" in v for v in violations)


def test_lint_events_all_valid_types(tmp_path):
    valid_types = ["created", "closed_merged", "closed_unmerged", "reopened", "review_requested", "reviewed", "comment"]
    events = [
        {"type": t, "timestamp": f"2024-01-{10 + i:02d}T10:00:00Z", "actor": "alice"}
        for i, t in enumerate(valid_types)
    ]
    assert lint_events(tmp_path / "events.json", events) == []


# --- lint_pr_dir ---


def _write_pr_dir(base, metadata=None, events=None):
    pr_dir = base / "prs" / "42"
    pr_dir.mkdir(parents=True)
    if metadata is not False:
        (pr_dir / "metadata.json").write_text(json.dumps(metadata or VALID_METADATA))
    if events is not False:
        (pr_dir / "events.json").write_text(json.dumps(events if events is not None else VALID_EVENTS))
    return pr_dir


def test_lint_pr_dir_valid(tmp_path):
    pr_dir = _write_pr_dir(tmp_path)
    assert lint_pr_dir(pr_dir) == []


def test_lint_pr_dir_missing_metadata(tmp_path):
    pr_dir = _write_pr_dir(tmp_path, metadata=False)
    violations = lint_pr_dir(pr_dir)
    assert any("metadata.json" in v and "missing" in v for v in violations)


def test_lint_pr_dir_missing_events(tmp_path):
    pr_dir = _write_pr_dir(tmp_path, events=False)
    violations = lint_pr_dir(pr_dir)
    assert any("events.json" in v and "missing" in v for v in violations)


def test_lint_pr_dir_invalid_json_metadata(tmp_path):
    pr_dir = _write_pr_dir(tmp_path, events=False)
    (pr_dir / "metadata.json").write_text("not json")
    (pr_dir / "events.json").write_text(json.dumps(VALID_EVENTS))
    violations = lint_pr_dir(pr_dir)
    assert any("invalid JSON" in v for v in violations)


# --- lint (integration) ---


def test_lint_clean_data_dir(tmp_path):
    (tmp_path / "last_harvest.json").write_text('{"last_run": null}')
    (tmp_path / "prs").mkdir()
    assert lint(tmp_path) == []


def test_lint_reports_all_pr_violations(tmp_path):
    (tmp_path / "last_harvest.json").write_text('{"last_run": null}')
    pr_dir = tmp_path / "prs" / "1"
    pr_dir.mkdir(parents=True)
    (pr_dir / "metadata.json").write_text(json.dumps({**VALID_METADATA, "number": "wrong"}))
    (pr_dir / "events.json").write_text(json.dumps([{"type": "bad", "timestamp": "2024-01-10T10:00:00Z", "actor": "x"}]))
    violations = lint(tmp_path)
    assert any("number" in v for v in violations)
    assert any("invalid type" in v for v in violations)


def test_lint_missing_state_file(tmp_path):
    (tmp_path / "prs").mkdir()
    violations = lint(tmp_path)
    assert any("last_harvest.json" in v for v in violations)
