#!/usr/bin/env bash
# Over-air proof for the fork's HT20 STBC TX (1 SS -> 2 STS Alamouti). The B210 transmits
# the 2 antenna streams synchronously (rf_tx_air2); a real RTL8812AU (devourer) receives.
# PASS = <devourer-stream> rate=12 (HT MCS0) crc_err=0 stbc=1.
#
# The STBC DATA pilot convention on the chip is determined empirically (the extra HT-LTF
# symbol may shift the cycling-pilot polarity index): sweeps TX_STBC_POL over a few values
# unless TX_STBC_POL is pre-set. Same idea as the LDPC D_TM sweep.
set -u
cd "$(dirname "$0")/.."
FORK="$HOME/git/gr-ieee802-11" ; DEVDIR="$HOME/git/devourer"
CH="${CH:-1}" ; FREQ="${FREQ:-2412e6}" ; TXGAIN="${TXGAIN:-80}" ; SECS="${SECS:-12}"
POLS="${TX_STBC_POL:-3 4 2 5}"
export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192
CAP="$HOME/sdr-caps/stbc.cf32" ; mkdir -p "$HOME/sdr-caps"
RXLOG=$(mktemp /tmp/txdev-stbc.XXXXXX) ; TXBIN=/tmp/tx_gen
cleanup(){ sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; pkill -9 -f rf_tx_air2 2>/dev/null; rm -f "$RXLOG" "$CAP" "$CAP".ant1 "$CAP".one; }
trap cleanup EXIT INT TERM

g++ -O2 -std=c++17 -I"$FORK/include" "$FORK/lib/tx/tx_gen.cc" "$FORK/lib/tx/wifi_tx.cc" -o "$TXBIN" || exit 1
sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; sleep 2
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_RX_DUMP_ALL=1 DEVOURER_RX_KEEP_CORRUPTED=1 \
  "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>/dev/null &
echo "[stbc] warming up chip RX (14s) ..."; sleep 14

best=""
for pol in $POLS; do
  STBC_2ANT=1 TX_STBC_POL=$pol "$TXBIN" "$CAP" stbc 0 20 2>/dev/null
  b0=$(grep "rate=12 " "$RXLOG" | grep "stbc=1" | grep -c "crc_err=0")
  timeout $((SECS + 8)) python3 rf_tx_air2.py --interleaved "$CAP" \
    --freq "$FREQ" --bw 20e6 --tx-gain "$TXGAIN" --secs "$SECS" >/dev/null 2>&1
  b1=$(grep "rate=12 " "$RXLOG" | grep "stbc=1" | grep -c "crc_err=0")
  echo "[stbc] TX_STBC_POL=$pol -> clean(rate=12 stbc=1 crc0) delta = $((b1 - b0))"
  [ $((b1 - b0)) -gt 0 ] && best=$pol
done
sudo pkill -9 -x WiFiDriverDemo 2>/dev/null
echo "[stbc] any stbc=1 frames seen (incl corrupted):"
grep -aoE "rate=12 bw=[0-9]+ stbc=[0-9]+ ldpc=[0-9]+" "$RXLOG" | grep "stbc=1" | sort | uniq -c | head
if [ -n "$best" ]; then echo "[stbc] RESULT: PASS at TX_STBC_POL=$best"; else echo "[stbc] RESULT: FAIL (no clean STBC frame)"; fi