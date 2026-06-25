#!/usr/bin/env bash
# Decisive test for the over-air "strong signal, 0 decodes" blocker: is the B210
# capturing a SPECTRALLY-INVERTED (conjugate/mirrored) version of devourer's TX?
# Signature: L-STF autocorr is conjugate-IMMUNE so sync_short plateaus either way,
# but sync_long's L-LTF cross-correlation only locks on the correct spectral sense.
# So if conj(capture) decodes and the original does not, inversion is confirmed and
# the fix is to conjugate the B210 RX stream.
#
# Captures to a DISK path (NOT /tmp, which is tmpfs/RAM here) and deletes after, to
# avoid the RAM exhaustion that wedged the host last run.
set -u
cd "$(dirname "$0")/.."
DEVDIR="$HOME/git/devourer"
CH="${CH:-149}" ; FREQ="${FREQ:-5745e6}" ; BW=20e6 ; RXGAIN="${RXGAIN:-65}" ; SECS="${SECS:-1}"
MCS="${MCS:-0}"
CAPDIR="${CAPDIR:-$HOME/sdr-caps}" ; mkdir -p "$CAPDIR"
CAP="$CAPDIR/si_orig.cf32" ; CONJ="$CAPDIR/si_conj.cf32"

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

TXLOG=$(mktemp /tmp/si-tx.XXXXXX)
cleanup() { sudo pkill -f WiFiDriverTxDemo 2>/dev/null; rm -f "$TXLOG" "$CAP" "$CONJ"; }
trap cleanup EXIT INT TERM

replay_count() { # $1 file -> prints "legacy=N htsig=M htdata_pass=P"
  local out; out=$(timeout 120 python3 tools_replay_iq.py --in "$1" --bw $BW 2>&1)
  local l h p
  l=$(printf '%s' "$out" | grep -oE 'decoded \(legacy\) frames: [0-9]+' | grep -oE '[0-9]+$' | tail -1)
  h=$(printf '%s' "$out" | grep -c 'HT-SIG] CRC-OK')
  p=$(printf '%s' "$out" | grep -c 'HT-DATA.*PASS')
  echo "legacy=${l:-0} htsig=${h} htdata_pass=${p}"
}

sudo pkill -f WiFiDriverTxDemo 2>/dev/null; sleep 3
echo "[si] devourer HT MCS$MCS TX on 8812 ch$CH ($FREQ) ..."
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_TX_MCS=$MCS DEVOURER_TX_HT_MCS=1 DEVOURER_TX_BW=20 \
  "$DEVDIR/build/WiFiDriverTxDemo" >"$TXLOG" 2>&1 &
sleep 6
echo "[si] TX fixed_rate=$(grep -m1 -oE 'fixed rate:[0-9]+' "$TXLOG" | grep -oE '[0-9]+$')  sent=$(grep -c 'sent successfully' "$TXLOG")"
echo "[si] capturing ${SECS}s -> $CAP ..."
python3 iq_capture.py --freq "$FREQ" --bw $BW --secs "$SECS" --rx-gain "$RXGAIN" --rx-ant RX2 --out "$CAP" 2>/dev/null
sudo pkill -f WiFiDriverTxDemo 2>/dev/null

echo "[si] writing conjugate ..."
python3 - "$CAP" "$CONJ" <<'PY'
import numpy as np, sys
x=np.fromfile(sys.argv[1],dtype=np.complex64)
np.conj(x).astype(np.complex64).tofile(sys.argv[2])
print(f"[si]   samples={len(x)} rms={np.sqrt(np.mean(np.abs(x)**2)):.4f}")
PY

echo "[si] ORIGINAL  replay: $(replay_count "$CAP")"
rm -f "$CAP"                      # free RAM-equivalent before the second replay
echo "[si] CONJUGATE replay: $(replay_count "$CONJ")"
echo "[si] -> if CONJUGATE decodes and ORIGINAL does not: SPECTRAL INVERSION confirmed."
