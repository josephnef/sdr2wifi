#!/usr/bin/env bash
# Does a given devourer adapter RECEIVE anything? Tune to a busy channel with
# DEVOURER_RX_DUMP_ALL (un-gated per-frame dump) and count ambient frames. If 0 on a
# busy channel, that adapter's RX (antenna/chip) is the problem.
#   VID=0x2357 PID=0x0120 CH=1 bash scripts/rx_sanity.sh   # 8821AU on 2.4GHz ch1
set -u
DEVDIR="$HOME/git/devourer"
VID="${VID:-0x0bda}" ; PID="${PID:-0x8812}" ; CH="${CH:-1}" ; SECS="${SECS:-18}"
RXLOG=$(mktemp /tmp/rxsan.XXXXXX)
cleanup() { sudo pkill -9 -f WiFiDriverDemo 2>/dev/null; rm -f "$RXLOG"; }
trap cleanup EXIT INT TERM
sudo pkill -9 -f WiFiDriverDemo 2>/dev/null; sleep 2
sudo env DEVOURER_VID=$VID DEVOURER_PID=$PID DEVOURER_CHANNEL=$CH \
  DEVOURER_RX_DUMP_ALL=1 DEVOURER_RX_KEEP_CORRUPTED=1 \
  "$DEVDIR/build/WiFiDriverDemo" >"$RXLOG" 2>&1 &
sleep $((SECS + 14))
sudo pkill -9 -f WiFiDriverDemo 2>/dev/null
N=$(grep -c corrupt-any "$RXLOG")
L=$(grep -c "Listening air" "$RXLOG")
echo "[rxsan] $VID:$PID ch$CH -> listening=$L  ambient frames=$N"
grep -oE "corrupt-any>len=[0-9]+ crc_err=[0-9]+ icv_err=[0-9]+ rate=[0-9]+ rssi=[-0-9,]+" "$RXLOG" | head -4
[ "$N" -gt 0 ] && echo "[rxsan] RX WORKS" || echo "[rxsan] RX DEAF (no ambient on a busy channel)"
