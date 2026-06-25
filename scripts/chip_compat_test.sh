#!/usr/bin/env bash
# Does the RTL8812AU's PHY decode the fork's TX frame AT ALL? Transmit it while the
# chip RX runs with DUMP_ALL (every decoded frame, any SA/CRC) + KEEP_CORRUPTED +
# STREAM_OUT. Compare ambient-frame count before/after TX and look for the canonical
# SA. 0 canonical-SA + no rate-bump => chip-compat PHY reject; crc_err=1 => decoded
# but imperfect.
set -u
cd "$(dirname "$0")/.."
FORK="$HOME/git/gr-ieee802-11" ; DEVDIR="$HOME/git/devourer"
FMT="${FMT:-ht}"
MCS="${MCS:-0}" ; CH="${CH:-1}" ; FREQ="${FREQ:-2412e6}" ; TXGAIN="${TXGAIN:-89}" ; SECS="${SECS:-20}"
EXPRATE=$(python3 -c "import desc_rate; print(desc_rate.encode_ht($MCS) if '$FMT'=='ht' else desc_rate.encode_vht(1,$MCS))")
export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192
CAP="$HOME/sdr-caps/cc.cf32" ; mkdir -p "$HOME/sdr-caps"
RXLOG=$(mktemp /tmp/cct.XXXXXX)
cleanup(){ sudo pkill -9 -f WiFiDriverDemo 2>/dev/null; pkill -9 -f rf_tx_air 2>/dev/null; rm -f "$RXLOG" "$CAP" "$CAP.one"; }
trap cleanup EXIT INT TERM

# IN=<cf32> uses that file (e.g. a GR-WiFi spec frame) as the control; else build ours.
if [ -n "${IN:-}" ]; then cp "$IN" "$CAP"; echo "[cct] using input $IN";
else
  g++ -O2 -std=c++17 -I"$FORK/include" "$FORK/lib/tx/tx_gen.cc" "$FORK/lib/tx/wifi_tx.cc" -o /tmp/tx_gen || exit 1
  /tmp/tx_gen "$CAP" "$FMT" "$MCS" 20 2>/dev/null
fi

sudo pkill -9 -f WiFiDriverDemo 2>/dev/null; sleep 2
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_STREAM_OUT=1 \
  DEVOURER_RX_KEEP_CORRUPTED=1 DEVOURER_RX_DUMP_ALL=1 \
  "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>/dev/null &
sleep 14
B=$(grep -c corrupt-any "$RXLOG")
timeout $((SECS + 8)) python3 rf_tx_air.py --in "$CAP" --freq "$FREQ" --bw 20e6 --tx-gain "$TXGAIN" --secs "$SECS" >/dev/null 2>&1
sudo pkill -9 -f WiFiDriverDemo 2>/dev/null
A=$(grep -c corrupt-any "$RXLOG")
SS=$(grep -c devourer-stream "$RXLOG")
echo "[cct] frames decoded by chip: before-TX=$B  after-TX=$A  (delta=$((A-B)))"
echo "[cct] canonical-SA <devourer-stream> lines (incl corrupted): $SS"
echo "[cct] rate=$EXPRATE ($FMT MCS$MCS rate=$EXPRATE) frames seen: $(grep -aoE "rate=$EXPRATE( |$)" "$RXLOG" | wc -l)"
echo "[cct] rate histogram of all decoded frames (DESC_RATE: 4=6M legacy, 12=HTMCS0):"
grep -aoE "rate=[0-9]+" "$RXLOG" | sort | uniq -c | sort -rn | head -8 | sed 's/^/[cct]   /'
grep -a devourer-stream "$RXLOG" | head -2
if [ "$SS" -gt 0 ]; then echo "[cct] -> chip DECODES the fork frame (SA matched)"; else echo "[cct] -> chip does NOT decode the fork frame's MAC (PHY reject or all-corrupt)"; fi
