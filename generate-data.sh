#!/usr/bin/env bash
# generate csv data for each commit since branching
set -eux

BASE_REF=dde7b5ea68989b47f4f588a99148fe6cc70f3d29
BRANCH=startup-lite
JUPYTERHUB="${JUPYTERHUB:-$HOME/dev/jpy/jupyterhub}"
HERE=$PWD

cd "$JUPYTERHUB"
git diff --exit-code
git diff --cached --exit-code

refs="$BASE_REF $(git rev-list $BASE_REF..$BRANCH)"
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
