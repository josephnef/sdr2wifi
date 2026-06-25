#!/usr/bin/env bash
# Capture a fresh devourer TX to a KEPT disk file for offline analysis. Disk-backed
# (~/sdr-caps), short by default. MODE=ht|vht|legacy.
set -u
cd "$(dirname "$0")/.."
DEVDIR="$HOME/git/devourer"
CH="${CH:-149}" ; FREQ="${FREQ:-5745e6}" ; BW=20e6 ; RXGAIN="${RXGAIN:-65}" ; SECS="${SECS:-0.5}"
MODE="${MODE:-ht}" ; MCS="${MCS:-0}"
OUT="${OUT:-$HOME/sdr-caps/keep_${MODE}_mcs${MCS}.cf32}" ; mkdir -p "$(dirname "$OUT")"

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

TXLOG=$(mktemp /tmp/ck-tx.XXXXXX)
cleanup() { sudo pkill -f WiFiDriverTxDemo 2>/dev/null; rm -f "$TXLOG"; }
trap cleanup EXIT INT TERM

if [ "$MODE" = legacy ]; then TXENV=""
elif [ "$MODE" = ht ]; then TXENV="DEVOURER_TX_MCS=$MCS DEVOURER_TX_HT_MCS=1 DEVOURER_TX_BW=20"
else TXENV="DEVOURER_TX_VHT=1 DEVOURER_TX_VHT_MCS=$MCS DEVOURER_TX_VHT_NSS=1 DEVOURER_TX_BW=20"; fi

sudo pkill -f WiFiDriverTxDemo 2>/dev/null; sleep 3
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH $TXENV "$DEVDIR/build/WiFiDriverTxDemo" >"$TXLOG" 2>&1 &
sleep 5
echo "[ck] TX $MODE MCS$MCS fixed_rate=$(grep -m1 -oE 'fixed rate:[0-9]+' "$TXLOG" | grep -oE '[0-9]+$')  sent=$(grep -c 'sent successfully' "$TXLOG")"
python3 iq_capture.py --freq "$FREQ" --bw $BW --secs "$SECS" --rx-gain "$RXGAIN" --rx-ant RX2 --out "$OUT" 2>/dev/null
sudo pkill -f WiFiDriverTxDemo 2>/dev/null
echo "[ck] kept: $OUT ($(du -h "$OUT" | cut -f1))"
