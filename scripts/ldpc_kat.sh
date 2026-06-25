#!/usr/bin/env bash
# Known-answer test for the fork's generalized QC-LDPC encoder: build lib/tx/ldpc_kat.cc
# and diff its per-code codewords against ldpc.py (tools/ldpc_kat.py). Bit-exact across all
# 12 IEEE 802.11 codes = the encoder is correct (deterministic; no SDR needed).
set -euo pipefail
cd "$(dirname "$0")/.."
FORK="${FORK:-$HOME/git/gr-ieee802-11}"
g++ -O2 -std=c++17 -I"$FORK/lib" "$FORK/lib/tx/ldpc_kat.cc" -o /tmp/ldpc_kat
/tmp/ldpc_kat > /tmp/ldpc_kat_cpp.txt
python3 tools/ldpc_kat.py > /tmp/ldpc_kat_py.txt
if diff -q /tmp/ldpc_kat_cpp.txt /tmp/ldpc_kat_py.txt >/dev/null; then
  echo "[ldpc-kat] PASS -- C++ encoder bit-exact to ldpc.py across all 12 codes"
else
  echo "[ldpc-kat] FAIL -- mismatch:"; diff /tmp/ldpc_kat_cpp.txt /tmp/ldpc_kat_py.txt; exit 1
fi
