#!/usr/bin/env bash
# Survey 5 GHz channels for ambient 802.11 congestion so the over-air coverage
# matrix can run where devourer's weak monitor-injection TX is not drowned by
# real APs. For each candidate channel: capture 1 s of IQ with NO devourer TX,
# replay through the fork, and count decoded (legacy) frames -- a proxy for
# ambient occupancy. Quietest channel = fewest ambient frames = best SNR margin
# for devourer's signal. Prints a sorted table; exits 0.
#
#   bash scripts/channel_survey.sh            # default UNII-1 + UNII-3 set
#   CHANS="36 149 165" bash scripts/channel_survey.sh
set -u
cd "$(dirname "$0")/.."

export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:${LD_LIBRARY_PATH:-}"
ulimit -n 8192

RXGAIN="${RXGAIN:-55}"
BW=20e6
# channel -> centre freq (Hz). UNII-1 (36-48) and UNII-3 (149-165); all non-DFS.
declare -A FREQ=( [36]=5180e6 [40]=5200e6 [44]=5220e6 [48]=5240e6
                  [149]=5745e6 [153]=5765e6 [157]=5785e6 [161]=5805e6 [165]=5825e6 )
CHANS="${CHANS:-36 44 48 149 157 165}"

CAP=$(mktemp /tmp/survey.XXXXXX.cf32)
ERR=$(mktemp /tmp/survey-replay.XXXXXX)
cleanup() { rm -f "$CAP" "$ERR"; }
trap cleanup EXIT INT TERM

echo "ambient 5 GHz survey (1 s/channel, no devourer TX, gain=$RXGAIN):"
RESULTS=$(mktemp /tmp/survey-res.XXXXXX)
for ch in $CHANS; do
  f="${FREQ[$ch]:-}"
  [ -z "$f" ] && { echo "  ch$ch: no freq mapping, skip"; continue; }
  python3 iq_capture.py --freq "$f" --bw $BW --secs 1 --rx-gain "$RXGAIN" --out "$CAP" 2>/dev/null
  rms=$(python3 -c "import numpy as np;x=np.fromfile('$CAP',dtype=np.complex64);print('%.4f'%np.sqrt(np.mean(np.abs(x)**2)))" 2>/dev/null)
  timeout 60 python3 tools_replay_iq.py --in "$CAP" --bw $BW >"$ERR" 2>&1 || true
  n=$(grep -oE 'decoded \(legacy\) frames: [0-9]+' "$ERR" | grep -oE '[0-9]+$' | tail -1)
  printf '%s\n' "$ch ${n:-0} ${rms:-?}" >>"$RESULTS"
  printf '  ch%-4s frames=%-6s rms=%s\n' "$ch" "${n:-0}" "${rms:-?}"
done

echo
echo "quietest channels (fewest ambient frames first):"
sort -k2 -n "$RESULTS" | head -3 | awk '{printf "  ch%-4s frames=%-6s rms=%s\n",$1,$2,$3}'
BEST=$(sort -k2 -n "$RESULTS" | head -1 | awk '{print $1}')
echo "BEST_CHANNEL=$BEST"
rm -f "$RESULTS"
