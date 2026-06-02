#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
BUNDLED_SCRIPT_DIR=$REPO_DIR/skills/meta/skillgraph-cartographer/scripts

if [ -n "${SKILLGRAPH_REPO_ROOT:-}" ]; then
  REPO_ROOT=$SKILLGRAPH_REPO_ROOT
else
  REPO_ROOT=$(pwd)
fi

case "${1:-}" in
  -h|--help)
    "$PYTHON_BIN" "$BUNDLED_SCRIPT_DIR/skillgraph.py" view --help
    exit 0
    ;;
esac

"$PYTHON_BIN" "$BUNDLED_SCRIPT_DIR/skillgraph.py" collect "$REPO_ROOT" \
  | "$PYTHON_BIN" "$BUNDLED_SCRIPT_DIR/skillgraph.py" view --stdin "$@"
