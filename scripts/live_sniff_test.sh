#!/usr/bin/env bash
# Test the previously-PROVEN live RX path (wifi_rx_sniff.py, the standard gr chain)
# against devourer's current over-air TX. Isolates "file-replay of real captures is
# broken" (live decodes, replay doesn't) from "real-RF decode is broken everywhere"
# (live also 0). No big captures -> no tmpfs/RAM risk.
set -u
cd "$(dirname "$0")/.."
DEVDIR="$HOME/git/devourer"
CH="${CH:-149}" ; FREQ="${FREQ:-5745e6}" ; BW=20e6 ; RXGAIN="${RXGAIN:-65}" ; SECS="${SECS:-15}"
MCS="${MCS:-0}" ; MODE="${MODE:-ht}"

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

TXLOG=$(mktemp /tmp/ls-tx.XXXXXX) ; RXLOG=$(mktemp /tmp/ls-rx.XXXXXX)
cleanup() { sudo pkill -f WiFiDriverTxDemo 2>/dev/null; pkill -f wifi_rx_sniff 2>/dev/null; rm -f "$TXLOG" "$RXLOG"; }
trap cleanup EXIT INT TERM

# legacy = no rate env: without DEVOURER_TX_HT_MCS the chip falls back to fixed
# rate 12 (6 Mbps legacy) on air regardless of the HT radiotap -- the most robust
# waveform, the original proven baseline (1407 frames decoded).
if [ "$MODE" = legacy ]; then TXENV=""
elif [ "$MODE" = ht ]; then TXENV="DEVOURER_TX_MCS=$MCS DEVOURER_TX_HT_MCS=1 DEVOURER_TX_BW=20"
else TXENV="DEVOURER_TX_VHT=1 DEVOURER_TX_VHT_MCS=$MCS DEVOURER_TX_VHT_NSS=1 DEVOURER_TX_BW=20"; fi

sudo pkill -f WiFiDriverTxDemo 2>/dev/null; sleep 3
echo "[ls] devourer $MODE MCS$MCS TX on 8812 ch$CH ($FREQ) ..."
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH $TXENV "$DEVDIR/build/WiFiDriverTxDemo" >"$TXLOG" 2>&1 &
sleep 5
echo "[ls] TX fixed_rate=$(grep -m1 -oE 'fixed rate:[0-9]+' "$TXLOG" | grep -oE '[0-9]+$')  sent=$(grep -c 'sent successfully' "$TXLOG")"
echo "[ls] live-sniffing ${SECS}s ..."
timeout $((SECS+15)) python3 wifi_rx_sniff.py --freq "$FREQ" --bw $BW --secs "$SECS" --rx-gain "$RXGAIN" --rx-ant RX2 >"$RXLOG" 2>/dev/null
sudo pkill -f WiFiDriverTxDemo 2>/dev/null
echo "[ls] --- sniff result ---"
grep -iE "total|devourer|frames|SA\(addr2\)|decoded" "$RXLOG" | head -12
