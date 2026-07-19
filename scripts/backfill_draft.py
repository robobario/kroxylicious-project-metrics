#!/usr/bin/env python3
"""
One-off: set the draft field in metadata.json for all open PRs that lack it.

Run after dev-preview.sh has populated data/ (which is a checkout of _data):

    GITHUB_TOKEN=<pat> python3 scripts/backfill_draft.py

Then commit the results back to _data:

    cd data
    git add .
    git diff --staged --quiet || git commit -s -m "chore: backfill draft flag for open PRs"
    git push
"""

import json
import os
import sys
from pathlib import Path

import requests

GITHUB_API = "https://api.github.com"


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        sys.exit(1)

    owner    = os.environ.get("GITHUB_OWNER", "kroxylicious")
    repo     = os.environ.get("GITHUB_REPO",  "kroxylicious")
    data_dir = Path(os.environ.get("DATA_DIR", "data"))

    prs_dir = data_dir / "prs"
    if not prs_dir.exists():
        print(f"No prs/ directory in {data_dir} — run dev-preview.sh first.", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })

    updated = skipped = 0
    for pr_dir in sorted(prs_dir.iterdir()):
        metadata_path = pr_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        meta = json.loads(metadata_path.read_text())
        if meta.get("state") != "open":
            continue
        if "draft" in meta:
            skipped += 1
            continue

        number = meta["number"]
        resp = session.get(f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}")
        if resp.status_code == 404:
            print(f"  #{number}: not found on GitHub (may have been closed since last harvest)")
            continue
        resp.raise_for_status()

        draft = resp.json().get("draft", False)
        meta["draft"] = draft
        metadata_path.write_text(json.dumps(meta, indent=2) + "\n")
        print(f"  #{number}: draft={draft}")
        updated += 1

    print(f"Done. {updated} updated, {skipped} already had the field.")


if __name__ == "__main__":
    main()
