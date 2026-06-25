#!/usr/bin/env bash
# Over-air VHT20 NSS=2 test. B210 transmits the 2 spatial streams (2 file_sources, sc8);
# the RTL8812AU (2T2R, devourer RX) separates them with its 2 RX antennas. PASS =
# canonical-SA <devourer-stream> with the VHT NSS=2 DESC_RATE and crc_err=0.
set -u
cd "$(dirname "$0")/.."
FORK="$HOME/git/gr-ieee802-11" ; DEVDIR="$HOME/git/devourer"
MCS="${MCS:-0}"   # per-stream VHT MCS
CH=1 ; FREQ=2412e6 ; TXGAIN="${TXGAIN:-82}" ; SECS="${SECS:-20}"
export PREFIX="$HOME/grwifi-install"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*'|head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"; ulimit -n 8192
EXP=$(python3 -c "import desc_rate; print(desc_rate.encode_vht(2,$MCS))")
CAP="$HOME/sdr-caps/vn2.cf32" ; RXLOG=$(mktemp /tmp/vn2.XXXXXX) ; TXBIN=/tmp/tx_gen
cleanup(){ sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; pkill -9 -f rf_tx_air2 2>/dev/null; rm -f "$RXLOG" "$CAP" "$CAP".a0 "$CAP".a1 "$CAP".one; }
trap cleanup EXIT INT TERM
g++ -O2 -std=c++17 -I"$FORK/include" "$FORK/lib/tx/tx_gen.cc" "$FORK/lib/tx/wifi_tx.cc" -o "$TXBIN" || exit 1
STBC_2ANT=1 "$TXBIN" "$CAP" vht2 "$MCS" 20 2>/dev/null
python3 -c "import numpy as np;x=np.fromfile('$CAP',dtype=np.complex64);x[0::2].tofile('$CAP.a0');x[1::2].tofile('$CAP.a1')"
sudo pkill -9 -f WiFiDriver 2>/dev/null; sleep 2
for d in /sys/bus/usb/devices/*/idProduct; do [ "$(cat "$d" 2>/dev/null)" = "8812" ] && \
  echo -n "$(basename "$(dirname "$d")")":1.0 | sudo tee /sys/bus/usb/drivers/rtw88_8812au/unbind >/dev/null 2>&1; done; sleep 1
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_STREAM_OUT=1 DEVOURER_RX_DUMP_ALL=1 \
  "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>/dev/null &
echo "[vht2] RX warmup 14s; expect rate=$EXP (VHT MCS$MCS, 2 SS) ..."; sleep 14
timeout $((SECS+8)) python3 rf_tx_air2.py --in0 "$CAP.a0" --in1 "$CAP.a1" --otw sc8 \
  --freq "$FREQ" --bw 20e6 --tx-gain "$TXGAIN" --secs "$SECS" >/dev/null 2>&1
sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; sleep 1
echo "[vht2] rate histogram:"; grep -aoE "rate=[0-9]+" "$RXLOG" | sort | uniq -c | sort -rn | head -5 | sed 's/^/[vht2]   /'
echo "[vht2] canonical-SA frames at rate=$EXP:"; grep -a devourer-stream "$RXLOG" | grep "rate=$EXP " | head -3
CLEAN=$(grep -a devourer-stream "$RXLOG" | grep "rate=$EXP " | grep -c "crc_err=0")
echo "[vht2] clean (canonical-SA rate=$EXP crc0): $CLEAN"
[ "$CLEAN" -gt 0 ] && echo "[vht2] RESULT: PASS -- chip decoded the fork's VHT NSS=2 TX" || echo "[vht2] RESULT: FAIL"
