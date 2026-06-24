#!/usr/bin/env bash
# Build the OOT dependencies (gr-foo + the gr-ieee802-11 fork) at the pinned refs
# in deps.env, installing into a prefix. Reproducible-build helper for sdr2wifi.
#
#   GRWIFI_PREFIX=~/grwifi-install ./scripts/build-deps.sh   # default prefix
#
# Requires: git, cmake, a C++ toolchain, and GNU Radio 3.10 dev files already present
# (see README -> "Reproduce"). Idempotent: re-running rebuilds in place.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/deps.env"

PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
WORK="${BUILD_WORKDIR:-${TMPDIR:-/tmp}/sdr2wifi-deps}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 2)}"
mkdir -p "$PREFIX" "$WORK"

build_one() {
    local name="$1" repo="$2" ref="$3" src="$WORK/$1"
    echo "==> $name @ $ref"
    if [ ! -d "$src/.git" ]; then
        git clone "$repo" "$src"
    fi
    git -C "$src" fetch origin
    git -C "$src" checkout -q "$ref"
    cmake -S "$src" -B "$src/build" \
        -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_PREFIX_PATH="$PREFIX" \
        -DCMAKE_BUILD_TYPE=Release
    cmake --build "$src/build" -j "$JOBS"
    cmake --install "$src/build"
}

build_one gr-foo         "$GRFOO_REPO"  "$GRFOO_REF"
build_one gr-ieee802-11  "$GRIEEE_REPO" "$GRIEEE_REF"

OOT_PY="$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' 2>/dev/null | head -1)"
OOT_PY="${OOT_PY%/ieee802_11}"
echo
echo "Done. OOT modules installed under: $PREFIX"
echo "Run the harness with:"
echo "  ./scripts/run-tests.sh        # or: ./run.sh 20e6 2"
echo "(the wrapper scripts set PYTHONPATH/LD_LIBRARY_PATH from $PREFIX themselves,"
echo " including the Debian/Ubuntu multiarch libdir.)"
