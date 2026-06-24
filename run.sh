#!/usr/bin/env bash
# Rung-1 gr-ieee802-11 software loopback (no hardware, no Qt).
# Usage: ./run.sh [bandwidth_hz] [seconds]   e.g. ./run.sh 10e6 3
set -e
cd "$(dirname "$0")"

# gr-foo + gr-ieee802-11 (branch feat/ht-vht-rx) are installed into a prefix:
#   Linux  -> $HOME/grwifi-install (override with GRWIFI_PREFIX)
#   macOS  -> /opt/homebrew
# Discover the OOT site-packages dir the modules landed in (3.14 / dist-packages / lib64).
PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
[ -d "$PREFIX" ] || PREFIX="/opt/homebrew"
OOT_PY="$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' 2>/dev/null | head -1)"
OOT_PY="${OOT_PY%/ieee802_11}"

ulimit -n 8192                                    # GNU Radio vmcircbuf wants a high fd limit
export PYTHONPATH="${OOT_PY:+$OOT_PY:}$(pwd)"      # OOT modules + repo (wifi_phy_hier)
export LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:$LD_LIBRARY_PATH"

# Prefer the Homebrew interpreter on macOS if present, else the system python3.
PY="python3"; [ -x /opt/homebrew/bin/python3.14 ] && PY=/opt/homebrew/bin/python3.14
exec "$PY" loopback_headless.py "${1:-10e6}" "${2:-3}"
