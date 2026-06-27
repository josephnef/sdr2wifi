#!/usr/bin/env bash
# Rung-3 over-real-air fused FEC: devourer 8812 TX (HT MCS7, ch6) → B210 SDR RX
# (gr-ieee802-11 fork, GR_KEEP_CORRUPTED) → SBI salvage. Radiates on ch6.
set -u
DEV="$HOME/git/devourer"
HERE="$(cd "$(dirname "$0")" && pwd)"
TX_SYSFS=${TX_SYSFS:-9-2}
SECS=${SECS:-25}
RATE=${RATE:-MCS7}
RXGAIN=${RXGAIN:-52}
FEC="--symbol-size 32 --overhead 0.25 --blocks-per-body 10 --k 8"

KILL(){ sudo pkill -9 StreamTxDem 2>/dev/null; }
trap KILL EXIT

export PREFIX="$HOME/grwifi-install"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$HERE"
GRL="$PREFIX/lib:$PREFIX/lib64"; for d in "$PREFIX"/lib/*-linux-gnu; do [ -d "$d" ] && GRL="$d:$GRL"; done
export LD_LIBRARY_PATH="$GRL:${LD_LIBRARY_PATH:-}"; ulimit -n 8192

# free the 8812 from the kernel driver
for i in /sys/bus/usb/devices/$TX_SYSFS/$TX_SYSFS:*; do
  ifc=$(basename "$i"); drv=$(readlink -f "$i/driver" 2>/dev/null)
  [ -n "$drv" ] && echo "$ifc" | sudo tee "$drv/unbind" >/dev/null 2>&1
done; sleep 1

head -c 400000 /dev/urandom > /tmp/fr3_src.bin
python3 "$DEV/tools/precoder/fused_fec_tx.py" --input /tmp/fr3_src.bin --repeat 120 \
  $FEC >/tmp/fr3_bodies.bin 2>/dev/null
echo "[rung3] TX bodies: $(wc -c </tmp/fr3_bodies.bin) bytes  rate=$RATE"

# TX (single backgrounded command — StreamTxDemo reads the body file from stdin)
sudo env DEVOURER_VID=0x0bda DEVOURER_PID=0x8812 DEVOURER_CHANNEL=6 \
     DEVOURER_TX_RATE=$RATE "$DEV/build/StreamTxDemo" < /tmp/fr3_bodies.bin \
     >/tmp/fr3_tx.log 2>&1 &
sleep 6
echo "[rung3] TX status:"; grep -iE "TX ready|error|bulk-OUT" /tmp/fr3_tx.log | tail -2

# RX: B210 over-air decode (HT) + SBI salvage
python3 "$HERE/fused_fec_rung3.py" --freq 2437e6 --bw 20e6 --rx-gain "$RXGAIN" \
  --secs "$SECS" $FEC 2>/dev/null
