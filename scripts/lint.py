#!/usr/bin/env python3
"""Validate the data directory structure and event schema."""

import json
import sys
from datetime import datetime
from pathlib import Path

VALID_EVENT_TYPES = frozenset({
    "created",
    "closed_merged",
    "closed_unmerged",
    "reopened",
    "review_requested",
    "reviewed",
    "comment",
})

METADATA_REQUIRED = {
    "number": int,
    "title": str,
    "author": str,
    "author_association": str,
    "labels": list,
    "target_branch": str,
    "created_at": str,
    "state": str,
    "merged": bool,
}

VALID_STATES = {"open", "closed"}

VALID_AUTHOR_ASSOCIATIONS = frozenset({
    "COLLABORATOR",
    "CONTRIBUTOR",
    "FIRST_TIMER",
    "FIRST_TIME_CONTRIBUTOR",
    "MANNEQUIN",
    "MEMBER",
    "NONE",
    "OWNER",
})


def _parse_timestamp(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def lint_state_file(data_dir):
    path = data_dir / "last_harvest.json"
    if not path.exists():
        return [f"{path}: missing"]
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON: {exc}"]
    if "last_run" not in state:
        return [f"{path}: missing 'last_run' key"]
    return []


def lint_metadata(path, data):
    violations = []
    for field, expected in METADATA_REQUIRED.items():
        if field not in data:
            violations.append(f"{path}: missing field '{field}'")
        elif not isinstance(data[field], expected):
            violations.append(
                f"{path}: '{field}' must be {expected.__name__}, got {type(data[field]).__name__}"
            )
    if data.get("state") not in VALID_STATES and "state" in data and isinstance(data["state"], str):
        violations.append(f"{path}: 'state' must be one of {sorted(VALID_STATES)}, got '{data['state']}'")
    if isinstance(data.get("author_association"), str) and data["author_association"] not in VALID_AUTHOR_ASSOCIATIONS:
        violations.append(f"{path}: 'author_association' has unrecognised value '{data['author_association']}'")
    if isinstance(data.get("created_at"), str) and _parse_timestamp(data["created_at"]) is None:
        violations.append(f"{path}: 'created_at' is not a valid ISO 8601 timestamp")
    if isinstance(data.get("labels"), list):
        for i, label in enumerate(data["labels"]):
            if not isinstance(label, str):
                violations.append(f"{path}: 'labels[{i}]' must be a string")
    return violations


def lint_events(path, data):
    if not isinstance(data, list):
        return [f"{path}: must be a JSON array"]
    violations = []
    prev_ts = None
    for i, event in enumerate(data):
        loc = f"{path}[{i}]"
        if not isinstance(event, dict):
            violations.append(f"{loc}: must be an object")
            continue
        if "type" not in event:
            violations.append(f"{loc}: missing 'type'")
        elif event["type"] not in VALID_EVENT_TYPES:
            violations.append(f"{loc}: invalid type '{event['type']}'")
        if "actor" not in event:
            violations.append(f"{loc}: missing 'actor'")
        aa = event.get("author_association")
        if aa is not None and aa not in VALID_AUTHOR_ASSOCIATIONS:
            violations.append(f"{loc}: 'author_association' has unrecognised value '{aa}'")
        if "timestamp" not in event:
            violations.append(f"{loc}: missing 'timestamp'")
        else:
            ts = _parse_timestamp(event["timestamp"])
            if ts is None:
                violations.append(f"{loc}: 'timestamp' is not valid ISO 8601")
            else:
                if prev_ts is not None and ts < prev_ts:
                    violations.append(f"{loc}: timestamp is before previous event (events must be sorted)")
                prev_ts = ts
    return violations


def lint_pr_dir(pr_dir):
    violations = []
    for filename, linter in [("metadata.json", lint_metadata), ("events.json", lint_events)]:
        path = pr_dir / filename
        if not path.exists():
            violations.append(f"{path}: missing")
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            violations.append(f"{path}: invalid JSON: {exc}")
            continue
        violations.extend(linter(path, data))
    return violations


def lint(data_dir):
    violations = lint_state_file(data_dir)
    prs_dir = data_dir / "prs"
    if prs_dir.exists():
        for entry in sorted(prs_dir.iterdir()):
            if entry.is_dir():
                violations.extend(lint_pr_dir(entry))
    return violations


def main():
    data_dir = Path("data")
    violations = lint(data_dir)
    if violations:
        for v in violations:
            print(v)
        sys.exit(1)
    print("Data directory is valid.")


if __name__ == "__main__":
    main()
