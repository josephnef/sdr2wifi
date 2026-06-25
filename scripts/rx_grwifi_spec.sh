#!/usr/bin/env bash
# RX-direction with a SPEC-COMPLIANT frame: transmit a GR-WiFi-generated 802.11
# HT/VHT frame (canonical SA) over the B210, and confirm the devourer driver
# receives it. Our own generator is decoder-targeted (placeholder STF) so the
# Realtek chip won't lock onto its HT/VHT; GR-WiFi follows the standard exactly,
# so the chip should receive it -- closing the RX direction for HT/VHT.
#
# Usage:  sudo scripts/rx_grwifi_spec.sh [ht|legacy] [mcs]
set -u
MODE="${1:-ht}" ; MCS="${2:-0}"
DEVDIR="$HOME/git/devourer"; FREQ=5180e6; CH=36; BW=20e6; RXPID=0x8812
CAP=/tmp/grw_canon.cf32
RXLOG=$(mktemp /tmp/rxgrw.XXXXXX); TXLOG=$(mktemp /tmp/rxgrw-tx.XXXXXX)
cleanup(){ pkill -f rf_tx_air.py 2>/dev/null; sudo pkill -f WiFiDriverDemo 2>/dev/null; rm -f "$RXLOG" "$TXLOG"; }
trap cleanup EXIT INT TERM
export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"; ulimit -n 8192

echo "[rxgrw] generating GR-WiFi spec $MODE MCS$MCS frame (canonical SA) ..."
python3 /tmp/gen_grwifi_canon.py "$MODE" "$MCS" 2>&1 | grep -E "MPDU SA|wrote"

echo "[rxgrw] starting devourer RX (8812, ch$CH, STREAM_OUT) ..."
sudo env DEVOURER_PID=$RXPID DEVOURER_CHANNEL=$CH DEVOURER_STREAM_OUT=1 \
  "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>/dev/null &
sleep 8

echo "[rxgrw] transmitting GR-WiFi frame over air for 20s ..."
timeout 30 python3 rf_tx_air.py --in "$CAP" --freq $FREQ --bw $BW --secs 20 \
  --tx-gain 70 >"$TXLOG" 2>/dev/null
sleep 2; sudo pkill -f WiFiDriverDemo 2>/dev/null; sleep 1

N=$(grep -c "devourer-stream" "$RXLOG")
echo "[rxgrw] <devourer-stream> (canonical-SA) lines: $N"
if [ "$N" -gt 0 ]; then
  grep -m2 "devourer-stream" "$RXLOG" | sed 's/body=.*/body=.../'
  echo "[rxgrw] RESULT: PASS -- devourer RECEIVED the GR-WiFi spec $MODE frame (RX-direction works)"
  exit 0
fi
echo "[rxgrw] RESULT: no canonical-SA frames. devourer RX activity:"
grep -aE "RX pkt" "$RXLOG" | tail -3
exit 1
