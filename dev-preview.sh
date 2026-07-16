#!/usr/bin/env bash
# Fetch the latest _data branch from origin, build the site locally,
# and open it in a browser for exploration.
set -euo pipefail

cd "$(dirname "$0")"

echo "Fetching _data from origin..."
git fetch origin _data

echo "Extracting data..."
rm -rf data
mkdir -p data
git archive origin/_data | tar -x -C data/

echo "Building site..."
python3 scripts/build_site.py

site="$(pwd)/site/index.html"
echo "Site built: file://$site"

if command -v xdg-open &>/dev/null; then
  xdg-open "$site"
elif command -v open &>/dev/null; then
  open "$site"
fi
