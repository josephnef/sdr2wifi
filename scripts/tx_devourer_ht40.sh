#!/usr/bin/env bash
# Over-air proof for the fork's HT40 (40 MHz) TX. The B210 transmits a 40 MHz frame
# built by the fork's own TX core (lib/tx/, via tx_gen, FMT=ht BW=40); a real RTL8812AU
# (devourer) running with DEVOURER_BW=40 (CHANNEL_WIDTH_40) receives it. PASS = a
# <devourer-stream> line at the expected HT DESC_RATE with bw=40 (cbw40) and crc_err=0.
#
# Frequency geometry: the B210 transmits the 40 MHz baseband centred at FREQ, which must
# equal the chip's 40 MHz LO CENTRE. devourer (get_40mhz_center_channel) only allows ONE
# 2.4 GHz 40 MHz config: CENTRE channel 6 (2437 MHz), with PRIMARY channel 4 (primary
# below centre) or 8 (primary above). So: DEVOURER_CHANNEL=4, B210 FREQ=2437e6.
#
#   MCS=0 bash scripts/tx_devourer_ht40.sh           # primary ch4
#   CH=8 bash scripts/tx_devourer_ht40.sh            # primary ch8 (other half)
set -u
cd "$(dirname "$0")/.."
FORK="$HOME/git/gr-ieee802-11"
DEVDIR="$HOME/git/devourer"
MCS="${MCS:-0}"
CH="${CH:-4}"            # devourer PRIMARY 20 MHz channel (4 or 8 for 2.4 GHz 40 MHz)
CHOFFSET="${CHOFFSET:-1}"  # devourer derives the real offset from channel vs centre
FREQ="${FREQ:-2437e6}"   # B210 centre = 40 MHz centre = channel 6
TXGAIN="${TXGAIN:-89}" ; SECS="${SECS:-20}"

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

CAP="$HOME/sdr-caps/txtest_ht40_mcs${MCS}.cf32" ; mkdir -p "$HOME/sdr-caps"
RXLOG=$(mktemp /tmp/txdev40-rx.XXXXXX) ; TXBIN=/tmp/tx_gen
cleanup() { sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; pkill -9 -f rf_tx_air 2>/dev/null; rm -f "$RXLOG" "$CAP" "$CAP.one"; }
trap cleanup EXIT INT TERM

echo "[ht40] building tx_gen ..."
g++ -O2 -std=c++17 -I"$FORK/include" "$FORK/lib/tx/tx_gen.cc" "$FORK/lib/tx/wifi_tx.cc" -o "$TXBIN" || exit 1
"$TXBIN" "$CAP" ht "$MCS" 40 2>/dev/null || exit 1

EXP=$(python3 -c "import desc_rate; print(desc_rate.encode_ht($MCS))")
echo "[ht40] TX ht MCS$MCS BW40 @ ${FREQ}Hz; chip primary ch$CH offset=$CHOFFSET; expect DESC_RATE=$EXP bw=40"

sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; sleep 2
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_BW=40 DEVOURER_CHOFFSET=$CHOFFSET \
  DEVOURER_STREAM_OUT=1 DEVOURER_RX_DUMP_ALL=1 DEVOURER_RX_KEEP_CORRUPTED=1 \
  "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>/dev/null &
sleep 14   # devourer RX init (firmware + channel + tx-power loop) takes ~13s
timeout $((SECS + 10)) python3 rf_tx_air.py --in "$CAP" --freq "$FREQ" --bw 40e6 \
  --tx-gain "$TXGAIN" --secs "$SECS" >/dev/null 2>&1
sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; sleep 1

echo "[ht40] all decoded-frame rates (DESC_RATE histogram):"
grep -aoE "rate=[0-9]+" "$RXLOG" | sort | uniq -c | sort -rn | head -6 | sed 's/^/[ht40]   /'
SS=$(grep -c "devourer-stream" "$RXLOG")
echo "[ht40] canonical-SA <devourer-stream> lines: $SS"
grep -aoE "<devourer-stream>rate=[0-9]+ len=[0-9]+ crc_err=[0-9]+[^ ]* .*bw=[0-9]+ stbc=[0-9]+ ldpc=[0-9]+ sgi=[0-9]+" "$RXLOG" | head -3
# devourer reports bw as the ChannelWidth_t enum (0=20 MHz, 1=40 MHz), not the MHz value.
CLEAN=$(grep -c "rate=$EXP .*crc_err=0.*bw=1 " "$RXLOG")
echo "[ht40] clean HT40 (rate=$EXP crc0 bw=1[40MHz]) frames: $CLEAN"
if [ "$CLEAN" -gt 0 ]; then
  echo "[ht40] RESULT: PASS -- RTL8812AU received the fork's HT40 MCS$MCS TX at 40 MHz"
else
  echo "[ht40] RESULT: FAIL (try CHOFFSET=2 with FREQ=2402e6, or sweep FREQ)"
fi
