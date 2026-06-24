# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this is

`sdr2wifi` is two things: (1) a **synthetic validation harness** for a forked
`gr-ieee802-11` that decodes modern OFDM, and (2) a software→cable→over-air **rung ladder**
that runs one 802.11 PHY/MAC flowgraph over progressively more real hardware.

The decoder lives in a **separate repo** — `~/git/gr-ieee802-11`
(github.com/josephnef/gr-ieee802-11, branch `feat/ht-vht-rx`). It decodes:

- 802.11a/g/p legacy (MCS 0–7, 20 MHz, 64-FFT)
- 802.11n HT20 SISO (MCS 0–7, 20 MHz, 64-FFT)
- 802.11n HT40 SISO (MCS 0–7, 40 MHz, 128-FFT)
- 802.11n HT20 2×2 MIMO (MCS 8–15, 20 MHz, 64-FFT, MMSE separation)
- 802.11ac VHT: **extension seams only** (enums / format / FEC type), no decode

This repo holds the flowgraphs and the test tooling; behavioral changes to the *decode* go
in the fork, not here.

The rung ladder (one PHY block, only the channel changes between rungs):

- **Rung 1** — `loopback_headless.py`: PHY TX → pad → clean `channel_model` (unit tap) → PHY
  RX. No SDR/USB/RF. Proves encode↔decode.
- **Rung 2** — `rf_loopback.py`: one full-duplex B210, TX out `TRXA` (ch0) → SMA + ~60 dB
  attenuators → RX in `RXB` (ch1). Same PHY block.
- **Rung 3** — `wifi_rx_sniff.py` / `run_rx.sh`: SDR as a pure over-air receiver.

## How to validate ("the tests")

There is no unit-test framework or linter. Validation = run a flowgraph and confirm a
non-zero decoded-frame count / a `CRC-32 PASS` line. The synthetic generator
(`tools_gen_wifi.py`) mirrors the decoder's own TX math, so a correct RX must decode it; the
RX-only replay chain (`tools_replay_iq.py`) plays a `.cf32` capture deterministically with no
SDR.

```sh
# env: ./run.sh / ./run_rx.sh set it for you; standalone:
export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:$LD_LIBRARY_PATH"; ulimit -n 8192

./tools_gen_wifi.py --mode legacy --out /tmp/w.cf32 && ./tools_replay_iq.py --in /tmp/w.cf32 --bw 20e6
./tools_gen_wifi.py --mode ht   --mcs 5 --out /tmp/w.cf32 && ./tools_replay_iq.py --in /tmp/w.cf32 --bw 20e6
./tools_gen_wifi.py --mode ht40 --mcs 3 --gap 2000 --out /tmp/w.cf32 && ./tools_replay_iq.py --in /tmp/w.cf32 --bw 40e6
./tools_gen_wifi.py --mode ht_mimo --mcs 8 --gap 2000 --out /tmp/w.cf32 \
  && ./tools_replay_iq.py --in /tmp/w.cf32 --in2 /tmp/w_ant1.cf32 --bw 20e6
./tools_gen_wifi.py --mode ht_mimo --mcs 8 --selftest      # bit-exact reference RX, no flowgraph
```

- `--bw >= 40e6` selects the 128-FFT HT40 front-end; `--in2` selects the 2-input MIMO RX.
- **Use `--gap 2000` for HT40 and MIMO reps.** Long frames with a small gap get clipped when
  the next frame's sync tag arrives early (a harness artifact, not a decode bug).
- The file-replay sync **yield varies run-to-run** (some reps not detected) — this is a
  replay-harness artifact. When a frame *is* detected it decodes correctly (0 CRC failures);
  judge by `fail=0`, not by detecting every rep.

**The regression gate is `scripts/run-tests.sh`** (run by CI via the `Dockerfile`; deps pinned
in `deps.env`, built by `scripts/build-deps.sh`). It hard-gates on the deterministic checks —
the bit-exact MIMO `--selftest` (all MCS), legacy/HT20/HT40 replay (single sync chain,
reliable), and the Rung-1 loopback — and treats the **2x2 MIMO end-to-end replay as
informational** (WARN, not FAIL): two independent sync chains diverge on frame-start
nondeterministically under noise at any SNR, corrupting whole frames, so MIMO *correctness* is
gated by `--selftest`, not the flowgraph. Run it after any decode change:
`./scripts/run-tests.sh` (add `--full` for every MCS).

## Where the decode lives (the fork)

In `~/git/gr-ieee802-11` (branch `feat/ht-vht-rx`):

- `lib/frame_equalizer_impl.cc` — the format-detecting equalizer. `sniff_ht_sig()` is the
  format-detection seam (HT-SIG QBPSK today; VHT-SIG-A would branch here). SISO HT path:
  `ht_begin`/`ht_estimate_ltf40`/`ht_data_symbol`/`ht_finish`. 2×2 MIMO path:
  `mimo_begin`/`mimo_estimate_ltf`/`mimo_data_symbol`/`mimo_finish`. `make(..., fft_len,
  n_rx)` selects 64/128-FFT and 1/2 RX antennas.
- `lib/sync_long.cc` — 64- and 128-tap L-LTF matched filters (`make(..., fft_len)`).
- `include/ieee802_11/mapper.h` — `Encoding` (legacy 0–7, `HT_MCS_*` 8–39, `VHT_MCS_*`
  40–49), `Format` {LEGACY,HT,VHT}, `FecType` {BCC,LDPC}.
- `lib/utils.{h,cc}` — `ofdm_param` (per-format geometry; `format`/`fec_type` fields).

After editing the fork, rebuild and **re-install** into the prefix, then re-run the harness:
```sh
cmake --build ~/git/gr-ieee802-11/build -j && cmake --install ~/git/gr-ieee802-11/build
```

## Environment that must be in place

The scripts import `ieee802_11` and `foo` as installed Python modules and fail to import
unless this toolchain is present:

- **GNU Radio 3.10.x**, **Python 3.14**.
- `gr-foo` + the `gr-ieee802-11` fork (branch `feat/ht-vht-rx`), built against the same GNU
  Radio and installed into a prefix:
  - **Linux:** `~/grwifi-install` (override with `GRWIFI_PREFIX`). `run.sh` / `run_rx.sh`
    auto-discover the OOT site-packages dir (`OOT_PY`) under `$PREFIX` and set
    `PYTHONPATH` / `LD_LIBRARY_PATH`.
  - **macOS (Homebrew):** `/opt/homebrew`.
- Rebuild recipe (same for both OOT modules, change `PREFIX`):
  ```sh
  cmake -S . -B build -DCMAKE_INSTALL_PREFIX="$PREFIX" \
        -DCMAKE_PREFIX_PATH="$PREFIX" -DCMAKE_BUILD_TYPE=Release
  cmake --build build -j && cmake --install build
  ```

## Non-obvious constraints (will bite you if changed)

- **`ulimit -n 8192`** before any run — GNU Radio's vmcircbuf opens many fds; the default
  limit gives "Too many open files" / "shmget invalid".
- **`set_min_output_buffer(1 << 22)`** on every stream block on the sample path: the OFDM TX
  emits a whole frame as one ~2 MB tagged burst, else "Buffer too small for
  min_noutput_items" on the pad block. A new block between PHY-out and PHY-in needs this too.
- `rf_loopback.py` uses **`otw=sc8`** so the full-duplex stream fits USB-2 at 5 MHz; 10/20
  MHz or `sc16` need USB-3. No-frames at Rung 2 is usually the USB-2 ceiling — confirm UHD
  prints "Operating over USB 3" or drop bandwidth.
- The flowgraph is driven by a **message path** (`message_strobe` → `ieee802_11.mac` → PHY)
  separate from the **sample path**; decoded frames are counted via
  `blocks.message_debug().num_messages()`.
- **Hardware safety (Rung 2):** never wire `TRXA → RXB` without ≥40 dB (use ~60 dB) of
  attenuation — it protects the RX front-end. Start TX gain low.

## Known scope limits for real over-air

- **2×2 MIMO sync:** the replay flowgraph uses two *independent* sync chains, which co-index
  only because the synthetic captures are noiseless with identical timing. Real over-air
  (with CFO and noise) needs sync driven off antenna 0 and applied to antenna 1 (shared LO),
  i.e. a `sync_long` co-indexed passthrough output. `wifi_rx_sniff.py` is the real-RX
  flowgraph and is wired for the legacy/single-antenna path.
- **HT40 frame sync:** the full-128 L-LTF matched filter has an intrinsic lag-26
  autocorrelation sidelobe (the non-HT-duplicate preamble); `search_frame_start`'s
  "both peaks in the top-4 and exactly 128 apart" rule disambiguates on clean signal and may
  need hardening at low SNR.

## `wifi_phy_hier.py` is generated — do not hand-edit

`wifi_phy_hier.py` is **grcc output**, not a compiled C++ block, generated from
gr-ieee802-11's `examples/wifi_phy_hier.grc` (see `grc_source:` in
`wifi_phy_hier.block.yml`). To change the PHY, edit the `.grc` in gr-ieee802-11 and
regenerate with `grcc`. `wifi_phy_hier.block.yml` is the GRC block descriptor (for use inside
GRC), not needed for the headless scripts.
