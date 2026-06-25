#!/usr/bin/env bash
# Which B210 RX port is the antenna actually on? Capture the SAME devourer TX burst
# on RX2 and on TX/RX and compare RMS. The higher-power port is where the antenna is
# connected; iq_capture.py defaults to RX2, so if TX/RX wins we've been capturing on
# the dead port (a software-fixable cause of weak coupling, not a physics limit).
set -u
cd "$(dirname "$0")/.."
DEVDIR="$HOME/git/devourer"
CH="${CH:-149}" ; FREQ="${FREQ:-5745e6}" ; BW=20e6 ; RXGAIN="${RXGAIN:-65}"

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

TXLOG=$(mktemp /tmp/aport-tx.XXXXXX)
cleanup() { sudo pkill -f WiFiDriverTxDemo 2>/dev/null; rm -f "$TXLOG" /tmp/aport_*.cf32; }
trap cleanup EXIT INT TERM

rms() { python3 -c "import numpy as np;x=np.fromfile('$1',dtype=np.complex64);print('%.5f'%np.sqrt(np.mean(np.abs(x)**2)))" 2>/dev/null; }

sudo pkill -f WiFiDriverTxDemo 2>/dev/null; sleep 3
echo "[aport] devourer HT MCS0 TX on 8812 ch$CH ($FREQ) ..."
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_TX_MCS=0 DEVOURER_TX_HT_MCS=1 DEVOURER_TX_BW=20 \
  "$DEVDIR/build/WiFiDriverTxDemo" >"$TXLOG" 2>&1 &
sleep 8
echo "[aport] TX fixed_rate=$(grep -m1 -oE 'fixed rate:[0-9]+' "$TXLOG" | grep -oE '[0-9]+$')  sent=$(grep -c 'sent successfully' "$TXLOG")"

for ANT in RX2 "TX/RX"; do
  tag=$(echo "$ANT" | tr -d '/')
  out=/tmp/aport_${tag}.cf32
  python3 iq_capture.py --freq "$FREQ" --bw $BW --secs 2 --rx-gain "$RXGAIN" --rx-ant "$ANT" --out "$out" 2>/dev/null
  echo "[aport] antenna=$ANT  RMS=$(rms "$out")"
done

# baseline noise (no TX) on RX2 for reference
sudo pkill -f WiFiDriverTxDemo 2>/dev/null; sleep 2
python3 iq_capture.py --freq "$FREQ" --bw $BW --secs 1 --rx-gain "$RXGAIN" --rx-ant RX2 --out /tmp/aport_noise.cf32 2>/dev/null
echo "[aport] noise floor (no TX, RX2) RMS=$(rms /tmp/aport_noise.cf32)"
