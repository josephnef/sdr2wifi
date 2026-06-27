#!/usr/bin/env bash
# On-air validation for the fork's HT40 LDPC TX (build_ht40_ldpc), and the tool to
# RESOLVE its tone mapping. The B210 transmits an HT40 MCS0 LDPC frame built by the
# fork's TX core (tx_gen "ldpc <mcs> 40"); a real RTL8812AU (devourer) receives it.
# PASS = a <devourer-stream> line with rate=12 (HT MCS0), bw=1 (40 MHz), ldpc=1,
# crc_err=0.
#
# WHY THIS EXISTS: the LDPC tone-mapping distance the RTL8812AU's decoder actually
# wants is unresolved. HT20 LDPC empirically needs IDENTITY (D_TM=1) -- D_TM>1 fails
# the chip -- contrary to the spec's D_TM=4. HT40's value (spec says 6) is therefore
# unknown, so this sweeps GR_LDPC_HT40_DTM and reports which value the chip decodes.
# Whatever passes becomes the build_ht40_ldpc default.
#
#   bash scripts/tx_devourer_ht40_ldpc.sh            # sweep D_TM in {1 6}
#   DTMS="1 2 3 4 6 9" bash scripts/tx_devourer_ht40_ldpc.sh
set -u
cd "$(dirname "$0")/.."
FORK="${FORK:-$HOME/git/gr-ieee802-11}"
DEVDIR="${DEVDIR:-$HOME/git/devourer}"
CH="${CH:-1}" ; FREQ="${FREQ:-2412e6}" ; TXGAIN="${TXGAIN:-89}" ; SECS="${SECS:-20}"
MCS="${MCS:-0}" ; TX_LEN="${TX_LEN:-24}" ; export TX_LEN
DTMS="${DTMS:-1 6}"              # tone-mapping distances to try
EXP=$((12 + MCS))               # devourer DESC_RATE for HT MCS0

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

CAP="$HOME/sdr-caps/txtest_ht40ldpc.cf32" ; mkdir -p "$HOME/sdr-caps"
RXLOG=$(mktemp /tmp/txdev-ht40ldpc.XXXXXX) ; TXBIN=/tmp/tx_gen
cleanup() { sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; pkill -9 -f rf_tx_air 2>/dev/null
            rm -f "$RXLOG" "$CAP"; }
trap cleanup EXIT INT TERM

CHOFFSET="${CHOFFSET:-1}"        # HT40+ (secondary above primary) -- matches tx_gen dup40

echo "[ht40-ldpc] building tx_gen ..."
g++ -O2 -std=c++17 -I"$FORK/include" "$FORK/lib/tx/tx_gen.cc" "$FORK/lib/tx/wifi_tx.cc" \
    -o "$TXBIN" || exit 1

# Run one cell: TX the capture, chip RX in HT40 mode, return the decoded-frame counts via
# globals CLEAN (rate=EXP bw=1 crc0 ldpc=$2) and ANYBW40 (any bw=1 frame at all).
# $1 = cap file, $2 = expected ldpc flag (0|1)
rx_cell() {
  : > "$RXLOG"
  sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; sleep 2
  sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_BW=40 DEVOURER_CHOFFSET=$CHOFFSET \
    DEVOURER_STREAM_OUT=1 DEVOURER_RX_DUMP_ALL=1 DEVOURER_RX_KEEP_CORRUPTED=1 \
    "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>/dev/null &
  sleep 14
  timeout $((SECS + 10)) python3 rf_tx_air.py --in "$1" --freq "$FREQ" --bw 40e6 \
    --tx-gain "$TXGAIN" --secs "$SECS" >/dev/null 2>&1
  sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; sleep 1
  # field order in <devourer-stream>: rate ... crc_err ... bw ... ldpc
  CLEAN=$(grep -c "rate=$EXP .*crc_err=0.*bw=1.*ldpc=$2" "$RXLOG")
  ANYBW40=$(grep -c "bw=1" "$RXLOG")
}

# --- sanity: HT40 BCC must decode, proving the RF/channel/bandwidth setup is right
# (so an LDPC failure below is the tone map, not channelization). ---
echo "=== sanity: HT40 BCC (MCS$MCS) ==="
"$TXBIN" "$CAP" ht "$MCS" 40 2>/dev/null || exit 1
rx_cell "$CAP" 0
echo "[ht40-ldpc] HT40 BCC clean frames=$CLEAN  (any bw=1 frames=$ANYBW40)"
if [ "$CLEAN" -eq 0 ]; then
  echo "[ht40-ldpc] HT40 BCC did NOT decode -> RF/channel setup wrong (freq/offset/bw)."
  echo "             Fix that before trusting the LDPC sweep. Sample lines:"
  grep -a "devourer-stream" "$RXLOG" | head -3
fi

WINNER=""
for DTM in $DTMS; do
  echo "=== HT40 LDPC D_TM=$DTM ==="
  GR_LDPC_HT40_DTM="$DTM" "$TXBIN" "$CAP" ldpc "$MCS" 40 2>/dev/null || { echo "tx_gen failed"; continue; }
  rx_cell "$CAP" 1
  echo "[ht40-ldpc] D_TM=$DTM: clean HT40-LDPC frames (rate=$EXP bw=1 crc0 ldpc1) = $CLEAN"
  grep -a "devourer-stream" "$RXLOG" | grep "ldpc=1" | head -2
  [ "$CLEAN" -gt 0 ] && { WINNER="$DTM"; echo "[ht40-ldpc] D_TM=$DTM DECODES on the chip"; }
done

echo
if [ -n "$WINNER" ]; then
  echo "[ht40-ldpc] RESULT: PASS -- RTL8812AU decodes HT40 LDPC at D_TM=$WINNER"
  echo "[ht40-ldpc] -> set that as build_ht40_ldpc's default (the env knob's fallback)."
else
  echo "[ht40-ldpc] RESULT: FAIL -- no D_TM in '$DTMS' decoded. Widen DTMS or check HT-SIG/geometry."
fi
