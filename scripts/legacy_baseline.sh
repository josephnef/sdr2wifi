#!/usr/bin/env bash
# Over-air sanity baseline: does devourer's most ROBUST TX (PrecoderDemo, legacy
# 6 Mbps BPSK 1/2) reach the B210 decodably? On a quiet channel any decoded frame
# is devourer's. This discriminates "HT/VHT monitor-injection is just weak" (legacy
# decodes, HT/VHT don't) from "bench RF coupling has degraded" (even legacy fails).
#
#   CH=149 FREQ=5745e6 bash scripts/legacy_baseline.sh
set -u
cd "$(dirname "$0")/.."
DEVDIR="$HOME/git/devourer"
CH="${CH:-149}" ; FREQ="${FREQ:-5745e6}" ; BW=20e6 ; RXGAIN="${RXGAIN:-70}" ; SECS="${SECS:-3}"
PID="${PID:-0x8812}"

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

CAP=$(mktemp /tmp/legbase.XXXXXX.cf32) ; TXLOG=$(mktemp /tmp/legbase-tx.XXXXXX) ; ERR=$(mktemp /tmp/legbase-rep.XXXXXX)
cleanup() { sudo pkill -f PrecoderDemo 2>/dev/null; rm -f "$CAP" "$TXLOG" "$ERR"; }
trap cleanup EXIT INT TERM

sudo pkill -f PrecoderDemo 2>/dev/null; sleep 2
echo "[leg] PrecoderDemo (legacy 6M) TX on PID=$PID ch$CH ($FREQ) ..."
sudo env DEVOURER_PID=$PID DEVOURER_CHANNEL=$CH "$DEVDIR/build/PrecoderDemo" >"$TXLOG" 2>&1 &
sleep 8
SENT=$(grep -c -iE "sent|tx" "$TXLOG")
echo "[leg] TX log lines indicating activity: $SENT"
echo "[leg] capturing ${SECS}s IQ at gain $RXGAIN ..."
python3 iq_capture.py --freq "$FREQ" --bw $BW --secs "$SECS" --rx-gain "$RXGAIN" --out "$CAP" 2>/dev/null
sudo pkill -f PrecoderDemo 2>/dev/null
rms=$(python3 -c "import numpy as np;x=np.fromfile('$CAP',dtype=np.complex64);print('%.4f'%np.sqrt(np.mean(np.abs(x)**2)))" 2>/dev/null)
echo "[leg] capture RMS: $rms"
timeout 90 python3 tools_replay_iq.py --in "$CAP" --bw $BW >"$ERR" 2>&1 || true
n=$(grep -oE 'decoded \(legacy\) frames: [0-9]+' "$ERR" | grep -oE '[0-9]+$' | tail -1)
echo "[leg] decoded legacy frames (clean channel -> these are devourer's): ${n:-0}"
[ "${n:-0}" -gt 0 ] && echo "[leg] RESULT: PASS (devourer legacy reaches SDR; HT/VHT weakness is injection-specific)" \
                     || echo "[leg] RESULT: FAIL (even legacy not decoded -> bench coupling / TX power limited)"
