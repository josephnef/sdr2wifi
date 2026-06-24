# sdr2wifi

[![ci](https://github.com/josephnef/sdr2wifi/actions/workflows/ci.yml/badge.svg)](https://github.com/josephnef/sdr2wifi/actions/workflows/ci.yml)

A GNU Radio 802.11 receiver bring-up and a **synthetic validation harness** for a forked
[`gr-ieee802-11`](https://github.com/josephnef/gr-ieee802-11) (branch `feat/ht-vht-rx`) that
decodes **modern OFDM**: 802.11a/g/p legacy, 802.11n **HT** at 20 and 40 MHz, and **2×2
MIMO** — all the way to a CRC-32-checked PSDU, with no SDR required for testing. The same
PHY/MAC flowgraph also runs over real hardware via a software→cable→over-air "rung ladder".

## What it decodes

| Format | MCS | Bandwidth | Streams | FFT | Modulation / coding |
|---|---|---|---|---|---|
| 802.11a/g/p (legacy) | 0–7 | 20 MHz | 1 | 64 | BPSK…64-QAM, BCC ½–¾ |
| 802.11n HT20 (SISO) | 0–7 | 20 MHz | 1 | 64 | BPSK…64-QAM, BCC ½–⅚ |
| 802.11n HT40 (SISO) | 0–7 | 40 MHz | 1 | 128 | BPSK…64-QAM, BCC ½–⅚ |
| 802.11n HT20 2×2 MIMO | 8–15 | 20 MHz | 2 | 64 | per-stream MCS 0–7, MMSE separation |
| 802.11ac VHT | 0–9 | — | — | — | extension seams only (enums / format / FEC), no decode |

Every mode is validated to a passing **CRC-32** over the recovered PSDU.

## Architecture

The project is two repos working together:

1. **The decoder — [`josephnef/gr-ieee802-11`](https://github.com/josephnef/gr-ieee802-11)
   (`feat/ht-vht-rx`).** A fork of bastibl's OOT module that adds the modern-OFDM receive
   path: HT-SIG detection, the 128-FFT HT40 front-end, the HT-LTF channel estimate, 2×2
   MMSE stream separation, the HT interleaver / stream parser, and VHT seams. This is a
   required dependency.
2. **This repo (`sdr2wifi`) — the harness + flowgraphs.** A synthetic waveform generator and
   an offline RX replay chain to validate the decoder without any radio, plus a three-rung
   ladder that runs the same PHY over progressively more real hardware.

The "rung ladder" keeps one PHY/MAC flowgraph and changes only what sits between PHY-out and
PHY-in, so a failure localizes to the layer you just added:

- **Rung 1 — software loopback** (`loopback_headless.py`): PHY TX → pad → clean
  `channel_model` (unit tap) → PHY RX. No SDR, no USB, no RF. Proves encode↔decode.
- **Rung 2 — single-B210 cable loopback** (`rf_loopback.py`): the internal channel is
  replaced by real RF over one full-duplex B210: TX out `TRXA` → SMA + attenuators → RX in
  `RXB`.
- **Rung 3 — real over-air RX** (`wifi_rx_sniff.py` / `run_rx.sh`): the SDR as a pure
  receiver decoding a real external transmitter.

## Requirements

- **GNU Radio 3.10.x** and **Python 3.14**.
- OOT modules **`gr-foo`** and the **`gr-ieee802-11` fork** (branch `feat/ht-vht-rx`), built
  against the same GNU Radio. Their exact pinned commits live in [`deps.env`](deps.env)
  (`gr-foo` is vanilla upstream; the modern-OFDM decode is in the fork).

## Reproduce

**Containerized (hermetic, no host setup beyond Docker)** — this is exactly what CI runs:

```sh
docker build -t sdr2wifi . && docker run --rm sdr2wifi   # builds deps, runs the test matrix
```

**Native** — build the pinned OOT deps into a prefix, then run the tests:

```sh
GRWIFI_PREFIX=~/grwifi-install ./scripts/build-deps.sh   # clone+build gr-foo + the fork
./scripts/run-tests.sh                                    # asserting synthetic test matrix
```

`build-deps.sh` installs into `$GRWIFI_PREFIX` (default `~/grwifi-install`; use `/opt/homebrew`
on macOS Homebrew). The wrapper scripts (`run.sh`, `run_rx.sh`, `run-tests.sh`) discover the
OOT site-packages under that prefix and set `PYTHONPATH` / `LD_LIBRARY_PATH` / `ulimit` for
you.

## Synthetic validation (no hardware)

The fastest path: generate a baseband capture in software, replay it through the RX-only
chain, and check for `CRC-32 PASS`. The generator mirrors the decoder's own TX math
(scrambler / convolutional code / interleaver / pilots), so a correct receiver must decode it.

```sh
# set the OOT env once (or just use ./run.sh / ./run_rx.sh which do it for you)
export PREFIX="${GRWIFI_PREFIX:-$HOME/grwifi-install}"
OOT=$(find "$PREFIX" -name ieee802_11 -type d -path '*packages*' | head -1); OOT="${OOT%/ieee802_11}"
export PYTHONPATH="$OOT:$PWD" LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64:$LD_LIBRARY_PATH"
ulimit -n 8192

# legacy 802.11a/g
./tools_gen_wifi.py --mode legacy --out /tmp/w.cf32
./tools_replay_iq.py --in /tmp/w.cf32 --bw 20e6                 # -> decoded frames: N

# HT20 SISO, MCS 0..7
./tools_gen_wifi.py --mode ht --mcs 5 --out /tmp/w.cf32
./tools_replay_iq.py --in /tmp/w.cf32 --bw 20e6                 # -> [HT-DATA] ... CRC-32 PASS

# HT40 SISO, MCS 0..7  (use a wide inter-frame gap for the longer frames)
./tools_gen_wifi.py --mode ht40 --mcs 3 --gap 2000 --out /tmp/w.cf32
./tools_replay_iq.py --in /tmp/w.cf32 --bw 40e6                 # -> [HT-DATA] ... CRC-32 PASS

# HT20 2×2 MIMO, MCS 8..15  (writes two antenna files)
./tools_gen_wifi.py --mode ht_mimo --mcs 8 --gap 2000 --out /tmp/w.cf32
./tools_replay_iq.py --in /tmp/w.cf32 --in2 /tmp/w_ant1.cf32 --bw 20e6   # -> [HT-MIMO] ... CRC-32 PASS

# MIMO data-model self-test (bit-exact reference receiver, no flowgraph)
./tools_gen_wifi.py --mode ht_mimo --mcs 8 --selftest          # -> bit_errors=0 ... PASS
```

`tools_gen_wifi.py` flags: `--mode {legacy|ht|ht40|ht_mimo}`, `--mcs N`, `--out PATH`,
`--reps N`, `--gap N` (zero samples between repeats — use `--gap 2000` for HT40/MIMO),
`--snr DB`, `--selftest`. `tools_replay_iq.py` flags: `--in`, `--in2` (2nd MIMO antenna →
2-input receiver), `--bw` (≥40e6 selects the 128-FFT HT40 front-end), `--freq`.

`tools_record_iq.py` captures raw IQ from an SDR to a `.cf32` file you can feed straight into
`tools_replay_iq.py` for offline debugging.

## Rung 1 — software loopback

```sh
./run.sh            # 10 MHz @ 5.89 GHz (802.11p), 3 s -> "decoded frames: N", RESULT: PASS
./run.sh 5e6 2      # 5 MHz (quarter-rate)
./run.sh 20e6 2     # 20 MHz (standard Wi-Fi)
```

## Rung 2 — single-B210 cable loopback

Same PHY, real RF over one full-duplex B210: TX out `TRXA` (ch0, ant `TX/RX`) → SMA cable +
attenuators → RX in `RXB` (ch1, ant `RX2`). Defaults 5 MHz @ 5.89 GHz with `sc8` over-the-wire
so the full-duplex stream fits USB-2.

```sh
ulimit -n 8192; export PYTHONPATH="$PWD"
# macOS also: export UHD_IMAGES_DIR=/opt/homebrew/share/uhd/images
./rf_loopback.py --check                     # open + configure the B210, NO transmit (sanity)
./rf_loopback.py --secs 5                     # real loopback (cable + pads must be wired)
./rf_loopback.py --tx-gain 60 --rx-gain 30    # tune levels until frames decode
```

> **Hardware safety:** never wire `TRXA → RXB` without **≥40 dB (use ~60 dB) of
> attenuation** — it protects the RX front-end. Start TX gain low. If no frames decode at
> 5 MHz, confirm UHD prints *"Operating over USB 3"* (full-duplex at higher rates needs
> USB-3) or drop bandwidth.

## Rung 3 — real over-air RX

```sh
./run_rx.sh --check                 # open + configure the SDR, no receive
./run_rx.sh --secs 10               # listen 10 s on 5 GHz ch36 (5180 MHz), 20 MHz
./run_rx.sh --freq 2437e6           # 2.4 GHz ch6 instead
```

Pair with any real 802.11 transmitter on the same channel.

## Environment notes (both OSes)

- **`ulimit -n 8192`** is required before any run — GNU Radio's vmcircbuf opens many file
  descriptors and fails ("Too many open files" / "shmget invalid") at the default limit.
- **`set_min_output_buffer(1 << 22)`** is applied to every stream block on the sample path:
  gr-ieee802-11's OFDM TX emits a whole frame as one ~2 MB tagged burst, so a block between
  PHY-out and PHY-in needs the big buffer or you get "Buffer too small for
  min_noutput_items".
- The flowgraph is driven by a separate **message path** (`message_strobe` →
  `ieee802_11.mac` → PHY) from the **sample path**; decoded frames are counted via
  `blocks.message_debug().num_messages()`.
- `rf_loopback.py` uses **`otw=sc8`** so the full-duplex stream fits USB-2 at 5 MHz.

## Repo layout

| File | Purpose |
|---|---|
| `loopback_headless.py` | Rung 1: software loopback flowgraph (TX → clean channel → RX). |
| `rf_loopback.py` | Rung 2: single-B210 full-duplex cable loopback. |
| `wifi_rx_sniff.py` | Rung 3: SDR as a pure over-air 802.11 receiver. |
| `run.sh` | Rung-1 wrapper (sets OOT env + `ulimit`, Linux & macOS). |
| `run_rx.sh` | Rung-3 wrapper for `wifi_rx_sniff.py`. |
| `tools_gen_wifi.py` | Synthetic 802.11 waveform generator (legacy / HT / HT40 / 2×2 MIMO). |
| `tools_replay_iq.py` | RX-only replay chain for `.cf32` captures (1 or 2 antennas). |
| `tools_record_iq.py` | Raw-IQ recorder from an SDR for offline replay. |
| `wifi_phy_hier.py` | The PHY hier block — **grcc-generated, do not hand-edit**. |
| `wifi_phy_hier.block.yml` | GRC block descriptor for `wifi_phy_hier` (GRC use only). |
| `deps.env` | Pinned dependency commits (gr-foo, the gr-ieee802-11 fork). |
| `scripts/build-deps.sh` | Clone + build the pinned OOT deps into a prefix. |
| `scripts/run-tests.sh` | Asserting synthetic test matrix (the CI gate). |
| `Dockerfile` | Hermetic build + test environment (also the CI image). |
