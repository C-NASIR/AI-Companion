#!/bin/bash
set -euo pipefail

DATA_DIR="/app/data"
CHILD_PID=""

reset_data_dir() {
  mkdir -p "${DATA_DIR}"
  rm -rf "${DATA_DIR}/events" "${DATA_DIR}/state"
  mkdir -p "${DATA_DIR}/events" "${DATA_DIR}/state"
}

cleanup() {
  reset_data_dir || true
}

forward_signal() {
  local signal="$1"
  if [[ -n "${CHILD_PID}" ]]; then
    kill "-${signal}" "${CHILD_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT
trap 'forward_signal TERM' TERM
trap 'forward_signal INT' INT

python -m app.startup_checks
reset_data_dir

"$@" &
CHILD_PID=$!
wait "${CHILD_PID}"
