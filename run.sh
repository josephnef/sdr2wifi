#!/usr/bin/env bash
# Rung-1 gr-ieee802-11 software loopback (no hardware, no Qt).
# Usage: ./run.sh [bandwidth_hz] [seconds]   e.g. ./run.sh 10e6 3
set -e
cd "$(dirname "$0")"
ulimit -n 8192                       # macOS default (256) is too low for GNU Radio buffers
export PYTHONPATH="$(pwd)"           # so `import wifi_phy_hier` (grcc-generated) resolves
exec /opt/homebrew/bin/python3.14 loopback_headless.py "${1:-10e6}" "${2:-3}"
