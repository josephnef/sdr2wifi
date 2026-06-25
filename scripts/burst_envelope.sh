#!/usr/bin/env bash
# Is devourer's over-air signal present as clean decodable BURSTS, or just elevated
# noise? Capture during a devourer TX and analyse the power envelope: peak burst SNR,
# burst count, duty cycle. Clean bursts at high peak SNR but 0 decodes => a sync/decode
# problem; flat low envelope => a coupling/SNR physics limit.
set -u
cd "$(dirname "$0")/.."
DEVDIR="$HOME/git/devourer"
CH="${CH:-149}" ; FREQ="${FREQ:-5745e6}" ; BW=20e6 ; RXGAIN="${RXGAIN:-65}" ; SECS="${SECS:-2}"
MCS="${MCS:-0}"

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

TXLOG=$(mktemp /tmp/be-tx.XXXXXX) ; CAP=/tmp/burst.cf32
cleanup() { sudo pkill -f WiFiDriverTxDemo 2>/dev/null; rm -f "$TXLOG"; }
trap cleanup EXIT INT TERM

sudo pkill -f WiFiDriverTxDemo 2>/dev/null; sleep 3
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH DEVOURER_TX_MCS=$MCS DEVOURER_TX_HT_MCS=1 DEVOURER_TX_BW=20 \
  "$DEVDIR/build/WiFiDriverTxDemo" >"$TXLOG" 2>&1 &
sleep 6
echo "[be] TX fixed_rate=$(grep -m1 -oE 'fixed rate:[0-9]+' "$TXLOG" | grep -oE '[0-9]+$')  sent=$(grep -c 'sent successfully' "$TXLOG")"
python3 iq_capture.py --freq "$FREQ" --bw $BW --secs "$SECS" --rx-gain "$RXGAIN" --rx-ant RX2 --out "$CAP" 2>/dev/null
sudo pkill -f WiFiDriverTxDemo 2>/dev/null

python3 - "$CAP" <<'PY'
import numpy as np, sys
x=np.fromfile(sys.argv[1],dtype=np.complex64)
if len(x)==0: print("[be] EMPTY capture"); sys.exit()
p=np.abs(x)**2
w=64; env=np.convolve(p,np.ones(w)/w,mode='same')
noise=np.percentile(env,10); peak=env.max(); thr=noise*8
above=env>thr
edges=np.diff(above.astype(int)); nb=int((edges==1).sum())
print(f"[be] samples={len(x)} peak_amp={np.sqrt(peak):.4f} noise_amp={np.sqrt(noise):.5f}")
print(f"[be] peak/noise = {peak/noise:.1f}x  => peak burst SNR ~{10*np.log10(max(peak/noise,1e-9)):.1f} dB")
print(f"[be] bursts(env>8x noise)={nb}  duty={above.mean()*100:.1f}%")
# percentile envelope shape
for q in (50,90,99,99.9):
    print(f"[be]   env p{q} amp = {np.sqrt(np.percentile(env,q)):.4f}")
PY
