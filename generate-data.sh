#!/usr/bin/env bash
# generate csv data for each commit since branching
set -eux

BASE_REF="${BASE_REF:-origin/main}"
BRANCH="${BRANCH:-quicker-startup}"
JUPYTERHUB="${JUPYTERHUB:-$HOME/dev/jpy/jupyterhub}"
HERE=$(dirname "$(realpath "$0")")

cd "$JUPYTERHUB"
git diff --exit-code
git diff --cached --exit-code

if [[ -z "$@" ]]; then
  refs="$(git show -s --format=%H $BASE_REF) $(git rev-list $BASE_REF..$BRANCH)"
else
  refs=""
  for ref in "$@"; do
    refs="$refs $(git show -s --format=%H $ref)"
  done
fi
echo "Running for $refs"
current_branch=$(git branch --show-current)

cd "$HERE"


for ref in $refs; do
    csv="${ref:0:7}.csv"
    if [[ -f $csv ]]; then
        echo "already have $csv"
        continue
    fi
    echo "generating $csv"
    cd "$JUPYTERHUB"
    git checkout $ref > /dev/null
    cd "$HERE"
    python startup-perf.py | tee $csv
done

cd "$JUPYTERHUB"
git switch "$current_branch"
