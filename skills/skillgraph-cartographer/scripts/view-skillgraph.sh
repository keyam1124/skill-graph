#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}

if [ -n "${SKILLGRAPH_REPO_ROOT:-}" ]; then
  REPO_ROOT=$SKILLGRAPH_REPO_ROOT
else
  REPO_ROOT=$(pwd)
fi

case "${1:-}" in
  -h|--help)
    "$PYTHON_BIN" "$SCRIPT_DIR/skillgraph.py" view --help
    exit 0
    ;;
esac

"$PYTHON_BIN" "$SCRIPT_DIR/skillgraph.py" collect "$REPO_ROOT" \
  | "$PYTHON_BIN" "$SCRIPT_DIR/skillgraph.py" view --stdin "$@"
