#!/usr/bin/env bash
# Capture a REAL chip-transmitted STBC frame as a reference. An RTL8812AU (devourer
# WiFiDriverTxDemo, DEVOURER_TX_STBC=1, HT MCS0) puts a spec-correct STBC beacon on the
# air; the B210 records it. The capture is the ground truth to diff our build_stbc against
# (HT-SIG bits, HT-LTF P-matrix, pilot pattern, Alamouti ordering).
#
#   bash scripts/capture_chip_stbc.sh [out.cf32] [stbc]   # stbc=1 default; 0 = plain HT
set -u
cd "$(dirname "$0")/.."
DEVDIR="$HOME/git/devourer"
OUT="${1:-$HOME/sdr-caps/chip_stbc.cf32}" ; STBC="${2:-1}"
CH=1 ; FREQ=2412e6 ; MCS=0 ; SECS="${SECS:-4}" ; RXGAIN="${RXGAIN:-55}"
TXLOG=$(mktemp /tmp/chiptx.XXXXXX)
cleanup(){ sudo pkill -9 -x WiFiDriverTxDemo 2>/dev/null; pkill -9 -f iq_capture 2>/dev/null; rm -f "$TXLOG"; }
trap cleanup EXIT INT TERM
mkdir -p "$(dirname "$OUT")"

sudo pkill -9 -x WiFiDriverTxDemo 2>/dev/null; sleep 2
# The rtw88_8812au kernel driver re-binds to the adapter between runs; unbind it so
# libusb can claim interface 0 (otherwise WiFiDriverTxDemo aborts at the claim assert).
for d in /sys/bus/usb/devices/*/idProduct; do
  if [ "$(cat "$d" 2>/dev/null)" = "8812" ]; then
    iface=$(basename "$(dirname "$d")"):1.0
    [ -e "/sys/bus/usb/drivers/rtw88_8812au/$iface" ] && \
      echo -n "$iface" | sudo tee /sys/bus/usb/drivers/rtw88_8812au/unbind >/dev/null 2>&1 && \
      echo "[cap] unbound rtw88_8812au from $iface"
  fi
done
sleep 1
echo "[cap] starting chip STBC TX (8812, MCS$MCS STBC=$STBC, ch$CH) ..."
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_TX_HT_MCS=1 DEVOURER_TX_MCS=$MCS \
  DEVOURER_TX_STBC=$STBC \
  "$DEVDIR/build/WiFiDriverTxDemo" >"$TXLOG" 2>&1 &
sleep 15   # firmware + channel + tx-power loop, then it starts injecting
grep -iE "STBC|MCS|inject|Listening|firmware" "$TXLOG" | tail -4 | sed 's/^/[cap-tx] /'
echo "[cap] capturing ${SECS}s on B210 @ ${FREQ} ..."
timeout $((SECS + 6)) python3 iq_capture.py --out "$OUT" --freq "$FREQ" --bw 20e6 \
  --rx-gain "$RXGAIN" --secs "$SECS" 2>&1 | grep -iE "captur|samples|error" | tail -2 | sed 's/^/[cap-rx] /'
sudo pkill -9 -x WiFiDriverTxDemo 2>/dev/null
echo "[cap] wrote $OUT ($(stat -c%s "$OUT" 2>/dev/null || echo 0) bytes)"