#!/usr/bin/env bash
# Over-air STBC retest after the HT-LTF P-matrix fix (standard [[1,-1],[1,1]]). B210
# transmits the 2-antenna STBC frame (2 file_sources, sc8); the RTL8812AU (devourer RX)
# decodes. PASS = canonical-SA <devourer-stream> with rate=12 stbc=1 crc_err=0.
set -u
cd "$(dirname "$0")/.."
FORK="$HOME/git/gr-ieee802-11" ; DEVDIR="$HOME/git/devourer"
CH=1 ; FREQ=2412e6 ; TXGAIN="${TXGAIN:-80}" ; SECS="${SECS:-18}"
export PREFIX="$HOME/grwifi-install"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*'|head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"; ulimit -n 8192
CAP="$HOME/sdr-caps/sr.cf32" ; RXLOG=$(mktemp /tmp/stbcrt.XXXXXX) ; TXBIN=/tmp/tx_gen
cleanup(){ sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; pkill -9 -f rf_tx_air2 2>/dev/null; rm -f "$RXLOG" "$CAP" "$CAP".a0 "$CAP".a1 "$CAP".one; }
trap cleanup EXIT INT TERM
g++ -O2 -std=c++17 -I"$FORK/include" "$FORK/lib/tx/tx_gen.cc" "$FORK/lib/tx/wifi_tx.cc" -o "$TXBIN" || exit 1
STBC_2ANT=1 "$TXBIN" "$CAP" stbc 0 20 2>/dev/null
python3 -c "import numpy as np;x=np.fromfile('$CAP',dtype=np.complex64);x[0::2].tofile('$CAP.a0');x[1::2].tofile('$CAP.a1')"
# 8812 -> RX: unbind kernel driver, run devourer RX
sudo pkill -9 -f WiFiDriver 2>/dev/null; sleep 2
for d in /sys/bus/usb/devices/*/idProduct; do [ "$(cat "$d" 2>/dev/null)" = "8812" ] && \
  echo -n "$(basename "$(dirname "$d")")":1.0 | sudo tee /sys/bus/usb/drivers/rtw88_8812au/unbind >/dev/null 2>&1; done; sleep 1
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_STREAM_OUT=1 \
  "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>/dev/null &
echo "[stbc] RX warmup 14s ..."; sleep 14
timeout $((SECS+8)) python3 rf_tx_air2.py --in0 "$CAP.a0" --in1 "$CAP.a1" --otw sc8 \
  --freq "$FREQ" --bw 20e6 --tx-gain "$TXGAIN" --secs "$SECS" >/dev/null 2>&1
sudo pkill -9 -x WiFiDriverDemo 2>/dev/null; sleep 1
echo "[stbc] canonical-SA STBC frames:"; grep -a devourer-stream "$RXLOG" | grep "stbc=1" | head -4
CLEAN=$(grep -a devourer-stream "$RXLOG" | grep "stbc=1" | grep -c "crc_err=0")
echo "[stbc] clean (canonical-SA stbc=1 crc0): $CLEAN"
[ "$CLEAN" -gt 0 ] && echo "[stbc] RESULT: PASS -- chip decoded the fork's STBC TX" || echo "[stbc] RESULT: FAIL"
