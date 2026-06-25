#!/usr/bin/env bash
# Capture a REAL devourer VHT transmission and analyse whether the fork decodes it.
# Uses the robust RTL8812AU (0bda:8812) as the VHT TX (the 8821AU wedges on rapid
# re-claims). Verifies the chip actually emits VHT (fixed rate:164 = VHT1SS_MCS4)
# BEFORE capturing, so an empty/legacy capture can't masquerade as a decode failure.
set -u
DEVDIR="$HOME/git/devourer"
# CH/FREQ overridable (use a quiet UNII-3 channel -- e.g. CH=149 FREQ=5745e6 --
# so devourer's weak monitor-injection TX is not drowned by ambient APs; see
# scripts/channel_survey.sh). Default ch36 for back-compat.
CH="${CH:-36}" ; FREQ="${FREQ:-5180e6}" ; BW=20e6
MODE="${1:-vht}"   # vht | ht
MCS="${2:-4}"
RXGAIN="${3:-55}"
TXLOG=$(mktemp /tmp/cav-tx.XXXXXX)
CAP=/tmp/real_${MODE}_mcs${MCS}.cf32

cleanup() { sudo pkill -f WiFiDriverTxDemo 2>/dev/null; rm -f "$TXLOG"; }
trap cleanup EXIT INT TERM

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

sudo pkill -f WiFiDriverTxDemo 2>/dev/null; sleep 3
echo "[cav] starting 8812AU $MODE MCS$MCS TX on ch$CH ..."
if [ "$MODE" = "ht" ]; then
  # HT requires the DEVOURER_TX_HT_MCS=1 gate, else it falls back to 6M legacy.
  TXENV="DEVOURER_TX_MCS=$MCS DEVOURER_TX_HT_MCS=1 DEVOURER_TX_BW=20"
  EXP=$((132 + MCS - 4))   # MGN_MCS0=0x80=128; MCS4=132
  MARK="HT-DATA" ; SIGMARK="HT-SIG"
else
  TXENV="DEVOURER_TX_VHT=1 DEVOURER_TX_VHT_MCS=$MCS DEVOURER_TX_VHT_NSS=1 DEVOURER_TX_BW=20"
  EXP=$((164 + MCS - 4))   # MGN_VHT1SS_MCS0=0xA0=160; MCS4=164
  MARK="VHT-DATA" ; SIGMARK="VHT-SIG-A"
fi
sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=$CH $TXENV \
  "$DEVDIR/build/WiFiDriverTxDemo" >"$TXLOG" 2>&1 &
sleep 10
RATE=$(grep -m1 -oE "fixed rate:[0-9]+" "$TXLOG" | grep -oE "[0-9]+$")
SENT=$(grep -c "sent successfully" "$TXLOG")
echo "[cav] devourer TX: fixed_rate=$RATE (expect $EXP for VHT1SS_MCS$MCS)  sent=$SENT"
if [ "${RATE:-0}" != "$EXP" ]; then
  echo "[cav] ABORT: devourer is NOT emitting $MODE MCS$MCS (rate=$RATE, expect $EXP). TX log tail:"
  tail -6 "$TXLOG"; exit 2
fi

echo "[cav] VHT TX confirmed. Capturing 2s IQ ..."
python3 iq_capture.py --freq $FREQ --bw $BW --secs 2 --rx-gain $RXGAIN --out "$CAP" 2>/dev/null
sudo pkill -f WiFiDriverTxDemo 2>/dev/null

echo "[cav] capture RMS: $(python3 -c "import numpy as np;x=np.fromfile('$CAP',dtype=np.complex64);print('%.4f'%np.sqrt(np.mean(np.abs(x)**2)))" 2>/dev/null)"
echo "[cav] replaying capture through the fork ..."
ERR=$(mktemp /tmp/cav-replay.XXXXXX)
timeout 90 python3 tools_replay_iq.py --in "$CAP" --bw $BW >"$ERR" 2>&1 || true
echo "[cav] legacy frames: $(grep -oE 'decoded \(legacy\) frames: [0-9]+' "$ERR" | tail -1)"
echo "[cav] $SIGMARK CRC-OK (by mcs):"
grep -oE "$SIGMARK\] CRC-OK mcs=[0-9]+" "$ERR" | sort | uniq -c | sort -rn | head -4
echo "[cav] $MARK PASS=$(grep -c "$MARK.*PASS" "$ERR")  fail=$(grep -c "$MARK.*fail" "$ERR")"
echo "[cav] capture kept at $CAP"
rm -f "$ERR"
