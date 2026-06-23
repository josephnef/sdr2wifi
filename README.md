# Rung-1: gr-ieee802-11 software loopback (no hardware)

Validates the full **802.11a/g/p PHY+MAC** in software: payload → MAC frame →
OFDM encode → (zero-pad + clean channel) → OFDM decode → MAC parse. No SDR, no
USB, no RF. Default config is **10 MHz @ 5.890 GHz = 802.11p** — the narrowband
mode that would fit USB 2.0 on the B220 Mini. Also runs at 5 MHz (quarter-rate)
and 20 MHz (standard Wi-Fi).

## Run
```sh
./run.sh            # 10 MHz, 3 s
./run.sh 5e6 2      # 5 MHz
./run.sh 20e6 2     # 20 MHz
```
Expect e.g. `decoded frames: 62` and `RESULT: PASS`.

## What's here
- `loopback_headless.py` — the flowgraph (top_block, no Qt). Counts decoded frames.
- `wifi_phy_hier.py` — the PHY hier block, generated with `grcc` from
  gr-ieee802-11's `examples/wifi_phy_hier.grc` (it is NOT a compiled C++ block).
- `run.sh` — wrapper that sets the two macOS-specific env tweaks below.

## Toolchain (how it was built on this Mac)
- GNU Radio **3.10.12.0** via Homebrew (uses Python **3.14** in a venv under the Cellar;
  the global `/opt/homebrew/bin/python3.14` imports it too).
- OOT modules **gr-foo** + **gr-ieee802-11**, branch **maint-3.10** (must match GR 3.10),
  built with `pybind11` and installed into `/opt/homebrew`:
  ```sh
  cmake -S . -B build -DCMAKE_INSTALL_PREFIX=/opt/homebrew -DCMAKE_PREFIX_PATH=/opt/homebrew -DCMAKE_BUILD_TYPE=Release
  cmake --build build -j && cmake --install build
  ```
  Source clones are in `/tmp/grwifi/` (re-clone via the proxy if gone).

## macOS gotchas (already handled in run.sh / the script)
1. **`ulimit -n`** — macOS default is 256; GNU Radio's vmcircbuf opens many fds and
   fails with *"Too many open files" / shmget invalid*. `run.sh` raises it to 8192.
2. **Output buffers** — gr-ieee802-11's OFDM TX emits a whole frame as one ~2 MB tagged
   burst, so every stream block on the sample path calls `set_min_output_buffer(1<<22)`
   (else *"Buffer too small for min_noutput_items"* on the pad block).
3. A `brew upgrade gnuradio` can orphan the OOT installs (they're in `/opt/homebrew`
   but not brew-tracked) — rebuild gr-foo + gr-ieee802-11 if blocks go missing.

## Next rungs (need hardware)
- **Rung 2:** single-B210 RF loopback — TX `TRXA` (ch0) → SMA cable + 60 dB pads →
  RX `RXB` (ch1, ant `RX2`); one flowgraph, 5 MHz fits USB 2.0.
- Then a real over-air 802.11p RX (5.9 GHz) once a USB-3 link is sorted.
