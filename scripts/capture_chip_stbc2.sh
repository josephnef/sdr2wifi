#!/usr/bin/env bash
# Capture a real chip-transmitted HT STBC frame on TWO B210 RX channels simultaneously,
# so the two transmit streams can be separated (2x2 channel solve) and the chip's STBC
# convention read off the air. Writes <out>.0 and <out>.1.
set -u
cd "$(dirname "$0")/.."
DEVDIR="$HOME/git/devourer"
OUT="${1:-$HOME/sdr-caps/chip_stbc2.cf32}" ; STBC="${2:-1}"
CH=1 ; FREQ=2412e6 ; MCS=0 ; SECS="${SECS:-5}" ; RXGAIN="${RXGAIN:-50}"
TXLOG=$(mktemp /tmp/chiptx2.XXXXXX)
cleanup(){ sudo pkill -9 -x WiFiDriverTxDemo 2>/dev/null; pkill -9 -f iq_capture2 2>/dev/null; rm -f "$TXLOG"; }
trap cleanup EXIT INT TERM
mkdir -p "$(dirname "$OUT")"

sudo pkill -9 -x WiFiDriverTxDemo 2>/dev/null; sleep 2
for d in /sys/bus/usb/devices/*/idProduct; do
  [ "$(cat "$d" 2>/dev/null)" = "8812" ] || continue
  ifc="$(basename "$(dirname "$d")")":1.0
  [ -e "/sys/bus/usb/drivers/rtw88_8812au/$ifc" ] && \
    echo -n "$ifc" | sudo tee /sys/bus/usb/drivers/rtw88_8812au/unbind >/dev/null 2>&1 && echo "[cap] unbound rtw88_8812au"
done
sleep 1
echo "[cap] chip HT MCS$MCS STBC=$STBC TX (ch$CH) ..."
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_TX_HT_MCS=1 DEVOURER_TX_MCS=$MCS \
  DEVOURER_TX_STBC=$STBC "$DEVDIR/build/WiFiDriverTxDemo" >"$TXLOG" 2>&1 &
sleep 15
grep -iE "fixed rate" "$TXLOG" | tail -1 | sed 's/^/[cap-tx] /'
echo "[cap] 2-channel capture ${SECS}s @ ${FREQ} gain $RXGAIN ..."
timeout $((SECS + 6)) python3 iq_capture2.py --out "$OUT" --freq "$FREQ" --bw 20e6 \
  --rx-gain "$RXGAIN" --secs "$SECS" 2>&1 | grep -iE "done|error|overflow" | tail -2 | sed 's/^/[cap-rx] /'
sudo pkill -9 -x WiFiDriverTxDemo 2>/dev/null
echo "[cap] wrote ${OUT}.0 ($(stat -c%s "$OUT.0" 2>/dev/null||echo 0)B) and ${OUT}.1 ($(stat -c%s "$OUT.1" 2>/dev/null||echo 0)B)"
