# kroxylicious-project-metrics

Collects PR data from [kroxylicious/kroxylicious](https://github.com/kroxylicious/kroxylicious) and publishes a metrics dashboard to GitHub Pages.

Two GitHub Actions pipelines run automatically:
- **Harvest** — every 4 hours, fetches PR events from the GitHub API and commits raw data to the `_data` branch.
- **Publish** — on every push to `main` and after each harvest, builds and deploys the static site.

## Prerequisites

- Python 3.11+
- [GitHub CLI](https://cli.github.com/) (`gh`), authenticated
- A PAT with `repo` read scope on `kroxylicious/kroxylicious`, stored as the `KROXYLICIOUS_PAT` repository secret

## Development setup

```bash
python3 -m pip install -e ".[dev]"
```

## Running the tests

```bash
python3 -m pytest
```

## Previewing the site locally

`dev-preview.sh` fetches the latest `_data` branch, builds `site/index.html`, and opens it in your browser:

```bash
./dev-preview.sh
```

## Repository layout

```
scripts/
  harvest.py        # fetches PR events from GitHub API
  lint.py           # validates the data directory schema
  build_site.py     # computes statistics and renders site/index.html
  backfill_draft.py # one-off: sets the draft flag on existing open PRs
data/               # gitignored; populated by dev-preview.sh or the harvest workflow
site/               # gitignored; generated output
committers.txt      # GitHub usernames of project committers
```
