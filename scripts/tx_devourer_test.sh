#!/usr/bin/env bash
# Over-air proof for the fork's modern-format TX: the B210 transmits a frame built
# by the fork's own TX core (lib/tx/, via tx_gen) and a real RTL8812AU (devourer)
# receives it, reporting the decoded DESC_RATE. PASS = devourer's <devourer-stream>
# rate matches the transmitted (format, MCS) with crc_err=0.
#
#   FMT=ht MCS=0 bash scripts/tx_devourer_test.sh
set -u
cd "$(dirname "$0")/.."
FORK="$HOME/git/gr-ieee802-11"
DEVDIR="$HOME/git/devourer"
FMT="${FMT:-ht}" ; MCS="${MCS:-0}" ; BW="${BW:-20}"
CH="${CH:-149}" ; FREQ="${FREQ:-5745e6}" ; TXGAIN="${TXGAIN:-70}" ; SECS="${SECS:-20}"

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

CAP="$HOME/sdr-caps/txtest_${FMT}_mcs${MCS}.cf32" ; mkdir -p "$HOME/sdr-caps"
RXLOG=$(mktemp /tmp/txdev-rx.XXXXXX) ; TXBIN=/tmp/tx_gen
cleanup() { sudo pkill -9 -f WiFiDriverDemo 2>/dev/null; pkill -9 -f rf_tx_air 2>/dev/null; rm -f "$RXLOG" "$CAP" "$CAP.one"; }
trap cleanup EXIT INT TERM

# build the standalone TX generator from the fork's TX core
echo "[txdev] building tx_gen ..."
g++ -O2 -std=c++17 -I"$FORK/include" "$FORK/lib/tx/tx_gen.cc" "$FORK/lib/tx/wifi_tx.cc" -o "$TXBIN" || exit 1
"$TXBIN" "$CAP" "$FMT" "$MCS" "$BW" || exit 1

# expected DESC_RATE
EXP=$(python3 -c "import desc_rate; print(desc_rate.encode_ht($MCS) if '$FMT'=='ht' else desc_rate.encode_vht(1,$MCS))")
echo "[txdev] transmitting $FMT MCS$MCS on ch$CH; expecting devourer DESC_RATE=$EXP"

sudo pkill -9 -f WiFiDriverDemo 2>/dev/null; sleep 2
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_STREAM_OUT=1 \
  "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>/dev/null &
sleep 14   # devourer RX init (firmware + channel + tx-power loop) takes ~13s
timeout $((SECS + 10)) python3 rf_tx_air.py --in "$CAP" --freq "$FREQ" --bw "${BW}e6" \
  --tx-gain "$TXGAIN" --secs "$SECS" >/dev/null 2>&1
sudo pkill -9 -f WiFiDriverDemo 2>/dev/null; sleep 1

N=$(grep -c "devourer-stream" "$RXLOG")
echo "[txdev] <devourer-stream> lines: $N"
grep -oE "<devourer-stream>rate=[0-9]+ len=[0-9]+ crc_err=[0-9]+[^ ]* .*bw=[0-9]+ stbc=[0-9]+ ldpc=[0-9]+ sgi=[0-9]+" "$RXLOG" | head -3
RATE=$(grep -m1 -oE "<devourer-stream>rate=[0-9]+" "$RXLOG" | grep -oE "[0-9]+$")
CLEAN=$(grep -c "rate=$EXP .*crc_err=0" "$RXLOG")
echo "[txdev] devourer reported rate=$RATE (expect $EXP); clean(rate+crc0)=$CLEAN"
if [ "${RATE:-0}" = "$EXP" ] && [ "$CLEAN" -gt 0 ]; then
  echo "[txdev] RESULT: PASS -- RTL8812AU received the fork's $FMT MCS$MCS TX"
else
  echo "[txdev] RESULT: FAIL"
fi
