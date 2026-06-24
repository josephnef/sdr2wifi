# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An incremental "rung ladder" for bringing up an **802.11a/g/p PHY+MAC** (via the
gr-ieee802-11 OOT module) under GNU Radio on this Mac. Each rung adds one piece of
real hardware while keeping the same PHY/MAC flowgraph, so a failure can be localized
to the newly added layer:

- **Rung 1** — `loopback_headless.py`: pure software. PHY TX → pad → `channels.channel_model`
  (clean, unit tap) → PHY RX. No SDR, no USB, no RF. Proves encode↔decode.
- **Rung 2** — `rf_loopback.py`: one B210 full-duplex over a physical cable. The internal
  channel model is replaced by real RF: TX out `TRXA` (ch0) → SMA + ~60 dB attenuators →
  RX in `RXB` (ch1). Same PHY block.
- **Next rung** — real over-air 802.11p RX, once a USB-3 link is sorted (see README "Next rung").

Both scripts instantiate the **same** `wifi_phy_hier` block, which does TX encode *and*
RX decode in one hier block. The only thing that changes between rungs is what sits
between PHY-out and PHY-in.

## Running (these are the "tests")

There is no unit-test framework or linter. Validation = run a script and check it prints
`RESULT: PASS` / a non-zero decoded-frame count.

```sh
# Rung 1 (software, no hardware) — wrapper sets ulimit + PYTHONPATH for you:
./run.sh            # 10 MHz @ 5.89 GHz (802.11p), 3 s  -> "decoded frames: N", RESULT: PASS
./run.sh 5e6 2      # 5 MHz (quarter-rate)
./run.sh 20e6 2     # 20 MHz (standard Wi-Fi)

# Rung 2 (needs B210 + cable + attenuators). Set env first (run.sh does NOT cover rung 2):
ulimit -n 8192; export PYTHONPATH="$PWD" UHD_IMAGES_DIR=/opt/homebrew/share/uhd/images
./rf_loopback.py --check                    # open+configure B210, print config, NO transmit
./rf_loopback.py --secs 5                   # real loopback (cable+pads must be wired)
./rf_loopback.py --tx-gain 60 --rx-gain 30  # tune levels until frames decode
```

**Hardware safety (rung 2):** never wire `TRXA → RXB` without ≥40 dB (use ~60 dB) of
attenuation — it protects the RX front-end. Start TX gain low.

## Environment that must be in place

The scripts import `ieee802_11` and `foo` as installed Python modules — they will fail
to import unless this exact toolchain is present:

- **GNU Radio 3.10.12.0** (Homebrew), which runs **Python 3.14**. `run.sh` hardcodes
  `/opt/homebrew/bin/python3.14`; use that interpreter, not a bare `python3`.
- **OOT modules** `gr-foo` + `gr-ieee802-11`, branch **maint-3.10** (must match GR 3.10),
  built and installed into `/opt/homebrew`. They live there but are **not brew-tracked**,
  so `brew upgrade gnuradio` can orphan them — rebuild if `import ieee802_11`/`import foo`
  start failing. Source clones expected under `/tmp/grwifi/`. Rebuild recipe:
  ```sh
  cmake -S . -B build -DCMAKE_INSTALL_PREFIX=/opt/homebrew -DCMAKE_PREFIX_PATH=/opt/homebrew -DCMAKE_BUILD_TYPE=Release
  cmake --build build -j && cmake --install build
  ```

## Non-obvious constraints (will bite you if changed)

- **`ulimit -n 8192`** is mandatory before any run. macOS default (256) is too low for
  GNU Radio's vmcircbuf; you get "Too many open files" / "shmget invalid".
- **`set_min_output_buffer(1 << 22)`** is applied to every stream block on the sample path.
  gr-ieee802-11's OFDM TX emits a whole frame as one ~2 MB tagged burst; without the big
  buffer you get "Buffer too small for min_noutput_items" on the pad block. If you add a
  block between PHY-out and PHY-in, it needs this call too.
- **`rf_loopback.py` uses `otw_format=sc8`** so the full-duplex stream fits USB-2 at 5 MHz.
  10/20 MHz, or sc16, need a USB-3 link. "No frames" at rung 2 is usually the USB-2 ceiling
  (overruns/underruns) — confirm UHD prints "Operating over USB 3", or drop bandwidth.
- The flowgraph is driven by a `message_strobe` → `ieee802_11.mac` → PHY message path; the
  sample path is a separate stream path. Decoded frames are counted via
  `blocks.message_debug().num_messages()`.

## `wifi_phy_hier.py` is generated — do not hand-edit

`wifi_phy_hier.py` is **grcc output**, not a compiled C++ block. It was generated from
gr-ieee802-11's `examples/wifi_phy_hier.grc` (see `grc_source:` in
`wifi_phy_hier.block.yml`). To change the PHY, edit the `.grc` in gr-ieee802-11 and
regenerate with `grcc`, rather than editing the `.py` directly. `wifi_phy_hier.block.yml`
is the GRC block descriptor (for use inside GRC), not needed for the headless scripts.
