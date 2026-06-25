#!/usr/bin/env bash
# Over-air proof for the fork's HT20 LDPC TX. The B210 transmits an HT20 MCS0 frame whose
# DATA is LDPC-coded (R=1/2 n=648, single codeword, D_TM=4 tone mapping, HT-SIG FEC=1)
# built by the fork's TX core (lib/tx/, tx_gen "ldpc"); a real RTL8812AU (devourer)
# receives it. PASS = <devourer-stream> rate=12 (HT MCS0) crc_err=0 ldpc=1.
#
#   bash scripts/tx_devourer_ldpc.sh
set -u
cd "$(dirname "$0")/.."
FORK="$HOME/git/gr-ieee802-11"
DEVDIR="$HOME/git/devourer"
CH="${CH:-1}" ; FREQ="${FREQ:-2412e6}" ; TXGAIN="${TXGAIN:-89}" ; SECS="${SECS:-20}"

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

CAP="$HOME/sdr-caps/txtest_ldpc.cf32" ; mkdir -p "$HOME/sdr-caps"
RXLOG=$(mktemp /tmp/txdev-ldpc.XXXXXX) ; TXBIN=/tmp/tx_gen
cleanup() { sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; pkill -9 -f rf_tx_air 2>/dev/null; rm -f "$RXLOG" "$CAP" "$CAP.one"; }
trap cleanup EXIT INT TERM

echo "[ldpc] building tx_gen ..."
g++ -O2 -std=c++17 -I"$FORK/include" "$FORK/lib/tx/tx_gen.cc" "$FORK/lib/tx/wifi_tx.cc" -o "$TXBIN" || exit 1
"$TXBIN" "$CAP" ldpc 0 20 2>/dev/null || exit 1
echo "[ldpc] TX HT20 MCS0 LDPC @ ${FREQ}Hz ch$CH; expect DESC_RATE=12 crc_err=0 ldpc=1"

sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; sleep 2
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_STREAM_OUT=1 \
  DEVOURER_RX_DUMP_ALL=1 DEVOURER_RX_KEEP_CORRUPTED=1 \
  "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>/dev/null &
sleep 14
timeout $((SECS + 10)) python3 rf_tx_air.py --in "$CAP" --freq "$FREQ" --bw 20e6 \
  --tx-gain "$TXGAIN" --secs "$SECS" >/dev/null 2>&1
sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; sleep 1

echo "[ldpc] canonical-SA <devourer-stream> lines:"
grep -a "devourer-stream" "$RXLOG" | head -4
CLEAN=$(grep -c "rate=12 .*crc_err=0.* ldpc=1" "$RXLOG")
echo "[ldpc] clean LDPC (rate=12 crc0 ldpc=1) frames: $CLEAN"
if [ "$CLEAN" -gt 0 ]; then
  echo "[ldpc] RESULT: PASS -- RTL8812AU received the fork's HT20 LDPC TX"
else
  echo "[ldpc] RESULT: FAIL"
fi
