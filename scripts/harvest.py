#!/usr/bin/env python3
"""Harvest PR events from GitHub and write raw data to the data directory."""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

UTC = timezone.utc
GITHUB_API = "https://api.github.com"
DEFAULT_LOOKBACK_DAYS = 90


def _parse_next_link(link_header):
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


def iter_prs(session, owner, repo, since):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
    params = {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100}
    while url:
        resp = session.get(url, params=params)
        resp.raise_for_status()
        params = None
        for pr in resp.json():
            updated_at = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
            if updated_at < since:
                return
            yield pr
        url = _parse_next_link(resp.headers.get("Link", ""))


def iter_timeline(session, owner, repo, pr_number):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{pr_number}/timeline"
    params = {"per_page": 100}
    while url:
        resp = session.get(url, params=params)
        resp.raise_for_status()
        params = None
        yield from resp.json()
        url = _parse_next_link(resp.headers.get("Link", ""))


def extract_metadata(pr):
    return {
        "number": pr["number"],
        "title": pr["title"],
        "author": pr["user"]["login"],
        "author_association": pr.get("author_association", "NONE"),
        "labels": [label["name"] for label in pr.get("labels", [])],
        "target_branch": pr["base"]["ref"],
        "created_at": pr["created_at"],
        "state": pr["state"],
        "merged": pr.get("merged_at") is not None,
    }


def extract_pr_events(pr):
    events = [
        {"type": "created", "timestamp": pr["created_at"], "actor": pr["user"]["login"]},
    ]
    if pr.get("merged_at"):
        merged_by = pr.get("merged_by")
        events.append({
            "type": "closed_merged",
            "timestamp": pr["merged_at"],
            "actor": merged_by["login"] if merged_by else None,
        })
    elif pr["state"] == "closed" and pr.get("closed_at"):
        events.append({
            "type": "closed_unmerged",
            "timestamp": pr["closed_at"],
            "actor": None,
        })
    return events


def extract_timeline_events(timeline):
    events = []
    for item in timeline:
        event_type = item.get("event")
        actor = (item.get("actor") or {}).get("login")

        if event_type == "reopened":
            events.append({"type": "reopened", "timestamp": item["created_at"], "actor": actor})
        elif event_type == "review_requested":
            events.append({"type": "review_requested", "timestamp": item["created_at"], "actor": actor})
        elif event_type == "reviewed":
            user = (item.get("user") or {}).get("login")
            events.append({
                "type": "reviewed",
                "timestamp": item["submitted_at"],
                "actor": user,
                "author_association": item.get("author_association"),
            })
        elif event_type == "commented":
            events.append({
                "type": "comment",
                "timestamp": item["created_at"],
                "actor": actor,
                "author_association": item.get("author_association"),
            })
    return events


def merge_events(existing, new_events):
    seen = {(e["type"], e["timestamp"]) for e in existing}
    combined = existing + [e for e in new_events if (e["type"], e["timestamp"]) not in seen]
    return sorted(combined, key=lambda e: e["timestamp"])


def since_from_state(last_run, default_lookback_days=DEFAULT_LOOKBACK_DAYS):
    if last_run:
        return datetime.fromisoformat(last_run)
    return datetime.now(UTC) - timedelta(days=default_lookback_days)


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def process_pr(session, owner, repo, pr, data_dir):
    pr_dir = data_dir / "prs" / str(pr["number"])
    pr_dir.mkdir(parents=True, exist_ok=True)

    title = pr["title"][:60] + ("…" if len(pr["title"]) > 60 else "")
    log.info("  #%-5d  %-62s  [%s]", pr["number"], title, pr["state"])

    write_json(pr_dir / "metadata.json", extract_metadata(pr))

    new_events = extract_pr_events(pr)
    new_events.extend(extract_timeline_events(list(iter_timeline(session, owner, repo, pr["number"]))))

    existing = load_json(pr_dir / "events.json", [])
    merged = merge_events(existing, new_events)
    added = len(merged) - len(existing)
    if added:
        log.info("           +%d event(s)", added)
    write_json(pr_dir / "events.json", merged)


def harvest(session, owner, repo, data_dir, run_start):
    state = load_json(data_dir / "last_harvest.json", {"last_run": None})
    since = since_from_state(state.get("last_run"))
    log.info("Harvesting %s/%s — PRs updated since %s", owner, repo, since.strftime("%Y-%m-%d %H:%M UTC"))

    count = 0
    for pr in iter_prs(session, owner, repo, since):
        process_pr(session, owner, repo, pr, data_dir)
        count += 1

    log.info("Done — %d PR(s) processed.", count)
    write_json(data_dir / "last_harvest.json", {"last_run": run_start.isoformat()})


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        sys.exit(1)

    owner = os.environ.get("GITHUB_OWNER", "kroxylicious")
    repo = os.environ.get("GITHUB_REPO", "kroxylicious")
    data_dir = Path(os.environ.get("DATA_DIR", "data"))

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    run_start = datetime.now(UTC)
    harvest(session, owner, repo, data_dir, run_start)


if __name__ == "__main__":
    main()
