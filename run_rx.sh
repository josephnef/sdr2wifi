#!/usr/bin/env bash
# Rung-3: SDR as a pure 802.11 RX, decoding a real external Wi-Fi transmitter
# (devourer / RTL8812AU). Wraps wifi_rx_sniff.py with the env it needs.
#
# Usage: ./run_rx.sh [args passed through to wifi_rx_sniff.py]
#   ./run_rx.sh --check          # open + configure the SDR, no receive
#   ./run_rx.sh --secs 10        # listen 10 s on 5 GHz ch36 (5180 MHz), 20 MHz
#   ./run_rx.sh --freq 2437e6    # 2.4 GHz ch6 instead
#
# Pairs with the devourer TX (run in a separate shell):
#   cd ~/git/devourer && head -c 64 /dev/urandom > /tmp/psdu.bin
#   sudo DEVOURER_PID=0x8812 DEVOURER_CHANNEL=36 \
#        ./build/PrecoderDemo --psdu /tmp/psdu.bin --interval-ms 2
set -e
cd "$(dirname "$0")"

# This box is Linux (GNU Radio 3.10.12.0 / Python 3.14), not the Mac CLAUDE.md describes.
# gr-foo + gr-ieee802-11 (maint-3.10) are installed into a local prefix:
PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
# Pick the actual site-packages dir the OOT modules landed in (3.14 / dist-packages / lib64).
OOT_PY="$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' 2>/dev/null | head -1)"
OOT_PY="${OOT_PY%/ieee802_11}"
[ -n "$OOT_PY" ] || { echo "error: gr-ieee802-11 not found under $PREFIX — build it first (see plan)"; exit 1; }

ulimit -n 8192                                   # GNU Radio vmcircbuf wants a high fd limit
export PYTHONPATH="$OOT_PY:$(pwd)"               # OOT modules + repo (wifi_phy_hier)
export LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:$LD_LIBRARY_PATH"
exec python3 wifi_rx_sniff.py "$@"
