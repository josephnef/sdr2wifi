#!/usr/bin/env bash
# RX-direction completeness check: SDR transmits a known PHY, devourer receives it.
#
#   rf_tx_air.py (B210)  --VHT MCS4, ch36/5180MHz, 20MHz-->  air  -->  RTL8812AU (devourer)
#
# Asserts devourer's <devourer-stream> reports the canonical SA AND a rate that
# desc_rate.py decodes to the PHY we transmitted. This is the inverse of the proven
# devourer-TX -> SDR-RX path: here the SDR is the known-good transmitter.
#
# Usage (from the sdr2wifi repo root):  sudo scripts/rx_direction_check.sh [MODE] [MCS]
#   MODE = legacy|ht|vht (default vht), MCS index (default 4; ignored for legacy)
set -u
MODE="${1:-vht}"
MCS="${2:-4}"
DEVDIR="$HOME/git/devourer"
FREQ=5180e6 ; CH=36 ; BW=20e6
RXPID=0x8812
CAP=$(mktemp /tmp/rxdir-cap.XXXXXX.cf32)
RXLOG=$(mktemp /tmp/rxdir-devourer.XXXXXX)
TXLOG=$(mktemp /tmp/rxdir-sdrtx.XXXXXX)

cleanup() {
  pkill -f "rf_tx_air.py"     2>/dev/null
  pkill -f "WiFiDriverDemo"   2>/dev/null
  rm -f "$CAP" "$RXLOG" "$TXLOG"
}
trap cleanup EXIT INT TERM

# --- gr env (mirror run_rx.sh) ---
export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

echo "[rxdir] generating $MODE MCS$MCS capture (canonical SA) ..."
if [ "$MODE" = "legacy" ]; then
  python3 tools_gen_wifi.py --mode legacy --reps 400 --out "$CAP" >/dev/null
else
  python3 tools_gen_wifi.py --mode "$MODE" --mcs "$MCS" --reps 400 --out "$CAP" >/dev/null
fi

echo "[rxdir] starting devourer RX (8812, ch$CH, STREAM_OUT) ..."
sudo env DEVOURER_PID=$RXPID DEVOURER_CHANNEL=$CH DEVOURER_STREAM_OUT=1 \
  "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>/dev/null &
sleep 8   # devourer init (efuse read + channel set)

echo "[rxdir] transmitting capture over air for 20s ..."
timeout 30 python3 rf_tx_air.py --in "$CAP" --freq $FREQ --bw $BW --secs 20 \
  >"$TXLOG" 2>/dev/null
sleep 2
pkill -f "WiFiDriverDemo" 2>/dev/null
sleep 1

CANON="57:42:75:05:d6:00"
N=$(grep -c "devourer-stream" "$RXLOG")
# canonical SA appears in the decoded body; <devourer-stream> already SA-filters.
RATE=$(grep -m1 "devourer-stream" "$RXLOG" | sed -n 's/.*rate=\([0-9]*\).*/\1/p')
echo "[rxdir] <devourer-stream> lines: $N"
if [ "$N" -gt 0 ] && [ -n "$RATE" ]; then
  DEC=$(python3 desc_rate.py "$RATE" 2>/dev/null)
  echo "[rxdir] devourer reports rate=$RATE -> $DEC"
  echo "[rxdir] (transmitted: $MODE MCS$MCS)"
  echo "[rxdir] RESULT: devourer RECEIVED SDR-transmitted frames. Compare the decoded"
  echo "        format/MCS above against what was transmitted."
  exit 0
fi
echo "[rxdir] RESULT: no canonical-SA frames received. Tune --tx-gain / antenna"
echo "        spacing / RX channel. devourer RX log tail:"
grep -aE "RX pkt|first_rx|channel" "$RXLOG" | tail -5
exit 1
