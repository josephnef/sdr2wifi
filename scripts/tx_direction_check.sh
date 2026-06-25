#!/usr/bin/env bash
# TX-direction completeness check: devourer transmits, the SDR decodes it.
#
#   devourer WiFiDriverTxDemo (RTL8821AU)  --VHT/HT MCS, ch36-->  air  -->  B210 (gr-ieee802-11 fork)
#
# Confirms the SDR fork blind-decodes a REAL Realtek-transmitted frame and FCS-grades
# it. This is the headline TX-direction test: it validates the M1 VHT decoder against
# real silicon (not just the synthetic generator), closing the circularity gap.
#
# Usage (from sdr2wifi repo root):  scripts/tx_direction_check.sh [MODE] [MCS]
#   MODE = ht|vht (default vht). For VHT, devourer emits DEVOURER_TX_VHT=1.
set -u
MODE="${1:-vht}"
MCS="${2:-4}"
DEVDIR="$HOME/git/devourer"
FREQ=5180e6 ; CH=36 ; BW=20e6
TXVID=0x2357 ; TXPID=0x0120   # RTL8821AU (TP-Link Archer T2U Plus), 1T1R AC
RXLOG=$(mktemp /tmp/txdir-sdrrx.XXXXXX)
TXLOG=$(mktemp /tmp/txdir-devtx.XXXXXX)

cleanup() {
  pkill -f "WiFiDriverTxDemo" 2>/dev/null
  pkill -f "wifi_rx_sniff"    2>/dev/null
  rm -f "$RXLOG" "$TXLOG"
}
trap cleanup EXIT INT TERM

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

# devourer TX env: VHT vs HT
if [ "$MODE" = "vht" ]; then
  TXENV="DEVOURER_TX_VHT=1 DEVOURER_TX_VHT_MCS=$MCS DEVOURER_TX_VHT_NSS=1 DEVOURER_TX_BW=20"
else
  TXENV="DEVOURER_TX_MCS=$MCS DEVOURER_TX_BW=20"
fi

echo "[txdir] starting devourer $MODE MCS$MCS TX on RTL8821AU, ch$CH ..."
sudo env DEVOURER_VID=$TXVID DEVOURER_PID=$TXPID DEVOURER_CHANNEL=$CH $TXENV \
  "$DEVDIR/build/WiFiDriverTxDemo" >"$TXLOG" 2>&1 &
sleep 8   # TX adapter init + start injecting

echo "[txdir] SDR (B210) receiving + decoding for 20s ..."
timeout 30 python3 wifi_rx_sniff.py --freq $FREQ --bw $BW --secs 20 >"$RXLOG" 2>/dev/null
pkill -f "WiFiDriverTxDemo" 2>/dev/null

SIGA=$(grep -c "VHT-SIG-A" "$RXLOG")
VDATA=$(grep -c "VHT-DATA.*PASS" "$RXLOG")
HDATA=$(grep -c "HT-DATA.*PASS" "$RXLOG")
HITS=$(grep -oE "[0-9]+ from devourer" "$RXLOG" | grep -oE "^[0-9]+" | tail -1)
echo "[txdir] SDR decode: VHT-SIG-A=$SIGA  VHT-DATA PASS=$VDATA  HT-DATA PASS=$HDATA  devourer-SA frames=${HITS:-0}"
if { [ "$MODE" = "vht" ] && [ "${VDATA:-0}" -gt 0 ]; } || \
   { [ "$MODE" = "ht" ] && [ "${HDATA:-0}" -gt 0 ]; }; then
  echo "[txdir] RESULT: PASS — SDR fork decoded REAL devourer $MODE frames with FCS PASS"
  exit 0
fi
echo "[txdir] RESULT: no $MODE FCS-PASS decode. SIG-A seen=$SIGA (detection vs data-decode)."
echo "        Sample decode lines:"; grep -E "VHT|HT-DATA" "$RXLOG" | head -5
echo "        devourer TX log tail:"; tail -4 "$TXLOG"
exit 1
