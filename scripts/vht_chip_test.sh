#!/usr/bin/env bash
# Over-air proof for the fork's VHT TX (build_vht20 / build_vht40 in lib/tx/): the B210
# transmits a single-antenna VHT frame built by tx_gen and a real RTL8812AU (devourer)
# receives it. PASS = devourer's <devourer-stream> rate == expected VHT DESC_RATE with
# crc_err=0 (and bw=1 for 40 MHz). 256-QAM (MCS8/9) needs a high link SNR -> high TXGAIN.
#
#   MCS=0 BW=20 bash scripts/vht_chip_test.sh
#   MCS=0 BW=40 bash scripts/vht_chip_test.sh        # 40 MHz RX via DEVOURER_BW=40
#   MCS=8 BW=20 TXGAIN=89 bash scripts/vht_chip_test.sh
set -u
cd "$(dirname "$0")/.."
FORK="$HOME/git/gr-ieee802-11" ; DEVDIR="$HOME/git/devourer"
MCS="${MCS:-0}" ; BW="${BW:-20}"
CH="${CH:-1}" ; FREQ="${FREQ:-2412e6}" ; TXGAIN="${TXGAIN:-82}" ; SECS="${SECS:-22}"
export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192
CAP="$HOME/sdr-caps/vht_mcs${MCS}_bw${BW}.cf32" ; mkdir -p "$HOME/sdr-caps"
RXLOG=$(mktemp /tmp/vhtchip.XXXXXX) ; TXBIN=/tmp/tx_gen
cleanup(){ sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; pkill -9 -f rf_tx_air 2>/dev/null; rm -f "$RXLOG" "$CAP" "$CAP.one"; }
trap cleanup EXIT INT TERM

g++ -O2 -std=c++17 -I"$FORK/include" "$FORK/lib/tx/tx_gen.cc" "$FORK/lib/tx/wifi_tx.cc" -o "$TXBIN" || exit 1
"$TXBIN" "$CAP" vht "$MCS" "$BW" || exit 1
EXP=$(python3 -c "import desc_rate; print(desc_rate.encode_vht(1,$MCS))")
EXPBW=$([ "$BW" = 40 ] && echo 1 || echo 0)
echo "[vht] tx VHT MCS$MCS BW$BW on ch$CH; expect devourer rate=$EXP bw=$EXPBW"

# devourer RX: 40 MHz needs DEVOURER_BW=40 (env-gated wide RX).
DEVBW_ENV=""; [ "$BW" = 40 ] && DEVBW_ENV="DEVOURER_BW=40"
sudo pkill -9 -f WiFiDriverDemo 2>/dev/null; sleep 2
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_STREAM_OUT=1 $DEVBW_ENV \
  "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>/dev/null &
sleep 14
timeout $((SECS + 10)) python3 rf_tx_air.py --in "$CAP" --freq "$FREQ" --bw "${BW}e6" \
  --tx-gain "$TXGAIN" --secs "$SECS" >/dev/null 2>&1
sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; sleep 1

echo "[vht] sample <devourer-stream> lines:"; grep -a devourer-stream "$RXLOG" | grep "rate=$EXP" | head -3
CLEAN=$(grep -a "rate=$EXP " "$RXLOG" | grep -c "crc_err=0")
echo "[vht] clean (rate=$EXP crc0): $CLEAN"
[ "$CLEAN" -gt 0 ] && echo "[vht] RESULT: PASS -- chip decoded the fork's VHT MCS$MCS BW$BW TX" || echo "[vht] RESULT: FAIL"
